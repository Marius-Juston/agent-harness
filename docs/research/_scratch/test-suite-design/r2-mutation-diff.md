# r2: mutation testing on the changed lines of a diff, and base-commit replay (gap 1)

Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc and arxiv up; s2 down (HTTP 429,
so s2-only results were skipped). Tool facts come from the installed wheels (mutmut 3.8.0, mutmut 2.5.1,
cosmic-ray 8.7.0), PyPI and the GitHub API, all read 2026-10-12.

Setup for every measurement: H at `bd9abb9` in throwaway clones in a scratch directory (never this worktree), tool
config added only in those clones, 32-core WSL2 box, `-m "not slow and not live"`, `-p no:randomly`, mutmut with
16 worker processes. The sample diff is H's merged #107 (`4dfd406..86c6b14`): 296 changed lines (253 added
non-blank) in `devkit/board.py`, `cli.py`, `gate.py` and `workflow.py`, spread over 24 functions, with 57 new tests.
Scripts: `scripts/replay_new_tests.py`, `scripts/changed_functions.py`, `scripts/weaken_probe.py`,
`scripts/r2_mutation_math.py` (moved from the gitignored `.scratch/` at synthesis).

## Findings

### F1. Only cosmic-ray filters by changed line; mutmut 3 scopes by function (VERIFIED, source read)
- **cosmic-ray 8.7.0 has a diff filter.** It ships `cr-filter-git` (console script
  `cosmic_ray.tools.filters.git:main`). It runs `git diff --relative -U0 <branch> .` and marks every pending
  mutant whose start-to-end rows miss the diff's new-side lines as SKIPPED. The base branch comes from
  `[cosmic-ray.filters.git-filter] branch` and defaults to `master`
  ([source](https://github.com/sixty-north/cosmic-ray/blob/master/src/cosmic_ray/tools/filters/git.py)).
  repo-L's note says "neither filters by diff line"
  (repo-L `docs/research/diff-mutation-and-matcher-corpus.md` §2). That is wrong for cosmic-ray and right for mutmut 3.
- **cosmic-ray's other limits for per-PR use (VERIFIED, source).** The `local` distributor runs mutants one at a
  time (`distribution/local.py`), and parallel runs need `cr-http-workers`, each with its own checkout. It writes each
  mutant into the source file in place, so it needs a disposable clone. Every mutant runs the same `test-command`:
  there is no per-mutant test selection from coverage. It also applies every operator that fits a line, which here
  gave up to 31 mutants on one line.
- **mutmut 3.8.0 has no line or diff filter.** Its configuration has no such key: `only_mutate`/`do_not_mutate`
  are file globs, and `mutate_only_covered_lines` filters by coverage, not by diff. `use_git_change_detection`
  only invalidates the cache when non-`.py` files change
  ([configuration.py](https://github.com/boxed/mutmut/blob/main/src/mutmut/configuration.py)). It offers three ways
  to narrow a run:
  (a) `mutmut run <names>` accepts fnmatch globs over mutant names, which are per function
  (`devkit.board.x_lint__mutmut_7`);
  (b) since 3.7.0, per-function source hashing re-tests only mutants in functions that changed, plus callers through
  dependency tracking ([HISTORY.rst](https://github.com/boxed/mutmut/blob/main/HISTORY.rst));
  (c) each mutant runs only the tests that called its function in the stats run (`tests_by_mangled_function_name`
  in `__main__.py`). mutmut 2.5.1 (August 2024) had `run --use-patch-file` ("only mutate lines added/changed in
  the given patch file"). The 3.0 rewrite (mutation schemata, pytest only) dropped it.
- **So there are three ways to mutate only a diff in Python:**
  1. cosmic-ray plus `cr-filter-git`: line-exact, serial and noisy;
  2. mutmut on the functions the diff touches, via globs (my `scripts/changed_functions.py` maps diff lines to
     globs) or via a warm cache;
  3. a custom engine like repo-L's (F3).

### F2. What it costs on H (VERIFIED, measured 2026-10-12)
| Run | Mutants | Wall | Peak RSS | Outcome |
|---|---|---|---|---|
| Fast suite, serial, no mutation (baseline) | – | 50.6 s | 0.29 GB | green |
| mutmut, whole `devkit/` (5,943 LOC), cold | 14,823 (2.49/LOC) | **474.6 s** (generation 52.9 s, ~364 s mutants at 40.7/s) | 3.1 GB | 7,760 killed, 4,432 survived, 2,617 no tests, 14 timeout |
| same, second run with generation and stats cached | 14,823 | 275.5 s | 3.3 GB | 7,756 / 4,437 / 2,617 / 13 |
| mutmut whole `devkit/` at #107's base (cold) | 13,411 | 396.1 s | – | 6,859 / 3,935 / 2,606 / 11 |
| **#107, mutmut warm incremental** (base cache, then checkout tip) | changed functions plus callers | **168.2 s** | 1.2 GB | stats rerun for 57 new tests |
| **#107, mutmut cold, 4 files + 24 function globs** | 2,331 | **194.5 s** | 1.2 GB | 1,060 killed, 1,096 survived, 174 no tests |
| **#107, cosmic-ray + `cr-filter-git`**, serial, the PR's 3 test files as test command | 402 of 4,108 (9.8%) on 81 lines | **289 s** (init 15.3 + filter 1.3 + exec 272.2; 0.68 s/mutant) | 0.1 GB | 303 killed, 99 survived on 39 lines; 13 lines had every mutant survive |

- **Whole-package mutation score:** 0.636 killed/(killed+survived), or 0.523 over all mutants. Of all mutants,
  17.7% have no test that calls the function in-process; `setup.py` (422) and `code.py` (278) are reached mostly
  through subprocesses. Per file the score runs from 0.24 (`ran.py`) and 0.34 (`cli.py`, mostly argparse help
  strings) to 0.93 (`backoff.py`). The guard `weaken.py` itself scores 0.64 (VERIFIED,
  `scripts/r2_mutation_math.py`).
- **The run is nearly but not fully deterministic.** Two cold runs on the same tree differed by 4 killed and 1
  timeout, at timeout and CPU-limit edges.
- **Practical traps.** mutmut writes a `mutants/` tree into the repo root. H's
  `tests/test_scrub.py` scans untracked files, so the first clean-test run failed on a string inside
  `mutants/mutmut-stats.json` until `mutants/` was ignored. mutmut also failed until `also_copy` listed every top-level
  file the tests read through `Path(__file__).parents[1]` (`.gitattributes`, `template/`...). A template that
  adopts mutmut needs `mutants/` in `.gitignore` and an `also_copy` list. With 16 workers the cold whole-package run
  peaks at 3.1 GB.
- **Scope is the cost driver, not wall time.** Function granularity inflates #107 to 2,331 mutants. One function,
  `cli.build`, has 13 changed lines (argparse wiring) yet gave 1,065 mutants (46%) and 696 of the 1,096 survivors.
  In a random 40 of mutmut's survivors, 13 were `"XX…XX"` string mutants. Most of the rest were help text or argparse
  keyword changes. By my reading about 10 of 40 looked actionable, such as `kind != "epic"` → `"EPIC"`,
  `fetch(with_comments=True)` → `False`, `"DONE WHEN"` lower-cased and `cap(..., SECTION_LINES)` → `None`
  (classification BELIEVED). cosmic-ray, line-exact and without string operators, gave 402 mutants and 99
  survivors. Its 13 all-survive lines were again mostly f-string message text (`or 'none'`, `'warning: '`) plus
  `fetch(with_comments=True)`. That is Google's arid-line problem.
- **Caveat on timing.** The cosmic-ray run overlapped part of a mutmut run on the same box, so its 272 s is an upper
  bound. Its test command was the PR's own 3 test files, so some survivors may be killed by other tests.

### F3. How repo-L runs per-diff mutation (SOURCED: repo-L note and code, read-only, 2026-10-12)
- repo-L built its own engine, `<app> dev mutants [--rev BRANCH] [--base master]` (`<app>/dev/mutants.py`, 737
  lines, dated 2026-10-04). It borrows cosmic-ray's operators for mutant generation (`_cosmic`), and it does the
  following:
  1. takes the changed lines since `merge-base`;
  2. picks tests: the branch's own changed tests, then affected ones ranked by how many changed modules they name,
     capped at 12;
  3. runs one `pytest-cov --cov-context=test` pass to map each line to its covering test ids;
  4. skips arid lines (imports, docstrings, logging and print calls, decorators, signatures);
  5. makes **one mutant per line**, in Google's survivability order (LCR, ROR, AOR, SBR, UOI, plus CRP and LOOP),
     choosing the candidate by a stable hash of `file:line`;
  6. runs each mutant only against its covering tests, fastest first with `-x`, in two sandboxes, never editing
     the working tree;
  7. reports survivors as `file:line [family] before -> after`, changed lines no selected test runs, and
     **vacuous `assert all(...)` over a never-checked iterable**.
- **Measured by repo-L** on a 264-line branch: a 43–48 s coverage run, 95 candidates, 40 run (the cap) in
  107–110 s, 1.07 GB peak, 9–11 survivors; the note names three it treats as real gaps (an `or` → `and` in a filter, an
  `and` → `or` in a refusal, a removed `validate_fighter(...)` call that left the suite green). It also caught the empty-`blows`
  vacuous test that review had flagged by hand (repo-L note §3).
- **It is wired to people, not the gate.** The builder agent runs it before hand-back ("kill or explain each
  surviving mutant"), and the reviewer runs `--rev <branch>`. repo-L's own next step lists "run `mutants` on
  hand-back automatically … not in the gate itself; it is minutes, not seconds" (repo-L note §4). Its history has 28
  commits mentioning mutants since 2026-10-03 (VERIFIED, `git log --grep`).

### F4. Base-commit replay: cheap, but a weak oracle check on its own (VERIFIED, `scripts/replay_new_tests.py`)
- **Method.** For each of H's last 12 first-parent merges, the tests a PR added or changed (AST diff per test
  function) ran on the tip, then on the base code with the tip's `tests/`.
- **Totals: 159 tests (155 new, 4 changed).** All 159 pass on the tip. On the base:
  - **12 pass (7.5%)**;
  - **49 fail on an assertion (30.8%)**;
  - **79 (49.7%) fail only because the API does not exist yet**: AttributeError 56, TypeError 14, collection or
    import error 9;
  - 19 (11.9%) fail on other exceptions (JSONDecodeError 10, FileNotFoundError, StopIteration...).
- **Every PR with new tests had at least one test that failed on the base.** No PR would be flagged by a "some new
  test must fail before" rule.
- **The 12 that pass before are legitimate.** I read 6 of them: boundary and negative cases written beside the
  feature ("a test floor may rise without approval", "no issue moves when the merge does not happen", "an explicit
  ref line wins over the default", allowed-path cases of a guard). A per-test "must fail before" rule would
  misfire on all of them.
- **Cost:** 0.3–8.0 s of pytest on the base per PR (median 1.2 s) plus a checkout.
- **Implication (BELIEVED).** Fail-before/pass-after proves a test *reaches* the change. Half the time the only
  reason it fails is an import or attribute error, which says nothing about its oracle. Mutation on the changed
  lines is the check that the oracle *distinguishes* behaviour. Replay is a cheap tripwire that should fire only
  when *no* new test fails on the base of a fix or feature PR; it does not replace mutation.

### F5. H's `devkit/weaken.py` covers 3 of 11 weakening kinds that repo-L detects (VERIFIED, `scripts/weaken_probe.py`)
- [weaken.py](../../../../devkit/weaken.py) (160 lines, regex over `git diff -U0 -- '*.py'`) flags three cases:
  an assert or test function removed from a test file beyond those added back (moves are allowed), and any added
  skip or xfail. Owner approval comes from a `weakening-ok:` line in the issue.
- **Synthetic diffs it flags:** assert deleted, test renamed out of collection (`test_f` → `check_f`), `skipif`
  added.
- **Synthetic diffs it misses:**
  - `== 4` loosened to `>= 0`;
  - `assert True`;
  - an assert wrapped in `try/except AssertionError`;
  - a `conftest.py` autouse monkeypatch;
  - a `pyproject.toml` `addopts -k 'not …'`, which is out of scope because only `*.py` is diffed;
  - an edited golden JSON file;
  - a new monkeypatch of the module under test;
  - fewer parametrize cases.
- **repo-L's `weakening.py`** (946 lines, `ast` and `tokenize`) names 20 kinds with severities. Among them are
  `conftest_edit`, `pytest_config_edit`, `coverage_config_edit`, `golden_edit`, `assert_loosened`,
  `assert_trivial`, `assert_swallowed`, `patch_under_test`, `cases_reduced`, `literal_widened` and
  `test_shadowed`. Accepted findings go into a `Weakening-Accepted:` merge trailer (SOURCED, repo-L
  `<app>/dev/weakening.py` header and `KINDS`).
- **Division of labour (BELIEVED).** Weakening checks guard *old* tests, replay checks that *new* tests reach the
  change, and diff mutation checks that the tests on the changed lines have teeth. None of them covers another's
  case.

### F6. Literature on diff-scoped mutation (SOURCED)
- **Google** (Petrović, Ivanković, Fraser, Just, *Practical Mutation Testing at Scale*,
  [arXiv 2102.11378](https://arxiv.org/abs/2102.11378), sections "Mutation Testing in Use at Google" and "RQ1 Mutant Suppression"). Google mutates only changed code during
  review and suppresses arid nodes, with at most one mutant per line. The median number of generated mutants per
  changelist is 820 with no suppression, 77 with 1-per-line and **7 with arid-1-per-line** (Mann-Whitney
  p < .0001; 25th and 75th percentiles 3 and 19). Live mutants per changelist have a median of 2 and a 99th
  percentile of 43. Before suppression, developers initially classified 85% of reported mutants as unproductive.
  H's #107 under cosmic-ray (402 mutants, 99 live) and under mutmut (2,331 mutants, 1,096 live) is the "no
  suppression" regime.
- **Commit-relevant mutants are not the same as mutants on changed lines.**
  - Ma, Laurent, Papadakis and colleagues ran 83 commits and 2,253,610 mutants in C and Java. The commit-relevant
    mutation score correlates only weakly with the traditional score (Kendall/Pearson 0.15–0.4), and mutating only
    the changed lines loses about 50–60% of commit-relevant mutants (*Commit-Aware Mutation Testing*, ICSME 2020,
    [doi:10.1109/ICSME46990.2020.00045](https://doi.org/10.1109/ICSME46990.2020.00045); abstract only, cut off
    after that figure).
  - MuDelta predicts relevant mutants with machine learning (ROC-AUC 0.80, 50 commits in Coreutils) and kills 45%
    more relevant mutants than mutants sampled from the changed lines
    ([doi:10.1109/ICSE43902.2021.00086](https://doi.org/10.1109/ICSE43902.2021.00086), abstract).
  - The TOSEM 2022 follow-up studies where such mutants lie
    ([doi:10.1145/3530786](https://doi.org/10.1145/3530786)); I fetched only its abstract, which gives no numbers.
  - BELIEVED: changed-line mutation is the cheap, practical floor. It misses mutants elsewhere that interact with
    the change, and mutmut's caller invalidation partly covers those.

## Answer for the decision (#114, BELIEVED unless marked)
- **Yes, it can be done:** cosmic-ray does it line-exactly out of the box, mutmut 3 per function, and neither
  matches Google's useful regime (about 7 mutants per change) without a filter on top.
- **The cost on H per PR of about 250 lines is 3–5 minutes** on 16–32 cores (VERIFIED, F2). That is 3–6 times the
  50 s fast suite, so it is too slow and too noisy (99 to 1,096 survivors) to gate every PR. A whole-package run is
  about 8 minutes cold, which fits nightly or weekly.
- **Default for the test-suite review component:**
  1. **Per PR, in the gate, for seconds:** the weakening check, extended toward repo-L's high-severity kinds
     (conftest, pytest and coverage config, goldens, assert loosening), plus a replay warning when no new or
     changed test fails on the base of a fix or feature PR.
  2. **Per PR, on hand-back or review, for minutes, advisory:** one mutant per changed non-arid line, run against
     its covering tests and capped at about 40, with survivors shown as `file:line before -> after`. The cheapest
     reuse is cosmic-ray's operators and `cr-filter-git`'s diff parsing, with a covering-test map from
     `pytest --cov-context=test` (repo-L's design, about 150 s for 264 lines). mutmut's function-level scope fits
     poorly here.
  3. **Scheduled:** whole-package mutmut (incremental cache kept between runs), trending the score per file and
     listing "no tests" functions.
- **Bounds:** cap 40 mutants and 5 minutes per PR, and treat survivors as review findings, never as a gate failure.
  Google's developers first judged 85% of unsuppressed mutants unproductive (F6).

## Gaps seen, not closed
- A Python arid-line filter's precision: which share of H's survivors disappears if f-string or message text,
  argparse help and logging are skipped. That needs a one-mutant-per-line prototype run on #107 and two or three
  more PRs.
- Whether diff-mutation survivors on H predict later defects. H has no defect-origin field (r1-sibling-suites gap),
  so this cannot be measured yet.
- cosmic-ray with `cr-http-workers` (parallel), and with the covering tests per mutant instead of the PR's test
  files, was not measured.
- The commit-relevant-mutant papers were read at abstract level only. Their share of relevant mutants *outside*
  changed lines in Python is unknown.

## Sources fetched
- mutmut 3.8.0 and 2.5.1 wheels (via `uvx`), [PyPI](https://pypi.org/project/mutmut/) and
  [GitHub](https://github.com/boxed/mutmut): BSD-3-Clause, 1,476 stars, pushed 2026-09-12, not archived.
  [HISTORY.rst](https://github.com/boxed/mutmut/blob/main/HISTORY.rst).
- cosmic-ray 8.7.0 wheel, [PyPI](https://pypi.org/project/cosmic-ray/) and
  [GitHub](https://github.com/sixty-north/cosmic-ray): MIT, 660 stars, pushed 2026-08-09, not archived.
- [arXiv 2102.11378](https://arxiv.org/abs/2102.11378), passages from "Mutation Testing in Use at Google" and "RQ1 Mutant Suppression", via the arxiv MCP.
- Abstracts via `uv run dev lit get`: [doi:10.1109/ICSME46990.2020.00045](https://doi.org/10.1109/ICSME46990.2020.00045),
  [doi:10.1109/ICSE43902.2021.00086](https://doi.org/10.1109/ICSE43902.2021.00086),
  [doi:10.1145/3530786](https://doi.org/10.1145/3530786).
- repo-L: `docs/research/diff-mutation-and-matcher-corpus.md`, `<app>/dev/mutants.py` (outline),
  `<app>/dev/weakening.py` (header and `KINDS`), agent prompts that call `dev mutants` (read-only).
- H: [devkit/weaken.py](../../../../devkit/weaken.py), [tests/test_scrub.py](../../../../tests/test_scrub.py),
  `git log` of `bd9abb9`.
