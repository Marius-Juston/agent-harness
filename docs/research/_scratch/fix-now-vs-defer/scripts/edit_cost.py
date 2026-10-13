"""What does a small change cost an agent in tool calls (#149)?

For every merged PR of H, repo-E and repo-R whose net diff is 1 to 20 lines (bookkeeping excluded, as in
measure_deferral.py), find the subagent transcripts that worked in that issue's worktree (`<repo>-worktrees/<N>-...`)
and count tool calls: in all, before the first file edit (orientation), and from the first to the last file edit (the
editing span, the closest measure of what a fix costs in place). Edits are Edit/Write/MultiEdit/NotebookEdit calls;
edits made through Bash (sed -i, heredocs) are not seen, so the span is a lower bound. Uses the gh cache that
measure_deferral.py writes, so run that first. The same counts for PRs above 20 lines are printed as a baseline.
Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/edit_cost.py`.
"""

import collections
import glob
import importlib.util
import json
import re
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("measure_deferral", HERE / "measure_deferral.py")
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TOOL = re.compile(r'"type":"tool_use","id":"[^"]+","name":"([A-Za-z_]+)"')
WORKTREE = re.compile(r"-worktrees/(\d+)-")
MODEL = re.compile(r'"model":"(claude-[a-z0-9-]+)"')
LIMIT = 20


def net_lines(pr: dict) -> int:
    return sum(
        (f.get("additions") or 0) + (f.get("deletions") or 0)
        for f in pr.get("files") or []
        if not md.BOOKKEEPING.match(f["path"])
    )


def runs_by_issue(repos: dict) -> dict:
    """(repo code, issue number) -> list of per-transcript tool-call sequences, with the models seen."""
    out = collections.defaultdict(list)
    for f in glob.glob(str(md.PROJECTS / "*" / "*" / "subagents" / "*.jsonl")):
        text = Path(f).read_text(errors="ignore")
        hits = collections.Counter()
        for m in WORKTREE.finditer(text):  # the base path runs back to the previous quote or space
            start = max(text.rfind('"', 0, m.start()), text.rfind(" ", 0, m.start())) + 1
            hits[(text[start : m.start()], m.group(1))] += 1
        if not hits:
            continue
        (base, n), _ = hits.most_common(1)[0]
        code = md.code_for_path(base + "-worktrees", repos)
        if code is None:
            continue
        calls = []
        for line in text.splitlines():
            calls += TOOL.findall(line)
        out[(code, int(n))].append({"calls": calls, "models": collections.Counter(MODEL.findall(text))})
    return out


PR_OPEN = re.compile(r"dev pr \d+|gh pr create")


def branch_windows(branches: set[str]) -> dict:
    """branch -> tool calls from its first mention in a transcript (main or subagent) to the next PR-open command.

    Small PRs were mostly made by the lead in its main session, with no worker worktree, often with Bash heredocs.
    The window can hold other lead work, and it misses the reads just before the branch was named, so it is an
    estimate, not a bound.
    """
    out = {}
    files = [
        *glob.glob(str(md.PROJECTS / "*" / "*.jsonl")),
        *glob.glob(str(md.PROJECTS / "*" / "*" / "subagents" / "*.jsonl")),
    ]
    for f in files:
        text = Path(f).read_text(errors="ignore")
        if "edit_cost.py" in text:  # the session that wrote this script mentions every branch
            continue
        lines = text.splitlines()
        for b in branches - out.keys():
            idx = [i for i, line in enumerate(lines) if b in line]
            if not idx:
                continue
            start = idx[0]
            end = next((i for i in idx if PR_OPEN.search(lines[i]) and '"tool_use"' in lines[i]), None)
            if end is None:
                continue
            calls = [c for line in lines[start : end + 1] for c in TOOL.findall(line)]
            edits = sum(c in EDIT_TOOLS for c in calls)
            out[b] = {"total": len(calls), "edits": edits}
    return out


def costs(runs: list[dict]) -> dict | None:
    calls = [c for r in runs for c in r["calls"]]
    edits = [i for i, c in enumerate(calls) if c in EDIT_TOOLS]
    if not edits:
        return None
    return {"total": len(calls), "before": edits[0], "span": edits[-1] - edits[0] + 1, "edits": len(edits)}


def q(xs: list[int]) -> str:
    if not xs:
        return "n=0"
    qs = statistics.quantiles(xs, n=4) if len(xs) > 1 else [xs[0]] * 3
    return f"n={len(xs)} median={statistics.median(xs):.0f} IQR={qs[0]:.0f}-{qs[2]:.0f} max={max(xs)}"


def main() -> None:
    repos = md.load_repos()
    runs = runs_by_issue(repos)
    small, large, models = [], [], collections.Counter()
    for code in ("H", "repo-E", "repo-R"):
        path = md.OUT / "gh" / f"{code}.prs.json"
        if not path.exists():
            continue
        for pr in json.loads(path.read_text()):
            m = re.match(r"(\d+)-", pr.get("headRefName") or "")
            if not pr.get("mergedAt") or not m:
                continue
            c = costs(runs.get((code, int(m.group(1))), []))
            if c is None:
                continue
            for r in runs[(code, int(m.group(1)))]:
                models.update(r["models"])
            n = net_lines(pr)
            (small if 0 < n <= LIMIT else large).append({**c, "repo": code, "net": n})
    for name, g in ((f"merged PRs, 1-{LIMIT} net lines", small), (f"merged PRs, > {LIMIT} net lines", large)):
        print(f"## {name}: {len(g)} with a matched worker transcript")
        print(
            "  by repo: " + ", ".join(f"{k}={v}" for k, v in sorted(collections.Counter(r["repo"] for r in g).items()))
        )
        for key, label in (
            ("total", "tool calls per issue, all runs"),
            ("before", "before the first edit"),
            ("span", "first to last edit"),
        ):
            print(f"  {label}: {q([r[key] for r in g])}")
    smalls = []
    for code in ("H", "repo-E", "repo-R"):
        path = md.OUT / "gh" / f"{code}.prs.json"
        if path.exists():
            smalls += [
                pr["headRefName"]
                for pr in json.loads(path.read_text())
                if pr.get("mergedAt") and 0 < net_lines(pr) <= LIMIT and re.match(r"\d+-", pr["headRefName"])
            ]
    win = branch_windows(set(smalls))
    print(f"## merged PRs, 1-{LIMIT} net lines, any transcript: {len(win)} of {len(smalls)} branches found")
    print(
        f"  tool calls from the branch's first mention to the PR-open command : {q([w['total'] for w in win.values()])}"
    )
    print(f"  file-edit calls in that window: {q([w['edits'] for w in win.values()])}")
    total = sum(models.values()) or 1
    print("models in these transcripts: " + ", ".join(f"{k} {v / total:.1%}" for k, v in models.most_common(3)))


if __name__ == "__main__":
    main()
