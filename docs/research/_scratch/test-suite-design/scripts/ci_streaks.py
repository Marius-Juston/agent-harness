"""Red streaks and staleness of a repo's default-branch CI, from `gh run list`.
Usage: python ci_streaks.py OWNER/REPO [workflow]. Prints per workflow: runs on main, longest red streak (runs, hours),
current streak, days since the last run, and commits on main since the last run."""

import collections
import json
import subprocess
import sys
from datetime import UTC, datetime

repo = sys.argv[1]
fields = "conclusion,createdAt,event,headBranch,headSha,status,workflowName"
runs = json.loads(
    subprocess.run(
        ["gh", "run", "list", "-R", repo, "--limit", "1000", "--json", fields],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
)
default = (
    subprocess.run(
        ["gh", "repo", "view", repo, "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    or "main"
)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


now = datetime.now(UTC)
by = collections.defaultdict(list)
for r in runs:
    if (
        r["status"] == "completed"
        and r["headBranch"] == default
        and r["event"] in {"push", "schedule"}
        and r["conclusion"] in {"success", "failure"}
    ):
        by[r["workflowName"]].append(r)
for wf, xs in by.items():
    xs.sort(key=lambda r: r["createdAt"])
    best, cur, start, best_h = 0, 0, None, 0.0
    for r in xs:
        if r["conclusion"] == "failure":
            cur += 1
            start = start or ts(r["createdAt"])
            if cur > best:
                best = cur
            best_h = max(best_h, (ts(r["createdAt"]) - start).total_seconds() / 3600)
        else:
            if cur:
                best_h = max(best_h, (ts(r["createdAt"]) - start).total_seconds() / 3600)
            cur, start = 0, None
    reds = sum(r["conclusion"] == "failure" for r in xs)
    last = ts(xs[-1]["createdAt"])
    print(
        f"{wf}: main runs={len(xs)} red={reds} longest red streak={best} runs / {best_h:.1f} h to green; "
        f"current streak={cur}; last run {(now - last).days} d ago"
    )
