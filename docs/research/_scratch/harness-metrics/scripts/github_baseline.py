"""GitHub half of the #502 baseline: per merged PR in a window, the fix-loop send-backs (`devkit:fix-loop` markers in
PR comments, devkit/board.py FIX_MARK), the review comments and the hours from open to merge. Prints aggregates only.

Usage: python -I github_baseline.py <owner/name> <since YYYY-MM-DD> <until YYYY-MM-DD>   (needs `gh`, read-only)
"""

import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime

FIX_MARK = re.compile(r"<!-- devkit:fix-loop cycle=(\d+) head=(\S*) key=(\S*) outcome=(\S+) -->")


def hours(a: str, b: str) -> float:
    f = "%Y-%m-%dT%H:%M:%SZ"
    return (datetime.strptime(b, f) - datetime.strptime(a, f)).total_seconds() / 3600


def main() -> None:
    repo, since, until = sys.argv[1:4]
    out = subprocess.run(
        ["gh", "pr", "list", "-R", repo, "--state", "merged", "--limit", "500", "--search",
         f"merged:{since}..{until}", "--json", "number,createdAt,mergedAt,comments,reviews,headRefName"],
        capture_output=True, text=True, check=True, timeout=180,
    ).stdout  # fmt: skip
    prs = json.loads(out)
    sent, heads, outcomes, cycles = 0, [], Counter(), Counter()
    reviews = []
    for p in prs:
        marks = [m for c in p["comments"] for m in FIX_MARK.finditer(c["body"])]
        if marks:
            sent += 1
            heads.append(len({m[2] for m in marks}))
            cycles[max(int(m[1]) for m in marks)] += 1
            outcomes.update(m[4] for m in marks)
        reviews.append(len(p["reviews"]))
    ttm = sorted(hours(p["createdAt"], p["mergedAt"]) for p in prs)
    n = len(prs)
    print(f"merged PRs {since}..{until}: {n}")
    print(f"sent back by integrate at least once: {sent} ({sent / n:.0%}); fix-loop comments: {sum(outcomes.values())}")
    print(f"  distinct heads sent back per sent-back PR: {dict(sorted(Counter(heads).items()))}")
    print(f"  highest fix cycle per sent-back PR: {dict(sorted(cycles.items()))}")
    print(f"  outcomes: {dict(outcomes.most_common())}")
    print(f"GitHub reviews per PR: {dict(sorted(Counter(reviews).items()))}")
    q = statistics.quantiles(ttm, n=4)
    print(f"hours open -> merged: median {statistics.median(ttm):.1f}, IQR {q[0]:.1f}-{q[2]:.1f}, max {ttm[-1]:.1f}")


if __name__ == "__main__":
    main()
