# Gaps after round 1 (lead, 2026-10-13)

## Findings
- Measured (r1-measured): 640 follow-ups filed during other work; 75% still open; closed ones closed fast (median
  3.1 h) and were normal-sized tickets (median 1,072 changed lines). Only ~3% were small with no rule in the way; the
  small ones were blocked by a rule (parallel ownership, research-only role, harness/owner rule, architect read-only).
  86% filed before the origin PR merged; 62% give no reason.
- Causes (r1-instruction-causes): only the tester has a fix-now path; builder Owns wording ("only when the issue needs
  it end to end"), hotspot -> lead who never builds, hand-back asks for follow-up numbers, no "fixed in passing" slot;
  weaken flagged a tightening (fixed since in #150); Anthropic scope guidance says "mention it", which the harness turns
  into an issue.
- Literature (r1-literature): no evidence that later is better; agent follow-ups always take the slow path (worker
  exits, ~42 tool calls of setup); tangled changes bound the rule; only superpowers and Google close the loop
  (triage before merge; self-assigned bug).

## Contradiction
The owner perceives frequent small deferrals; the data says small deferrals are ~3% and the larger problem is that
75% of follow-ups are never done and most give no reason. Both point to the same fix: a fix-now path plus a triage
step at integrate, and a reason on every filed follow-up.

## Stop
One round: the three answers agree on the rule; thresholds (~20 lines / 2 files) stay ASSUMED until re-measured with
scripts/measure_deferral.py in 2-4 weeks (file that as an experiment). Synthesis keeps code names and tracked scripts.
