"""Combine the two replay outputs into the numbers r1-conflicts-generation.md quotes (#346, sub-question d).

Usage:
    python3 conflicts_summary.py conflicts-agent-harness.json conflicts-repo-E.json
"""

import json
import sys


def main() -> None:
    runs = []
    for path in sys.argv[1:]:
        with open(path) as f:
            runs.append(json.load(f))
    tot = lambda k: sum(r[k] for r in runs)  # noqa: E731
    out = {
        k: tot(k)
        for k in (
            "merged_prs",
            "pairs_judged",
            "conflicting_pairs",
            "pairs_both_prs_open",
            "conflicting_pairs_both_prs_open",
            "prs_whose_handback_head_conflicts_with_main_at_merge",
            "prs_sent_back_by_integrate",
            "sent_back_with_a_conflicting_pair",
        )
    }
    out["conflicting_pair_rate"] = round(out["conflicting_pairs"] / out["pairs_judged"], 3)
    out["conflicting_pr_rate"] = round(
        out["prs_whose_handback_head_conflicts_with_main_at_merge"] / out["merged_prs"], 3
    )
    for level in ("predictors", "pr_level"):
        comb = {}
        for name in runs[0][level]:
            c = {k: sum(r[level][name][k] for r in runs) for k in ("tp", "fp", "fn", "tn")}
            c["precision"] = round(c["tp"] / (c["tp"] + c["fp"]), 3) if c["tp"] + c["fp"] else None
            c["recall"] = round(c["tp"] / (c["tp"] + c["fn"]), 3) if c["tp"] + c["fn"] else None
            comb[name] = c
        out[level] = comb
    conflicts = [p for r in runs for p in r["pairs"] if p["conflict"]]
    on_main = [p for p in conflicts if p["b_opened_h_before_a_merged"] <= 0]  # A landed before B's dev pr
    both_open = [p for p in conflicts if p["b_opened_h_before_a_merged"] > 0]
    out["conflicts_a_on_main_at_b_dev_pr"] = len(on_main)
    out["of_those_sent_back"] = sum(p["b_sent_back"] for p in on_main)
    out["conflicts_both_open"] = len(both_open)
    out["of_those_sent_back_both_open"] = sum(p["b_sent_back"] for p in both_open)
    out["owns_predicted_sent_back"] = sum(p["b_sent_back"] and p["owns"] for p in conflicts)
    early = [
        round((p["earliest_flag_h_before_a_merged"] - p["b_opened_h_before_a_merged"]) * 60, 1)
        for p in both_open
        if p["earliest_flag_h_before_a_merged"] is not None
    ]
    out["both_open_flag_minutes_before_b_dev_pr"] = sorted(early)
    gap = sorted(round(p["b_opened_h_before_a_merged"] * 60, 1) for p in both_open)
    out["both_open_minutes_from_b_dev_pr_to_a_merge"] = gap
    out["minutes_from_a_merge_to_b_merge_conflicting"] = sorted(
        round(p["b_merged_h_after_a_merged"] * 60) for p in conflicts
    )
    out["send_back_minutes_after_a_merged"] = sorted(
        m for p in conflicts if p["b_sent_back"] for m in p.get("send_back_min_after_a_merged", [])[:1]
    )
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
