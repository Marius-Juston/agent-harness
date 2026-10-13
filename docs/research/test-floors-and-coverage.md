# Research: test-count and coverage floors in the gate

Status: v1 (2026-10-12). Issue: #58.
Labels: SOURCED = cited and fetched; VERIFIED = measured in this repo (script named); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: one targeted paper plus tool documentation; `uv run dev lit status` on 2026-10-12 showed openalex, crossref, europepmc and arxiv up, s2 down (HTTP 429).
Companions: [agent-harness.md](agent-harness.md) (why the gate enforces rather than describes), [test-suite-design.md](test-suite-design.md) (which kinds of tests, E2E by project type, flakes and CI by project size).

## 0. Findings in one paragraph
A gate that ran 0 tests already fails here; the remaining hole is a test file or a whole directory silently dropping out, which a passing suite never shows. A **test-count floor** (`[tests] min_tests` in `.devkit/ratchets.toml`) closes it. **Coverage is a poor quality target** (low to moderate correlation with fault detection once suite size is controlled), so it is used only as a **ratchet floor** that catches a large drop, never as a goal to climb. **Diff coverage** (the share of changed executable lines a test runs) is printed for each change and is **report-only**: a hard per-change gate buys tests written to satisfy a number. Tools: coverage.py through pytest-cov (works with pytest-xdist) and diff-cover, all free and open source. Measured cost here: none beyond noise.

## 1. Question and scope
How should the gate guard against tests silently not running, and should coverage block a change? Out of scope: mutation testing (a better effectiveness measure, too slow for a per-change gate; BELIEVED).

## 2. Findings
- **Coverage as a target (SOURCED).** Inozemtseva and Holmes generated 31,000 test suites for five Java systems of up to 724,000 lines and measured statement, decision and modified-condition coverage against mutation-detection effectiveness. They found "a low to moderate correlation between coverage and effectiveness when the number of test cases in the suite is controlled for", that stronger coverage kinds add no insight, and that coverage is "useful for identifying under-tested parts of a program" but "should not be used as a quality target" ([Coverage is not strongly correlated with test suite effectiveness](https://doi.org/10.1145/2568225.2568271), ICSE 2014; abstract read from the authors' PDF).
- **Prior art in a sibling project (VERIFIED there, not here).** A consuming iOS project's CI spec (037, CI/CD) fails when the executed test count is 0 or below a `MIN_TESTS` floor of about 90% of the suite, keeps line coverage as a ratchet floor set at its introduction value, and reports patch coverage without gating it, because a hard patch gate on view code "buys junk tests".
- **Tools (SOURCED).** pytest-cov (MIT) reports the combined coverage of all pytest-xdist workers ([pytest-cov: distributed testing](https://pytest-cov.readthedocs.io/en/latest/xdist.html)). diff-cover (Apache-2.0) reads a coverage XML report and compares the branch with `--compare-branch` using `...` notation, i.e. changes since the branches diverged, plus staged and unstaged changes; `--fail-under` is optional, so without it the report never fails ([diff-cover README](https://github.com/Bachmann1234/diff_cover)). coverage.py's `sysmon` core is only the default from Python 3.14, and on 3.12 and 3.13 it lacks branch coverage ([coverage.py configuration](https://coverage.readthedocs.io/en/latest/config.html)).
- **Cost (VERIFIED).** This repository's default test run (791 tests, `-n 8`): 4.40 s without coverage, 4.38 s with `--cov=devkit` on the default (ctrace) core, so the gate does not set a core. Line coverage of `devkit/`: 77.6%. Measured with `uv run python -m pytest tests -n 8 -m "not slow and not live" --cov=devkit --cov-report=json:...` on 2026-10-12.

## 3. Recommendation for this project
- `[tests] min_tests`: about 95% of the default run's passed count, so dropping one test file fails while deleting a few obsolete tests does not. A floor only rises; lowering it needs an owner-approved `[[raise]]` (it loosens the ratchet like a budget raise).
- `[tests] min_coverage`: the measured line percentage rounded down, same ratchet rule. Absent means coverage is not measured at all, so a project without source code pays nothing.
- Diff coverage: printed by the tests stage, never gating. Revisit a gate only with evidence that it catches defects the floor misses.
- The gate prints a notice when the measured value has moved well past a floor, so floors follow the suite upward.

## Sources
Inline links above, all fetched 2026-10-12.

## Follow-ups
- [ ] Decide whether diff coverage should gate once there is data on how often it is low on changes that later broke. DONE WHEN a note section reports that data and a decision.
