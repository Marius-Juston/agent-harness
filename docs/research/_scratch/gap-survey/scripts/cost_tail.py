"""How much of the attributed spend sits in the tail of issues? (#498: does a per-dispatch budget pay?)

Reads the JSON of `uv run dev agent-stats --cost --since <date> --json` and prints, per issue, the modelled $,
then the median, p90, max, the share of issue-attributed spend in issues above 3x the median, and the spend by
agent type. Usage: uv run python docs/research/_scratch/gap-survey/scripts/cost_tail.py <cost.json>
"""

import json
import statistics
import sys


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    per = {k: v["usd"]["total"] for k, v in d["issues"].items()}
    runs = {k: v["runs"] for k, v in d["issues"].items()}
    xs = sorted(per.values())
    med = statistics.median(xs)
    p90 = xs[int(0.9 * (len(xs) - 1))]
    attributed = sum(xs)
    tail = [k for k, v in per.items() if v > 3 * med]
    print(
        f"total ${d['total']:.2f}; merged issues {d['merged_issues']}; per merged issue ${d['total'] / d['merged_issues']:.2f}"
    )
    print(f"issues with runs: {len(xs)}; attributed ${attributed:.2f} ({attributed / d['total']:.0%} of total)")
    print(f"per issue: median ${med:.2f}, p90 ${p90:.2f}, max ${xs[-1]:.2f}")
    tail_usd = sum(per[k] for k in tail)
    print(
        f"issues above 3x median: {len(tail)} ({len(tail) / len(xs):.0%}), ${tail_usd:.2f} = {tail_usd / attributed:.0%} of attributed"
    )
    for k in sorted(tail, key=per.get, reverse=True):
        print(f"  {k}: ${per[k]:.2f} over {runs[k]} runs")
    print(
        "by type: "
        + ", ".join(
            f"{t} {v['runs']} runs ${v['usd']['total']:.2f} ({v['usd']['total'] / d['total']:.0%})"
            for t, v in sorted(d["types"].items(), key=lambda kv: -kv[1]["usd"]["total"])
        )
    )


if __name__ == "__main__":
    main()
