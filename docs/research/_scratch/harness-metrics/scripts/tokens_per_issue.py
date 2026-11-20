"""Tokens per merged issue from `dev agent-stats --cost --json` (#502): totals, the attributed issues' median and IQR.

Usage: python -I tokens_per_issue.py <cost.json>
"""

import json
import statistics
import sys


def tok(x: dict) -> float:
    return sum(x["tokens"].values())


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as f:
        c = json.load(f)
    total = sum(tok(v) for v in c["types"].values())
    issues = sorted(tok(v) for v in c["issues"].values())
    usd = sorted(v["usd"]["total"] for v in c["issues"].values())
    q, qu = statistics.quantiles(issues, n=4), statistics.quantiles(usd, n=4)
    n = c["merged_issues"]
    print(
        f"all tokens {total / 1e6:.1f}M, ${c['total']:.2f}; {n} merged issues: {total / n / 1e6:.2f}M, ${c['total'] / n:.2f} each"
    )
    print(
        f"attributed issues {len(issues)}: median {statistics.median(issues) / 1e6:.2f}M tokens"
        f" (IQR {q[0] / 1e6:.2f}-{q[2] / 1e6:.2f}M), median ${statistics.median(usd):.2f} (IQR {qu[0]:.2f}-{qu[2]:.2f})"
    )
    print(f"shared (lead, unnamed) {tok(c['shared']) / 1e6:.1f}M tokens, ${c['shared']['usd']['total']:.2f}")
    out = sum(v["tokens"]["output"] for v in c["types"].values())
    read = sum(v["tokens"]["read"] for v in c["types"].values())
    print(f"output {out / 1e6:.2f}M = {out / total:.2%} of all tokens; cache reads {read / total:.1%}")


if __name__ == "__main__":
    main()
