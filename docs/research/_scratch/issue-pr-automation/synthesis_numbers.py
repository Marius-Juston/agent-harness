"""Arithmetic behind the reconciled numbers in docs/research/issue-pr-automation.md (#346 synthesis).

Usage (from this folder): python3 synthesis_numbers.py
Reads report-bookkeeping.json (bookkeeping.py), conflicts-summary.json (conflicts_summary.py) and the retry classes
printed by retries134.py (copied below, because that script needs the owner's transcript cache).
"""

import json
import statistics
from pathlib import Path

r = json.loads(Path("report-bookkeeping.json").read_text())
c = json.loads(Path("conflicts-summary.json").read_text())
split = r["integrate_loop_turn_split"]
turn_int = split["integrate call"]["usd"] / split["integrate call"]["turns"]
spend = r["overview_usd_all"]
print(f"window spend ${spend:,.0f}; integrate-call turn ${turn_int:.3f}")

# Break-even of r1-against: $19.3 build cost paid back in 3 windows = $6.4 per window; stopgaps in 2 = $9.6.
BREAK, STOPGAP = 19.3 / 3, 19.3 / 2
print(f"break-even ${BREAK:.2f}/window (stopgap ${STOPGAP:.2f}); = {BREAK / turn_int:.0f} integrate turns")

# 1. integrate retries, retries134.py classes (same-session count, as command-sequences.md detector G)
R134 = {
    "same head+feedback+flags": 34,
    "same head+feedback, new flag": 27,
    "new head": 37,
    "new feedback": 16,
    "no PR number": 26,
    "PR not found": 1,
}
total = sum(R134.values())
pending = 15  # of the 34, the try before was a pending-check refusal; #322 (v0.1.11) now waits instead
lo, hi = R134["same head+feedback+flags"] - pending, R134["same head+feedback+flags"]
print(f"\nretries {total} (session count); cache hits {lo}-{hi}; saving ${lo * turn_int:.2f}-{hi * turn_int:.2f}")
print(
    f"  same head and feedback {R134['same head+feedback+flags'] + R134['same head+feedback, new flag']} of {total}"
    f" = {(R134['same head+feedback+flags'] + R134['same head+feedback, new flag']) / total:.0%}"
)
merged = r["integrate"]["prs_merged_in_these_tries"]
print(
    f"  lead integrate-loop turns per merged PR {split['integrate call']['turns'] / merged:.2f} (calls only), "
    f"{r['integrate']['lead_integrate_loop_turns'] / merged:.2f} (with check reads)"
)

# 2. review relay: the n-gram basis of r1-against against the per-PR basis of r1-transcripts
rr = r["review_relay_per_pr"]
print(f"\nreview relay n-gram basis: 10 x 3 x $0.077 = ${10 * 3 * 0.077:.2f}")
print(
    f"  per-PR basis: {rr.get('prs')} PRs; lead relay $ {r['categories']['lead']['2 review relay']['usd']:.1f} "
    f"over {r['categories']['lead']['2 review relay']['turns']} turns"
)

# 5. conflicts: avoided resumes at dev pr, and send-back delay a post-merge sweep removes
FIX_RESUME = 1.27  # harness-efficiency.md §4
print(
    f"\nconflicts: {c['conflicting_pairs']} pairs of {c['pairs_judged']}; PRs {c['prs_whose_handback_head_conflicts_with_main_at_merge']}"
    f" of {c['merged_prs']}; dev-pr check avoids up to {c['of_those_sent_back']} send-backs = ${c['of_those_sent_back'] * FIX_RESUME:.2f}"
)
print(
    f"  send-back delay after A merged: median {statistics.median(c['send_back_minutes_after_a_merged'])} min "
    f"(n={len(c['send_back_minutes_after_a_merged'])})"
)
o = c["predictors"]["owns"]
print(f"  conflicting pairs Owns did not predict: {o['fn']} (r1-against's build threshold: 5 per window)")

# 4. duplicates: base rate and what an alert at 48 % of filings means
base = 13 / 791
print(f"\nduplicate base rate 13/791 = {base:.1%}; alerts on 48% of filings -> {0.48 / base:.0f} alerts per duplicate")

# 3 / bookkeeping share
cat = r["categories"]
lead_any = cat["lead"]["ANY of 1-9 (no reviewer spawn, no x-rows)"]
print(
    f"\nlead bookkeeping {lead_any['turns']} turns ${lead_any['usd']:.0f}; "
    f"share of lead turns {lead_any['turns'] / r['overview']['lead']['turns']:.1%}"
)

# Bounds of the four filed changes together (§6): lower and upper per window, from §4's rows
LOWER = {"#347": 3.11, "#348": lo * turn_int, "#349": 0.75, "#350": 1.73}
UPPER = {"#347": 23.89, "#348": hi * turn_int, "#349": c["of_those_sent_back"] * FIX_RESUME, "#350": 8.37}
print(
    f"\nfiled four together: ${sum(LOWER.values()):.1f}-{sum(UPPER.values()):.1f} per window "
    f"({sum(UPPER.values()) / spend:.1%} of spend at the upper bound)"
)
