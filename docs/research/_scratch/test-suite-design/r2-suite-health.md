# r2: suite-health signals for a periodic test-suite audit (#112, gap 3)

Search coverage: `uv run dev lit status` on 2026-10-12: OK, partial (s2 down, rate limited; openalex, crossref,
europepmc, arxiv up). Every `lit search` ran with s2 skipped; the flaky-test core (Lam, Alshammari, Gruber, Parry)
came back through openalex and crossref. Also used: the arxiv MCP (one paper, searched by passage), WebFetch (Fowler,
Dropbox, Bazel, DROPS, author PDFs), context7 (pytest docs), the GitHub and PyPI APIs, and `gh` on H and E.
Code names as in [r1-sibling-suites.md](r1-sibling-suites.md): H = this repository, L, R, E = the siblings.

Scripts (moved at synthesis to [scripts/](scripts/)): `rerun_outcomes.py` (per-test outcomes
over N JUnit reports), `ci_streaks.py` (red streaks and staleness of default-branch CI), `ratio_drift.py`
(test/prod ratio and asserts per test at sampled commits, read through git), `flake_math.py` (detection arithmetic).

## Findings

### F1. What H already computes, and what it does not (VERIFIED, read `devkit/gate.py`, `devkit/weaken.py`, `.devkit/ratchets.toml`, `scripts/suite_metrics.py`, `pyproject.toml`, `.github/workflows/ci.yml`, `template/.github/workflows/maintenance.yml`)
- **Gate, every change:** `min_tests` and `min_coverage` floors, and a note that suggests raising a floor the suite has
  outgrown (`TEST_FLOOR_STALE = 0.90`). An empty run or an all-skipped run fails ("a gate that ran nothing does not
  pass"). `weaken.py` flags a diff that deletes asserts or tests or adds skips unless the issue holds an owner-written
  `weakening-ok:`. pytest runs with `--strict-markers`, pytest-randomly (a new order every run) and xdist.
- **What the gate throws away:** `_run` writes the JUnit report into a temporary directory and keeps only the pass
  count; only a failing run's log is saved. So no per-test outcome or duration history exists. Flakes cannot be mined
  from H's gate runs today.
- **`scripts/suite_metrics.py` (r1, run by hand):** static ratio, oracles per test, clones, kinds, markers, stray test
  files, durations and top-10 share (`--run`), fix commits that add tests, and per-workflow CI failure %, reruns,
  default-branch failures, and `shas_with_both_pass_and_fail` (a free flake signal). It does **not** compute red-streak
  length or duration, how stale the CI is against main, tests skipped only in CI, or drift between two dates.
- **Not computed anywhere in H:** flake rate per test, quarantine, fresh-clone parity, never-run tests, slow-test
  budget, ratio drift. `xfail_strict` is not set. pytest's `xfail_strict = true` turns an unexpected pass into a
  failure ([pytest docs, skipping](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/skipping.rst),
  SOURCED via context7).
- **Template:** `maintenance.yml` (weekly) keeps one stable-titled issue ("Broken links") current and closes it when
  clean. That is the reuse pattern for the audit's output (F8).

### F2. Measured on H and E today (VERIFIED, 2026-10-12)
- **Six randomized reruns of H's gate suite in a fresh `git clone --local`** (`uv sync --locked`, `-n 4`,
  `-m "not slow and not live"`, `--randomly-seed` 1..6, `rerun_outcomes.py`): 922 test ids, **0 mixed outcomes, 0
  failures**. Wall time 8.5-11.5 s per run. Clone took 0.15 s and sync 0.43 s on a warm uv cache. The summed median
  test time was 25.9 s, the top-10 tests took 35.8% of it, the slowest test 2.0 s (7.9%), and only 2 tests exceed 1 s.
  r1's 66.5% top-10 share included the two `slow` template tests (16.4 s + 4.9 s). Six clean runs only bound the per-test
  flake rate weakly: they detect p = 20% with 74% probability and p = 1% with 6% (`flake_math.py`).
- **Fresh clone versus working tree, both directions.** The fresh clone passed all 922. In the working tree, r1 saw 3
  `test_scrub.py` failures from untracked scratch files: a test that fails only in the working tree. R is the
  opposite case: 14% of its items pass only where a git-ignored asset exists. A parity check must compare both ways.
- **Tests that never run in CI.** H's latest main CI run passed **920**; the fresh clone passed **922**. The two
  `test_plugin.py` cases `skipif(shutil.which("claude") is None)` skip on the CI runner. The separate `plugin` job
  validates the same thing, so nothing is lost here (BELIEVED), but the gate prints no skip count, so nobody would see
  it if the skip hid a real loss. The 2 `slow` tests (`test_template.py`: rendered project gate, fresh clone following
  README) are deselected by the gate and run in no CI job. The `template` CI job covers the first; the second runs
  nowhere. 3 `live` tests run in CI via `pytest -m live tests/test_security.py`.
- **A stale floor nobody acted on.** The same CI log prints `raise min_tests to 874 (920 passed)`. The floor is still
  759 (17.5% below the passed count). The notice works; no step turns it into work.
- **Red streaks (`ci_streaks.py`).** H `ci` on main: 28 runs, 0 red. E `gate` on main: 53 runs, 1 red, longest streak 1.
  **E `maintenance` (weekly, first run 2026-10-12): red, and no "Broken links" issue exists.** Its link check found
  1,245 errors (lychee also scanned `docs/research/_scratch/`). The step that files the issue then failed with
  `GraphQL: Body is too long (maximum is 65536 characters) (createIssue)`. So the job meant to turn a signal into an
  issue failed silently on the first run. L's case was the same failure at larger scale: 92 red runs from one config
  bug, then Actions disabled ([r1-sibling-suites.md](r1-sibling-suites.md) F6).
- **Drift is cheap to recompute from git.** `ratio_drift.py` read H's 31 first-parent commits in 0.1 s. test/prod
  rose from 0.84 to 1.10, and asserts per test function from 2.31 to 2.43, between commits 10 and 30. H's history
  is two days long, so this shows the method and its cost, not a trend. No stored state is needed for these two
  signals: the audit recomputes them at the last audit's commit.

### F3. Flake detection: when to look, and what reruns can and cannot see (SOURCED)
- **Most flakes are flaky at birth, not all.** In 55 Java projects (245 flaky tests that compile at their introducing
  commit), **75% were flaky when added**. Running detectors on new or modified tests catches **85%**. The other
  **15% become flaky through changes elsewhere**, which only running detectors on all tests finds. The number of
  flaky tests grows with project size, but more slowly (Kendall τ = 0.365 with number of tests) ([A large-scale longitudinal study of flaky tests](https://doi.org/10.1145/3428270) (OOPSLA 2020), full text). Implication (BELIEVED): new and changed tests get a rerun
  check at PR time. The periodic audit covers the other 15% with whole-suite reruns.
- **Reruns find only frequent flakes.** Re-running 24 projects 10,000 times found 811 flaky tests (3.6% of tests).
  **About a quarter would have been found with 10 reruns, half with 100, two thirds with 1,000.** Over 33% failed 10
  times or fewer in 10,000 runs ([FlakeFlagger: Predicting Flakiness Without Rerunning Tests](https://doi.org/10.1109/icse43902.2021.00140) (ICSE 2021), full
  text). Arithmetic (VERIFIED, `flake_math.py`): k reruns see a p = 1% flake with probability 10% (k = 10), 39% (50)
  or 87% (200). A 95% bound of p < 1% needs 299 clean runs, and p < 0.1% needs 2,995. The ≤ 5% spurious-red budget
  needs p ≤ 5.1e-5 per test at 1,000 tests and 5.1e-6 at 10,000. Reruns cannot verify that, so at medium and large
  size the history of real gate runs is the only affordable sample (BELIEVED).
- **Feature-based predictors are a triage aid, not a detector.** FlakeFlagger beat a vocabulary classifier on F1 in
  16 of 23 projects with far fewer false positives (380 against 4,674). Test execution time, coverage and coverage of
  changed lines, and third-party library use were predictive (same paper). In Python, CANNIER combined reruns with
  ML on 89,668 tests from 30 projects and cut rerun cost by about an order of magnitude ([CANNIER, EMSE
  2023](https://doi.org/10.1007/s10664-023-10307-w), abstract). FlakeFighters packages several classifiers as a
  pytest plugin ([FlakeFighters: A Pytest Plugin to Detect Flaky Test Failures](https://doi.org/10.1145/3832783.3834641) (ASE 2026), abstract only, no repository
  checked). Python order dependence is 59% of flakes (Gruber, in [r1-ci-cd.md](r1-ci-cd.md) F5), and iPFlakies
  detects and patches order-dependent Python tests ([ICSE 2022 demo](https://doi.org/10.1109/icse-companion55297.2022.9793801), abstract).
- **Reproduction is fragile.** For 137 known non-order-dependent Python flakes run 100 times each, Shaker (stress
  injection) found 37.2% against 35.8% for plain reruns (McNemar p = 0.84), and fewer than half reproduced as flaky
  at all on new hardware ([Evaluating Shaker for Flaky Test Detection in Python Projects](https://doi.org/10.5753/sast.2026.30984) (SAST 2026), abstract). 46.5%
  of flaky tests in 52 projects were resource-affected ([arXiv 2310.12132](https://arxiv.org/abs/2310.12132),
  abstract). So the audit records the machine and the worker count with each outcome (BELIEVED).
- **The cheap free signal already exists:** a sha with both a pass and a fail. `suite_metrics.py` counts it per
  workflow. Per test, it needs the gate to keep JUnit outcomes (F1). L keeps such a ledger: 22,707 executions over
  5,938 ids ([r1-sibling-suites.md](r1-sibling-suites.md) F6).

### F4. Quarantine: practice, limits, and the cost of ignoring failures (SOURCED)
- **Practice.** The multivocal review lists the responses as fix, rerun, ignore or disable, quarantine, annotate as
  flaky, or remove. Developers commented out flaky tests in 10 of 77 fix commits. Grey literature describes
  quarantine as a separate staging suite kept until the test is fixed ([Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review](https://arxiv.org/abs/2212.00908) (JSS 2023), §4.6). Interviews with 14 practitioners name guidelines and a stable infrastructure as the key mitigations
  ([A Qualitative Study on the Sources, Impacts, and Mitigation Strategies of Flaky Tests](https://doi.org/10.1109/icst53961.2022.00034) (ICST 2022), abstract).
- **Limits.** Fowler: keep quarantined tests out of the main pipeline. Because "tests keep getting thrown into
  quarantine and forgotten", cap it: "only allow 8 tests in quarantine", or "no longer than a week". Quarantined tests
  "are useless as regression tests" ([Fowler 2011](https://martinfowler.com/articles/nonDeterminism.html)).
- **Automated quarantine grows.** Dropbox's Athena retries a failing test up to 10 times and quarantines tests that
  flap across hosts. It notifies the author and the owning team, not reverting. **Quarantines roughly doubled within
  a few months** of launch ([Dropbox, 2019](https://dropbox.tech/infrastructure/athena-our-automated-build-health-management-system)).
  Google's auto-quarantine "could easily mask a real race condition" (Micco, in [r1-ci-cd.md](r1-ci-cd.md) F5).
- **Ignored failures cost.** In Firefox, builds with all tests passing had a median of 2 crash reports. Builds with
  failing tests had 508 (Beta) and 291 (Production), and builds with flaky tests 514 and 234 ([The impact of failing, flaky, and high failure tests on the number of crash reports associated with Firefox builds](https://doi.org/10.1145/3236024.3275529) (FSE 2018), abstract; correlation, not cause).
- **Classifying a failure as flaky by matching old messages can hide true failures** (498 flaky tests, 230,439
  failure messages, 22 Java projects; [230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers](https://doi.org/10.1109/icst60714.2024.00031) (ICST 2024), abstract; rates not read).

### F5. Red streaks and CI decay (SOURCED)
- **Test Alert Snooze.** 27 Python and Java projects on GitHub Actions over 90 days. The same test failing in ≥ 2
  consecutive revisions is **42.9% of test failures**. **Median persistence: 2 revisions, about one day; worst 11
  commits and 16 days.** During a snooze only 8.2% of commits are fixes. The resolving change is most often a test
  change (32.4%) rather than a fix (11.8%) ([Test Alert Snooze: An Empirical Study of Consecutive Test Failures on CI](https://doi.org/10.4230/lipics.esem.2026.24) (ESEM 2026),
  abstract page). So a 2-run, 1-day streak is the norm. Longer than that is the tail worth an issue (BELIEVED).
- **CI-Odor (Vassallo et al., ICSE 2019).** After surveying 124 developers, the authors built detectors for four
  anti-patterns that "slowly creep into a project": Slow Build, Broken Release Branch, Skip Failed Tests and Late
  Merging. Rules: a build slower than the 3rd quartile of recent master builds is medium severity, and one above Q3 +
  1.5·IQR is high, with a weekly regression trend. Broken master is reported as average time broken over consecutive
  broken builds. Skip Failed Tests is ΔBreaks < 0 ∧ (ΔRuns < 0 ∨ ΔSkipped > 0) between consecutive builds of a
  branch. On 18,474 build logs of 36 Java projects: 3,823 high-severity warnings, broken master in every project, slow
  builds in half (27% of builds), skip-failed-tests in 42% of projects (0.65% of builds). 55% of 13 original
  developers would adopt the tool ([ICSE 2019](https://doi.org/10.1109/icse.2019.00028), abstract; rules and results
  from the chapter version in [Vassallo's PhD thesis](http://carminevassallo.com/publications/VassalloPhDThesis.pdf)
  §7.4, full text). H's `weaken.py` is a stricter, diff-level form of Skip Failed Tests (BELIEVED).
- **Catalog.** 79 CI bad practices from 13 expert interviews and more than 2,300 Stack Overflow posts ([An empirical characterization of bad practices in continuous integration](https://doi.org/10.1007/s10664-019-09785-8) (EMSE 2020)). The full text could not be fetched (repository 404,
  publisher paywall), so no item is cited here.
- **DORA:** fixing a broken build takes priority over other work ([r1-ci-cd.md](r1-ci-cd.md) F1).

### F6. Slow tests and size labels (SOURCED)
- Bazel ties size to a timeout: small 60 s, medium 300 s, large 900 s, enormous 3,600 s. It advises timeouts "as
  tight as you can without incurring any flakiness" and warns about oversized ones with
  `--test_verbose_timeout_warnings` ([Bazel test encyclopedia](https://bazel.build/reference/test-encyclopedia)). The
  per-change budget is DORA's 10 min ([r1-ci-cd.md](r1-ci-cd.md) F1). Google's flake rates rise with test size (0.5%,
  1.6%, 14% for small, medium, large; [r1-empirical-by-size.md](r1-empirical-by-size.md) F6). So a slow-test signal is
  a flake-risk signal too (BELIEVED).

### F7. Test/code co-evolution (SOURCED, abstracts)
- Repository mining over size, commits and coverage tells **synchronous** from **phased** co-evolution, in which tests
  lag and then catch up ([TeMo, EMSE 2011](https://doi.org/10.1007/s10664-010-9143-7)). No study found
  gives a drift threshold. The thresholds in F8 are judgment, anchored on r1's tier floors
  ([r1-empirical-by-size.md](r1-empirical-by-size.md), size tiers) and H's own drift history.

### F8. Proposed audit for #114 (BELIEVED; numbers from F1-F7 and r1)
Tiers by collected tests, since the flake budget scales with N. Prod KLOC is secondary.
Small (S) < 1,000 tests or gate < 2 min, medium (M) 1,000-5,000 or 2-10 min, large (L) > 5,000 or > 10 min.

| # | Check | How | Issue when | Evidence | H today |
|---|---|---|---|---|---|
| 1 | Flaky tests | S: k = 5 reruns of the gate suite, random seeds, fresh clone. M: k = 10 plus the outcome ledger. L: ledger only, plus 10 isolated reruns of each test that failed since the last audit. Any id with mixed outcomes on one tree is flaky | any flaky test; or main's same-sha pass+fail share > 1% of gate runs | F3, Lam 15% late flakes | 0/922 in 6 runs |
| 2 | Quarantine | `flaky` marker, each with an open issue; deselected from the gate, run on schedule | count > max(3, 0.5% of tests) (Fowler's 8 at about 1,600 tests); any entry older than 14 days (S, M) or 7 days (L); a marker without an issue | F4 | no marker yet |
| 3 | Red streak and staleness | `ci_streaks.py` per workflow on main; also scheduled jobs and their reporting steps | main red ≥ 3 runs or > 24 h; any scheduled job red; the last CI run on main older than the newest main commit by > 1 day, or Actions disabled | F5, F2 (E), L | 0 red; E maintenance red |
| 4 | Fresh-clone parity | `git clone --local`, `uv sync --locked`, gate suite; compare per-id outcomes with the working tree | any id whose outcome differs (both directions) | F2, R 14% | 922 = 922 |
| 5 | Never-run tests | collected ids minus ids run by any CI job or schedule; ids skipped in CI but passing locally; test files outside `testpaths`; `xfail_strict` off | any id run nowhere; skipped-in-CI count rose | F1, F2, L 12 stray files | 1 slow id runs nowhere; 2 CI-only skips |
| 6 | Slow tests | per-id durations from the ledger or audit run; gate wall-time trend (CI-Odor quartile rule) | gate wall > tier budget (S 2 min, M 10 min, L selected subset 10 min); a non-`slow` test > 5 s (S) or > 60 s (any tier, Bazel small); wall-time outlier > Q3 + 1.5·IQR | F6 | max 2.0 s, wall 9-12 s |
| 7 | Ratio and oracle drift | `ratio_drift.py` at the last audit's commit and at HEAD | test/prod falls > 10% relative, or below 0.3 (S, M) or 0.5 (L); asserts per test fall > 10%; single-oracle share rises > 5 pp | F7, r1 tiers | 1.10, 2.43 |
| 8 | Stale floors | the gate's own "raise min_tests/min_coverage" note | note present at audit time; the audit opens the PR that raises it | F2 | 759 vs 920 |
| 9 | Fix commits without a test | `suite_metrics.py` history since the last audit | < 80% of code fixes add a test | r1 F7 (H 100%, L 81%, R 41%) | 100% |

**Trigger.** Run when any of these fires first. Time: 30 days (S), 14 (M), 7 (L). Merged PRs since the last audit:
25 (S, M) or 50 (L). Growth: collected tests or prod SLOC up 25%, or a tier boundary crossed. Checks 3 and 8 are a few
`gh` and git calls, so they also run in every `dev orient`, not only at audit time. Do not rely on cron alone: GitHub
disables schedules in public repos after 60 days without activity ([r1-ci-cd.md](r1-ci-cd.md) F7), L's Actions are
disabled, and private-repo minutes are scarce. So the lead starts the audit locally when `orient` reports it due.

**Cost on H (VERIFIED, F2):** six reruns about 1 min, fresh clone about 10 s, drift 0.1 s, `gh` a few seconds; under 2 min.

**Output.** One issue per failing check with a stable title ("Suite health: flaky tests", ...), updated rather than
duplicated and closed when clean (the `maintenance.yml` pattern). Each has a DONE WHEN, e.g. "0 mixed outcomes in 10
seeded reruns of `<id>`". **Bodies are capped well under 65,536 characters** (top 20 items plus a count; the full report
as a comment or artifact), which is E's failure. The audit's last snapshot (commit, collected count, tier) goes in a
footer of one "Suite health" tracking issue, so drift and triggers need no committed state.

**Prerequisite in the gate:** keep each run's JUnit outcomes and durations (id, outcome, seconds, tree, seed,
workers, host) in an append-only local ledger under the gate's state dir. Checks 1, 5 and 6 read it. It costs one
file append per run. This is what L built.

## Gaps seen, not closed
- **E's `maintenance.yml` reporting step fails on large bodies** (template defect, H's `template/`): cap the body,
  and exclude `docs/research/_scratch/` from the link check. For the lead to file.
- **H's gate discards JUnit**, so no flake or duration history exists (F1). This is the prerequisite above; needs an issue.
- **H's `slow` fresh-clone README test runs in no CI job** (F2). A scheduled `--full` run would close it (also r1-ci-cd F6).
- Zampetti's 79-item catalog was not read (the full text could not be fetched). iDFlakies' per-suite-run flake
  probability was not extracted (abstract only).
- Thresholds in F8 are judgment. No study gives quarantine caps, drift limits or audit cadence with outcome data.
  Test Alert Snooze and Lam are the closest.
- Flake ledgers of L and their isolated-rerun verdicts were not re-read this round. Their real flake rate per test is unknown.
- FlakeFighters: no public repository was checked, so it is not recommended. pytest-flakefinder's last release was 2022.

## Tools checked (GitHub and PyPI APIs, 2026-10-12)
- [pytest-randomly](https://github.com/pytest-dev/pytest-randomly): MIT, 722 stars, pushed 2026-10-05, 5.0.0. Already in H.
- [pytest-rerunfailures](https://github.com/pytest-dev/pytest-rerunfailures): MPL-2.0, 479 stars, 16.7 (2026-09-17).
  Isolated reruns of a failed id in the audit, never in the gate.
- [pytest-repeat](https://github.com/pytest-dev/pytest-repeat): MPL-2.0, 196 stars, 0.9.4 (2025-04-07).
  `--count=k` for check 1 on a few ids.
- [pytest-flakefinder](https://github.com/dropbox/pytest-flakefinder): Apache-2.0, 158 stars, 1.1.0 (2022-10-26). Stale.
- [flaky](https://github.com/box/flaky): Apache-2.0, 397 stars, 3.8.1 (2024-03-12). It reruns flaky tests automatically,
  which hides them, so it is not for the gate.
- [pytest-reportlog](https://github.com/pytest-dev/pytest-reportlog): MIT, 106 stars, 1.0.0 (2025-11-11). A JSON-lines
  per-test log, a candidate format for the ledger. pytest's built-in JUnit XML is enough.
- Best free option: H's existing stack (pytest-randomly + xdist + JUnit) plus a ledger and pytest-rerunfailures for
  audit-time isolation. No new gate dependency.

## Sources fetched (2026-10-12)
- [A large-scale longitudinal study of flaky tests](https://doi.org/10.1145/3428270) (OOPSLA 2020) (author PDF, full text);
  [FlakeFlagger: Predicting Flakiness Without Rerunning Tests](https://doi.org/10.1109/icse43902.2021.00140) (ICSE 2021) (author preprint, full text)
- Abstracts via `dev lit get`: [CANNIER EMSE 2023](https://doi.org/10.1007/s10664-023-10307-w),
  [FlakeFighters: A Pytest Plugin to Detect Flaky Test Failures](https://doi.org/10.1145/3832783.3834641) (ASE 2026), [iPFlakies](https://doi.org/10.1109/icse-companion55297.2022.9793801),
  [iDFlakies ICST 2019](https://doi.org/10.1109/icst.2019.00038), [Evaluating Shaker for Flaky Test Detection in Python Projects](https://doi.org/10.5753/sast.2026.30984) (SAST 2026),
  [arXiv 2310.12132](https://arxiv.org/abs/2310.12132), [The impact of failing, flaky, and high failure tests on the number of crash reports associated with Firefox builds](https://doi.org/10.1145/3236024.3275529) (FSE 2018),
  [230,439 Test Failures Later: An Empirical Evaluation of Flaky Failure Classifiers](https://doi.org/10.1109/icst60714.2024.00031) (ICST 2024), [Vassallo ICSE 2019](https://doi.org/10.1109/icse.2019.00028),
  [An empirical characterization of bad practices in continuous integration](https://doi.org/10.1007/s10664-019-09785-8) (EMSE 2020) (title only), [co-evolution EMSE 2011](https://doi.org/10.1007/s10664-010-9143-7),
  [A Qualitative Study on the Sources, Impacts, and Mitigation Strategies of Flaky Tests](https://doi.org/10.1109/icst53961.2022.00034) (ICST 2022)
- [Test Alert Snooze: An Empirical Study of Consecutive Test Failures on CI](https://doi.org/10.4230/lipics.esem.2026.24) (ESEM 2026) (DROPS landing page)
- [Test Flakiness' Causes, Detection, Impact and Responses: A Multivocal Review](https://arxiv.org/abs/2212.00908) (JSS 2023) (arxiv MCP, §4.6 passages)
- [Vassallo PhD thesis](http://carminevassallo.com/publications/VassalloPhDThesis.pdf) (§1.6, §7.4)
- [Fowler 2011](https://martinfowler.com/articles/nonDeterminism.html), [Dropbox Athena 2019](https://dropbox.tech/infrastructure/athena-our-automated-build-health-management-system),
  [Bazel test encyclopedia](https://bazel.build/reference/test-encyclopedia), pytest skipping docs (context7)
- `gh run list` / `gh run view` on H and E; H fresh clone at bd9abb9; GitHub and PyPI JSON for the tools above
