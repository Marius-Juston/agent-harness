# r1: empirical statistics on test suites by project size

Search coverage: `uv run dev lit status` on 2026-10-12 showed openalex, crossref, europepmc and arxiv up and s2 down (HTTP 429). The arxiv MCP timed out twice, so arXiv papers were fetched as PDFs from arxiv.org. Full texts were read for Yuan 2014, Luo 2014, Kochhar 2013 (QSIC), Labuschagne 2017, Gruber 2021, Memon 2017, Inozemtseva 2014, the Google SWE book ch. 11, three Google Testing Blog posts and the SQLite testing page. Everything else was read as an abstract only, and the finding says so.

Labels: VERIFIED = measured here (method given); SOURCED = cited and fetched; BELIEVED = judgment; ASSUMED = unchecked premise.

## Findings

### F1. Test count grows with project size, but test density falls (SOURCED)
- Kochhar et al. studied 20,817 GitHub projects of 500 LOC to 17 MLOC. 38.34% had no test files and 61.65% had at least one. Among projects with tests, 84.87% had fewer than 100, 10.7% had 100 to 500, fewer than 4.5% had more than 500, and 17 had more than 10,000 ([Kochhar et al., QSIC 2013](https://orbilu.uni.lu/bitstream/10993/15352/1/qsic-2013.pdf), full text).
- Projects with tests are larger: mean 107,096 LOC (median 3,549) against 5,605 (median 1,353) without. Test count correlates with LOC (Spearman ρ = 0.427). **Tests per LOC fall as LOC grows (ρ = −0.451)**. Test count correlates weakly with developer count (ρ = 0.207) and with bug count (ρ = 0.181, 1,081 projects) (same source).
- **Caveat:** Kochhar's "test case" is a file whose name contains "test", not a test function (same source, §III). The counts are therefore test-file counts, and the absolute numbers understate test functions.
- Python baseline: Gruber et al. sampled 22,352 PyPI projects that run under pytest, with 876,186 tests and about 62 MLOC. The median project has **3 tests** (mean 39.2) and **682 LOC** (mean 2,791.9). Mean tests per project rise with maturity: Pre-Alpha 14.5, Alpha 22.5, Beta 53.9, Production/Stable 79.5 ([Gruber et al., ICST 2021](https://arxiv.org/pdf/2101.09077), Table V). The aggregate is about **14.1 tests per KLOC**, computed from those totals (VERIFIED, `876186/62e6*1000`; it includes many barely tested projects).

### F2. Test code is commonly 10% to 25% of a codebase; mature critical projects go far beyond (SOURCED, VERIFIED)
- Labuschagne et al. studied 61 Java projects on Travis CI over two years. Test code grew from **10.8% to 21.9%** of all SLOC (2.94 MLOC test against 10.48 MLOC production, a ratio of 0.28). Test SLOC grew 267.8% while production SLOC grew 59.1% ([Labuschagne et al., ESEC/FSE 2017](https://www.cs.ubc.ca/~rtholmes/papers/fse_2017_labuschange.pdf), Table 1 and Fig. 1). In other words, the test share rises as a project matures.
- The upper extreme is SQLite: about 155.8 KSLOC of library code and **590 times as much** test code and scripts (92,053 KSLOC), with 100% branch coverage and 51,445 distinct TCL test cases, many of them parameterised ([SQLite, "How SQLite Is Tested"](https://www.sqlite.org/testing.html), as of 3.42.0). This is an outlier, not a target (BELIEVED).
- Sibling repos (VERIFIED, Python only, measured 2026-10-12). Test files are paths under `tests?/` or named `test_*.py`, `*_test.py` or `conftest.py`. SLOC counts non-blank, non-`#` lines from `git ls-files '*.py'`, and test functions are matched by `def test_…(`. These are function counts, so parametrisation makes the collected count higher. Here 466 functions collect as 791 tests (see `docs/research/test-floors-and-coverage.md`).

  | repo (HEAD) | prod SLOC | test SLOC | test/prod | test fns | fns per prod KLOC |
  |---|---|---|---|---|---|
  | agent-harness (bd9abb9) | 6,085 | 5,506 | 0.90 | 466 | 76.6 |
  | repo-R (7e5c305) | 33,640 | 4,596 | 0.14 | 274 | 8.1 |
  | repo-L (086fb686) | 167,739 | 88,912 | 0.53 | 3,656 | 21.8 |
  | repo-E (3748c23) | 3,542 (research scripts only) | 4 | 0.00 | 1 | 0.3 |

  repo-R's production count includes `experiments/` and `sim/`, which lowers its density. repo-E has no product code yet.

### F3. Which test kinds the projects actually have (SOURCED, abstracts)
- Trautsch and Grabowski studied 10 Python projects over 70K revisions. Developers believe they write more unit tests than they do, and most projects have very few true unit tests ([ICST 2017](https://doi.org/10.1109/icst.2017.26), abstract).
- Trautsch's thesis covered 27 Java and Python projects with more than 49,000 tests. Under the IEEE and ISTQB definitions, **most projects have more integration tests than unit tests**. Under the developers' own labels, the two counts do not differ significantly. The defect types the two kinds detect also do not differ ([Trautsch thesis, Göttingen](https://www.swe.informatik.uni-goettingen.de/node/2889), abstract).
- Trautsch, Herbold and Grabowski classified 38,782 Java test cases and compared them with mutation testing over five defect types. They found **no evidence that unit or integration tests are better at detecting any defect type** ([JSS 2020](https://www.swe.informatik.uni-goettingen.de/node/2878), abstract). Caveat: the faults were mutants, not real escaped bugs (BELIEVED).
- **Google's ratios are guidance, not measurements.** In 2015 the Google Testing Blog suggested 70% unit, 20% integration and 10% end-to-end "as a good first guess" ([Wacker, "Just Say No to More End-to-End Tests"](https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html)). The SWE book gives "around 80%" unit, 15% integration and 5% end-to-end "as a very rough guideline". It sizes tests by resources: small runs in one process with no I/O or sleep, medium on one machine with localhost network only, and large anywhere ([Software Engineering at Google, ch. 11](https://abseil.io/resources/swe-book/html/ch11.html)). No study found here measures how the unit, integration and E2E mix changes with project size (gap G1).

### F4. Which tests would have caught production failures (SOURCED, full text)
- Yuan et al. studied 198 randomly sampled user-reported failures from Cassandra, HBase, HDFS, MapReduce and Redis, 48 of them catastrophic ([OSDI 2014](https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf)):
  - **77% (153/198) could be reproduced by a unit test.** The rate is 58% for Redis, whose test framework cannot inject node failures or call internals (Table 8).
  - **98% manifest on 3 or fewer nodes.** 77% need more than one input event, but 90% need 3 or fewer, and their order matters (Findings 1 and 3). That argues for small multi-step scenario tests, not full-scale E2E environments (BELIEVED).
  - **92% of catastrophic failures came from incorrect handling of non-fatal errors that the software signalled explicitly.** In 58%, simple testing of the error-handling code would have caught the fault. In 35%, the handler was empty or only logged, aborted on an overly general exception, or carried a TODO or FIXME. In another 23%, "any statement coverage testing" of the handler would have caught it.
- Implication (BELIEVED): for a growing repo, error paths deserve explicit tests and a static rule (empty `except`, bare `except Exception`, TODO in a handler) before the repo adds E2E breadth.

### F5. What CI test failures mean (SOURCED, full text)
- Labuschagne et al. (61 projects): **18% of test-suite executions failed, and 13% of those failures were flaky.** Of the non-flaky failures, 74% came from a bug in the system under test and **26% from incorrect or obsolete tests**, which is maintenance cost. In failed builds, only 0.38% of test-case executions failed ([ESEC/FSE 2017](https://www.cs.ubc.ca/~rtholmes/papers/fse_2017_labuschange.pdf)). Derived: about 2.3% of all executions are flaky failures, and about 4.1% are failures from bad tests (VERIFIED arithmetic).
- At Google's scale, Memon et al. looked at 5,562,881 test targets over one month (Feb–Mar 2016). **91.3% never failed.** Only **1.23%** ever caught a real breakage or fix, and 46,694 were flaky. The ratio of passed to failed targets per change was 99:1. **Targets more than 10 dependency edges from the changed code "hardly ever break."** TAP ran about 150M test runs a day over a codebase of about 2 billion LOC ([Memon et al., ICSE-SEIP 2017](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/45861.pdf)). Large suites are therefore mostly redundant for any one change, which is the case for dependency-based test selection (BELIEVED).
- Beller et al. analysed 2,640,825 Java and Ruby Travis builds. Testing is "the single most important reason why builds fail", and multiple integration environments catch 10% more failures ([MSR 2017](https://doi.org/10.1109/msr.2017.62), abstract).

### F6. Flaky tests: prevalence and causes (SOURCED)
- **Open source, Java:** Luo et al. studied 201 commits that fix flaky tests in 51 Apache projects; 161 had a known cause. The causes were **async wait 45%, concurrency 20% and test-order dependency 12%**. **78% of flaky tests were flaky when first written.** 24% of fixes changed the code under test, and 94% of those fixed a real bug in it ([Luo et al., FSE 2014](https://mir.cs.illinois.edu/lamyaa/publications/fse14.pdf), full text). The same paper quotes Google TAP: 73K of 1.6M daily test failures (4.56%) were flaky.
- **Open source, Python:** Gruber et al. found 7,571 flaky tests out of 876,186 (**0.86%**) in 1,006 of 22,352 projects (**4.5%**). The causes were **order dependency 59%**, test infrastructure 28%, and mostly network and randomness APIs for the remaining 13%. Stable projects were flakier (5.3%) than pre-alpha ones (3.4%) because they test more. **A 95% confidence that a passing test is not flaky needs about 170 reruns** ([ICST 2021](https://arxiv.org/pdf/2101.09077), full text).
- **Google:** Micco (2016) reports that about **1.5% of all test runs** give a flaky result and **almost 16% of tests** have some flakiness. **About 84% of pass-to-fail transitions in post-submit involve a flaky test.** A project averages about 1,000 tests, so about 15 tests fail on each release run ([Google Testing Blog, 2016-05-27](https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html)). Listfield (2017) counts 63K of 4.2M tests flaky in a week. Over a week, **0.5% of small, 1.6% of medium and 14% of large tests were flaky**. Flakiness rises linearly with binary size (r² = 0.82) and RAM use (r² = 0.76), and **size predicts it better than the tool does**. The flaky shares by tool were Java WebDriver 10.45%, Python WebDriver 18.72%, an internal integration tool 14.94% and the Android emulator 25.46% ([Google Testing Blog, 2017-04-17](https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html)). The SWE book says tests "begin to lose value" as flakiness nears 1%, and puts Google's rate at about 0.15%. It does not define that metric, so the figure is not comparable with the 1.5% of runs ([SWE book ch. 11](https://abseil.io/resources/swe-book/html/ch11.html)).
- **Microsoft:** Lam et al. studied six large proprietary projects and found asynchronous calls the leading cause, as in open source ([ICSE 2020, MSR page](https://www.microsoft.com/en-us/research/?p=671544), abstract only, no rates obtained; gap G3).
- **Suite-level arithmetic (VERIFIED, `1-(1-p)**N`, assuming independent tests).** Let p be each test's chance of failing spuriously on one run. The chance that a run of N tests is red for no reason is:

  | p per test-run | N=100 | N=500 | N=1,000 | N=5,000 |
  |---|---|---|---|---|
  | 0.01% | 1% | 5% | 10% | 39% |
  | 0.15% | 14% | 53% | 78% | ~100% |
  | 1.5% | 78% | ~100% | 100% | 100% |

  To keep spurious red runs at or below 5%, p must be ≤ 5.1e-4 at N=100, ≤ 5.1e-5 at N=1,000 and ≤ 5.1e-6 at N=10,000. The allowed per-test flake rate falls tenfold for each tenfold growth in the suite. That is why flakiness becomes the binding constraint at medium-to-large size (BELIEVED).

### F7. End-to-end tests: cost and value (SOURCED; value data is thin)
- **Cost: flakiness.** Large tests are about 28 times as flaky as small ones (14% against 0.5%, F6), and browser and emulator tools sit at 10% to 25% flaky (Listfield 2017).
- **Cost: maintenance.** Hammoudi, Rothermel and Tonella recorded **1,065 breakages across 453 versions** of popular web applications in record/replay tests ([ICST 2016](https://doi.org/10.1109/icst.2016.16), abstract). Christophe et al. studied how common Selenium tests are and how they are maintained, but only the abstract was read and it gives no numbers ([ICSME 2014](https://doi.org/10.1109/icsme.2014.36)). The 26% of non-flaky CI failures from obsolete tests in F5 applies to all test kinds.
- **Value:** no study read here measures the escaped-defect yield of E2E tests against unit or integration tests on real faults. Yuan's 77% and 98% figures (F4) and Trautsch's "no difference in defect types" (F3) suggest most defects need no full-system test. The 23% that are not unit-reproducible, and multi-component flows, are where E2E tests earn their cost (BELIEVED). Google's GWS team required tests with every change in about 2005, and within a year emergency pushes "dropped by half" (SWE book ch. 11; an anecdote, not a controlled study).

### F8. Effectiveness against suite size (SOURCED)
- **Size dominates.** Inozemtseva and Holmes built 31,000 suites over five Java systems. Effectiveness correlated with the number of test methods from moderate to very high: adjusted r² 0.26 to 0.97, so r 0.51 to 0.98. **Coverage correlates only low to moderately once size is controlled** ([ICSE 2014](https://www.cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf), §4.1, full text; r values VERIFIED as √r²).
- Namin and Andrews found coverage "sometimes" correlated with effectiveness when size is controlled, that size and coverage together predict better than size alone, and that the relation is nonlinear ([ISSTA 2009](https://doi.org/10.1145/1572272.1572280), abstract).
- Zhang and Mesbah built 6,700 suites from 24,000 assertions in five Java projects. **The number of assertions correlates strongly with effectiveness** and drives the size effect ([ESEC/FSE 2015](https://doi.org/10.1145/2786805.2786858), abstract). Counting tests without assertions overstates a suite (BELIEVED).
- Papadakis et al. used CoreBench and Defects4J. Correlations between mutation score and real-fault detection are **weak once suite size is controlled**. Still, the top-ranked suites by mutation score detect significantly more real faults than random suites of the same size ([ICSE 2018](https://doi.org/10.1145/3180155.3180183), abstract).
- Chen et al. argue that suite size is neither a confounder nor a variable to manipulate directly, and give a method for comparing criteria at equal size ([ASE 2020](https://doi.org/10.1145/3324884.3416667), abstract). The literature has not settled how to control for size (BELIEVED).

## Size tiers (BELIEVED synthesis; the numbers come from F1–F8)

The tiers are based on production SLOC, which is measurable and matches how Kochhar and Gruber bin projects. The boundaries are judgment. The data mark the median OSS project at under 5 KLOC: Gruber's median is 682 LOC, and in Kochhar about 67% of projects are 500 to 5,000 LOC. Kochhar's top 23% are above 10 KLOC.

| | Small (< 5 KLOC prod) | Medium (5–50 KLOC) | Large (> 50 KLOC) |
|---|---|---|---|
| Typical OSS reality | median Python project 3 tests (F1). Most such projects have < 100 test files | stable PyPI projects average ~80 tests. 100–500 test files is the 10.7% band (F1) | > 500 test files is the top 4.5%. Test SLOC share 10–25% and rising (F2) |
| Density seen in practice | 14 tests/KLOC aggregate (Gruber). agent-harness 77/KLOC | repo-R 8/KLOC, test/prod 0.14 | repo-L 22/KLOC, test/prod 0.53. Industrial Java test/prod 0.28 (F2) |
| Suggested floor (BELIEVED) | ≥ 1 test per public function with logic, every error path tested (F4). test/prod ≥ 0.3 | test/prod 0.3–0.8. A test-count floor (existing note) | test/prod ≥ 0.5. Assertions per test tracked (F8) |
| Mix (guidance, F3) | almost all small or unit. 0–3 smoke or E2E tests over the main entry point | about 80/15/5. A handful (≤ 10) of E2E tests on critical user journeys | about 80/15/5 by count. E2E kept to critical paths and run on a schedule or before merge, not on every push |
| Flake budget per test-run for ≤ 5% spurious red (F6) | ≤ 5e-4 (N ≈ 100) | ≤ 5e-5 (N ≈ 1,000) | ≤ 5e-6 (N ≈ 10,000). Needs quarantine and automatic reruns, as Google does |
| Flake detection | rerun new tests a few times. 170 reruns for 95% confidence (Gruber) is too costly for every change | run new or changed tests in random order and N-times (order dependency is 59% of Python flakes) | track flakiness continuously. Large tests are ~28× as flaky as small (F6) |
| Selection | run everything (seconds) | run everything on merge. Affected tests on push | dependency-based selection. Distance > 10 hardly ever breaks (F5) |
| Effectiveness check | coverage floor only (existing note) | add mutation testing on changed modules, report-only | mutation sampling. Coverage is not a target (F8) |

## Gaps seen, not closed
- **G1.** No measured data on how the unit, integration and E2E mix changes with project size. Only Google's guidance ratios and Trautsch's "integration outnumbers unit" in 27 projects were found.
- **G2.** No study of E2E tests' real-fault yield per unit of cost. Value is inferred from Yuan and Trautsch, and cost from flakiness and breakage counts.
- **G3.** Microsoft flaky-test rates (Lam et al. ISSTA 2019 and ICSE 2020) and the Apple flaky ranking paper (ICSE-SEIP 2020) were not read in full. The author PDF link redirected to an HTML page.
- **G4.** Kochhar counts test files, not test functions. No large cross-project study of test functions per KLOC by size bin was found apart from Gruber's PyPI totals.
- **G5.** Several findings come from abstracts only: Trautsch JSS 2020 and thesis, Zhang and Mesbah, Papadakis, Namin and Andrews, Chen, Beller, Hammoudi and Christophe. Their effect sizes and threats to validity were not checked.
- **G6.** Just et al. FSE 2014 (mutants as a substitute for real faults) was found, but its abstract was truncated. The note does not use it.
- **G7.** The sibling-repo ratios count `def test_` functions, not collected tests. They are a one-day snapshot.

## Sources fetched (2026-10-12)
- Yuan et al., OSDI 2014, full PDF: https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf
- Luo et al., FSE 2014, full PDF: https://mir.cs.illinois.edu/lamyaa/publications/fse14.pdf
- Kochhar et al., QSIC 2013, full PDF: https://orbilu.uni.lu/bitstream/10993/15352/1/qsic-2013.pdf
- Labuschagne, Inozemtseva and Holmes, ESEC/FSE 2017, full PDF: https://www.cs.ubc.ca/~rtholmes/papers/fse_2017_labuschange.pdf
- Gruber, Lukasczyk, Kroiß and Fraser, ICST 2021, full PDF: https://arxiv.org/pdf/2101.09077
- Memon et al., ICSE-SEIP 2017, full PDF: https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/45861.pdf
- Inozemtseva and Holmes, ICSE 2014, full PDF: https://www.cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf
- Micco, Google Testing Blog 2016: https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
- Listfield, Google Testing Blog 2017: https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html
- Wacker, Google Testing Blog 2015: https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html
- Software Engineering at Google, ch. 11: https://abseil.io/resources/swe-book/html/ch11.html
- SQLite testing page: https://www.sqlite.org/testing.html
- Abstracts via `uv run dev lit get`: 10.1109/icst.2017.26, 10.1145/1572272.1572280, 10.1145/2786805.2786858, 10.1145/3180155.3180183, 10.1145/3324884.3416667, 10.1109/msr.2017.62, 10.1109/icst.2016.16, 10.1109/icsme.2014.36
- Trautsch pages, abstracts: https://www.swe.informatik.uni-goettingen.de/node/2878, https://www.swe.informatik.uni-goettingen.de/node/2889
- Lam et al. ICSE 2020, MSR landing page: https://www.microsoft.com/en-us/research/?p=671544
