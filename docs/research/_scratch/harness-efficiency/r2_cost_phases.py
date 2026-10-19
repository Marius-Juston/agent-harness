"""Round-2 cost anatomy for #254: why researchers and the lead cost what they cost.

Usage: python3 r2_cost_phases.py <claude-project-dir> <repo-E-repo> [--exclude <session-id>]... > out.txt

Reads the main session transcripts (*.jsonl = lead) and subagent transcripts (*/subagents/*.jsonl, agent type from the
.meta.json). Prices are the per-M fit from extra_stats.py (input 4.09, output 20.05, cache read 0.20, cache write 7.95).
Transcript output_tokens are streaming snapshots that undercount (4.2 M vs 27.0 M Opus 5.5 output in the cost-state records, summed per process segment), so the
billed output of a turn is its visible characters (thinking summary, text, tool input) times the fitted tokens per
character, plus an equal share per turn of the rest of the cost-state output (hidden thinking; an assumption). Tokens per
character of content entering the context come from a least-squares fit of cache growth (section C).

Prints:
  A  researcher turns and cost by phase of the tool calls a turn makes (a turn's whole cost goes to its phases)
  B  carried context cost by content category: tokens x (cache write once + cache read on every later turn)
  C  context growth per turn (median over runs), first-turn base, and calibration of chars per token
  D  note size vs auditor / extender cost (size of the target note at the run's start, from git)
  E  dev lit: output per call, papers returned vs followed up vs cited in the run's own writing, fields reused
  G  researcher levers: persisted-output re-reads, note reads, batching, a search/source helper (Opus or Sonnet
     prices), Sonnet prices for the role, tool-result clearing (naive and oracle), fixed overhead
  F  lead: context per call, what fills it, episodes by waking event, polling, compactions; simulated compaction
"""

import glob
import json
import os
import re
import statistics as st
import subprocess
import sys
from collections import Counter, defaultdict
from itertools import pairwise

P_IN, P_OUT, P_CR, P_CW = 4.09e-6, 20.05e-6, 0.20e-6, 7.95e-6
HIDDEN = 0.0  # billed output tokens per turn not visible in the transcript; set from cost-state records below
TPC, VTPC = 0.42, 0.44  # context tokens per char of tool/user content and of assistant content; set by the fit

args = sys.argv[1:]
EXCL = [args[i + 1] for i, a in enumerate(args) if a == "--exclude"]
PROJ, REPO = [a for i, a in enumerate(args) if a != "--exclude" and (i == 0 or args[i - 1] != "--exclude")][:2]

NOTE_RE = re.compile(r"docs/research/[\w./-]+?\.md")
READ_VERBS = {"sed", "cat", "head", "tail", "grep", "rg", "awk", "wc", "less", "nl", "diff", "python3", "uv"}


def text_of(c):
    if isinstance(c, str):
        return c
    out = []
    for b in c or []:
        if b.get("type") == "text":
            out.append(b.get("text", ""))
        elif b.get("type") == "image":
            out.append("x" * 6000)
        elif b.get("type") == "tool_reference":
            out.append(json.dumps(b))
    return "\n".join(out)


def note_key(path):
    m = NOTE_RE.search(path or "")
    return m.group(0) if m else None


def strip_cd(cmd):
    return re.sub(r'^\s*cd\s+("[^"]*"|\S+)\s*(&&|;)\s*', "", cmd.strip())


# ---------------------------------------------------------------- loading


def load(f, at):
    """One run: turns (unique assistant messages in order) and pieces (content entering the context after a turn)."""
    turns, pieces, compact = [], [], []
    by_id = {}
    prompt = None
    effort = Counter()
    cs_segs = []  # one [totalCostUSD, outputTokens] per Claude Code process: a drop in the running total starts one
    with open(f, errors="ignore") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("type")
            k = len(turns) - 1  # index of the last turn seen; pieces enter the context of turn k + 1
            if t == "cost-state":
                tot = d.get("totalCostUSD", 0) or 0
                out = d.get("modelUsage", {}).get("claude-opus-5-5", {}).get("outputTokens", 0)  # Opus only
                if not cs_segs or tot < cs_segs[-1][0]:
                    cs_segs.append([tot, out])
                else:
                    cs_segs[-1] = [tot, out]
            if t == "api-request":
                effort[(d.get("params", {}).get("output_config") or {}).get("effort", "?")] += 1
            elif t == "assistant":
                m = d["message"]
                mid = m.get("id")
                u = m.get("usage") or {}
                if mid not in by_id:
                    tr = dict(
                        ts=d.get("timestamp", ""),
                        cr=u.get("cache_read_input_tokens", 0),
                        cw=u.get("cache_creation_input_tokens", 0),
                        inp=u.get("input_tokens", 0),
                        out=u.get("output_tokens", 0),
                        tools=[],
                        vis=0,
                    )
                    by_id[mid] = tr
                    turns.append(tr)
                tr = by_id[mid]
                tr["out"] = max(tr["out"], u.get("output_tokens", 0))
                for b in m.get("content", []):
                    if b.get("type") == "tool_use":
                        tr["tools"].append((b["id"], b["name"], b.get("input", {})))
                        tr["vis"] += len(json.dumps(b.get("input", {})))
                    else:
                        tr["vis"] += len(b.get("text") or b.get("thinking") or "")
            elif t == "user":
                c = d["message"].get("content")
                if isinstance(c, str):
                    if prompt is None:
                        prompt = c
                    pieces.append(dict(k=k, kind=user_kind(c, at), n=len(c), tid=None, txt=None))
                else:
                    for b in c or []:
                        if b.get("type") == "tool_result":
                            txt = text_of(b.get("content"))
                            pieces.append(dict(k=k, kind="result", n=len(txt), tid=b.get("tool_use_id"), txt=txt))
                        elif b.get("type") == "text":
                            if prompt is None:
                                prompt = b.get("text", "")
                            pieces.append(dict(k=k, kind="prompt", n=len(b.get("text", "")), tid=None, txt=None))
            elif t == "attachment":
                a = d.get("attachment", {})
                r = d.get("rendered") or []
                n = sum(len(x.get("content", "")) if isinstance(x, dict) else len(str(x)) for x in r)
                if n:
                    ak = a.get("type", "?")
                    if ak == "queued_command":
                        pr = a.get("prompt", "")
                        ak = user_kind(pr if isinstance(pr, str) else json.dumps(pr), at)
                    elif ak == "hook_additional_context":
                        ak = "hook"
                    else:
                        ak = "att:" + ak
                    pieces.append(dict(k=k, kind=ak, n=n, tid=None, txt=None))
            elif t == "system" and d.get("subtype") == "compact_boundary":
                compact.append(len(turns))
    return dict(
        at=at,
        f=f,
        turns=turns,
        pieces=pieces,
        compact=compact,
        prompt=prompt or "",
        effort=effort,
        cs_out=sum(o for _, o in cs_segs),
    )


def user_kind(c, at):
    if "[Subagent hand-back]" in c:
        return "hand-back report"
    if "<task-notification>" in c:
        return "task-notification"
    if c.lstrip().startswith(("Goal check-in", "A session-scoped Stop", "Stop hook feedback")):
        return "goal check-in / stop hook"
    if "<command-name>" in c or "<local-command" in c:
        return "slash command"
    return "owner prompt" if at == "lead" else "task prompt"


def runs():
    out = []
    for f in glob.glob(os.path.join(PROJ, "*.jsonl")):
        if any(e in f for e in EXCL):
            continue
        out.append(load(f, "lead"))
    for f in glob.glob(os.path.join(PROJ, "*", "subagents", "*.jsonl")):
        if any(e in f for e in EXCL):
            continue
        try:
            with open(f[:-6] + ".meta.json") as fh:
                at = json.load(fh).get("agentType", "?")
        except OSError:
            at = "?"
        at = at.replace("harness-ops:", "")
        out.append(load(f, at))
    return [r for r in out if r["turns"]]


# ---------------------------------------------------------------- phases


def own_notes(run):
    own = set()
    for tr in run["turns"]:
        for _, name, inp in tr["tools"]:
            if name in ("Write", "Edit", "MultiEdit"):
                n = note_key(inp.get("file_path", ""))
                if n and not n.endswith("INDEX.md"):
                    own.add(n)
            elif name == "Bash":
                for m in re.finditer(r"(?:>|tee)\s*\S*?(docs/research/[\w./-]+?\.md)", inp.get("command", "")):
                    own.add(m.group(1))
    return own


def phase(name, inp, own, written):
    """Phase of one tool call. `written`: own notes already written in this run (a later read of one is revision)."""

    def note_phase(paths):
        if any(p.endswith("INDEX.md") for p in paths) and len(paths) == 1:
            return "orient"
        if paths and all(p in own for p in paths):
            return "write" if any(p in written for p in paths) else "read-notes"
        return "read-notes"

    if name in ("Agent", "Task"):
        return "spawn"
    if name == "Skill":
        return "orient"
    if name == "WebSearch":
        return "search"
    if name == "WebFetch":
        return "source"
    if name.startswith("mcp__arxiv__"):
        t = name.split("__")[2]
        return (
            "search"
            if t in ("search_papers", "semantic_search", "citation_graph", "list_papers", "get_abstract")
            else "source"
        )
    if name.startswith("mcp__"):
        return "source"
    if name in ("Monitor", "TaskOutput", "BashOutput", "TaskStop"):
        return "wait"
    if name in ("SubagentHandback", "SendMessage"):
        return "final"
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        p = inp.get("file_path", "")
        return "compute" if "/.scratch/" in p and not p.endswith(".md") else "write"
    if name == "Read":
        p = inp.get("file_path", "")
        if p.endswith(".pdf"):
            return "source"
        if re.search(r"(CLAUDE|GOAL|HANDOFF|INDEX)\.md$", p):
            return "orient"
        n = note_key(p)
        if n:
            return note_phase([n])
        return "read-other"
    if name != "Bash":
        return "other"
    c = strip_cd(inp.get("command", ""))
    lc = c.lower()
    if "dev lit" in lc:
        return "search"
    if "dev reuse" in lc:
        return "search"
    if "dev cite-check" in lc or "dev docs" in lc:
        return "cite-check"
    if re.search(
        r"dev (check|pr\b|test)|git (commit|push|add)|gh pr create|dev board (new|comment|close)|gh issue (create|comment)",
        lc,
    ):
        return "pr"
    if re.search(
        r"dev (board (brief|claim|next|show|list)|orient|research new)|gh issue view|git worktree|(claude|goal|index)\.md",
        lc,
    ):
        return "orient"
    if re.match(r"(curl|wget|pdftotext|lynx|w3m)\b", c) or " curl " in c:
        return "source"
    if re.match(r"(sleep|until|while)\b", c):
        return "wait"
    if re.search(r"(>|tee)\s*\S*docs/research/\S+\.md", c):
        return "write"
    notes = NOTE_RE.findall(c)
    w = c.split()[0] if c.split() else ""
    if notes and (w in READ_VERBS or "dev code" in lc):
        return note_phase(notes)
    if re.match(r"(uv run (--\S+ )*python|python3?|uv run --with)", c):
        return "compute"
    if w in READ_VERBS or "dev code" in lc or w in ("ls", "find"):
        return "read-other"
    if w == "git" or w == "gh":
        return "git"
    return "other"


def annotate(run):
    """Per turn: list of phases; per piece: its phase (tool results take the phase of their call)."""
    own = own_notes(run)
    written = set()
    tid_phase, tid_call = {}, {}
    for tr in run["turns"]:
        ph = []
        for tid, name, inp in tr["tools"]:
            p = phase(name, inp, own, written)
            ph.append(p)
            tid_phase[tid] = p
            tid_call[tid] = (name, inp)
            if name in ("Write", "Edit", "MultiEdit"):
                n = note_key(inp.get("file_path", ""))
                if n:
                    written.add(n)
            elif name == "Bash":
                for m in re.finditer(r"(?:>|tee)\s*\S*?(docs/research/[\w./-]+?\.md)", inp.get("command", "")):
                    written.add(m.group(1))
        tr["phases"] = ph or ["final"]
    for pc in run["pieces"]:
        if pc["kind"] == "result":
            pc["phase"] = tid_phase.get(pc["tid"], "other")
            pc["call"] = tid_call.get(pc["tid"], ("?", {}))
        else:
            pc["phase"] = "ctx:" + pc["kind"]
    run["own"] = own


def turn_cost(tr):
    return tr["cr"] * P_CR + tr["cw"] * P_CW + tr["inp"] * P_IN + out_tok(tr) * P_OUT


def out_tok(tr):
    return tr["vis"] * VTPC + HIDDEN


def segments(run):
    """Turn index ranges between compactions (context resets)."""
    cuts = sorted(set([0] + run["compact"] + [len(run["turns"])]))
    return [(a, b) for a, b in pairwise(cuts) if b > a]


def carried(run):
    """Carried cost per content category: tokens x (P_CW once + P_CR x later turns in the same segment).
    Assistant output (visible + thinking, scaled) is its own category under the turn's first phase."""
    res = Counter()
    T = len(run["turns"])
    segs = segments(run)
    seg_end = {}
    for a, b in segs:
        for i in range(a, b):
            seg_end[i] = b
    # base context: first turn of each segment
    for a, b in segs:
        tr = run["turns"][a]
        base = tr["cr"] + tr["cw"] + tr["inp"]
        res["base (system, tools, CLAUDE.md, prompt)"] += base * P_CW + base * (b - a - 1) * P_CR
    for i, tr in enumerate(run["turns"]):
        if i + 1 >= T:
            break
        tok = tr["vis"] * VTPC
        reads = max(0, seg_end.get(i + 1, T) - (i + 2))
        res["out:" + tr["phases"][0]] += tok * (P_CW + reads * P_CR)
    for pc in run["pieces"]:
        e = pc["k"] + 1
        if e >= T or e < 0:
            continue
        tok = pc["n"] * TPC
        pc["tok"] = tok
        reads = max(0, seg_end.get(e, T) - (e + 1))
        pc["carry"] = tok * (P_CW + reads * P_CR)
        pc["reads"] = reads
        res[pc["phase"]] += pc["carry"]
    return res


# ---------------------------------------------------------------- helpers


def med(v):
    return st.median(v) if v else 0


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * q))] if v else 0


def spearman(x, y):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(o):
            r[i] = pos
        return r

    rx, ry = rank(x), rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0


def table(rows, head):
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for r in rows:
        print("| " + " | ".join(str(x) for x in r) + " |")


# ---------------------------------------------------------------- main

RUNS = runs()
for r in RUNS:
    annotate(r)
RES = [r for r in RUNS if r["at"] == "researcher"]
AUD = [r for r in RUNS if r["at"] == "research-auditor"]
LEAD = [r for r in RUNS if r["at"] == "lead"]
print(f"runs: {len(RUNS)} (researcher {len(RES)}, research-auditor {len(AUD)}, lead {len(LEAD)})")

# ---- C first: calibrate tokens per character from cache growth on turns where the cache held
import numpy as np  # noqa: E402

X, Y = [], []
for r in RUNS:
    if r["at"] not in ("researcher", "lead", "builder"):
        continue
    T = r["turns"]
    add = Counter()
    for pc in r["pieces"]:
        add[pc["k"] + 1] += pc["n"]
    for i in range(1, len(T)):
        prev = T[i - 1]["cr"] + T[i - 1]["cw"] + T[i - 1]["inp"]
        if prev * 0.98 <= T[i]["cr"] <= prev * 1.02:
            X.append([T[i - 1]["vis"], add[i]])
            Y.append(T[i]["cr"] + T[i]["cw"] + T[i]["inp"] - prev)
X, Y = np.array(X, float), np.array(Y, float)
(VTPC, TPC), *_ = np.linalg.lstsq(X, Y, rcond=None)
r2 = 1 - ((Y - X @ np.array([VTPC, TPC])) ** 2).sum() / ((Y - Y.mean()) ** 2).sum()
CS_OUT = sum(r["cs_out"] for r in LEAD)
VIS_OUT = VTPC * sum(t["vis"] for r in RUNS for t in r["turns"])
HIDDEN = max(0.0, CS_OUT - VIS_OUT) / sum(len(r["turns"]) for r in RUNS)
print(
    f"\n## C. calibration over {len(Y)} turns (researcher, builder, lead): context tokens per char of tool/user content "
    f"{TPC:.3f} ({1 / TPC:.2f} chars/token), per char of assistant content {VTPC:.3f}, R2 {r2:.2f}; "
    f"billed output {CS_OUT / 1e6:.1f}M tokens, visible {VIS_OUT / 1e6:.1f}M, hidden per turn {HIDDEN:.0f}"
)
bytype = Counter()
for r in RUNS:
    bytype[r["at"]] += sum(turn_cost(t) for t in r["turns"])
tot_all = sum(bytype.values())
print(
    f"modelled cost all agents ${tot_all:.0f} (cost records summed per process segment: $2,756; scripts/cost_segments.py); by type:",
    {k: f"${v:.0f} ({v / tot_all:.0%})" for k, v in bytype.most_common(8)},
)

# effort per agent type from api-request records
eff = defaultdict(Counter)
for r in RUNS:
    eff[r["at"]].update(r["effort"])
print("effort per agent type (api-request records, versions 2.1.300+):", {k: dict(v) for k, v in eff.items() if v})

# ---- A: researcher turns and cost by phase
print("\n## A. researcher turns and cost by phase of the turn's tool calls")
for label, group in (("researcher", RES), ("research-auditor", AUD)):
    tc, cc = Counter(), Counter()
    total = 0
    for r in group:
        for tr in r["turns"]:
            c = turn_cost(tr)
            total += c
            for p in tr["phases"]:
                tc[p] += 1 / len(tr["phases"])
                cc[p] += c / len(tr["phases"])
    nt = sum(tc.values())
    print(f"\n{label}: {len(group)} runs, {nt:.0f} turns, ${total:.0f}")
    table(
        [
            (p, f"{tc[p]:.0f}", f"{tc[p] / nt:.1%}", f"${cc[p]:.0f}", f"{cc[p] / total:.1%}")
            for p, _ in cc.most_common()
        ],
        ["phase", "turns", "turn share", "cost", "cost share"],
    )

# ---- B: carried context cost by content category
print("\n## B. carried context cost by content (tokens x (write once + read on each later turn))")
for label, group in (("researcher", RES), ("research-auditor", AUD), ("lead", LEAD)):
    tot = Counter()
    actual = sum(turn_cost(tr) for r in group for tr in r["turns"])
    for r in group:
        tot.update(carried(r))
    s = sum(tot.values())
    print(f"\n{label}: modelled ${s:.0f} vs actual ${actual:.0f} ({s / actual:.0%})")
    table([(k, f"${v:.0f}", f"{v / s:.1%}") for k, v in tot.most_common(22)], ["content", "carried cost", "share"])

# result size and carry by tool family for researchers
print("\nresearcher tool results: calls, chars, median chars, mean later reads, carried cost")
fam = defaultdict(list)
for r in RES:
    for pc in r["pieces"]:
        if pc["kind"] != "result" or "carry" not in pc:
            continue
        name, inp = pc["call"]
        if name == "Bash":
            c = strip_cd(inp.get("command", ""))
            m = re.search(r"dev (lit \w+|code \w+|cite-check|check|pr|board \w+)", c)
            key = (
                "dev " + m.group(1)
                if m
                else (
                    "Bash read of a note"
                    if pc["phase"] in ("read-notes", "write")
                    else "Bash " + (c.split()[0] if c.split() else "?")
                )
            )
        elif name == "Read":
            key = (
                "Read note"
                if note_key(inp.get("file_path", ""))
                else ("Read pdf" if inp.get("file_path", "").endswith(".pdf") else "Read other")
            )
        elif name.startswith("mcp__"):
            key = name.replace("mcp__", "")
        else:
            key = name
        fam[key].append(pc)
rows = []
for k, v in fam.items():
    n = [p["n"] for p in v]
    rows.append(
        (
            k,
            len(v),
            f"{sum(n) / 1e6:.2f}M",
            int(med(n)),
            f"{st.mean(p['reads'] for p in v):.0f}",
            sum(p["carry"] for p in v),
        )
    )
rows.sort(key=lambda x: -x[5])
table(
    [(*r[:5], f"${r[5]:.0f}") for r in rows[:24]],
    ["tool", "calls", "chars", "median chars", "mean later turns", "carried $"],
)

# whole-note reads
big = [
    pc
    for r in RES + AUD
    for pc in r["pieces"]
    if pc.get("phase") in ("read-notes", "write") and pc["n"] > 30000 and "carry" in pc
]
print(
    f"\nnote reads over 30k chars (researcher+auditor): {len(big)}, chars {sum(p['n'] for p in big) / 1e6:.2f}M, carried ${sum(p['carry'] for p in big):.0f}"
)
allnotes = [pc for r in RES + AUD for pc in r["pieces"] if pc.get("phase") in ("read-notes",) and "carry" in pc]
print(
    f"all reads of other/existing notes: {len(allnotes)}, chars {sum(p['n'] for p in allnotes) / 1e6:.2f}M, carried ${sum(p['carry'] for p in allnotes):.0f}"
)

# ---- C: context growth
print("\n## C. context per turn (median over runs that reach the turn)")
for label, group in (
    ("researcher", RES),
    ("research-auditor", AUD),
    ("builder", [r for r in RUNS if r["at"] == "builder"]),
):
    pts = [1, 10, 20, 40, 60, 80, 100, 150, 200]
    row = []
    for p in pts:
        v = [
            r["turns"][p - 1]["cr"] + r["turns"][p - 1]["cw"] + r["turns"][p - 1]["inp"]
            for r in group
            if len(r["turns"]) >= p
        ]
        row.append(f"{p}:{med(v) / 1000:.0f}k(n={len(v)})")
    print(label, " ".join(row))
    T = [len(r["turns"]) for r in group]
    print(
        f"  turns median {med(T)}, p90 {pct(T, 0.9)}; share of runs over 100 turns {sum(t > 100 for t in T) / len(T):.0%}"
    )
    # share of cost from turns after turn 60
    late = sum(turn_cost(tr) for r in group for tr in r["turns"][60:])
    allc = sum(turn_cost(tr) for r in group for tr in r["turns"])
    print(
        f"  cost in turns after 60: {late / allc:.0%}; after 100: {sum(turn_cost(tr) for r in group for tr in r['turns'][100:]) / allc:.0%}"
    )
# growth per turn by phase of the preceding turn (researchers)
gp = defaultdict(list)
for r in RES:
    T = r["turns"]
    for i in range(1, len(T)):
        if T[i]["cr"] >= (T[i - 1]["cr"] + T[i - 1]["cw"] + T[i - 1]["inp"]) * 0.98:
            gp[T[i - 1]["phases"][0]].append(T[i]["cw"] + T[i]["inp"])
print(
    "researcher: median context added by the next turn, by phase of the turn before:",
    {k: int(med(v)) for k, v in sorted(gp.items(), key=lambda kv: -sum(kv[1]))},
)

# ---- D: note size vs auditor / extender cost
print("\n## D. target-note size at run start vs run cost")


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True).stdout.strip()


def size_before(path, ts):
    c = git("rev-list", "-1", f"--before={ts}", "--all", "--", path)
    if not c:
        return None
    s = git("cat-file", "-s", f"{c}:{path}")
    return int(s) if s.isdigit() else None


def target(r):
    if r["at"] == "researcher":
        o = [p for p in r["own"] if "/_scratch/" not in p]
        return (o[0], "issue") if o else (sorted(r["own"])[0] if r["own"] else None, "round")
    m = NOTE_RE.findall(r["prompt"])
    m = [x for x in m if not x.endswith("INDEX.md")]
    if m:
        return m[0], "audit"
    cnt = Counter()
    for pc in r["pieces"]:
        if pc.get("phase") == "read-notes":
            for n in NOTE_RE.findall(json.dumps(pc["call"][1])):
                cnt[n] += pc["n"]
    return (cnt.most_common(1)[0][0] if cnt else None), "audit"


rows = defaultdict(list)
for r in RES + AUD:
    path, mode = target(r)
    if not path:
        continue
    sz = size_before(path, r["turns"][0]["ts"])
    if r["at"] == "researcher" and mode == "issue":
        mode = "extend" if sz else "new"
    if mode == "round":
        continue
    cost = sum(turn_cost(tr) for tr in r["turns"])
    readc = sum(
        pc["n"]
        for pc in r["pieces"]
        if pc.get("phase") in ("read-notes", "write") and path.split("/")[-1] in json.dumps(pc["call"][1])
    )
    end = r["turns"][-1]
    rows[mode].append(
        dict(sz=sz or 0, cost=cost, turns=len(r["turns"]), end=end["cr"] + end["cw"] + end["inp"], readc=readc)
    )
for mode, v in rows.items():
    v2 = [x for x in v if x["sz"]] if mode != "new" else v
    print(
        f"{mode}: n={len(v)} (with size {len(v2)}), median cost ${med([x['cost'] for x in v]):.2f}, turns {med([x['turns'] for x in v])}, end ctx {med([x['end'] for x in v]) / 1000:.0f}k, target-note chars read {med([x['readc'] for x in v]) / 1000:.0f}k"
    )
    if len(v2) >= 8 and mode != "new":
        xs = [x["sz"] for x in v2]
        print(
            f"   spearman(size, cost)={spearman(xs, [x['cost'] for x in v2]):.2f}, (size, turns)={spearman(xs, [x['turns'] for x in v2]):.2f}, (size, end ctx)={spearman(xs, [x['end'] for x in v2]):.2f}, (size, chars read)={spearman(xs, [x['readc'] for x in v2]):.2f}"
        )
        cut = sorted(xs)[len(xs) // 2]
        lo = [x for x in v2 if x["sz"] < cut]
        hi = [x for x in v2 if x["sz"] >= cut]
        print(
            f"   below median size {cut // 1000}KB: n={len(lo)} cost ${med([x['cost'] for x in lo]):.2f} end {med([x['end'] for x in lo]) / 1000:.0f}k; at/above: n={len(hi)} cost ${med([x['cost'] for x in hi]):.2f} end {med([x['end'] for x in hi]) / 1000:.0f}k"
        )

# ---- E: dev lit output vs use
print("\n## E. dev lit output and what is used afterwards")
PAPER = re.compile(r"^- (.+) \((.*?)\) \[(.*?)\]\s*$")
litcalls = Counter()
litchars = Counter()
ret = followed = cited = 0
venue_hit = snip_hit = cited_n = 0
dup_chars = tot_paper_chars = snippet_chars = 0
get_after_search = 0
n_flag = Counter()
for r in RES:
    written = []
    later_inputs = []  # (turn index, text of non-search tool inputs)
    for i, tr in enumerate(r["turns"]):
        for _, name, inp in tr["tools"]:
            s = json.dumps(inp).lower()
            if name in ("Write", "Edit", "MultiEdit"):
                written.append(inp.get("content", "") + inp.get("new_string", ""))
            elif (
                name == "Bash" and re.search(r"<<\s*'?EOF", inp.get("command", "")) and ".md" in inp.get("command", "")
            ):
                written.append(inp.get("command", ""))
            if not (name == "Bash" and "dev lit search" in s):
                later_inputs.append((i, s))
    wtext = "\n".join(written).lower()
    walnum = re.sub(r"[^a-z0-9]", "", wtext)
    seen = set()
    for pc in r["pieces"]:
        if pc["kind"] != "result" or pc["phase"] != "search":
            continue
        name, inp = pc["call"]
        cmd = inp.get("command", "") if name == "Bash" else ""
        for m in re.finditer(r"dev lit (status|search|get|refs|cited-by)", cmd):
            litcalls[m.group(1)] += 1
        if ms := re.search(r"dev lit (status|search|get|refs|cited-by)", cmd):
            litchars[ms.group(1)] += pc["n"]
            for m in re.finditer(r"dev lit search [^|;&]*?(-n\s*\d+)", cmd):
                n_flag[m.group(1).replace(" ", "")] += 1
        if "dev lit search" not in cmd:
            continue
        lines = (pc["txt"] or "").split("\n")
        for j, ln in enumerate(lines):
            m = PAPER.match(ln)
            if not m:
                continue
            title, meta, ids = m.groups()
            snip = lines[j + 1].strip() if j + 1 < len(lines) and lines[j + 1].startswith("  ") else ""
            chars = len(ln) + len(snip)
            tot_paper_chars += chars
            snippet_chars += len(snip)
            key = ids.lower()
            if key in seen:
                dup_chars += chars
                continue
            seen.add(key)
            ret += 1
            idv = [x.split(":", 1)[1].lower() for x in ids.split() if ":" in x]
            idv = [x for x in idv if len(x) > 6]
            talnum = re.sub(r"[^a-z0-9]", "", title.lower())[:45]
            fol = any(any(x in s for x in idv) for k2, s in later_inputs if k2 > pc["k"])
            cit = any(x in wtext for x in idv) or (len(talnum) >= 25 and talnum in walnum)
            followed += fol
            cited += cit
            if fol and any("dev lit get" in s for k2, s in later_inputs if k2 > pc["k"] and any(x in s for x in idv)):
                get_after_search += 1
            if cit:
                cited_n += 1
                v = re.sub(r"[^a-z0-9]", "", meta.split(",")[1].lower())[:20] if "," in meta else ""
                if len(v) >= 8 and v in walnum:
                    venue_hit += 1
                words = re.findall(r"[a-z]+", snip.lower())
                if any(
                    " ".join(words[a : a + 8]) in re.sub(r"[^a-z ]", " ", wtext)
                    for a in range(0, max(0, len(words) - 8), 4)
                ):
                    snip_hit += 1
print("dev lit calls by subcommand (researchers; one Bash call may hold several):", dict(litcalls))
print("dev lit result chars by subcommand:", {k: f"{v / 1e6:.2f}M" for k, v in litchars.items()})
print("-n flags used:", dict(n_flag.most_common(8)))
print(
    f"search papers returned (unique per run) {ret}; followed up later {followed} ({followed / max(1, ret):.0%}); "
    f"cited in the run's own writing {cited} ({cited / max(1, ret):.0%}); followed up via dev lit get {get_after_search}"
)
print(
    f"search output paper chars {tot_paper_chars / 1e6:.2f}M, of which snippets {snippet_chars / max(1, tot_paper_chars):.0%}, repeats of a paper already returned in the run {dup_chars / max(1, tot_paper_chars):.0%}"
)
print(
    f"cited papers whose venue string reappears {venue_hit}/{cited_n}; whose snippet has an 8-word run reused {snip_hit}/{cited_n}"
)

# ---- G: researcher levers
print("\n## G. researcher levers (simulated on the same runs)")
act_res = sum(turn_cost(t) for r in RES for t in r["turns"])
act_aud = sum(turn_cost(t) for r in AUD for t in r["turns"])
print(
    f"researcher actual ${act_res:.0f} over {len(RES)} runs (${act_res / len(RES):.2f} per run); auditor ${act_aud:.0f}"
)
parts = Counter()
for r in RES:
    for t in r["turns"]:
        parts["cache read"] += t["cr"] * P_CR
        parts["cache write"] += t["cw"] * P_CW
        parts["input"] += t["inp"] * P_IN
        parts["output"] += out_tok(t) * P_OUT
print("researcher cost by token kind:", {k: f"{v / act_res:.0%}" for k, v in parts.items()})

# G1 persisted-output re-reads: a Bash result too large is saved to tool-results/, then the agent Reads the whole file
pers = [
    pc
    for r in RES + AUD
    for pc in r["pieces"]
    if pc["kind"] == "result"
    and pc["call"][0] == "Read"
    and "tool-results" in pc["call"][1].get("file_path", "")
    and "carry" in pc
]
nsaved = sum(
    1 for r in RES + AUD for pc in r["pieces"] if pc["kind"] == "result" and "Full output saved to" in (pc["txt"] or "")
)
print(
    f"G1 persisted outputs {nsaved}; Reads of a persisted file {len(pers)}, {sum(p['n'] for p in pers) / 1e6:.2f}M chars, "
    f"with offset/limit {sum(1 for p in pers if p['call'][1].get('offset') or p['call'][1].get('limit'))}, carried ${sum(p['carry'] for p in pers):.0f}"
)

# G2 whole-note reads (> 30k chars) of existing notes, and all note reads
for lab, cond in (("over 30k chars", lambda pc: pc["n"] > 30000), ("all", lambda pc: True)):
    v = [pc for r in RES + AUD for pc in r["pieces"] if pc.get("phase") == "read-notes" and "carry" in pc and cond(pc)]
    print(
        f"G2 reads of existing notes ({lab}): {len(v)}, {sum(p['n'] for p in v) / 1e6:.2f}M chars, carried ${sum(p['carry'] for p in v):.0f}"
    )

# G3 batching: consecutive single-call turns of the same phase (search or source) that a parallel call could merge.
# Saving per merged turn: that turn's cache read plus its share of fixed per-turn output (ASSUMED: half its output).
merge, msave = 0, 0.0
for r in RES:
    T = r["turns"]
    for i in range(1, len(T)):
        a, b = T[i - 1], T[i]
        one = len(a["tools"]) == 1 and len(b["tools"]) == 1
        if one and a["phases"][0] == b["phases"][0] in ("search", "source") and a["tools"][0][1] == b["tools"][0][1]:
            merge += 1
            msave += b["cr"] * P_CR + 0.5 * out_tok(b) * P_OUT
print(
    f"G3 back-to-back single-call search/source turns with the same tool: {merge} "
    f"({merge / sum(len(r['turns']) for r in RES):.0%} of researcher turns); merging half of them saves about ${msave / 2:.0f}"
)


# G4 source/search helper: search+source turns and their results move to a fresh helper (26k base, own accumulation),
# which returns an extract of EXTRACT tokens per 10 such turns. Helper priced at Opus or at Sonnet 5.5 (half of input,
# cache write and output; same cache read). Researcher context loses those results and outputs, gains the extracts.
def sim_helper(r, sonnet, EXTRACT=1500, BASE=26000):
    T = r["turns"]
    ss = {i for i, t in enumerate(T) if t["phases"][0] in ("search", "source")}
    if not ss:
        tc = sum(turn_cost(t) for t in T)
        return tc, tc
    k = 0.5 if sonnet else 1.0
    tok_at = Counter()  # tokens of search/source content entering researcher context before turn i
    for pc in r["pieces"]:
        if pc["kind"] == "result" and pc["phase"] in ("search", "source") and "tok" in pc:
            tok_at[pc["k"] + 1] += pc["tok"]
    for i in ss:
        tok_at[i + 1] += T[i]["vis"] * VTPC
    removed, helper_ctx, cost, nss = 0.0, BASE, 0.0, 0
    for i, t in enumerate(T):
        removed += tok_at[i]
        if i in ss:
            nss += 1
            new = t["cw"] + t["inp"]
            cost += helper_ctx * P_CR + new * P_CW * k + out_tok(t) * P_OUT * k
            helper_ctx += new
            if nss % 10 == 0:  # hand-back, new helper
                removed -= EXTRACT
                helper_ctx = BASE
                cost += BASE * P_CW * k
            continue
        ctx = t["cr"] + t["cw"] + t["inp"]
        eff = max(BASE, ctx - removed)
        cost += max(0, eff - t["cw"] - t["inp"]) * P_CR + t["cw"] * P_CW + t["inp"] * P_IN + out_tok(t) * P_OUT
    return cost, sum(turn_cost(t) for t in T)


for son in (False, True):
    new = sum(sim_helper(r, son)[0] for r in RES)
    print(
        f"G4 search+source helper on {'Sonnet 5.5' if son else 'Opus 5.5'}: ${new:.0f} vs ${act_res:.0f} ({1 - new / act_res:.0%} less)"
    )

# G5 model routing of the whole researcher role to Sonnet 5.5 (same turns, same tokens: an upper bound on price alone)
son_all = sum(
    t["cr"] * P_CR + 0.5 * (t["cw"] * P_CW + t["inp"] * P_IN + out_tok(t) * P_OUT) for r in RES for t in r["turns"]
)
print(f"G5 whole researcher role at Sonnet 5.5 prices, same tokens: ${son_all:.0f} ({1 - son_all / act_res:.0%} less)")


# G6 tool-result clearing (what clear_tool_uses would do): when context passes TRIG, stub all tool results but the
# newest KEEP; the cache is rewritten from the first cleared result on (ASSUMED: everything after the 26k base).
# oracle=True clears only when it pays back within the run's real remaining turns (an upper bound for any policy).
def sim_clear(r, TRIG, KEEP, oracle=False, stub=40, BASE=26000):
    T = r["turns"]
    pcs = sorted((pc for pc in r["pieces"] if pc["kind"] == "result" and "tok" in pc), key=lambda p: p["k"])
    alive, removed, cost, clears, j = [], 0.0, 0.0, 0, 0
    for i, t in enumerate(T):
        while j < len(pcs) and pcs[j]["k"] + 1 <= i:
            alive.append(pcs[j]["tok"])
            j += 1
        ctx = t["cr"] + t["cw"] + t["inp"] - removed
        new = t["cw"] + t["inp"]
        out = out_tok(t) * P_OUT
        if ctx > TRIG and len(alive) > KEEP:
            drop = sum(max(0, x - stub) for x in alive[:-KEEP])
            after = ctx - drop
            left = len(T) - i - 1
            pays = drop * left * P_CR > (after - BASE) * (P_CW - P_CR)
            if drop > 20000 and (pays or not oracle):
                removed += drop
                alive = [min(x, stub) for x in alive[:-KEEP]] + alive[-KEEP:]
                cost += BASE * P_CR + (after - BASE) * P_CW + out
                clears += 1
                continue
        cost += max(0, ctx - new) * P_CR + new * P_CW + out
    return cost, clears


for TRIG, KEEP, orc in ((100e3, 15, False), (150e3, 15, False), (100e3, 15, True), (150e3, 30, True)):
    tot = n = 0
    for r in RES:
        c, k = sim_clear(r, TRIG, KEEP, orc)
        tot += c
        n += k
    print(
        f"G6 clearing trigger {TRIG / 1000:.0f}k keep {KEEP}{' oracle' if orc else ''}: ${tot:.0f} vs ${act_res:.0f} "
        f"({1 - tot / act_res:+.0%} saved), {n} clears (no re-fetch cost modelled)"
    )

# G7 fixed per-run overhead: base context and the skill listing
base = [r["turns"][0]["cr"] + r["turns"][0]["cw"] + r["turns"][0]["inp"] for r in RES]
skl = [sum(pc["n"] for pc in r["pieces"] if pc["kind"] == "att:skill_listing") for r in RES]
ts_turns = sum(1 for r in RES for t in r["turns"] for x in t["tools"] if x[1] == "ToolSearch")
print(
    f"G7 researcher first-turn context median {med(base) / 1000:.1f}k; skill listing median {med(skl)} chars per run; "
    f"ToolSearch calls {ts_turns} ({ts_turns / len(RES):.1f} per run)"
)

skl_all = sum(pc.get("carry", 0) for r in RUNS for pc in r["pieces"] if pc["kind"] == "att:skill_listing")
print(f"G7 skill listing carried cost, researcher+auditor+lead runs where computed: ${skl_all:.0f}")

# ---- F: lead
print("\n## F. lead sessions")
for r in sorted(LEAD, key=lambda r: -len(r["turns"])):
    T = r["turns"]
    if len(T) < 50:
        continue
    ctx = [t["cr"] + t["cw"] + t["inp"] for t in T]
    q = [ctx[min(len(ctx) - 1, int(len(ctx) * x))] // 1000 for x in (0.1, 0.25, 0.5, 0.75, 0.9)]
    print(
        f"{os.path.basename(r['f'])[:8]} calls {len(T)} cost ${sum(turn_cost(t) for t in T):.0f} "
        f"ctx at 10/25/50/75/90% of the session's calls {q}k, max {max(ctx) // 1000}k, compactions {len(r['compact'])}"
    )
allctx = [t["cr"] + t["cw"] + t["inp"] for r in LEAD for t in r["turns"]]
act_lead = sum(turn_cost(t) for r in LEAD for t in r["turns"])
print(
    f"lead calls {len(allctx)}, ${act_lead:.0f}; mean ctx {st.mean(allctx) / 1000:.0f}k; calls over 200k "
    f"{sum(c > 200e3 for c in allctx) / len(allctx):.0%}, over 400k {sum(c > 400e3 for c in allctx) / len(allctx):.0%}, "
    f"over 600k {sum(c > 600e3 for c in allctx) / len(allctx):.0%}"
)
lparts = Counter()
for r in LEAD:
    for t in r["turns"]:
        lparts["cache read"] += t["cr"] * P_CR
        lparts["cache write"] += t["cw"] * P_CW
        lparts["output"] += out_tok(t) * P_OUT
print("lead cost by token kind:", {k: f"{v / act_lead:.0%}" for k, v in lparts.items()})
post = []
for r in LEAD:
    for c in r["compact"]:
        if c < len(r["turns"]) and c > 0:
            a, b = r["turns"][c - 1], r["turns"][c]
            post.append((a["cr"] + a["cw"] + a["inp"], b["cr"] + b["cw"] + b["inp"]))
print("compactions (context before -> after, k):", [(x // 1000, y // 1000) for x, y in post])

# what wakes the lead: episodes = turns from one incoming event to the next
ev_turns, ev_cost, ev_n = Counter(), Counter(), Counter()
alone_cost = [0.0]
EVENTS = (
    "owner prompt",
    "hand-back report",
    "task-notification",
    "goal check-in / stop hook",
    "slash command",
    "peer-message",
)
for r in LEAD:
    T = r["turns"]
    trig = {}
    for pc in r["pieces"]:
        if pc["kind"] in EVENTS:
            i = pc["k"] + 1
            kind = "worker completion" if pc["kind"] in ("hand-back report", "task-notification") else pc["kind"]
            trig.setdefault(i, kind)
            if kind == "owner prompt":
                trig[i] = kind
    kinds_at = defaultdict(set)
    for pc in r["pieces"]:
        kinds_at[pc["k"] + 1].add(pc["kind"])
    alone = [
        i
        for i, ks in kinds_at.items()
        if i < len(T) and "task-notification" in ks and not ks & {"hand-back report", "owner prompt", "result"}
    ]
    ev_n["(turns woken by a task-notification alone)"] += len(alone)
    ev_cost["(turns woken by a task-notification alone)"] += 0
    ev_turns["(turns woken by a task-notification alone)"] += len(alone)
    alone_cost[0] += sum(turn_cost(T[i]) for i in alone)
    cur = "start"
    for i, t in enumerate(T):
        if i in trig:
            cur = trig[i]
            ev_n[cur] += 1
        ev_turns[cur] += 1
        ev_cost[cur] += turn_cost(t)
print(
    f"lead episodes by the event that started them (turns woken by a task-notification alone cost ${alone_cost[0]:.0f}):"
)
table(
    [
        (
            k,
            ev_n[k],
            f"{ev_turns[k]}",
            f"${ev_cost[k]:.0f}",
            f"{ev_cost[k] / act_lead:.0%}",
            f"{ev_turns[k] / max(1, ev_n[k]):.1f}",
        )
        for k, _ in ev_cost.most_common()
    ],
    ["event", "events", "turns", "cost", "share", "turns per event"],
)
fine = Counter()
for r in LEAD:
    carried(r)
    for pc in r["pieces"]:
        if "carry" not in pc:
            continue
        if pc["kind"] == "result":
            name, inp = pc["call"]
            if name in ("Agent", "Task"):
                k = "Agent tool result"
            elif name == "Bash":
                c = strip_cd(inp.get("command", ""))
                m = re.search(
                    r"dev (integrate|orient|board \w+|check|lit|code|status|quota|research)|gh (pr|issue|run|api) ?(\w+)?|^(sleep|until)",
                    c,
                )
                k = "Bash " + (m.group(0) if m else (c.split()[0] if c.split() else "?"))
            else:
                k = name if not name.startswith("mcp__") else "mcp " + name.split("__")[1]
        else:
            k = pc["kind"]
        fine[k] += pc["carry"]
s_ = sum(fine.values())
print(f"lead carried cost of content (assistant output and base excluded), ${s_:.0f}:")
table([(k, f"${v:.0f}", f"{v / s_:.1%}") for k, v in fine.most_common(18)], ["content", "carried", "share"])
for kind in ("hand-back report", "task-notification", "owner prompt"):
    v = [pc["n"] for r in LEAD for pc in r["pieces"] if pc["kind"] == kind]
    print(f"{kind}: {len(v)}, median {med(v):.0f} chars, p90 {pct(v, 0.9)}, total {sum(v) / 1e6:.2f}M")
poll = [t for r in LEAD for t in r["turns"] if "wait" in t["phases"]]
print(f"lead turns that wait (sleep/until/Monitor/TaskOutput): {len(poll)}, ${sum(turn_cost(t) for t in poll):.0f}")
allw = [(r["at"], t) for r in RUNS for t in r["turns"] if "wait" in t["phases"]]
print(f"all agents: wait turns {len(allw)}, ${sum(turn_cost(t) for _, t in allw):.0f}")


# simulated compaction: whenever context passes H, compact to B (one call reading the context, S output tokens, re-write B)
def sim_compact(r, H, B, S=12000):
    off, cost, n = 0, 0.0, 0
    for t in r["turns"]:
        ctx = t["cr"] + t["cw"] + t["inp"]
        eff = ctx - off
        if eff > H:
            cost += eff * P_CR + S * P_OUT + B * P_CW
            off = ctx - B
            eff = B
            n += 1
        new = t["cw"] + t["inp"]
        cost += max(0, eff - new) * P_CR + t["cw"] * P_CW + t["inp"] * P_IN + out_tok(t) * P_OUT
    return cost, n


B = int(med([y for _, y in post])) if post else 60000
print(
    f"\nsimulated lead compaction to {B // 1000}k (measured median after a real compaction) whenever context passes H:"
)
for H in (150e3, 200e3, 300e3, 400e3):
    tot = n = 0
    for r in LEAD:
        c, k = sim_compact(r, H, B)
        tot += c
        n += k
    print(
        f"  H={H / 1000:.0f}k: ${tot:.0f} vs ${act_lead:.0f} ({1 - tot / act_lead:.0%} less), {n} compactions "
        f"(no re-orientation cost modelled)"
    )
for lab, grp in (("researcher", RES), ("builder", [r for r in RUNS if r["at"] == "builder"])):
    act = sum(turn_cost(t) for r in grp for t in r["turns"])
    for H in (150e3, 200e3):
        tot = n = 0
        for r in grp:
            c, k = sim_compact(r, H, 60000)
            tot += c
            n += k
        print(
            f"  {lab} compaction to 60k at H={H / 1000:.0f}k: ${tot:.0f} vs ${act:.0f} ({1 - tot / act:.0%} less), {n} compactions"
        )
