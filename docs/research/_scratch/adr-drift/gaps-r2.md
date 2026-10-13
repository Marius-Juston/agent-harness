# Gaps after round 2 (lead, 2026-10-13) and stop decision

Round 2: [r2-mechanisms](r2-mechanisms.md), [r2-replay](r2-replay.md).

## What changed the recommendation
- Owner question "better than ADRs?": yes. Minimum set: tests/hooks (already hold 10 of 23 decisions), one decision
  table in .devkit/ with rule, why-link, enforced-by marker and status plus a ~30-line check (rendered index linked,
  not loaded), a board-lint warning for decisions made only in comments, and a hook denying direct curl/WebFetch to
  the literature APIs. No ADR directory, no path-scoped rules (0 of 5 reversals prevented; not loaded on Grep or
  scripted edits; the C1 edit had no rule loaded).
- Replay over history: gate only checks with zero false positives and no rules fitted on the history (2 deleted Owns
  paths); generate rather than check (3 DONE WHEN ticks, 4 integrate writes the floor, 6 HANDOFF live state); warn
  until a held-out replay (1a backticked `uv run dev`, whose syntax rules were fitted on this history; 1b; 7 markers:
  put markers on the reason); sweep (8 links, with a fixed lychee config; agent-harness has no sweep). Amended after
  the audit of PR #237, which also added the deny-hook replay (r3_host_replay.py) and the integrate-time Owns rule.
- Every recommended check rests on 0-1 real events; precision is in-sample. A held-out replay over the next merges
  is required before any warning becomes a gate.

## Stop
Stop after round 2: both open questions (ADRs or not; which drift checks and at what level) now have a measured
answer, and a third round would only add more in-sample history. Remaining uncertainty goes to follow-ups.
