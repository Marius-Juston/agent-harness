"""Can a transcript line's own `gitBranch` and `effort` replace the description heuristic and add effort (#502)?
Per agent type over a window: runs, the share of assistant lines carrying `gitBranch` of the `<issue>-<slug>` form and
`effort`, the effort values, and how often the branch's issue agrees with devkit.stats._target. Prints counts only.

Usage: uv run python docs/research/_scratch/harness-metrics/scripts/branch_attribution.py <since> <until>
"""

import re
import sys
from collections import Counter, defaultdict

from devkit import stats
from devkit.paths import main_checkout

ISSUE_BRANCH = re.compile(r"^(\d+)-")


def main() -> None:
    since, until = sys.argv[1:3]
    runs = stats.load_runs(stats.project_dirs(), since, until)
    by_pr = stats.pr_issues(stats.merged_prs(since, until))
    merged = set().union(*by_pr.values()) if by_pr else set()
    name = main_checkout().name
    rows: dict[str, Counter] = defaultdict(Counter)
    efforts: dict[str, Counter] = defaultdict(Counter)
    for r in runs:
        row = rows[r.kind]
        row["runs"] += 1
        branches: Counter = Counter()
        for rec in stats._records(r.path):
            if rec.get("type") != "assistant":
                continue
            row["lines"] += 1
            m = ISSUE_BRANCH.match(rec.get("gitBranch") or "")
            row["issue_branch_lines"] += bool(m)
            if m:
                branches[int(m[1])] += 1
            if "effort" in rec:
                row["effort_lines"] += 1
                efforts[r.kind][str(rec["effort"])] += 1
        target = stats._target(r, by_pr, name)
        branch = branches.most_common(1)[0][0] if branches else None
        row["branch_issue"] += branch is not None
        row["branch_issue_merged"] += branch in merged
        row["heuristic_issue"] += target.startswith("#")
        row["both"] += branch is not None and target.startswith("#")
        row["agree"] += branch is not None and target == f"#{branch}"
        row["branch_only_merged"] += branch in merged and not target.startswith("#")
    cols = ("runs", "lines", "issue_branch_lines", "effort_lines", "branch_issue", "branch_issue_merged",
            "heuristic_issue", "both", "agree", "branch_only_merged")  # fmt: skip
    print("agent type".ljust(26) + "".join(c[:12].rjust(13) for c in cols))
    total: Counter = Counter()
    for kind, row in sorted(rows.items(), key=lambda kv: -kv[1]["runs"]):
        total.update(row)
        print(kind[:25].ljust(26) + "".join(str(row[c]).rjust(13) for c in cols))
    print("all".ljust(26) + "".join(str(total[c]).rjust(13) for c in cols))
    for kind, c in efforts.items():
        print(f"effort {kind}: {dict(c)}")


if __name__ == "__main__":
    main()
