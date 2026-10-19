"""Review-loop convergence for #254 round 2 (r2-review-convergence.md).

Usage:
  python3 review_convergence.py fetch <cache_dir>                 # gh: merged PRs in SINCE..UNTIL, both repos
  python3 review_convergence.py rounds <cache_dir> <project_dir>  # writes <cache_dir>/rounds.json, prints tables
  python3 review_convergence.py comments <cache_dir>              # round-1's comment count, replicated and widened
  python3 review_convergence.py classify <cache_dir>              # keyword flags, delta-only and minor-verdict stats
  python3 review_convergence.py misses <cache_dir>                # SZZ-style post-merge defect candidates
  python3 review_convergence.py lead <project_dir>                # lead calls spent on the review loop
  python3 review_convergence.py sample <cache_dir>                # the material that was hand-coded
  python3 review_convergence.py codes                             # hand-code tallies with Wilson 95 % intervals

Sources:
  - GitHub: every PR merged in SINCE..UNTIL in REPOS (comments, commits, files) via `gh pr view`.
  - Claude Code subagent transcripts (<project_dir>/*/subagents/*.jsonl + .meta.json) of the review agent types.
    A review agent is spawned once and resumed with SendMessage ("The coordinator sent a message ..."); a *round* is
    one segment that ends in a SubagentHandback (or, for agents without that tool, an end_turn text). A coordinator
    message that arrives before the current segment has handed back is a mid-round correction, not a new round.
Token cost uses the per-M prices fitted in extra_stats.py (input 4.09, output 20.05, cache read 0.20, write 7.95).
"""

import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

SINCE = "2026-10-11"
UNTIL = "2026-10-16"  # frozen window end (inclusive), so a rerun sees the same 188 + 73 PRs
# the consuming repository is private and named by code (repo-E): to rerun, set REPO_E to its bare GitHub
# repository name (no owner; OWNER below is prepended), e.g. REPO_E=<name> python3 review_convergence.py fetch <dir>
REPO_E = os.environ.get("REPO_E", "repo-E")
REPOS = [REPO_E, "agent-harness"]
OWNER = "Marius-Juston"
PRICE = dict(inp=4.09, out=20.05, cr=0.20, cw=7.95)
REVIEW_TYPES = {
    "reviewer": "code",
    "harness-ops:reviewer": "code",
    "security-reviewer": "security",
    "research-auditor": "audit",
    "harness-ops:research-auditor": "audit",
}
COORD = "The coordinator sent a message while you were working"
FIELDS = "number,title,body,comments,commits,mergedAt,createdAt,additions,deletions,files,headRefName,closingIssuesReferences"


def jload(path):
    with open(path) as fh:
        return json.load(fh)


def jdump(obj, path):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, default=list)


def wtext(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def lines(path):
    with open(path) as fh:
        return fh.readlines()


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[:300])
    return r.stdout


def fetch(cache):
    os.makedirs(f"{cache}/prs", exist_ok=True)
    jobs = []
    for repo in REPOS:
        lst = json.loads(
            gh(
                "pr",
                "list",
                "-R",
                f"{OWNER}/{repo}",
                "--state",
                "merged",
                "--limit",
                "500",
                "--search",
                f"merged:{SINCE}..{UNTIL}",
                "--json",
                "number",
            )
        )
        jobs += [(repo, p["number"]) for p in lst]

    def one(job):
        repo, n = job
        out = f"{cache}/prs/{repo}-{n}.json"
        if not (os.path.exists(out) and os.path.getsize(out)):
            wtext(out, gh("pr", "view", str(n), "-R", f"{OWNER}/{repo}", "--json", FIELDS))

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, jobs))
    for repo in REPOS:
        merged = ["--state", "merged", "--limit", "500", "--search", f"merged:{SINCE}..{UNTIL}"]
        wtext(
            f"{cache}/merge_{repo}.json",
            gh("pr", "list", "-R", f"{OWNER}/{repo}", *merged, "--json", "number,mergeCommit"),
        )
        issues = ["--state", "all", "--limit", "1000", "--search", f"created:>={SINCE}"]
        fields = "number,title,labels,createdAt,body,state"
        wtext(f"{cache}/issues_{repo}.json", gh("issue", "list", "-R", f"{OWNER}/{repo}", *issues, "--json", fields))
    print(len(jobs), "PRs cached")


def load_prs(cache):
    prs = {}
    for f in glob.glob(f"{cache}/prs/*.json"):
        d = jload(f)
        repo = os.path.basename(f).rsplit("-", 1)[0]
        prs[(repo, d["number"])] = d
    return prs


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def segments(path):
    """Split one review agent transcript into rounds. Returns list of dicts."""
    rows = [json.loads(x) for x in lines(path)]
    has_handback = any(
        b.get("type") == "tool_use" and "Handback" in b.get("name", "")
        for d in rows
        if d.get("type") == "assistant" and isinstance(d["message"]["content"], list)
        for b in d["message"]["content"]
    )
    segs, cur, seen, out_seen = [], None, set(), {}
    for d in rows:
        t = d.get("type")
        if t == "user":
            x = text_of(d["message"]["content"])
            if x.startswith("<system-reminder>") or not x.strip():
                continue
            if cur is None or (x.startswith(COORD) and cur["done"]):
                cur = dict(start=d.get("timestamp"), prompt=x, mid=[], report="", done=False, tok=Counter(), tools=0)
                segs.append(cur)
            elif x.startswith(COORD):
                cur["mid"].append(x[:400])
        elif t == "assistant" and cur is not None:
            m = d["message"]
            mid, u = m.get("id"), m.get("usage") or {}
            if mid in seen:
                extra = u.get("output_tokens", 0) - out_seen.get(mid, 0)
                if extra > 0:
                    cur["tok"]["out"] += extra
                    out_seen[mid] += extra
            else:
                seen.add(mid)
                out_seen[mid] = u.get("output_tokens", 0)
                cur["tok"]["inp"] += u.get("input_tokens", 0)
                cur["tok"]["cr"] += u.get("cache_read_input_tokens", 0)
                cur["tok"]["cw"] += u.get("cache_creation_input_tokens", 0)
                cur["tok"]["out"] += u.get("output_tokens", 0)
                cur["tok"]["calls"] += 1
            for b in m["content"] if isinstance(m["content"], list) else []:
                if b.get("type") in ("tool_use", "text"):
                    cur["tok"]["out_est"] += (
                        len(json.dumps(b.get("input")) if b["type"] == "tool_use" else b["text"]) // 4
                    )
                if b.get("type") == "tool_use":
                    cur["tools"] += 1
                    if "Handback" in b.get("name", ""):
                        cur["report"] = b["input"].get("message", "")
                        cur["done"] = True
                        cur["end"] = d.get("timestamp")
                elif b.get("type") == "text" and b["text"].strip() and not has_handback:
                    cur["report"] = b["text"]
            if not has_handback and m.get("stop_reason") == "end_turn":
                cur["done"] = True
                cur["end"] = d.get("timestamp")
    for s in segs:
        # transcripts log output_tokens at stream start, so recorded output is far too low; price the larger of the
        # recorded value and a visible-content estimate (chars/4, thinking excluded): a lower bound on output cost.
        s["tok"]["out"] = max(s["tok"]["out"], s["tok"]["out_est"])
        s["cost"] = sum(s["tok"][k] * PRICE[k] for k in PRICE) / 1e6
        s["tok"] = dict(s["tok"])
    return segs


VERB_RE = re.compile(
    r"(?i)\b(do not merge|not merge|send back|sent back|block(?:s|ed|ing)?|revise|reject|"
    r"changes requested|merge|accept|approve|pass)\b"
)
COND_RE = re.compile(
    r"(?i)^[*\s]*(after|with (?:fixes|one|two|three|small|the|minor)|once (?:the )?fix|if |pending|"
    r"but only after|subject to)"
)


def verdict(report):
    """pass / fix / none: the first verdict word, after the word 'verdict' when the report has one."""
    head = report[:600]
    if not head.strip():
        return "none"
    v = re.search(r"(?i)verdict", head)
    m = VERB_RE.search(head, v.end() if v else 0) or VERB_RE.search(head)
    if not m:
        return "unclear"
    w = m.group(1).lower()
    if w in ("merge", "accept", "approve", "pass"):
        return "fix" if COND_RE.search(head[m.end() : m.end() + 40]) else "pass"
    return "fix"


FIND_HEAD = re.compile(
    r"(?im)^[#*\s]{0,6}(findings|blocking|blockers?|fix(?:es)? (?:needed|required|that block)|must fix|"
    r"new findings|remaining findings?|finding that blocks)"
)
STOP_HEAD = re.compile(
    r"(?im)^[#*\s]{0,6}(follow-?ups?|non-?blocking|nits?\b|not blocking|remaining low|optional|"
    r"what (holds|works|passes)|checked|tests?:|files:)"
)
TOP_ITEM = re.compile(r"(?m)^(?:\*\*)?(\d+)[.)]\s")


def n_findings(report, v):
    """Findings in one report: top-level numbered items under a Findings/Blocking heading, up to a follow-ups or nits
    heading; else inline '(1) ... (2)' markers. 0 for a pass verdict. Spot-checked by hand; see the note."""
    if v == "pass":
        return 0
    m = FIND_HEAD.search(report)
    tail = report[m.end() :] if m else report
    stop = STOP_HEAD.search(tail, 1)
    tail = tail[: stop.start()] if stop else tail
    items = {int(x) for x in TOP_ITEM.findall(tail)}
    if not items:
        items = {int(x) for x in re.findall(r"\((\d)\)\s", report[:1500])}
    return max(len(items), 1)


NUM_RE = re.compile(r"(?:PRs?\s*#?|pull/|#)(\d{1,4})\b")
NUM_LIST_RE = re.compile(r"(?:PRs?|#)\s*#?(\d{1,4})(?:\s*(?:,|/|\+|and|&)\s*#?(\d{1,4}))+")


def numbers(s):
    out = set(int(n) for n in NUM_RE.findall(s))
    for m in re.finditer(r"#?(\d{2,4})(?:\s*/\s*#?(\d{2,4}))+", s):
        out |= set(int(x) for x in re.findall(r"\d{2,4}", m.group(0)))
    for m in re.finditer(r"#(\d{2,4})\s*(?:\+|and|&)\s*#?(\d{2,4})", s):
        out |= {int(m.group(1)), int(m.group(2))}
    for m in re.finditer(r"(?i)PRs?\s+(\d{2,4})(?:\s*(?:,|and|&)\s*(\d{2,4}))*", s):
        out |= set(int(x) for x in re.findall(r"\d{2,4}", m.group(0)))
    return out


def resolver(prs):
    by_issue = defaultdict(set)
    for (repo, n), d in prs.items():
        m = re.match(r"(\d+)-", d.get("headRefName", ""))
        if m:
            by_issue[(repo, int(m.group(1)))].add(n)
        for ref in d.get("closingIssuesReferences") or []:
            by_issue[(repo, ref["number"])].add(n)

    def resolve(repo, nums):
        hits = set()
        for n in nums:
            if (repo, n) in prs:
                hits.add((repo, n))
            else:
                hits |= {(repo, p) for p in by_issue.get((repo, n), ())}
        return hits

    return resolve


def rounds(cache, project):
    prs = load_prs(cache)
    resolve = resolver(prs)
    agents = []
    for meta in sorted(glob.glob(f"{project}/*/subagents/*.meta.json")):
        m = jload(meta)
        role = REVIEW_TYPES.get(m.get("agentType"))
        if not role:
            continue
        segs = segments(meta.replace(".meta.json", ".jsonl"))
        if not segs:
            continue
        desc, first = m.get("description", ""), segs[0]["prompt"]
        repo = "agent-harness" if re.search(r"agent-harness", desc + first[:300]) else REPO_E
        target = resolve(repo, numbers(desc)) or resolve(repo, numbers(first[:400]))
        for i, s in enumerate(segs):
            s["idx"] = i
            s["verdict"] = verdict(s["report"])
            s["findings"] = n_findings(s["report"], s["verdict"])
            # A resumed reviewer is sometimes handed a different PR; the report names the PR it reviewed first.
            named = set(re.findall(r"PRs? #?(\d{1,4})", s["report"][:250]))
            named |= set(re.findall(r"(?m)^\s*\*{0,2}\([A-D]\)\*{0,2}\s*PR #?(\d{1,4})", s["report"]))
            own = resolve(repo, {int(x) for x in named})
            if not own and i:
                own = resolve(repo, numbers(s["prompt"][:400]))
            s["prs"] = sorted(own or target)
        agents.append(
            dict(file=meta, type=m["agentType"], role=role, desc=desc, repo=repo, prs=sorted(target), segs=segs)
        )
    jdump(agents, f"{cache}/rounds.json")
    # builder fix rounds: a builder resumed after its handback with review findings
    fix = defaultdict(list)
    for meta in sorted(glob.glob(f"{project}/*/subagents/*.meta.json")):
        m = jload(meta)
        t = m.get("agentType", "")
        if t in REVIEW_TYPES or not re.search(r"builder|researcher|tester", t):
            continue
        for s in segments(meta.replace(".meta.json", ".jsonl"))[1:]:
            if re.search(
                r"(?i)\b(re-?review|review|audit|auditor|finding|verdict)", s["prompt"][:700]
            ) and not re.search(r"(?i)conflicts? with main|fails the gate|merged tree fails", s["prompt"][:700]):
                fix[t.split(":")[-1]].append(s)
    for t, ss in sorted(fix.items()):
        print(
            f"{t} resumes carrying review/audit findings: {len(ss)}, mean ${sum(s['cost'] for s in ss) / len(ss):.2f}, "
            f"{sum(s['tok'].get('cr', 0) + s['tok'].get('cw', 0) for s in ss) / len(ss) / 1e6:.2f}M input tokens each, "
            f"total ${sum(s['cost'] for s in ss):.0f}"
        )
    report(prs, agents)


def pct(a, b):
    return f"{100 * a / b:.0f} %" if b else "n/a"


def report(prs, agents):
    print(
        f"review agents: {len(agents)}, rounds: {sum(len(a['segs']) for a in agents)}, "
        f"unmapped agents: {sum(1 for a in agents if not a['prs'])}"
    )
    for a in agents:
        if not a["prs"]:
            print("  unmapped:", a["type"], "|", a["desc"])
    # rounds per (PR, role) and per PR (sum over roles = review passes the lead had to wait for)
    per = defaultdict(list)
    for a in agents:
        for s in a["segs"]:
            for p in s["prs"]:
                per[(tuple(p), a["role"])].append(s)
    for p in per:
        per[p].sort(key=lambda s: s["start"])
    print("\n## rounds per (PR, role), merged PRs since", SINCE)
    print("role | PRs | mean rounds | median | one-round pass | >=4 rounds | max | tokens M/round | $/round | $/PR")
    for role in ["code", "security", "audit", "all"]:
        keys = [k for k in per if role == "all" or k[1] == role]
        if role == "all":
            grp = defaultdict(list)
            for k in keys:
                grp[k[0]] += per[k]
            series = list(grp.values())
        else:
            series = [per[k] for k in keys]
        if not series:
            continue
        n = [len(s) for s in series]
        one = sum(1 for s in series if len(s) == 1 and s[0]["verdict"] == "pass")
        segs = [x for s in series for x in s]
        tok = sum(sum(v for k, v in x["tok"].items() if k not in ("calls", "out_est")) for x in segs) / len(segs) / 1e6
        cost = sum(x["cost"] for x in segs)
        n.sort()
        print(
            f"{role} | {len(series)} | {sum(n) / len(n):.2f} | {n[len(n) // 2]} | {one} ({pct(one, len(series))}) | "
            f"{sum(1 for x in n if x >= 4)} | {max(n)} | {tok:.2f} | {cost / len(segs):.2f} | {cost / len(series):.2f}"
        )
    print("\nround-count histogram (per PR x role):", sorted(Counter(len(v) for v in per.values()).items()))
    print("verdicts by round index:")
    by_i = defaultdict(Counter)
    for v in per.values():
        for i, s in enumerate(v):
            by_i[min(i + 1, 5)][s["verdict"]] += 1
    for i in sorted(by_i):
        print(f"  round {i}{'+' if i == 5 else ''}: {dict(by_i[i])}")
    print("findings (rough enumerated-item count) in non-pass rounds, by round index:")
    for i in sorted(by_i):
        f = [
            s["findings"]
            for v in per.values()
            for j, s in enumerate(v)
            if min(j + 1, 5) == i and s["verdict"] != "pass"
        ]
        if f:
            f.sort()
            print(f"  round {i}: n={len(f)} mean {sum(f) / len(f):.1f} median {f[len(f) // 2]}")
    # cost by round index
    print("cost by round index ($ mean, tokens M mean):")
    for i in sorted(by_i):
        ss = [s for v in per.values() for j, s in enumerate(v) if min(j + 1, 5) == i]
        print(
            f"  round {i}: n={len(ss)} ${sum(s['cost'] for s in ss) / len(ss):.2f} "
            f"{sum(s['tok'].get('cr', 0) + s['tok'].get('cw', 0) + s['tok'].get('inp', 0) for s in ss) / len(ss) / 1e6:.2f}M in, "
            f"{sum(s['tok'].get('out', 0) for s in ss) / len(ss) / 1e3:.1f}k out, {sum(s['tools'] for s in ss) / len(ss):.1f} tool calls"
        )
    ones = sum(1 for v in per.values() if len(v) == 1)
    five = sum(1 for v in per.values() if len(v) >= 5)
    allr = [len(v) for v in per.values()]
    print(
        f"per (PR, role): n={len(per)} mean {sum(allr) / len(allr):.2f}, one round {ones} ({pct(ones, len(per))}), "
        f">=5 rounds {five} ({pct(five, len(per))})"
    )
    # Anthropic's measure: passing after one review and fix round. Here: one round, or two where the second (the
    # check of the fixes) found nothing blocking. Rounds to pass: rounds up to the first pass verdict, else all rounds.
    conv1 = sum(1 for v in per.values() if len(v) == 1 or (len(v) == 2 and v[1]["verdict"] == "pass"))
    rtp = [next((i + 1 for i, x in enumerate(v) if x["verdict"] == "pass"), len(v)) for v in per.values()]
    print(
        f"converged after one review-and-fix round: {conv1}/{len(per)} ({pct(conv1, len(per))}); "
        f"mean rounds to pass {sum(rtp) / len(rtp):.2f}"
    )
    for role in ("code", "security", "audit"):
        vs = [v for k, v in per.items() if k[1] == role]
        c = sum(1 for v in vs if len(v) == 1 or (len(v) == 2 and v[1]["verdict"] == "pass"))
        print(f"  {role}: {c}/{len(vs)} ({pct(c, len(vs))}), >=5 rounds {sum(1 for v in vs if len(v) >= 5)}")
    later = [s for v in per.values() for s in v[1:]]
    conf = [s for s in later if s["verdict"] == "pass"]
    for role in ("code", "security", "audit"):
        lr = [s for k, v in per.items() if k[1] == role for s in v[1:]]
        beyond3 = [s for k, v in per.items() if k[1] == role for s in v[3:]]
        print(
            f"  later {role} rounds: {len(lr)}, pass {pct(sum(1 for s in lr if s['verdict'] == 'pass'), len(lr))}, "
            f"${sum(s['cost'] for s in lr):.0f}; rounds beyond the 3rd: {len(beyond3)} (${sum(s['cost'] for s in beyond3):.0f})"
        )
    for name, grp in (
        ("resumed (SendMessage)", [s for s in later if s["idx"] > 0]),
        ("fresh spawn", [s for s in later if s["idx"] == 0]),
    ):
        if grp:
            print(
                f"later rounds, {name}: {len(grp)}, mean ${sum(s['cost'] for s in grp) / len(grp):.2f}, "
                f"{sum(s['tok'].get('cr', 0) + s['tok'].get('cw', 0) + s['tok'].get('inp', 0) for s in grp) / len(grp) / 1e6:.2f}M input"
            )
    print(
        f"rounds after the first: {len(later)}, of which pass (confirmation, nothing blocking) {len(conf)} "
        f"({pct(len(conf), len(later))}); cost of later rounds ${sum(s['cost'] for s in later):.0f}, "
        f"of confirmation rounds ${sum(s['cost'] for s in conf):.0f}"
    )
    heavy = sorted(per.items(), key=lambda kv: -len(kv[1]))[:8]
    print("most rounds:", ", ".join(f"{k[0][0]}#{k[0][1]} {k[1]} {len(v)}" for k, v in heavy))
    top6 = [v for k, v in heavy[:6]]
    later_top = sum(len(v) - 1 for v in top6)
    print(
        f"top 6 (PR, role) series hold {later_top} of {len(later)} later rounds ({pct(later_top, len(later))}), "
        f"${sum(s['cost'] for v in top6 for s in v):.0f} of review cost"
    )
    from datetime import datetime

    def ts(x):
        return datetime.fromisoformat(x.replace("Z", "+00:00"))

    dur = sorted(
        (ts(s["end"]) - ts(s["start"])).total_seconds() / 60 for a in agents for s in a["segs"] if s.get("end")
    )
    print(
        f"round wall time: median {dur[len(dur) // 2]:.1f} min, p90 {dur[int(len(dur) * 0.9)]:.1f} min (n={len(dur)})"
    )
    lat = []
    for (p, _role), v in per.items():
        if len(v) > 1 and tuple(p) in prs and prs[tuple(p)].get("mergedAt"):
            lat.append((ts(prs[tuple(p)]["mergedAt"]) - ts(v[0]["start"])).total_seconds() / 3600)
    one = []
    for (p, _role), v in per.items():
        if len(v) == 1 and tuple(p) in prs and prs[tuple(p)].get("mergedAt"):
            one.append((ts(prs[tuple(p)]["mergedAt"]) - ts(v[0]["start"])).total_seconds() / 3600)
    lat.sort()
    one.sort()
    print(
        f"first review -> merge: multi-round median {lat[len(lat) // 2]:.1f} h (n={len(lat)}); "
        f"single-round median {one[len(one) // 2]:.1f} h (n={len(one)})"
    )
    total = sum(s["cost"] for a in agents for s in a["segs"])
    print(f"total review cost ${total:.0f} over {sum(len(a['segs']) for a in agents)} rounds")
    merged = {k for k in prs}
    reviewed = {p[0] for p in per}
    print(f"\nmerged PRs: {len(merged)}; with any agent review round in transcripts: {len(reviewed & merged)}")
    for repo in REPOS:
        m = {k for k in merged if k[0] == repo}
        print(f"  {repo}: merged {len(m)}, reviewed {len({k for k in reviewed if k[0] == repo})}")


LOCAL = {
    REPO_E: os.path.expanduser(f"~/GitHub/{REPO_E}"),
    "agent-harness": os.path.expanduser("~/GitHub/agent-harness"),
}
CODE_EXT = (".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".sh", ".yml", ".yaml", ".toml", ".json")


def git(repo, *args):
    return subprocess.run(["git", "-C", LOCAL[repo], *args], capture_output=True, text=True).stdout


def is_code(path):
    low = path.lower()
    return (
        low.endswith(CODE_EXT)
        and not low.startswith(("tests/", "docs/", "evals/"))
        and "/tests/" not in low
        and "test_" not in os.path.basename(low)
        and ".test." not in low
        and ".spec." not in low
        and not low.endswith(("uv.lock", "package-lock.json"))
        and "generated" not in low
    )


def misses(cache):
    """SZZ-style: blame the code lines each bug-fix PR deletes or changes (at main just before the fix merged) and map
    the blamed commits to the merged PRs that introduced them. Prints candidate (introducing PR -> fix PR) pairs."""
    prs = load_prs(cache)
    rounds_data = jload(f"{cache}/rounds.json") if os.path.exists(f"{cache}/rounds.json") else []
    roles = defaultdict(Counter)
    for a in rounds_data:
        for s in a["segs"]:
            for p in s["prs"]:
                roles[tuple(p)][a["role"]] += 1
    out = []
    for repo in REPOS:
        bugs = {
            i["number"]: i
            for i in jload(f"{cache}/issues_{repo}.json")
            if any(lb["name"] in ("type:bug", "bug") for lb in i["labels"])
        }
        merges = {p["number"]: p["mergeCommit"]["oid"] for p in jload(f"{cache}/merge_{repo}.json")}
        owner = {}
        for (r, n), d in prs.items():
            if r == repo:
                for c in d["commits"]:
                    owner.setdefault(c["oid"], n)
        for (r, n), d in sorted(prs.items()):
            if r != repo or n not in merges:
                continue
            m = re.match(r"(\d+)-", d.get("headRefName", ""))
            linked = {ref["number"] for ref in d.get("closingIssuesReferences") or []} | (
                {int(m.group(1))} if m else set()
            )
            fixed = sorted(i for i in linked if i in bugs)
            if not fixed:
                continue
            mc = merges[n]
            diff = git(repo, "diff", "-U0", f"{mc}^1", mc)
            blamed, path = Counter(), None
            for line in diff.splitlines():
                if line.startswith("--- "):
                    path = line[6:] if line.startswith("--- a/") else None
                elif line.startswith("@@") and path and is_code(path):
                    hm = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
                    start, cnt = int(hm.group(1)), int(hm.group(2) or 1)
                    if cnt == 0:
                        continue
                    bl = git(repo, "blame", "-l", "-s", "-L", f"{start},{start + cnt - 1}", f"{mc}^1", "--", path)
                    for b in bl.splitlines():
                        sha = b.split()[0].lstrip("^")
                        if sha in owner and owner[sha] != n:
                            blamed[(owner[sha], path)] += 1
            filed = min(bugs[i]["createdAt"] for i in fixed)
            for (intro, path), k in sorted(blamed.items()):
                di = prs[(repo, intro)]
                out.append(
                    dict(
                        repo=repo,
                        intro=intro,
                        intro_merged=di["mergedAt"],
                        fix=n,
                        bugs=fixed,
                        filed=filed,
                        when="after-merge" if filed > di["mergedAt"] else "before-merge",
                        path=path,
                        lines=k,
                        reviews=dict(roles.get((repo, intro), {})),
                        bug_title=bugs[fixed[0]]["title"][:90],
                    )
                )
    jdump(out, f"{cache}/misses.json")
    pairs = {(o["repo"], o["intro"], o["fix"]) for o in out}
    print(f"candidate (introducing PR -> bug-fix PR) pairs: {len(pairs)}")
    for o in out:
        print(
            f"{o['repo']} intro #{o['intro']} -> fix #{o['fix']} bugs {o['bugs']} {o['when']} {o['path']} "
            f"({o['lines']} lines) reviews {o['reviews']} | {o['bug_title']}"
        )


R1_PREFIX = ("Security review", "Security re-review", "Code review", "Code re-review", "Research audit cycle")
SKIP_FIRST = ("ci ran", "dev integrate", "integrate", "owner", "lead", "correction", "commons")


def first_line(body):
    return re.sub(r"[`*_#]", "", body.strip().split("\n")[0]).strip()


def kind(body):
    f = first_line(body).lower()
    if f.startswith(SKIP_FIRST):
        return None
    if re.match(r".{0,45}\bfix", f):
        return "fix"
    if re.search(r"review|audit|security-reviewer|research-auditor", f[:60]):
        return "verdict"
    return None


def comments(cache):
    """Round counts from the review comments the lead posts on each PR (the round-1 method), plus fix pushes."""
    prs = load_prs(cache)
    lst = jload(f"{cache}/merge_{REPO_E}.json")
    # round-1 replication: the 60 most recently created merged repo-E PRs, r1's five prefixes
    recent = sorted((p["number"] for p in lst), reverse=True)[:60]
    n = [sum(1 for c in prs[(REPO_E, x)]["comments"] if c["body"].lstrip("*").startswith(R1_PREFIX)) for x in recent]
    rv = [k for k in n if k]
    print(
        f"r1 replication: {len(rv)} of 60 with review comments, mean {sum(rv) / len(rv):.2f}, one round "
        f"{sum(1 for k in rv if k == 1)} ({pct(sum(1 for k in rv if k == 1), len(rv))}), >=4 {sum(1 for k in rv if k >= 4)}"
    )
    rows = []
    for (repo, num), d in sorted(prs.items()):
        ks = [(c["createdAt"], kind(c["body"])) for c in d["comments"]]
        ver = [t for t, k in ks if k == "verdict"]
        fixc = [t for t, k in ks if k == "fix"]
        commits = [
            c
            for c in d["commits"]
            if len(c.get("parents") or [1]) == 1 and not c["messageHeadline"].startswith("Merge ")
        ]
        after = [c for c in commits if ver and c["committedDate"] > ver[0]]
        rows.append(
            dict(
                repo=repo,
                pr=num,
                verdicts=len(ver),
                fixes=len(fixc),
                commits=len(commits),
                commits_after_first_review=len(after),
            )
        )
    jdump(rows, f"{cache}/comment_rounds.json")
    for repo in [*REPOS, "all"]:
        rr = [r for r in rows if (repo == "all" or r["repo"] == repo) and r["verdicts"]]
        tot = [r for r in rows if repo == "all" or r["repo"] == repo]
        if not rr:
            print(f"{repo}: no PR with a review comment ({len(tot)} merged)")
            continue
        v = sorted(r["verdicts"] for r in rr)
        print(
            f"{repo}: {len(rr)}/{len(tot)} merged PRs with review comments; verdict comments mean {sum(v) / len(v):.2f}, "
            f"median {v[len(v) // 2]}, one {pct(v.count(1), len(v))}, >=4 {sum(1 for x in v if x >= 4)}; "
            f"commits after first review mean {sum(r['commits_after_first_review'] for r in rr) / len(rr):.1f}"
        )


FLAGS = {
    "pre-existing": r"pre-?existing|already on main|on main (?:already|since|from)|not (?:introduced|caused) by (?:this|the|#)"
    r"|predates|was always true|not new\b|baseline follow-?up|exists? on main|main (?:also|already) "
    r"|same (?:gap|bug|problem) (?:is |exists )?on main|(?:also|already) (?:fails|true|broken) on main",
    "follow-up / out of scope": r"out of (?:this PR'?s? )?scope|outside (?:the|this|its) (?:PR|diff|scope|Owns)"
    r"|beyond (?:this|the) PR|follow-?up|separate issue|file (?:it|one|an issue)|after (?:the )?merge"
    r"|can wait|not (?:for|in) this PR|later issue|belongs (?:to|on|in) #",
    "PR body / process": r"PR body|the body (?:says|claims|lists)|evidence (?:section|line)|PR description|commit message",
    "gate-enforced (ruff/format/cite-check/budget)": r"\bruff\b|\bformat(?:ting|ter)?\b|\blint(?:er)?\b(?! in check)"
    r"|cite-?check|typos|word budget|ratchet|coverage floor",
    "regression / fix-introduced": r"regression|regress|introduced (?:in|by) (?:round|the fix|this round|r\d)"
    r"|new in this round|reopen|broke|the fix (?:adds|introduces|opens|breaks)",
    "same class / another instance": r"same (?:kind|class|bug|gap|pattern|problem) as|another (?:instance|case|route|path|way)"
    r"|still (?:get|gets|pass|passes|leak|leaks|verif)|other (?:call ?sites|routes|paths)"
    r"|every (?:other )?(?:caller|route|instance)|partly fixed|only partly",
    "nit / wording": r"\bnit\b|wording|typo|optional|cosmetic|docstring|comment says",
}


def finding_items(report):
    m = FIND_HEAD.search(report)
    tail = report[m.end() :] if m else report
    parts = re.split(r"(?m)^(?:\*\*)?\d+[.)]\s", tail)
    return [x for x in parts[1:] if x.strip()] or ([tail] if m else [])


def classify(cache):
    """Keyword flags on each finding item of each non-pass round (an item may carry several flags)."""
    agents = jload(f"{cache}/rounds.json")
    per = defaultdict(list)
    for a in agents:
        for s in a["segs"]:
            for p in s["prs"]:
                per[(tuple(p), a["role"])].append(s)
    by_round = defaultdict(Counter)
    n_items = Counter()
    rep_flags = defaultdict(Counter)
    samples = defaultdict(list)
    for v in per.values():
        v.sort(key=lambda s: s["start"])
        for i, s in enumerate(v):
            if s["verdict"] == "pass":
                continue
            r = "1" if i == 0 else "2+"
            items = finding_items(s["report"])
            n_items[r] += len(items)
            seen = set()
            for it in items:
                for f, rx in FLAGS.items():
                    if re.search(rx, it, re.I):
                        by_round[r][f] += 1
                        seen.add(f)
                        samples[f].append(it[:220].replace("\n", " "))
            for f in seen:
                rep_flags[r][f] += 1
            rep_flags[r]["_reports"] += 1
    for r in ("1", "2+"):
        print(f"round {r}: {rep_flags[r]['_reports']} non-pass reports, {n_items[r]} finding items")
        for f in FLAGS:
            print(
                f"  {f}: items {by_round[r][f]} ({pct(by_round[r][f], n_items[r])}), "
                f"reports {rep_flags[r][f]} ({pct(rep_flags[r][f], rep_flags[r]['_reports'])})"
            )
    jdump(samples, f"{cache}/flag_samples.json")
    delta_rx = (
        r"(?i)\bdelta\b|only (?:the )?(?:changes|diff|delta)|(?:diff|changes|changed) since|unchanged since"
        r"|re-?(?:checked|read) only|nothing (?:else )?changed since|this round only|the round-?\d+ diff"
    )
    later = [s for v in per.values() for s in v[1:]]
    dl = sum(1 for s in later if re.search(delta_rx, s["report"][:900]))
    dp = sum(1 for s in later if re.search(delta_rx, s["prompt"][:900]))
    print(
        f"later rounds whose report says it reviewed only the delta: {dl}/{len(later)} ({pct(dl, len(later))}); "
        f"whose prompt asks for the delta: {dp}/{len(later)} ({pct(dp, len(later))})"
    )
    minor_rx = (
        r"(?i)no blockers?|nothing blocks|none of (?:them|these|the findings|the problems) (?:blocks?|lets)"
        r"|neither (?:one )?blocks|non-?blocking|not a blocker|all (?:the )?fixes are small|one-line|small fix"
        r"|after (?:one |two |the )?small fix|nothing here can harm|no user (?:is|can be) harmed"
    )
    minor_next = []
    n_minor = n_fix = 0
    for v in per.values():
        for i, s in enumerate(v):
            if s["verdict"] != "fix":
                continue
            n_fix += 1
            if re.search(minor_rx, s["report"][:500]):
                n_minor += 1
                if i + 1 < len(v):
                    minor_next.append(v[i + 1])
    print(
        f"non-pass rounds whose verdict line says the fixes are small or non-blocking: {n_minor}/{n_fix} "
        f"({pct(n_minor, n_fix)}); a further round followed {len(minor_next)} of them, which passed "
        f"{pct(sum(1 for s in minor_next if s['verdict'] == 'pass'), len(minor_next))}, cost ${sum(s['cost'] for s in minor_next):.0f}"
    )
    t_d = [s for s in later if re.search(delta_rx, s["report"][:900])]
    t_f = [s for s in later if not re.search(delta_rx, s["report"][:900])]
    for name, grp in (("delta", t_d), ("not delta", t_f)):
        if grp:
            print(
                f"  {name}: mean ${sum(s['cost'] for s in grp) / len(grp):.2f}, "
                f"{sum(s['tools'] for s in grp) / len(grp):.1f} tool calls, pass {pct(sum(1 for s in grp if s['verdict'] == 'pass'), len(grp))}"
            )


def series(cache):
    per = defaultdict(list)
    for a in jload(f"{cache}/rounds.json"):
        for s in a["segs"]:
            for p in s["prs"]:
                per[(tuple(p), a["role"])].append(s)
    for v in per.values():
        v.sort(key=lambda s: s["start"])
    return per


def sample(cache):
    """Print the material that was hand-coded: 60 random round-1 finding items (seed 11), then every later round
    whose verdict was not a pass."""
    import random

    per = series(cache)
    items = [(k[1], it) for k, v in per.items() if v[0]["verdict"] != "pass" for it in finding_items(v[0]["report"])]
    random.seed(11)
    for i, (role, it) in enumerate(random.sample(items, 60)):
        print(f"[{i + 1}] {role}: {it[:330]!r}")
    k = 0
    for key, v in sorted(per.items()):
        for i, s in enumerate(v):
            if i and s["verdict"] != "pass":
                k += 1
                print(f"### {k} {key[0][0]}#{key[0][1]} {key[1]} round {i + 1}/{len(v)}\n{s['report'][:1400]}\n")


# Hand codes (one coder) of the material `sample` prints; the per-item codes are listed in r2-review-convergence.md.
ROUND1_CODES = {
    "introduced, substantive": 27,
    "introduced, minor": 22,
    "PR body / process": 5,
    "pre-existing": 2,
    "follow-up / future scope": 4,
    "enforced by dev check": 0,
}
LATER_CODES = {
    "introduced by the previous fix": 22,
    "missed earlier, same class": 13,
    "missed earlier, other": 6,
    "minor residue only": 8,
    "new input (owner or lead)": 2,
    "pre-existing on main": 0,
}


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (c - h, c + h)


def codes():
    for name, cs in (("round-1 finding items", ROUND1_CODES), ("later non-pass rounds", LATER_CODES)):
        n = sum(cs.values())
        print(f"{name} (n={n}):")
        for k, v in cs.items():
            lo, hi = wilson(v, n)
            print(f"  {k}: {v} ({100 * v / n:.0f} %, 95 % CI {100 * lo:.0f}-{100 * hi:.0f} %)")
    nb = n1 = sum(ROUND1_CODES.values())
    nb = n1 - ROUND1_CODES["introduced, substantive"]
    lo, hi = wilson(nb, n1)
    print(f"round-1 items that need not block: {nb}/{n1} ({100 * nb / n1:.0f} %, CI {100 * lo:.0f}-{100 * hi:.0f} %)")
    for label, k, n in (
        ("converged after one review-and-fix round", 135, 169),
        ("security only", 44, 67),
        ("reviewed PRs with a traced post-merge defect, confident", 9, 141),
        ("reviewed PRs with a traced post-merge defect, all candidates", 13, 141),
    ):
        lo, hi = wilson(k, n)
        print(f"{label}: {k}/{n} ({100 * k / n:.0f} %, CI {100 * lo:.0f}-{100 * hi:.0f} %)")


def lead_overhead(project):
    """Lead API calls that spawn or resume a review agent, or post a review verdict, and what each one cost."""
    review_ids = set()
    for meta in glob.glob(f"{project}/*/subagents/*.meta.json"):
        if jload(meta).get("agentType") in REVIEW_TYPES:
            review_ids.add(os.path.basename(meta).removeprefix("agent-").removesuffix(".meta.json"))
    calls, cost, seen = 0, 0.0, set()
    for f in glob.glob(f"{project}/*.jsonl"):
        for line in lines(f):
            d = json.loads(line)
            if d.get("type") != "assistant" or not isinstance(d["message"]["content"], list):
                continue
            m = d["message"]
            hit = False
            for b in m["content"]:
                if b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                cmd = inp.get("command", "")
                hit = hit or (
                    (b["name"] == "Agent" and inp.get("subagent_type") in REVIEW_TYPES)
                    or (b["name"] == "SendMessage" and inp.get("to") in review_ids)
                    or (
                        b["name"] == "Bash"
                        and "gh pr comment" in cmd
                        and kind(cmd.split("--body", 1)[-1].lstrip(" '\"=")) == "verdict"
                    )
                )
            if hit and m.get("id") not in seen:
                seen.add(m.get("id"))
                u = m.get("usage") or {}
                calls += 1
                cost += (
                    u.get("input_tokens", 0) * PRICE["inp"]
                    + u.get("cache_read_input_tokens", 0) * PRICE["cr"]
                    + u.get("cache_creation_input_tokens", 0) * PRICE["cw"]
                ) / 1e6
    print(
        f"lead calls that spawn/resume a reviewer or post a verdict: {calls}, input-side cost ${cost:.0f} "
        f"(${cost / max(calls, 1):.2f} per call)"
    )


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        fetch(sys.argv[2])
    elif cmd == "rounds":
        rounds(sys.argv[2], sys.argv[3])
    elif cmd == "codes":
        codes()
    elif cmd == "sample":
        sample(sys.argv[2])
    elif cmd == "lead":
        lead_overhead(sys.argv[2])
    elif cmd == "classify":
        classify(sys.argv[2])
    elif cmd == "comments":
        comments(sys.argv[2])
    elif cmd == "misses":
        misses(sys.argv[2])
