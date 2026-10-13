# Gaps after round 1 (lead, 2026-10-12)

## Findings that shape the recommendation
- No test shape (pyramid/trophy/honeycomb) has outcome data; Google's book says 80/15/5, the 70/20/10 is a blog guess.
  Test size predicts flakiness (0.5% / 1.6% / 14% small/medium/large) (r1-theory-shapes).
- Tests per LOC fall as projects grow; suite size, not coverage, predicts fault detection; flake budget per test must
  shrink 10x per 10x tests (r1-empirical-by-size). 77% of production failures reproducible by a unit test, 92% of
  catastrophic ones from error handling (Yuan).
- Over-testing costs: ~36% of failing runs are maintenance (flaky or obsolete tests); coverage-based deletion loses
  faults; select, don't delete (r1-against).
- CI: full fast suite under ~10 min per change, re-gate merged tree, slow/live/mutation scheduled; three harness defects
  filed as #117-#119 (r1-ci-cd).
- Agents game tests (ImpossibleBench, EvilGenie) and write weak oracles; mechanical guards beat an LLM reviewer;
  mutmut is the free mutation tool (r1-llm-tests-tools).
- Siblings: H lean (1.10 test/prod, 1.4% CI fail); repo-L CI red for 92 runs unnoticed, DB/file tests 52% of suite but
  81% of reds, per-diff mutation caught vacuous tests review missed; repo-R no test CI, 14% of items fail in a fresh
  clone on one ignored asset (r1-sibling-suites).

## Open, for round 2
1. Mutation on changed code in Python: mutmut/cosmic-ray changed-lines mode, runtime on H and a sample diff; how
   repo-L runs per-diff mutation; fail-before/pass-after replay of new tests on the base commit vs H's devkit/weaken.py.
2. Necessary end-to-end tests by project type (CLI/library, web service with DB, LLM-agent app with evals,
   simulation/robotics): evidence, smoke vs golden vs contract, cost; any study linking suite shape to escaped defects.
3. Suite health signals for a periodic audit: flake detection and quarantine, fresh-clone runs, CI-red-for-N-runs,
   test-to-code drift by size tier; what the audit component should compute and when it should run.
