"""`uv run dev agent-stats`: a scorecard of agent runs from Claude Code transcripts (tokens, calls, waste).

Guidance should be kept only when it changes behavior or outcomes; this is the measurement half of that loop. It reads
~/.claude/projects/<this checkout's key>/ (main sessions and their subagents, worktrees included) and reports, per
agent type: runs, median tool calls, median fresh/cached/output tokens, and the share of tool-result characters that
came from whole-file reads and long outputs (the cheapest waste to remove).

Token counts come from each message's `usage`. Claude Code writes one line per content block, each repeating the
message's usage; older builds wrote the stream-start usage (stop_reason null, output_tokens near zero) on all but the
last line. So each message id counts once, with its largest `usage.output_tokens`, which is the final one when it was
logged. Messages that never logged a final usage are counted as "partial"; their output is a lower bound.
`<synthetic>` messages (written by Claude Code, not the model) are skipped, and a run without a real assistant message
is not a run: one reader (read_run, load_runs) feeds every view, so the scorecard and `--cost` count alike (#450).

Three views judge harness changes (docs/research/harness-efficiency.md §2, §4, §9; #255): `--cost` prices each token
kind per agent type and per merged issue, `--reviews` counts review rounds per (merged PR, reviewer role), `--misses`
pairs bug-fix PRs with the reviewed PRs whose lines they change (candidates to check).
"""

import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from devkit import board, config, paths
from devkit.paths import main_checkout

BIG_RESULT = 8000  # characters


def project_dirs() -> list[Path]:
    base = Path.home() / ".claude/projects"
    key = re.sub(r"[^A-Za-z0-9]", "-", str(main_checkout()))
    return sorted(p for p in base.glob(f"{key}*") if p.is_dir())


def _result_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
    return 0


def report(as_json: bool = False) -> str:
    data: dict[str, list[Run]] = defaultdict(list)
    for r in load_runs(project_dirs()):
        data[r.kind].append(r)
    rows = []
    for kind, runs in sorted(data.items(), key=lambda kv: -len(kv[1])):
        med = lambda k, runs=runs: int(statistics.median(getattr(r, k) for r in runs))  # noqa: E731
        chars = sum(r.result_chars for r in runs) or 1
        tools: Counter[str] = sum((r.tools for r in runs), Counter())
        top = ", ".join(f"{t} {n}" for t, n in sorted(tools.items(), key=lambda kv: -kv[1])[:4])
        rows.append(
            {
                "type": kind,
                "runs": len(runs),
                "calls": med("calls"),
                "fresh": med("fresh"),
                "cached": med("cached"),
                "output": med("output"),
                "partial_share": round(sum(r.partial for r in runs) / (sum(r.messages for r in runs) or 1), 2),
                "big_share": round(sum(r.big for r in runs) / chars, 2),
                "top_tools": top,
            }
        )
    if as_json:
        return json.dumps(rows, indent=1)
    if not rows:
        return "no transcripts for this checkout yet"
    out = [
        f"{'agent type':<24}{'runs':>5}{'calls':>7}{'fresh tok':>11}{'cached tok':>12}{'out tok':>9}{'big%':>6}  top tools (medians per run)"
    ]
    for r in rows:
        out.append(
            f"{r['type'][:23]:<24}{r['runs']:>5}{r['calls']:>7}{r['fresh']:>11,}{r['cached']:>12,}{r['output']:>9,}"
            f"{r['big_share'] * 100:>5.0f}%  {r['top_tools']}"
        )
    msgs = sum(r.messages for runs in data.values() for r in runs) or 1
    partial = sum(r.partial for runs in data.values() for r in runs) / msgs
    out.append(
        "out tok = usage.output_tokens of each message's final record, once per message id;"
        f" {partial:.0%} of messages logged only a stream-start usage (their output is a lower bound)."
    )
    out.append(f"big% = share of tool-result characters in results over {BIG_RESULT} chars (whole files, long logs).")
    return "\n".join(out)


# --- --cost, --reviews, --misses ------------------------------------------------------------------------------------

# Output tokens per visible character (thinking summary, text, tool input): least-squares fit of cache growth over
# 30,818 turns, R² 0.56 (harness-efficiency.md §2). Transcripts log output_tokens at stream start, 6.4x under billed.
VIS_TOKENS_PER_CHAR = 0.443
KINDS = ("input", "write_1h", "write_5m", "read", "output")
PRICE_OF = dict(zip(KINDS, ("input", "cache_write_1h", "cache_write_5m", "cache_read", "output"), strict=True))
REVIEW_ROLES = {"reviewer": "code", "security-reviewer": "security", "research-auditor": "audit"}
COORD = "The coordinator sent a message while you were working"
NUM_RE = re.compile(r"(?:\bPRs?\s*#?|pull/|#)(\d{1,6})\b")
# a reference into another repository: owner/name#12, owner/name/pull/12, `-R owner/name`, github.com/owner/name
REPO_REF = re.compile(r"(?:\b[\w-]+/([\w.-]+?)(?:#\d|/pull/\d)|-R\s+[\w-]+/([\w.-]+)|github\.com/[\w-]+/([\w.-]+))")


@dataclass
class Run:
    """One transcript: a main session or one subagent, its assistant turns and (main sessions) its cost records."""

    kind: str
    session: str
    path: Path
    start: str = ""
    desc: str = ""
    prompt: str = ""
    turns: list[dict] = field(default_factory=list)
    billed: list[dict] = field(default_factory=list)  # modelUsage of each Claude Code process (`cost-state`)
    tools: Counter[str] = field(default_factory=Counter)  # tool_use blocks by tool name
    result_chars: int = 0  # tool-result characters
    big: int = 0  # of those, in results over BIG_RESULT characters

    @property
    def calls(self) -> int:
        return self.tools.total()

    @property
    def fresh(self) -> int:
        return sum(t["input"] + t["write_1h"] + t["write_5m"] for t in self.turns)

    @property
    def cached(self) -> int:
        return sum(t["read"] for t in self.turns)

    @property
    def output(self) -> int:
        return sum(t["logged"] for t in self.turns)

    @property
    def messages(self) -> int:
        return len(self.turns)

    @property
    def partial(self) -> int:
        """Messages that never logged a final usage (stop_reason set): their output is a lower bound."""
        return sum(not t["final"] for t in self.turns)


def _records(path: Path) -> Iterable[dict]:
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            yield d


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _visible(content: object) -> int:
    n = 0
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict):
            n += len(json.dumps(b.get("input", {}))) if b.get("type") == "tool_use" else len(b.get("text") or "")
            n += len(b.get("thinking") or "")
    return n


def read_run(path: Path, kind: str, session: str, desc: str = "") -> Run:
    run = Run(kind, session, path, desc=desc)
    turns: dict[str, dict] = {}
    for d in _records(path):
        run.start = run.start or d.get("timestamp", "")
        t, msg = d.get("type"), d.get("message") or {}
        if t == "cost-state":
            # a running total per process: a drop starts a new process (a resumed session), counted at its maximum
            total = d.get("totalCostUSD") or 0
            if not run.billed or total < run.billed[-1]["total"]:
                run.billed.append({"total": total, "usage": {}})
            run.billed[-1] = {"total": total, "usage": d.get("modelUsage") or {}}
        elif t == "assistant" and msg.get("model") != "<synthetic>":
            u = msg.get("usage") or {}
            turn = turns.get(msg.get("id") or f"line{len(turns)}")
            if turn is None:
                written = u.get("cache_creation_input_tokens", 0)
                w1 = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0)
                turn = turns[msg.get("id") or f"line{len(turns)}"] = {
                    "model": msg.get("model", ""),
                    "input": u.get("input_tokens", 0),
                    "write_1h": w1,
                    "write_5m": written - w1,
                    "read": u.get("cache_read_input_tokens", 0),
                    "logged": 0,
                    "vis": 0,
                    "final": False,
                }
            turn["logged"] = max(turn["logged"], u.get("output_tokens", 0))
            turn["vis"] += _visible(msg.get("content"))
            turn["final"] = turn["final"] or msg.get("stop_reason") is not None
            run.tools.update(
                c.get("name", "?")
                for c in msg.get("content") or []
                if isinstance(c, dict) and c.get("type") == "tool_use"
            )
        elif t == "user" and isinstance(msg.get("content"), list):
            for c in msg["content"]:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    n = _result_chars(c.get("content"))
                    run.result_chars += n
                    run.big += n if n > BIG_RESULT else 0
            if not run.prompt:
                text = _text(msg["content"])
                run.prompt = "" if text.startswith("<system-reminder>") else text.strip()
        elif t == "user" and not run.prompt:
            text = _text(msg.get("content"))
            run.prompt = "" if text.startswith("<system-reminder>") else text.strip()
    run.turns = list(turns.values())
    return run


def load_runs(dirs: Iterable[Path], since: str = "", until: str = "") -> list[Run]:
    """Every main session and subagent transcript under the project dirs whose first timestamp falls in since..until
    (YYYY-MM-DD, inclusive; empty = open)."""
    runs = []
    for proj in dirs:
        runs += [read_run(p, "main session", p.stem) for p in sorted(proj.glob("*.jsonl"))]
        for sub in sorted(proj.glob("*/subagents/*.jsonl")):
            meta_path = sub.with_suffix(".meta.json")
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            runs.append(read_run(sub, meta.get("agentType", "?"), sub.parent.parent.name, meta.get("description", "")))
    return [r for r in runs if r.turns and since <= r.start[:10] and (not until or r.start[:10] <= until)]


def _shown_out(turn: dict) -> float:
    return max(turn["logged"], turn["vis"] * VIS_TOKENS_PER_CHAR)


def hidden_per_turn(runs: list[Run]) -> float:
    """Billed output the transcripts do not show (hidden thinking), in tokens per turn: in each session whose cost
    records cover its transcripts, records' output of the models the transcripts use minus the visible estimate,
    spread evenly over those turns (the even spread is ASSUMED, harness-efficiency.md §2).

    This differs on purpose from the note's formula (r2_cost_phases.py: all records' Opus output minus 0.443 x visible
    chars, over every turn of every run; 233 tokens there, 351 on 2026-10-11..16 here, 302 with this one). A session
    without records, or whose records stop early, adds turns but no billed output and would skew the rest, so only
    sessions whose records reach their visible estimate count; and a turn's visible part is the larger of its logged
    output and the estimate, as in cost_segments.py."""
    sessions: dict[str, list[Run]] = defaultdict(list)
    for r in runs:
        sessions[r.session].append(r)
    extra = turns = 0.0
    for rs in sessions.values():
        procs = [p for r in rs for p in r.billed]
        models = {t["model"] for r in rs for t in r.turns}
        billed = sum(u.get("outputTokens", 0) for p in procs for m, u in p["usage"].items() if m in models)
        shown = sum(_shown_out(t) for r in rs for t in r.turns)
        if procs and billed >= shown:
            extra += billed - shown
            turns += sum(len(r.turns) for r in rs)
    return extra / turns if turns else 0.0


def _price(model: str, prices: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """The model's prices by exact id (a trailing -YYYYMMDD snapshot date ignored); None when not configured."""
    return prices.get(re.sub(r"-\d{8}$", "", model))


def _turn(t: dict, hidden: float, prices: dict[str, dict[str, float]]) -> tuple[dict, dict | None]:
    """A turn's tokens per kind and their $ per kind (None when its model has no configured price)."""
    toks = {**{k: t[k] for k in KINDS if k != "output"}, "output": _shown_out(t) + hidden}
    p = _price(t["model"], prices)
    return toks, ({k: n * p[PRICE_OF[k]] / 1e6 for k, n in toks.items()} if p else None)


# the cost records' token fields per kind; their cache writes are not split by TTL and are priced at the 1-hour
# rate (99.6 % of writes in harness-efficiency.md §2 were 1-hour)
RECORD_FIELDS = {"input": "inputTokens", "write": "cacheCreationInputTokens", "read": "cacheReadInputTokens"}
RECORD_FIELDS["output"] = "outputTokens"
RECORD_PRICE = {"input": "input", "write": "cache_write_1h", "read": "cache_read", "output": "output"}


def reconcile(runs: list[Run], hidden: float, prices: dict[str, dict[str, float]]) -> dict:
    """Transcript-priced tokens and $ per kind against the cost records (per process), over the sessions that have
    records. Transcripts miss most uncached input and calls that are logged only in the records (side calls)."""
    sessions: dict[str, list[Run]] = defaultdict(list)
    for r in runs:
        sessions[r.session].append(r)

    def side() -> dict:
        return {"tokens": dict.fromkeys(RECORD_FIELDS, 0.0), "usd": dict.fromkeys(RECORD_FIELDS, 0.0)}

    out = {"sessions": 0, "transcript": side(), "records": side(), "recorded_usd": 0.0}
    for rs in sessions.values():
        procs = [p for r in rs for p in r.billed]
        if not procs:
            continue
        out["sessions"] += 1
        out["recorded_usd"] += sum(p["total"] for p in procs)
        for t in (t for r in rs for t in r.turns):
            toks, usd = _turn(t, hidden, prices)
            for k, n in toks.items():
                kind = "write" if k.startswith("write") else k
                out["transcript"]["tokens"][kind] += n
                out["transcript"]["usd"][kind] += usd[k] if usd else 0.0
        for model, u in (mu for p in procs for mu in p["usage"].items()):
            price = _price(model, prices)
            for kind, f in RECORD_FIELDS.items():
                out["records"]["tokens"][kind] += u.get(f, 0)
                out["records"]["usd"][kind] += u.get(f, 0) * price[RECORD_PRICE[kind]] / 1e6 if price else 0.0
    return out


def pr_issues(merged: list[dict]) -> dict[int, set[int]]:
    """Merged PR number -> the issues it closes or its branch (`<issue>-<slug>`) names."""
    out = {}
    for p in merged:
        m = re.match(r"(\d+)-", p.get("headRefName") or "")
        out[p["number"]] = {i["number"] for i in p.get("closingIssuesReferences") or []} | ({int(m[1])} if m else set())
    return out


def _elsewhere(run: Run, repo_name: str) -> bool:
    head = run.desc + "\n" + run.prompt[:400]
    return any(next(n for n in m if n) != repo_name for m in REPO_REF.findall(head))


def _numbers(text: str) -> list[int]:
    return [int(n) for n in NUM_RE.findall(text)]


def _target(run: Run, by_pr: dict[int, set[int]], repo_name: str) -> str:
    """The merged issue a run worked on, from the first PR or issue number its description (else prompt) names;
    "shared" for main sessions and runs naming none, "other" for runs that name another repository."""
    if run.kind == "main session":
        return "shared"
    if _elsewhere(run, repo_name):
        return "other"
    merged = set().union(*by_pr.values()) if by_pr else set()
    for n in _numbers(run.desc) + _numbers(run.prompt[:400]):
        if by_pr.get(n):
            return f"#{min(by_pr[n])}"
        if n in merged:
            return f"#{n}"
    return "shared"


def cost(runs: list[Run], prices: dict[str, dict[str, float]], merged: list[dict], repo_name: str) -> dict:
    hidden = hidden_per_turn(runs)
    by_pr = pr_issues(merged)

    def row() -> dict:
        return {"runs": 0, "tokens": dict.fromkeys(KINDS, 0), "usd": dict.fromkeys((*KINDS, "total"), 0.0)}

    types: dict[str, dict] = defaultdict(row)
    targets: dict[str, dict] = defaultdict(row)
    unpriced: Counter[str] = Counter()  # model -> turns
    for r in runs:
        rows = (types[r.kind], targets[_target(r, by_pr, repo_name)])
        for x in rows:
            x["runs"] += 1
        for t in r.turns:
            toks, usd = _turn(t, hidden, prices)
            if usd is None:
                unpriced[t["model"]] += 1
            for x in rows:
                for k, n in toks.items():
                    x["tokens"][k] += n
                    x["usd"][k] += usd[k] if usd else 0.0
                    x["usd"]["total"] += usd[k] if usd else 0.0
    issues = {k: v for k, v in targets.items() if k.startswith("#")}
    return {
        "reconcile": reconcile(runs, hidden, prices),
        "unpriced": dict(unpriced),
        "types": dict(types),
        "issues": issues,
        "shared": targets.get("shared", row()),
        "other": targets.get("other", row()),
        "total": sum(x["usd"]["total"] for x in types.values()),
        "merged_issues": len(set().union(*by_pr.values())) if by_pr else 0,
        "hidden": hidden,
        "turns": sum(len(r.turns) for r in runs),
    }


def _n(x: float) -> str:
    return f"{x / 1e6:.1f}M" if x >= 1e6 else f"{x / 1e3:.0f}k" if x >= 1e3 else f"{x:.0f}"


def _cost_line(label: str, x: dict) -> str:
    toks = "".join(f"{_n(x['tokens'][k]):>9}" for k in KINDS)
    usd = x["usd"]
    dollars = [usd["input"], usd["write_1h"] + usd["write_5m"], usd["read"], usd["output"], usd["total"]]
    return f"{label[:29]:<30}{x['runs']:>5}{toks} |" + "".join(f"{d:>9.2f}" for d in dollars)


def cost_report(c: dict, top: int = 15) -> str:
    head = (
        f"{'':<30}{'runs':>5}{'input':>9}{'write1h':>9}{'write5m':>9}{'read':>9}{'output':>9} |"
        f"{'input $':>9}{'write $':>9}{'read $':>9}{'output $':>9}{'total $':>9}"
    )
    out = ["cost by agent type (tokens, then $ at [tool.devkit.prices])", head]
    out += [_cost_line(k, v) for k, v in sorted(c["types"].items(), key=lambda kv: -kv[1]["usd"]["total"])]
    issues = sorted(c["issues"].items(), key=lambda kv: -kv[1]["usd"]["total"])
    out += ["", f"cost by merged issue (top {min(top, len(issues))} of {len(issues)} with runs)", head]
    out += [_cost_line(k, v) for k, v in issues[:top]]
    out += [_cost_line("shared (lead, unnamed)", c["shared"]), _cost_line("other repositories", c["other"])]
    if c["unpriced"]:
        models = ", ".join(f"{m} ({n} turns)" for m, n in sorted(c["unpriced"].items()))
        out.append(f"not priced (no exact id in [tool.devkit.prices], counted at $0 above): {models}")
    rec = c["reconcile"]
    out += ["", f"reconciliation with the cost records (`cost-state`, per process) of the {rec['sessions']} sessions"]
    out.append(f"{'kind':<10}{'transcript tok':>16}{'recorded tok':>16}{'transcript $':>14}{'recorded $':>14}")
    for k in RECORD_FIELDS:
        t, r = rec["transcript"], rec["records"]
        out.append(f"{k:<10}{_n(t['tokens'][k]):>16}{_n(r['tokens'][k]):>16}{t['usd'][k]:>14,.2f}{r['usd'][k]:>14,.2f}")
    priced = sum(rec["transcript"]["usd"].values())
    scale = rec["recorded_usd"] / priced if priced and rec["recorded_usd"] else 1.0
    out.append(
        f"{'total':<42}{priced:>14,.2f}{sum(rec['records']['usd'].values()):>14,.2f}"
        f"  (the records' own costUSD: ${rec['recorded_usd']:,.2f}, all models)"
    )
    n = c["merged_issues"]
    own = c["total"] - c["other"]["usd"]["total"]
    attributed = sum(v["usd"]["total"] for v in c["issues"].values())
    out += [
        "",
        f"this repository: ${own:,.2f} transcript-priced, ${own * scale:,.2f} scaled to the cost records (x{scale:.2f}"
        f" = records' costUSD / transcript-priced $ of the sessions with records), over {n} merged issues",
        (
            f"per merged issue: ${own / n:,.2f} transcript-priced, ${own * scale / n:,.2f} records-scaled"
            f" ({attributed / own if own else 0:.0%} of the spend attributed to one issue by the run's description)"
            if n
            else "no merged issue in the window"
        ),
        f"output = max(logged usage.output_tokens, visible chars x {VIS_TOKENS_PER_CHAR}) + {c['hidden']:.0f} hidden"
        f" tokens per turn over {c['turns']:,} turns (hidden_per_turn; harness-efficiency.md §2). Transcripts miss"
        " most uncached input and the calls only the records log; the reconciliation shows how much. A run's issue is"
        " a heuristic.",
    ]
    return "\n".join(out)


# reviews: a round is one review pass ending in a hand-back (or, without a hand-back tool, an end_turn reply); a
# coordinator message that arrives before the round handed back is a correction, not a new round (§4)

VERB_RE = re.compile(
    r"(?i)\b(do not merge|not merge|send back|sent back|block(?:s|ed|ing)?|revise|reject|"
    r"changes requested|merge|accept|approve|pass)\b"
)
COND_RE = re.compile(
    r"(?i)^[*\s]*(after|with (?:fixes|one|two|three|small|the|minor)|once (?:the )?fix|if |pending|"
    r"but only after|subject to)"
)


def verdict(report: str) -> str:
    """pass / fix / none / unclear: the first verdict word, after the word 'verdict' when the report has one."""
    head = report[:600]
    if not head.strip():
        return "none"
    v = re.search(r"(?i)verdict", head)
    m = VERB_RE.search(head, v.end() if v else 0) or VERB_RE.search(head)
    if not m:
        return "unclear"
    if m[1].lower() in ("merge", "accept", "approve", "pass"):
        return "fix" if COND_RE.search(head[m.end() : m.end() + 40]) else "pass"
    return "fix"


def rounds(path: Path) -> list[dict]:
    rows = list(_records(path))
    blocks = [
        b
        for d in rows
        if d.get("type") == "assistant"
        for b in (d.get("message") or {}).get("content") or []
        if isinstance(b, dict)
    ]
    has_handback = any(b.get("type") == "tool_use" and "Handback" in b.get("name", "") for b in blocks)
    out: list[dict] = []
    cur: dict | None = None
    for d in rows:
        msg = d.get("message") or {}
        if d.get("type") == "user":
            text = _text(msg.get("content"))
            if not text.strip() or text.startswith("<system-reminder>"):
                continue
            if cur is None or (text.startswith(COORD) and cur["done"]):
                cur = {"start": d.get("timestamp", ""), "prompt": text, "report": "", "done": False}
                out.append(cur)
        elif d.get("type") == "assistant" and cur is not None:
            for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if b.get("type") == "tool_use" and "Handback" in b.get("name", ""):
                    cur["report"], cur["done"] = (b.get("input") or {}).get("message", ""), True
                elif b.get("type") == "text" and b.get("text", "").strip() and not has_handback:
                    cur["report"] = b["text"]
            if not has_handback and msg.get("stop_reason") == "end_turn":
                cur["done"] = True
    return out


def review_series(runs: list[Run], merged: list[dict], repo_name: str) -> dict[tuple[int, str], list[str]]:
    """(merged PR, reviewer role) -> verdicts in round order. A round belongs to the PR its report names first, else
    (resumed rounds) to the PRs its prompt names, else to the PRs the agent's description or first prompt names."""
    by_pr = pr_issues(merged)

    def resolve(nums: Iterable[int]) -> set[int]:
        return {p for n in nums for p in ([n] if n in by_pr else [q for q, iss in by_pr.items() if n in iss])}

    found: dict[tuple[int, str], list[tuple[str, str]]] = defaultdict(list)
    for run in runs:
        role = REVIEW_ROLES.get(run.kind.split(":")[-1])
        if not role or _elsewhere(run, repo_name):
            continue
        target = resolve(_numbers(run.desc)) or resolve(_numbers(run.prompt[:400]))
        for i, r in enumerate(rounds(run.path)):
            own = resolve(int(n) for n in re.findall(r"PRs? #?(\d{1,6})", r["report"][:250]))
            if not own and i:
                own = resolve(_numbers(r["prompt"][:400]))
            for p in own or target:
                found[(p, role)].append((r["start"], verdict(r["report"])))
    return {k: [v for _, v in sorted(rs)] for k, rs in sorted(found.items())}


def convergence(series: Iterable[tuple[str, list[str]]]) -> dict[str, dict]:
    """Per role and over all: series, those passing after one review-and-fix round (one round, or two where the second
    passes: Anthropic's measure), mean rounds to the first pass verdict (all rounds when none passed), 5+ round series."""
    groups: dict[str, list[list[str]]] = defaultdict(list)
    for role, verdicts in series:
        groups[role].append(verdicts)
        groups["all"].append(verdicts)
    out = {}
    for role, vs in groups.items():
        to_pass = [next((i + 1 for i, v in enumerate(s) if v == "pass"), len(s)) for s in vs]
        out[role] = {
            "series": len(vs),
            "rounds": sum(map(len, vs)),
            "converged": sum(1 for s in vs if len(s) == 1 or (len(s) == 2 and s[1] == "pass")),
            "mean_to_pass": sum(to_pass) / len(vs),
            "five_plus": sum(1 for s in vs if len(s) >= 5),
        }
    return out


def reviews_report(series: dict[tuple[int, str], list[str]]) -> str:
    conv = convergence((role, v) for (_, role), v in series.items())
    if not conv:
        return "no review rounds on merged PRs in this window's transcripts"
    out = [
        f"{'role':<10}{'series':>7}{'rounds':>7}  {'one-round convergence':<22}{'rounds to pass':>15}{'5+ rounds':>10}"
    ]
    for role in sorted(conv, key=lambda r: (r == "all", r)):
        x = conv[role]
        one = f"{x['converged']}/{x['series']} ({x['converged'] / x['series']:.0%})"
        out.append(
            f"{role:<10}{x['series']:>7}{x['rounds']:>7}  {one:<22}{x['mean_to_pass']:>15.2f}{x['five_plus']:>10}"
        )
    long = sorted(((k, v) for k, v in series.items() if len(v) >= 5), key=lambda kv: -len(kv[1]))
    out.append("series of 5+ rounds: " + (", ".join(f"#{p} {role} {len(v)}" for (p, role), v in long) or "none"))
    out.append(
        "series = one reviewer role's rounds on one merged PR; one-round convergence = one round, or two where the"
        " second passes (harness-efficiency.md §4; verdicts parsed from the hand-back, a heuristic)."
    )
    return "\n".join(out)


# misses: SZZ-style blame of the code lines each bug-fix PR changes, at main just before it merged (§4)

CODE_EXT = (".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".sh", ".yml", ".yaml", ".toml", ".json")


def is_code(path: str) -> bool:
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    return (
        low.endswith(CODE_EXT)
        and not low.startswith(("tests/", "docs/", "evals/"))
        and "/tests/" not in low
        and not name.startswith("test_")
        and ".test." not in name
        and ".spec." not in name
        and name not in ("uv.lock", "package-lock.json")
        and "generated" not in low
    )


def trace(prs: list[dict], bugs: dict[int, dict], git: Callable[..., str]) -> list[dict]:
    """(introducing PR, bug-fix PR) candidates: a PR fixes a bug when it closes or its branch names a bug issue; the
    lines it removes or changes in code files are blamed at its merge commit's first parent, and each blamed commit
    maps to the merged PR that carried it (a branch commit, or the squash commit itself: a squash merge puts none of
    the branch's commits on main). `after_merge`: the bug was filed after the introducing PR merged."""
    owner = {c["oid"]: p["number"] for p in prs for c in p.get("commits") or []}
    owner |= {mc: p["number"] for p in prs if (mc := (p.get("mergeCommit") or {}).get("oid"))}
    merged_at = {p["number"]: p.get("mergedAt", "") for p in prs}
    by_pr = pr_issues(prs)
    out = []
    for p in sorted(prs, key=lambda p: p["number"]):
        fixed = sorted(i for i in by_pr[p["number"]] if i in bugs)
        mc = (p.get("mergeCommit") or {}).get("oid")
        if not fixed or not mc:
            continue
        blamed: Counter[tuple[int, str]] = Counter()
        path = None
        for line in git("diff", "-U0", f"{mc}^1", mc).splitlines():
            if line.startswith("--- "):
                path = line[6:] if line.startswith("--- a/") else None
            elif line.startswith("@@") and path and is_code(path):
                hm = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
                start, count = (int(hm[1]), int(hm[2] or 1)) if hm else (0, 0)
                if count:
                    blame = git("blame", "-l", "-s", "-L", f"{start},{start + count - 1}", f"{mc}^1", "--", path)
                    for b in blame.splitlines():
                        intro = owner.get(b.split()[0].lstrip("^")) if b.split() else None
                        if intro and intro != p["number"]:
                            blamed[(intro, path)] += 1
        filed = min(bugs[i]["createdAt"] for i in fixed)
        out += [
            {"intro": i, "fix": p["number"], "bugs": fixed, "after_merge": filed > merged_at[i], "path": f, "lines": k}
            for (i, f), k in sorted(blamed.items())
        ]
    return out


def misses_report(pairs: list[dict], reviewed: dict[int, dict[str, int]]) -> str:
    after = [x for x in pairs if x["after_merge"]]
    out = []
    for x in sorted(after, key=lambda x: (x["intro"], x["fix"])):
        roles = ", ".join(f"{r} {n}" for r, n in sorted(reviewed.get(x["intro"], {}).items())) or "not reviewed"
        bugs = ", ".join(f"#{b}" for b in x["bugs"])
        out.append(f"#{x['intro']} ({roles}) <- fix #{x['fix']} for bug {bugs}: {x['path']} ({x['lines']} lines)")
    hit = {x["intro"] for x in after} & set(reviewed)
    out.append(
        f"reviewed PRs in a candidate post-merge defect pair (check each): {len(hit)} of {len(reviewed)} reviewed PRs"
        + (f" ({len(hit) / len(reviewed):.0%})" if reviewed else "")
    )
    out.append(
        "SZZ-style blame over-blames: check each pair against its bug issue before counting it (harness-efficiency.md"
        " §4). Reviewed = merged PRs with an agent review round in this window's transcripts; (role rounds)."
    )
    return "\n".join(out)


# --- I/O edge: GitHub and the CLI -------------------------------------------------------------------------------


# `gh pr list --json commits` also asks for every commit's authors and GitHub refuses it past ~50 PRs (500k nodes)
MERGED_QUERY = """query($owner: String!, $name: String!, $endCursor: String) { repository(owner: $owner, name: $name) {
  pullRequests(states: MERGED, first: 100, after: $endCursor) { pageInfo { hasNextPage endCursor }
    nodes { number headRefName mergedAt mergeCommit { oid } closingIssuesReferences(first: 20) { nodes { number } }
      commits(first: 100) { nodes { commit { oid } } } } } } }"""


def merged_prs(since: str = "", until: str = "") -> list[dict]:
    """Every merged PR (merged since..until, inclusive dates), in `gh pr list --json` shape."""
    owner, name = board.repo().split("/")
    out = board.gh(
        "api", "graphql", "--paginate", "-f", f"query={MERGED_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}",
        "--jq", ".data.repository.pullRequests.nodes[]",
    )  # fmt: skip
    prs = []
    for line in out.splitlines():
        p = json.loads(line)
        if since <= (p["mergedAt"] or "")[:10] and (not until or p["mergedAt"][:10] <= until):
            p["closingIssuesReferences"] = p["closingIssuesReferences"]["nodes"]
            p["commits"] = [{"oid": c["commit"]["oid"]} for c in p["commits"]["nodes"]]
            prs.append(p)
    return prs


def bug_issues() -> dict[int, dict]:
    out = {}
    for label in ("type:bug", "bug"):
        args = ("issue", "list", "-R", board.repo(), "--state", "all", "--label", label, "--limit", "1000")
        try:
            out |= {i["number"]: i for i in json.loads(board.gh(*args, "--json", "number,title,createdAt"))}
        except board.BoardError:
            continue  # a repository without that label
    return out


def run(mode: str, since: str = "", until: str = "", as_json: bool = False) -> str:
    runs = load_runs(project_dirs(), since, until)
    merged = merged_prs(since, until)
    name = main_checkout().name
    if mode == "cost":
        c = cost(runs, config.get().prices, merged, name)
        return json.dumps(c, indent=1) if as_json else cost_report(c)
    series = review_series(runs, merged, name)
    if mode == "reviews":
        if as_json:
            return json.dumps([{"pr": p, "role": r, "verdicts": v} for (p, r), v in series.items()], indent=1)
        return reviews_report(series)
    reviewed: dict[int, dict[str, int]] = defaultdict(dict)
    for (p, role), v in series.items():
        reviewed[p][role] = len(v)
    pairs = trace(merged, bug_issues(), lambda *a: paths.git(*a, cwd=paths.root(), check=False))
    return json.dumps(pairs, indent=1) if as_json else misses_report(pairs, dict(reviewed))
