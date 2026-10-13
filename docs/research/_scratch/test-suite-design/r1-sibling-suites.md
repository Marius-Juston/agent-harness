r1: the sibling repositories' test suites, measured (#112, round 1)

Search coverage: no literature search in this round (measurement only). `dev lit status` per QUESTION.md on 2026-10-12:
OK, partial (s2 down; openalex, crossref, europepmc, arxiv up). Measured 2026-10-12.

Repositories are code-named because `tests/test_scrub.py` forbids their names and home paths in this public repo
(the lead has the mapping): **H** = this repository (agent-harness, head bd9abb9), **L** = the large agent-run game
engine (head 086fb686, ~1,400 agent-era commits), **R** = the robotics simulation research repository (head
7e5c305), **E** = the new research-first product repository (head 3748c23, no product code yet).

Reproduce: [scripts/suite_metrics.py](../../../../scripts/suite_metrics.py) `<repo> --ci --collect [--run]
[--code <pkg>] [--python <venv python>]`. Static part reads tracked files through git; `--collect`/`--run` use the
repo's own environment with no sync, no cache provider and no bytecode; `--python` runs a clone with the original's
venv. Raw JSON stayed in `.scratch/suite/` (local, holds private names). L and R were run only in `git clone --local`
copies in the scratchpad; every sibling's `git status` was clean after.

## Findings

### F1. Size and ratio (VERIFIED, suite_metrics.py static part)

| | H | L | R | E |
|---|---|---|---|---|
| test functions (AST) | 465 | 3,362 | 274 | 1 |
| collected items (pytest) | 927 | 5,938 | 355 | 1 |
| test LOC : package LOC | 5,502 : 5,011 = **1.10** | 85,466 : 142,246 = **0.60** | 4,596 : 20,953 = **0.22** | 4 : 0 |
| test LOC : all tracked .py | 0.90 | 0.51 | 0.14 | ~0 |
| median LOC per test | 6 | 13 | 10 | 2 |
| fixtures defined | 21 | 101 | 15 | 0 |
| parametrized / Hypothesis tests | 55 / 23 | 310 / 50 | 17 / 0 | 0 / 0 |

LOC = non-blank, non-comment lines. Package = `devkit/` for H, the engine package for L, `sim/` for R (the "all"
row adds plugins, migrations, experiments and research scripts).

### F2. Oracles: almost no test asserts nothing; density differs 2x (VERIFIED, static; hand-checked)
- Oracles per test (assert + pytest.raises/warns/fail + mock `assert_*` + calls to helpers that assert, resolved
  transitively): **H mean 2.62, median 2, p90 5; L 5.78, 4, 11; R 2.88, 3, 5.**
- Single-oracle tests: **H 31.6 % (147), R 22.3 % (61), L 9.5 % (320).**
- Tests with no explicit oracle: H 2, L 8, R 0. **All 10 hand-read: every one is an implicit "must not raise" oracle**
  (Hypothesis `never_crashes`, a validator that raises, a state machine run) or delegates to another test with a
  patched environment. True assertion-free tests: **0** in every repo. (A first pass also flagged an autouse fixture
  named `test_jobs`; the script now skips fixtures.)
- Mock-only tests (only `assert_called*`): **0 everywhere**; `assert_called*` appears in 5 L tests, 0 in H and R.
  Test doubles (monkeypatch, `patch`, `Mock`): H 48.6 % of tests, R 14.6 %, L 13.3 %. H's are mostly
  `monkeypatch.setattr` of its own module functions (BELIEVED: couples tests to internals; not measured).
- Static counting cannot see vacuous oracles. L's own review log found **23 "vacuous or missing test" findings**, e.g.
  `assert all(blow["hp_before"] >= 1 for blow in blows)` over an empty `blows` (SOURCED: L
  `docs/research/diff-mutation-and-matcher-corpus.md` §1, read 2026-10-12). L answered with diff-scoped mutation
  testing plus a vacuous-`all()` detector (same note §3). So "asserts per test" is a floor check, not a quality score.

### F3. Duplication is low in all three real suites (VERIFIED, static)
- Type-1 clones (identical AST): H 1 group (2 tests), L 3 groups (6), R 0.
- Type-2 clones (same AST shape, names and literals abstracted, >= 40 nodes): H 1 group (2), L 1 group (2), R 0.
- Same-file near-duplicates (abstracted 4-gram Jaccard >= 0.8, different shape): **H 8 tests (1.7 %), R 2 (0.7 %),
  L 13 (0.4 %)**; examples are sibling refusal cases that could be one parametrized test (H `test_workflow.py`
  0.96, `test_setup.py` 0.92). No repo shows LLM-style redundancy at scale.

### F4. Kinds: mostly pure and medium; almost no marked E2E (VERIFIED static, heuristic BELIEVED)
- Heuristic from calls in the test, its fixtures and helpers: pure / io (fs or db) / process (subprocess) / marked
  e2e (`live`, `soak`, `scale`...): **H 208 / 169 / 85 / 3; L 1,241 / 1,737 / 384 / 0; R 218 / 55 / 1 / 0.**
- The `net` signal is noisy: of 6 sampled `net`-flagged tests 2 were real (H's `live` security tests), the rest fakes
  or mocked openers, so only markers make a test e2e (script docstring says so). Precision of pure/io not measured.
- Markers in use: H `live` 3, `slow` 2; L `slow` 15, `perf` 3, `postgres` 3, `timeout` 6 plus bookkeeping markers,
  with `--strict-markers` and `xfail_strict`; R none but `skipif` 5. L's real end-to-end tests (scenario replays,
  marathons, playtests) carry no marker; they are found by run time instead.
- Stray test files outside `testpaths` (never run by the gate): **L 12** (a legacy `tests/` dir, 11 files, plus one
  research asset), H 1 (the template's own test, intentional), R 0.

### F5. Run time: a heavy tail of a few broad tests (VERIFIED)
- **H**: 927 collected, serial, `-m "not live"`: **47 s** wall; top 10 tests = 66.5 % of summed time; two template
  tests (render a project and run its gate; fresh clone to green gate) take 16.4 s + 4.9 s.
  921 passed; the 3 failures were `test_scrub.py` flagging this round's own scratch files (environment, not suite).
- **R** (clone): **61 s** serial; top 10 = 61.6 %. **26 failed + 24 errors of 355 (14 %), every one a
  `FileNotFoundError` on one git-ignored asset** (`third_party/mujoco_menagerie/.../ur10e.xml`): the suite is not
  hermetic and nothing (no CI) catches it.
- **L** (not run: its pytest plugins write ledgers into the git dir). Its local gate records (265 records,
  2026-10-02..05): fast tier median **219 s**, p90 526 s (n=211); full tier median **534 s**, p90 822 s (n=45).
  Latest full suite: 5,866 passed in 547 s (xdist). 368 test functions had a phase >= 2 s, 4,479 s summed; the
  largest files are dice-style renders 437 s and two marathon files 345 s + 342 s. L's own profile before tiering:
  gate 13–16 min, 68 % of slow time in generated render tests (SOURCED: L `docs/research/dev-workflow-and-test-time.md`
  §1–2). Collect-only: L 5,938 items in 11.4 s, R 355 in 2.0 s.

### F6. CI structure and history (VERIFIED, `gh run list` / `gh run view`, all runs on record)
- **H**: one `ci` workflow, jobs `check` (the same `dev check` agents run, then gitleaks, uv audit, SBOM, live security
  tests), `template` (render a project, sync, run its gate), `plugin` (`claude plugin validate --strict`); PR, push to
  main, weekly schedule; PR runs cancel superseded ones. **77 runs: 73 pass, 1 fail (1.4 %), 3 cancelled; median
  0.7 min, p90 1.4 min; 0 reruns; 0 red main.** The one failure was a test catching a config regression on a PR
  (`test_config.py`), fixed by the next commit.
- **E**: `gate` (check + citations), `board`, `maintenance`. **gate: 135 runs, 118 pass, 15 fail (11.3 %); median
  0.4 min, p90 0.7; 1 rerun that passed; 1 red main.** Of the 15 failures **12 were citation/link checks** on research
  notes, 1 a test (`test_lit.py` 20-process rate test, a timing assertion; the current test's docstring says it now
  reads the bucket's own grant times "so CPU scheduling cannot break it"), 2 left no failed-step log.
- **L**: `engine.yml` (core py3.14 + a PostgreSQL service job) and a nightly soak. **Actions are disabled at the
  repository level** (`actions/permissions` enabled=false); no run after 2026-09-26. In its two days: **92 runs, all
  red. The postgres job failed 92/92 at "Initialize containers"** (`docker create ... unknown shorthand flag: 'U'`:
  the `--health-cmd 'pg_isready -U ...'` quoting); core passed 58, failed 31 (+3 early matrix runs). The one soak run
  took 63.7 min and passed. All verification moved to a local, locked gate with records in the git dir.
- **L local gate logs** (990 logs, 2026-10-02..05): suite stage **red 49/279 (17.6 %)**, selected stage red 12/56;
  165 distinct failing test functions over 61 red runs. **io-kind tests are 51.7 % of the suite but 80.6 % of the
  tests that went red; pure tests 36.9 % of the suite, 8.5 % of reds**; failing tests are longer (median 21 LOC vs
  13). Red here mixes caught regressions with flakes; L's flake ledger (from 2026-10-04) holds 22,707 recorded test
  executions over 5,938 ids and 3 isolated-rerun verdicts: 2 flaky, 1 real.
- L also logged a silent-gate failure: from T-0740 to T-0825 **every builder gate ran no test in its selected stage
  and still passed** (pytest rootdir taken from an option value), which led to a "ran contract": per-stage floors
  (suite >= 2,800 tests, perf >= 40, collected >= 2,300) and "an empty run is not a pass" (SOURCED: L
  `docs/research/dev-workflow-and-test-time.md` §4b and L's floors file, read 2026-10-12).
- **R**: no test CI at all (a path labeler and GitHub's dependency graph only).
- H's own floors: `min_tests = 759`, `min_coverage = 77` (77.8 % of `devkit/` measured) in `.devkit/ratchets.toml`
  (VERIFIED, read). E has no `[tests]` floor.

### F7. Fixes and tests in git history (VERIFIED counts; interpretation BELIEVED)
Fix commit = non-merge commit whose subject matches fix/fixes/fixed/bug/regression and that changes non-test `.py`.

| | H | L | R |
|---|---|---|---|
| code commits that also touch tests | 96.7 % (29/30) | 77.9 % (647/831) | 36.3 % (89/245) |
| fix commits | 6 | 64 | 29 |
| ... adding a `def test` | **100 %** | **81.2 %** | **41.4 %** |

- The subject heuristic is noisy: in E every "fix" is a research-audit fix (E excluded); in R several are A/B default
  flips (`PR_*` switches) validated by experiments, not tests.
- **Most of L's fixes are caught before merge by review and mutation, not by the suite**: 49 "review fix" and 35
  "mutant" commit subjects. In a random 10 of L's fix commits, 8 touched tests; the 2 without were May 2026 commits
  from before the agent harness ("fixes, graph, combat", "fix executor bug"). In R's random 10, 6 touched tests.
- Escapes to main that a test should have caught are not separable from this history: neither L nor R records how a
  defect was found (L's own retro: 72 % of open tickets have no origin; L `docs/research/workflow-retrospective-2026-10.md`
  §0, SOURCED). Tests catching bugs pre-merge are visible only as red PR/gate runs (H 1, E 1 test failure; L 61 red
  local gates, flake share unknown).

### F8. What each repository does well and badly (BELIEVED, from F1–F7)
- **H — well**: ~1:1 test:code; every fix adds a test; CI runs the exact local gate and stays under 1.5 min p90; the
  template E2E (render + gate, fresh clone + README) is a separate job; live tests are marked and run where network
  exists; count and coverage floors. **Badly**: one-third single-oracle tests; heavy monkeypatching of internals;
  2 tests are 47 % of run time; `test_scrub.py` reads untracked working files, so any scratch note with private
  paths reddens the suite (it did in this round).
- **L — well**: the most mature practice seen: run-contract floors, flake ledger with isolated reruns,
  coverage-based test selection, per-diff mutation testing, weakening detector, Hypothesis state machine, strict
  markers and xfail, tests citing 274 distinct tickets. **Badly**: GitHub CI was red its whole life from one config
  bug, then disabled, so nothing verifies off the dev machine; 12 test files sit outside `testpaths`; full gate ~9 min
  with a tail of render and marathon tests; broad DB-backed tests dominate red runs; its E2E tests carry no marker.
- **R — well**: fast (61 s), dense oracles, mostly pure tests, a regression-case file. **Badly**: no CI; 14 % of
  items fail on a fresh clone from one ignored asset with no skip or fetch; 0.22 test:code; under half of fixes add
  a test; no slow/integration markers; no property tests.
- **E**: one smoke test, no product code yet; its gate mostly guards research notes (12 of 15 failures).

## Gaps seen, not closed
- Per-test durations for L from a clean run (needs a clone plus PostgreSQL, ~10 min); here only gate logs.
- Coverage and mutation score per repo (only H's 77.8 % line coverage is on record).
- Precision of the pure/io/process heuristic (only the `net` signal was sampled, 2/6).
- How many of L's 61 red gates were real regressions vs flakes (the ledger starts 2026-10-04).
- A defect-origin field (found by test / review / mutant / playtest) exists in none of the repos, so "bugs the suite
  missed" cannot be counted; H's board could record it.

## Sources fetched
- [scripts/suite_metrics.py](../../../../scripts/suite_metrics.py) (this round) over the four working trees and
  `git log`; `gh run list --limit 1000` and `gh run view --log-failed` / `--json jobs` per repo;
  `gh api repos/<o>/<r>/actions/permissions` for L.
- L's local gate records, gate logs, flake ledger and floors file (git dir and package, read-only).
- L notes `docs/research/dev-workflow-and-test-time.md`, `diff-mutation-and-matcher-corpus.md`,
  `workflow-retrospective-2026-10.md` (private sibling, read 2026-10-12).
- H: [.devkit/ratchets.toml](../../../../.devkit/ratchets.toml), [tests/test_scrub.py](../../../../tests/test_scrub.py),
  `.github/workflows/ci.yml`.
