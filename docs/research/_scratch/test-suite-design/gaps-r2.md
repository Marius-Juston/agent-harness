# Gaps after round 2 (lead, 2026-10-12)

Round 2 settled the three open gaps:
1. Mutation: cosmic-ray filters to changed lines (cr-filter-git); mutmut 3.x only per function. A full diff run is 3-5
   min with ~100+ survivors, so per-PR it is advisory (one mutant per changed line, capped ~40), whole-package mutmut is
   scheduled. Replay of new tests on the base is cheap (0.3-8 s/PR) and a useful gate warning. weaken.py caught 3 of 11
   weakening kinds; filed as its own issue (r2-mutation-diff).
2. E2E: no study links suite composition to escaped defects (third search); escapes cluster at boundaries, so each
   project type needs at least one test across each boundary; per-type minimum E2E sets; evals scheduled with a
   suite-level threshold (r2-e2e-by-type).
3. Audit: nine checks with tier thresholds, trigger (30/14/7 days or 25/50 merged PRs or 25% growth), issue output;
   defects filed as #117, #120, #121 (r2-suite-health).

## Stop
Stop after round 2: the remaining gaps (thresholds are judgement; no outcome data for quarantine caps or audit cadence;
abstract-only sources) are not closed by more search. Synthesis must keep VERIFIED claims on tracked scripts (move what
the numbers rest on from gitignored .scratch/ into the scratch folder's scripts/), keep code names, reconcile the
trigger with #111's (docs/research/architecture-consolidation.md on PR #126: first-parent commits beat merged-PR counts
and days at agent pace), and extend rather than repeat test-floors-and-coverage.md.
