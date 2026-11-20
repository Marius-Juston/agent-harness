"""Transcript tokens and $ per (agent type, model) in a window (#502), through devkit.stats' one reader, plus the share
of merged issues with any run on this machine and the hours from an issue's first run to its PR's merge.

Usage: uv run python docs/research/_scratch/harness-metrics/scripts/by_model.py <since> <until>
"""

import statistics
import sys
from collections import defaultdict
from datetime import datetime

from devkit import config, stats
from devkit.paths import main_checkout


def main() -> None:
    since, until = sys.argv[1:3]
    runs = stats.load_runs(stats.project_dirs(), since, until)
    merged = stats.merged_prs(since, until)
    prices = config.get().prices
    hidden = stats.hidden_per_turn(runs)
    rows: dict[tuple[str, str], dict] = defaultdict(lambda: {"turns": 0, "tok": 0, "out": 0, "usd": 0.0})
    for r in runs:
        for t in r.turns:
            toks, usd = stats._turn(t, hidden, prices)
            row = rows[(r.kind, t["model"])]
            row["turns"] += 1
            row["tok"] += sum(toks.values())
            row["out"] += toks["output"]
            row["usd"] += sum(usd.values()) if usd else 0.0
    print(f"{'agent type':<30}{'model':<22}{'turns':>7}{'tokens':>12}{'output':>10}{'$':>9}")
    for (kind, model), x in sorted(rows.items(), key=lambda kv: -kv[1]["usd"]):
        print(f"{kind[:29]:<30}{model[:21]:<22}{x['turns']:>7}{int(x['tok']):>12,}{int(x['out']):>10,}{x['usd']:>9.2f}")
    by_pr = stats.pr_issues(merged)
    name = main_checkout().name
    first: dict[str, str] = {}
    for r in runs:
        tgt = stats._target(r, by_pr, name)
        if tgt.startswith("#") and r.start and (tgt not in first or r.start < first[tgt]):
            first[tgt] = r.start
    issues = set().union(*by_pr.values()) if by_pr else set()
    print(f"\nmerged PRs {len(merged)}, issues {len(issues)}, issues with a run on this machine {len(first)}")
    merged_at = {f"#{i}": p["mergedAt"] for p in merged for i in by_pr[p["number"]]}
    f = "%Y-%m-%dT%H:%M:%S"
    hrs = sorted(
        (datetime.strptime(merged_at[k][:19], f) - datetime.strptime(v[:19], f)).total_seconds() / 3600
        for k, v in first.items()
        if k in merged_at
    )
    if len(hrs) > 1:
        q = statistics.quantiles(hrs, n=4)
        print(f"hours first run -> merge: n {len(hrs)}, median {statistics.median(hrs):.1f}, IQR {q[0]:.1f}-{q[2]:.1f}")


if __name__ == "__main__":
    main()
