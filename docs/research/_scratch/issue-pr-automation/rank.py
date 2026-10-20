"""Modelled savings per bookkeeping candidate (#346 r1), recomputed from bookkeeping.py's report.

Usage: python3 rank.py report-bookkeeping.json
Every saving is in modelled $ over the window (Opus 5.5 list prices, sequences.py cost model). "lower" assumes the
replacement removes only what is certainly mechanical; "upper" assumes it removes every turn it could.
"""

import json
import sys

OUT = 0.443 * 20.0 / 1e6  # $ per character the model writes (0.443 tokens per char, $20 per M output tokens)

with open(sys.argv[1]) as fh:
    r = json.load(fh)
lead = r["categories"]["lead"]
allc = r["categories"]["all"]
rr = r["review_relay_per_pr"]
it = r["integrate"]
split = r["integrate_loop_turn_split"]
turn_int = split["integrate call"]["usd"] / split["integrate call"]["turns"]
lead_turn = lead["ANY of 1-9 (no reviewer spawn, no x-rows)"]["usd_per_turn"]
filing_turn = allc["4 filing/upkeep of issues"]["usd_per_turn"]

rows = []

# 1 integrate --ready: lower = retries where nothing changed and checks were not pending (#322 already waits);
#   upper = one integrate turn per burst instead of one per PR
nothing = it["retry_what_changed"]["nothing changed (same head, same flags)"]
pending = it["outcome_before_a_retry_where_nothing_changed"].get("refused: pending check", 0)
bursts = r["integrate_bursts"]["bursts"]
rows.append(
    (
        "1 integrate --ready queue",
        (nothing - pending) * turn_int,
        (split["integrate call"]["turns"] - bursts) * turn_int,
    )
)

# 2 reviewer posts its own GitHub review: lower = the output tokens the lead spends copying reviews and findings;
#   upper = posting turns + findings messages cut to a 200-char pointer + reviewer hand-backs cut to 300 chars
copied = rr["lead_posted_review_chars"] + rr["relay_message_chars"] - 200 * rr["findings_messages_to_author"]
hb = r["handbacks_to_lead"]["reviewer"]["carry_usd_above_300_chars"]
save_key = next(k for k in rr if k.startswith("save_upper_usd"))
rows.append(("2 dev review post (structured review)", copied * OUT, rr[save_key] + hb))

# 3 local scheduler for board event + usage-poll: the lead's own upkeep runs (sync/list/next/lint/usage-poll)
rows.append(("3 local scheduler", 0.0, lead["3 board upkeep runs"]["usd"]))

# 4 duplicate check in board new: items later closed as duplicate/superseded (filing turn + lead close turn), plus
#   the manual issue searches before a board new that the check would replace
fate = r["board_new"]["fate"]
dups = sum(v for k, v in fate.items() if "duplicate" in k)
searches = sum(r["board_new_preceded_by_issue_search"].values())
rows.append(
    (
        "4 related/duplicate check in board new",
        dups * filing_turn,
        dups * (filing_turn + lead_turn) + searches * filing_turn,
    )
)

# 5 conflict pre-check: lead conflict relays and integrate conflict outcomes (lower); plus every worker call that hit
#   a conflict (upper)
c5 = lead["5 conflict relay"]["usd"]
rows.append(
    (
        "5 merge-tree conflict pre-check",
        it["outcomes_all"].get("conflict", 0) * turn_int,
        c5 + allc["5 conflict seen in output"]["usd"] + it["outcomes_all"].get("conflict", 0) * turn_int,
    )
)

# 6 generated PR body: checkbox and gate lines of heredoc summaries (lower), the whole summary text (upper)
comp = r["pr_summary_composition_chars"]
gen = sum(v.get("checkbox lines", 0) + v.get("gate/test result lines", 0) for v in comp.values())
tot = sum(v.get("all", 0) for v in comp.values())
rows.append(("6 generated PR body", gen * OUT, tot * OUT))

# 7 HANDOFF skeleton: the lead's HANDOFF/SESSION writes
h = r["handoff"].get("lead", {"usd": 0.0})
rows.append(("7 generated HANDOFF skeleton", 0.0, h["usd"]))

# 8 changelog with git-cliff: every CHANGELOG.md write
rows.append(("8 git-cliff changelog", 0.0, sum(v["usd"] for v in r["changelog"].values())))

# 9 board claim --worktree: lead worktree adds not already chained with the claim
wc = r["worktree_claim"]
rows.append(
    (
        "9 board claim --worktree",
        0.0,
        (wc["worktree add (lead)"] - wc["... with board claim in the same call"]) * lead_turn,
    )
)

# new: owner decisions recorded by hand, issues closed by hand after a merge
rows.append(
    (
        "N1 record owner decisions (AskUserQuestion -> comment)",
        0.0,
        lead["x owner-decision record"]["usd"] + lead["x owner-decision relay"]["usd"],
    )
)
closes = r["board_new"]["lead_manual_closes"]
rows.append(("N2 close issues done by a merged PR", 0.0, closes.get("done by a merged PR or release", 0) * lead_turn))
rows.append(("N3 reviewer PR checkout worktrees", 0.0, r["categories"]["reviewer"]["9 worktree+claim/cleanup"]["usd"]))

total = r["overview_usd_all"]
print(f"window {r['window']}, modelled spend ${total:,.0f}; lead ${r['overview']['lead']['usd']:,.0f}")
print(f"mean $ per turn: integrate {turn_int:.3f}, lead bookkeeping {lead_turn:.3f}, filing {filing_turn:.3f}")
print(f"{'candidate':55s} {'lower $':>8s} {'upper $':>8s} {'upper % of spend':>17s}")
for name, lo, up in sorted(rows, key=lambda x: -x[2]):
    print(f"{name:55s} {lo:8.2f} {up:8.2f} {100 * up / total:16.2f}%")
