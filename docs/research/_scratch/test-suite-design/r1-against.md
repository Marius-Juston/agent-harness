r1-against: devil's advocate against "a pyramid of many unit tests plus a few E2E, with coverage floors"

Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc and arxiv OK; s2 DOWN (rate limited, skipped). arXiv MCP was rate limited or timed out, so I used `lit get` and author-hosted PDFs instead. Full texts were read for Yuan 2014, Alquraan 2018, Memon 2017, Machalica 2019, Labuschagne 2017, Shi 2014, Shi 2018, Inozemtseva 2014 and Timperley 2018. The rest are abstracts or web pages.

## Verdict
The evidence does not support a blanket rule of "more unit tests plus a coverage floor". It does support three narrower claims:
1. Tests have a measurable carrying cost.
2. Some defect classes appear only at integration or system level.
3. Deleting tests is safe only for obsolete tests or when guided by evidence. Deleting by coverage-redundancy loses real faults.

"Fewer tests" is better when the extra tests are change-detectors, flaky, obsolete or slow enough to push feedback past about 10 minutes. It is not better when the tests are merely redundant by coverage, because redundancy by coverage does not mean redundancy against future real faults (finding 9). In short, the case is for better-placed tests, not for fewer by default.

## Findings

### A. Tests have a real, measured carrying cost
1. **Over a third of failing CI runs are not caused by a bug.** SOURCED: [Labuschagne, Inozemtseva & Holmes, ESEC/FSE 2017](https://www.cs.ubc.ca/~rtholmes/papers/fse_2017_labuschange.pdf) ([doi](https://doi.org/10.1145/3106237.3106288)) studied 61 Travis CI Java projects.
   - 18% of test-suite executions fail, and 13% of those failures are flaky.
   - Of the non-flaky failures, only 74% were caused by a bug in the system under test. The other 26% were "incorrect or obsolete tests".
   - Over 99% of test-case executions could have been skipped with a perfect oracle.

   VERIFIED (`check_r1_against.py`, below): this gives about 0.55 false failures per real-bug failure, and 36% of failing runs are maintenance cost rather than benefit. This assumes each failing run has a single cause, as the paper classifies them.
2. **Most tests never fail, even at Google.** SOURCED: [Memon et al., ICSE-SEIP 2017](https://research.google.com/pubs/archive/45861.pdf) ([doi](https://doi.org/10.1109/icse-seip.2017.16)), Table II.
   - Of 5,562,881 test targets, 5,082,803 passed at least once and never failed.
   - Only 68,466 had real pass/fail transitions after flakes were removed. Another 46,694 of 115,160 targets that both passed and failed were flaky.
   - The tests that do fail are "closer" to the code they test.
   - Google is "unable to regression test each code change individually", which raises lag before feedback.

   VERIFIED arithmetic: 91.4% of targets never failed, and 1.23% detected a real breakage or fix.
3. **Flakiness grows with suite size and erodes trust.** SOURCED: [Micco, Google Testing Blog, 2016](https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html).
   - About 1.5% of all test runs report a flaky result.
   - "Almost 16% of our tests have some level of flakiness."
   - About 84% of observed pass-to-fail transitions involve a flaky test.
   - Flakes are inserted at about the rate they are fixed.
   - People learn to ignore legitimate failures.

   VERIFIED (script): a 1,000-test release gate at 1.5% independent flaky runs expects 15 spurious failures. The chance that all tests pass is 2.7e-7. This is why each added test that can flake has a cost that compounds with suite size. BELIEVED: the independence assumption overstates the effect for correlated flakes.
4. **Mock-heavy tests couple tests to the implementation.**
   - SOURCED: [Spadini et al., EMSE 2018](https://doi.org/10.1007/s10664-018-9663-0) manually analysed more than 2,000 mock usages in 4 systems and surveyed more than 100 developers. Developers say keeping a mock's behaviour in sync with the real class is hard and that mocking raises test-production coupling. The repository data confirm it: mocks persist for the whole life of a test class, and production changes often force test changes.
   - SOURCED: Google's [SWE book ch. 13 "Test Doubles"](https://abseil.io/resources/swe-book/html/ch13.html) reports that mock-heavy tests "required constant effort to maintain while rarely finding bugs". Overuse "can easily result in brittle tests". It also notes that a widely used API "has been mocked thousands or even tens of thousands of times", which makes API changes expensive.
5. **Change-detector tests have negative value.** SOURCED: [Eagle, Google TotT, 2015](https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html). A test that verifies call order on mocks "breaks in response to any change to the production code, without verifying correct behavior". Such tests "provide negative value … These tests should be re-written or deleted."
   - SOURCED: [SWE book ch. 12 "Unit Testing"](https://abseil.io/resources/swe-book/html/ch12.html) defines a brittle test as one that "fails in the face of an unrelated change to production code that does not introduce any real bugs". Its prescription is to test via public APIs and to test state, not interactions.
6. **Coding agents over-mock.** SOURCED: [arXiv 2602.00409 (2026)](https://arxiv.org/abs/2602.00409) studied 1.2M commits from 2025 in 2,168 TS/JS/Python repositories.
   - 36% of coding-agent commits add mocks to tests, against 26% for non-agent commits.
   - 23% of agent commits touch test files, against 13% for non-agents.
   - The authors call for mocking guidance in agent configuration files.

   BELIEVED: this is the most directly relevant cost signal for an agent-run repository. A count floor would reward exactly this behaviour. The source is a preprint and was not peer reviewed when fetched.
7. **Coverage is a poor target, so a coverage floor buys less than it seems.** SOURCED: [Inozemtseva & Holmes, ICSE 2014](https://cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf) generated 31,000 suites for 5 Java systems of up to 724k LOC. Once suite size is controlled, the correlation between coverage and mutant detection is "low to moderate". Stronger coverage types add no insight, and coverage "should not be used as a quality target".
   - Contrary evidence: [Zhang & Mesbah, FSE 2015](https://doi.org/10.1145/2786805.2786858) found that assertion count and assertion coverage correlate strongly with effectiveness, and that suite size matters partly through assertions. More tests with real assertions do help.
8. **Slow suites delay feedback.** SOURCED: [DORA "Test automation"](https://dora.dev/capabilities/test-automation/) says developers "should be able to get feedback from automated tests in less than ten minutes". It also says: "If you have a test suite that is expensive to maintain and unreliable, don't be afraid to prune it down … A test suite of ten tests that is reliable, fast, and trustworthy is much better than a test suite of hundreds". SOURCED: [SWE book ch. 14](https://abseil.io/resources/swe-book/html/ch14.html) says "Engineers don't wait for slow tests."
   - Gap: I found no primary study that measures a causal effect of suite duration on deploy frequency. [Ghaleb et al., EMSE 2019](https://doi.org/10.1007/s10664-019-09695-9) documents builds that run far over the 10-minute norm, but I fetched only its metadata, not its results.

### B. Deleting tests: what the evidence says
9. **Coverage-guided reduction loses real faults.**
   - SOURCED: [Rothermel et al., ICSM 1998](https://doi.org/10.1109/icsm.1998.738487) reversed an earlier optimistic result: "fault detection capabilities of test suites can be severely compromised by minimization".
   - SOURCED: [Shi et al., FSE 2014](https://mir.cs.illinois.edu/gyori/pubs/fse14reduction.pdf) studied 18 projects with 261,235 tests. Statement-coverage reduction cuts suite size by 62.9% on average but loses up to 20.5% of killed mutants. Reduction based on mutants has no mutant loss and keeps 11.9 percentage points more tests.
   - SOURCED: [Shi et al., ISSTA 2018](https://mir.cs.illinois.edu/gyori/pubs/issta18.pdf) studied 1,478 real failed builds in 32 projects. Reduced suites missed all of a build's faults in up to 52.2% of failed builds (FBDL), even though traditional metrics showed only 2.7% mutant loss. Those traditional metrics predict FBDL poorly.
   - In the same study, an oracle that knows future failures could keep about 20% of tests and miss nothing. So most tests are dead weight, but no metric tells you which ones in advance.
10. **What developers actually delete is mostly obsolete tests.** SOURCED: [Bhatta, Kendemah & Jha, MSR 2025](https://doi.org/10.1109/msr66628.2025.00071) built a dataset of 24,431 confirmed deleted tests from 449,592 commits in 7 Java projects.
    - 91.4% of deleted tests were obsolete because the production code was deleted. 7% were redundant and 1.6% were failing.
    - Deleting 20% of the "redundant" tests reduced coverage or mutation score, so they were not truly redundant.
    - Deletions are small, one or two tests per commit.
11. **Google's practice is to run tests less often, not to delete them by rule.**
    - SOURCED: [Memon 2017](https://research.google.com/pubs/archive/45861.pdf) proposes re-running "tests that almost never FAIL" less frequently.
    - SOURCED: [Machalica et al. (Facebook), ICSE-SEIP 2019](https://arxiv.org/abs/1810.05286) describe predictive test selection that picks fewer than a third of the dependency-selected tests. It still catches more than 95% of individual test failures and more than 99.9% of faulty changes, at half the infrastructure cost.
    - The only Google sources found that prescribe deletion target change-detector tests (finding 5). The Google practice of "test deletion" named in the question was not found as a documented policy. Selection beats deletion because the tests stay available for post-submit and release runs.

### C. Projects that need more integration, system or E2E tests than a pyramid suggests
12. **Distributed systems: catastrophic failures sit in error handling and partitions.**
    - SOURCED: [Yuan et al., OSDI 2014](https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf) studied 198 sampled user-reported failures in Cassandra, HBase, HDFS, MapReduce and Redis. 92% of catastrophic failures came from incorrect handling of non-fatal errors that the software signalled explicitly. In 58% of them, "simple testing of error handling code" would have caught the fault.
    - In the same study, 77% of failures need more than one input event, and "almost all" manifest on no more than three nodes.
    - Contrary evidence, which the devil's advocate must concede: the same paper finds that 77% of failures can be reproduced by a unit test. Its example unit test, however, starts a 3-node mini-cluster (`startMiniCluster(3)`), so its "unit test" is what most pyramids call an integration test.
    - SOURCED: [Alquraan et al., OSDI 2018](https://www.usenix.org/system/files/osdi18-alquraan.pdf) studied 136 network-partition failures in 25 systems. About 80% were catastrophic and 88% could be triggered by isolating a single node.
    - The same study reports that developers "used mocking … to test the impact of network partitioning on only one component and on just one side of the partition. However, this approach is inadequate for end-to-end testing". Their NEAT fault-injection framework found 32 new failures in 7 systems.
13. **Robotics and simulation: system-level simulation tests catch most bugs.** SOURCED: [Timperley et al., ICST 2018](https://www.clairelegoues.com/assets/papers/TimperleyCrashing2018.pdf) studied 228 ArduPilot bugs.
    - Most can be reproduced in software-in-the-loop simulation, and only about 10% depend on environmental factors.
    - 81 of 228 bugs depend on a specific configuration, and many need continuous radio-control input.
    - VERIFIED: that is 35.5% config-dependent and 9.6% environment-dependent. These are system-level triggers that unit tests rarely exercise.
    - SOURCED: [Afzal et al., ICST 2020](https://doi.org/10.1109/icst46399.2020.00020) interviewed robotics practitioners and names "Component integration" as one of three major testing-challenge themes.
14. **Microservices: the honeycomb and the trophy are practitioner arguments against the pyramid.**
    - SOURCED: [Spotify Engineering, 2018](https://engineering.atspotify.com/2018/01/testing-of-microservices) says the pyramid "can be actively harmful" for microservices. Too many unit tests "restricts how we can change the code". Their honeycomb is mostly integration tests, few implementation-detail tests, and ideally no integrated tests.
    - SOURCED: [Dodds, 2019](https://kentcdodds.com/blog/write-tests) gives "Write tests. Not too many. Mostly integration." Note that his "diminishing returns above 70% coverage" figure is, in his words, "I made that number up".
    - SOURCED: [Fowler, 2021](https://martinfowler.com/articles/2021-test-shapes.html) argues that the shape debate is "moot". It turns on whether "unit" means sociable or solitary. What matters is tests that "run quickly & reliably, and only fail for useful reasons".
    - BELIEVED: these are opinions, not measurements. I found no controlled comparison of the shapes.
15. **LLM and agent applications: evals, not unit asserts, are the main test.**
    - SOURCED: [Anthropic, "Demystifying evals for AI agents", Jan 2026](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) says outputs vary between runs, so measure pass@k or pass^k over trials. Checking a fixed tool-call sequence is "too rigid and results in overly brittle tests"; "grade what the agent produced, not the path it took". "20-50 simple tasks drawn from real failures is a great start."
    - SOURCED: the [Tangent study (ASE 2026)](https://doi.org/10.1145/3832783.3837414) labelled 2,572 test methods in 240 modules and interviewed 10 practitioners. Testing of LLM-agent apps is "dominated by narrowly scoped unit tests" with "heavy mocking, and shallow validation" and limited coverage of complex interactions. This is a pyramid applied where it misses the main risk.
    - SOURCED: [Shankar et al. 2024](https://arxiv.org/abs/2404.12272) documents "criteria drift": eval criteria cannot be fully fixed in advance. Eval suites need ongoing human calibration, which unit-test thinking does not budget for.
    - SOURCED: [CheckList, ACL 2020](https://arxiv.org/abs/2005.04118) is behavioural (capability) testing. It found new bugs in "an extensively tested" commercial model, and users with it found almost three times as many bugs.

## When fewer tests are better (conclusion, BELIEVED, built on the findings above)
- **Delete:**
  - Change-detector and interaction-only mock tests whose assertions restate the implementation (5, 4).
  - Obsolete tests for removed behaviour (10).
  - Persistently flaky tests that nobody owns and that cannot be fixed. Quarantine them first (3, 8).
- **Do not delete by coverage-redundancy.** It loses real-fault detection by unpredictable amounts, with FBDL up to 52% (9). To cut run time, use selection or prioritisation (11), or move slow tests to post-submit, rather than deleting them.
- **Shift the mix toward integration, system or eval tests** when the main risk sits between components:
  - Distributed protocols and error paths (12).
  - Configuration-dependent or simulated systems (13).
  - Thin services with little internal logic (14).
  - LLM or agent behaviour (15).
- **Gate on the right thing.** Use run time under about 10 minutes, zero tolerated flakes, and assertion or mutation quality (7, 8). Do not gate on raw test count or line coverage.
- **For an agent-run repository**, count floors and coverage floors reward over-mocked, assertion-light tests (6, 7). A review component should flag mock density and implementation-restating tests, not only missing tests.
- **Fair concession:** the pyramid's core claim, catch errors with the fastest test that can catch them, is well supported (DORA; Memon's finding that failing tests sit close to the code; Yuan's 77% unit-reproducible failures). The argument here is against volume floors and solitary-mock unit tests, not against fast tests.

## Gaps not closed
- No primary causal study links suite duration to deploy frequency or lead time. The DORA 10-minute target is guidance, and Ghaleb 2019's results were not read.
- No controlled comparison of pyramid, trophy and honeycomb on defect escape rates was found.
- No documented Google "test deletion" policy beyond the change-detector guidance. A fetch of the SWE book ch. 11 or ch. 23 might close this.
- Spadini 2018 gives no quantitative coupling effect size in its abstract. The full text was not read.
- Effect sizes for LLM-app eval suites against unit tests (defects caught per cost) were not found.
- I have no data on over-testing in the sibling repos. Another round should measure mock density and the share of change-detector tests there.

## Verification script
`check_r1_against.py` (now `scripts/check_r1_against.py`) was run with `python3 -I` on 2026-10-12. It uses only the sources' published numbers.
```
expected flaky failures per 1000-test release: 15.0
P(all 1000 pass | 1.5% flaky runs, independent): 2.73e-07
share of suite runs: flaky-fail 0.023, broken/obsolete-test fail 0.041, real-bug fail 0.116
false failures per real-bug failure: 0.55
share of failing runs not caused by a bug: 0.36
Google: never failed 0.914; real-edge targets 0.0123
ArduPilot: config-dependent 0.355, environment-dependent 0.096, normal-op 0.807
```

## Sources fetched
- Labuschagne, Inozemtseva, Holmes. ESEC/FSE 2017. https://www.cs.ubc.ca/~rtholmes/papers/fse_2017_labuschange.pdf
- Memon et al. ICSE-SEIP 2017. https://research.google.com/pubs/archive/45861.pdf
- Machalica et al. ICSE-SEIP 2019. https://arxiv.org/abs/1810.05286
- Micco. Google Testing Blog, 2016. https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
- Eagle. Google TotT, 2015. https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html
- Software Engineering at Google, ch. 12, 13 and 14. https://abseil.io/resources/swe-book/html/ch12.html, https://abseil.io/resources/swe-book/html/ch13.html, https://abseil.io/resources/swe-book/html/ch14.html
- Spadini et al. EMSE 2018 (abstract). https://doi.org/10.1007/s10664-018-9663-0
- Over-mocking by coding agents. arXiv 2602.00409 (abstract). https://arxiv.org/abs/2602.00409
- Inozemtseva and Holmes. ICSE 2014. https://cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf
- Zhang and Mesbah. FSE 2015 (abstract). https://doi.org/10.1145/2786805.2786858
- DORA, Test automation. https://dora.dev/capabilities/test-automation/
- Ghaleb et al. EMSE 2019 (metadata only). https://doi.org/10.1007/s10664-019-09695-9
- Rothermel et al. ICSM 1998 (abstract). https://doi.org/10.1109/icsm.1998.738487
- Shi et al. FSE 2014. https://mir.cs.illinois.edu/gyori/pubs/fse14reduction.pdf
- Shi et al. ISSTA 2018. https://mir.cs.illinois.edu/gyori/pubs/issta18.pdf
- Bhatta, Kendemah, Jha. MSR 2025 (abstract). https://doi.org/10.1109/msr66628.2025.00071
- Yuan et al. OSDI 2014. https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf
- Alquraan et al. OSDI 2018. https://www.usenix.org/system/files/osdi18-alquraan.pdf
- Timperley et al. ICST 2018. https://www.clairelegoues.com/assets/papers/TimperleyCrashing2018.pdf
- Afzal et al. ICST 2020 (abstract). https://doi.org/10.1109/icst46399.2020.00020
- Spotify Engineering, 2018. https://engineering.atspotify.com/2018/01/testing-of-microservices
- Dodds, 2019. https://kentcdodds.com/blog/write-tests
- Fowler, 2021. https://martinfowler.com/articles/2021-test-shapes.html
- Anthropic, Demystifying evals for AI agents, 2026. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Tangent. ASE 2026 (abstract). https://doi.org/10.1145/3832783.3837414
- Shankar et al. 2024 (abstract). https://arxiv.org/abs/2404.12272
- Ribeiro et al. CheckList, ACL 2020 (abstract). https://arxiv.org/abs/2005.04118
