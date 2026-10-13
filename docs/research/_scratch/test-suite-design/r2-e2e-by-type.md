r2: which end-to-end tests are necessary by project type, and does suite composition predict escaped defects (#112, round 2, gap 2)

Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc and arxiv OK; s2 DOWN (HTTP 429, skipped;
it did not matter for the searches below). arXiv MCP worked this round. Full text was read for Ghosh et al. SoCC 2022
(Microsoft PDF, extracted with pypdf). Everything else is an abstract, an author abstract page, official docs or a
secondary summary, and is marked as such. Sibling repos were read through `git` and file reads only, at the heads
round 1 measured (L 086fb686, R 7e5c305, H bd9abb9); nothing was run or changed in them.

## Answer first
- **No study links suite composition (the unit : integration : E2E mix) to escaped or production defects.** A third,
  targeted search found none. The adjacent evidence is mixed. Test metrics predict post-release defects weakly or not at
  all once size is controlled. The two kinds of evidence that do exist both point to the boundaries between components:
  what escapes in production incident studies, and what engineers say would have caught it. Scenario and system tests
  are named most often (A1 to A4).
- **Necessary E2E differs by project type, and the cheapest kind that covers the type's main escape class should be
  picked** (B, C). In short:
  - **CLI/library:** an installed-artifact smoke test plus golden output tests of the public surface.
  - **Web service with a DB:** the same tests on the production database engine, plus a migration test, an API
    contract test and 1-5 journey tests.
  - **LLM-agent app:** a stub-model pipeline E2E in the gate. Evals run as a scheduled pass-rate check, never as
    per-case asserts in the gate.
  - **Simulation/robotics:** seeded scenario tests in simulation, a regression scenario for each field bug, a
    configuration matrix, and a fresh-clone run.
- **Siblings (D):** L already has nearly every kind, but its PostgreSQL path and its E2E tiers are not enforced off the
  dev machine. R has scenario regression cases but no hermetic fresh-clone run. H lacks an installed-wheel smoke test
  and golden tests of the JSON its siblings consume. E needs only one smoke test now. It needs a stub-model E2E and an
  eval set once it has model-calling code.

## A. Suite composition against escaped/production defects (the gap, searched again)

A1. **Nothing measures shape against escapes.** I searched for test level against post-release defects, E2E escaped
defects and test-type effectiveness in industrial case studies (lit search, 2026-10-12). It returned no study that
varies or observes the unit/integration/E2E mix and measures field defects. Round 1 found the same (r1-theory-shapes F9,
r1-empirical-by-size G1/G2). The closest new item characterises *where* escapes occur, not which tests missed them:
across more than 14k pre- and post-release defects in C/C++ and Java OSS, post-release defects concentrate in "older,
frequently modified, and high-churn components" and need longer fixes. SOURCED (abstract):
[Cotroneo et al., arXiv 2604.26672, 2026](https://arxiv.org/abs/2604.26672). This is a preprint with no test-type
variable. BELIEVED implication: risk-based placement (churn × age) is better supported than shape-based placement.

A2. **Test-metric studies against field defects disagree, and the controlled ones say "limited".**
- **Mockus et al.:** two industrial projects. Higher coverage was associated with fewer field-reported problems after
  adjusting for pre-release changes, but "test effort increases exponentially with test coverage" while the reduction
  in field problems "increases linearly". SOURCED (abstract): [Mockus, Nagappan, Dinh-Trong, ESEM 2009](https://doi.org/10.1109/esem.2009.5315981).
- **Kochhar et al.:** 100 large Java OSS projects. "coverage has an insignificant correlation with the number of bugs
  that are found after the release of the software at the project level, and no such correlation at the file level."
  SOURCED (abstract on the [Microsoft Research page](https://www.microsoft.com/en-us/research/publication/code-coverage-and-post-release-defects-a-large-scale-study-on-open-source-projects/)):
  [Kochhar, Lo, Lawall, Nagappan, IEEE Trans. Reliability 2017](https://doi.org/10.1109/tr.2017.2727062).
- **Apache systems:** when other metrics such as class size are controlled, "test-related factors have a limited
  connection to post-release defects". SOURCED (abstract): [EMSE 2021, doi:10.1007/s10664-020-09891-y](https://doi.org/10.1007/s10664-020-09891-y).
- **Test smells:** 221 releases of 10 systems and more than 1M test cases. Smelly tests are more change- and
  defect-prone, and "production code is more defect-prone when tested by smelly tests". SOURCED (abstract):
  [Spadini et al., ICSME 2018](https://doi.org/10.1109/icsme.2018.00010).
- **Unit-test adoption (one team, before and after):** a 32-developer Microsoft team adopted automated unit tests and
  saw "a 20.9% decrease in test defects at a cost of approximately 30% more development time", plus a "relative
  decrease" in customer-found defects. SOURCED (abstract): [Williams, Kudrjavets, Nagappan, ISSRE 2009](https://doi.org/10.1109/issre.2009.32).
  It is one team with no control, so it measures adoption, not mix.

A3. **Production-incident studies show where escapes cluster: across component boundaries, in error handling, in
configuration and in flags.**
- **Microsoft Teams, 152 high-severity incidents** (SOURCED, full text: [Ghosh et al., SoCC 2022](https://doi.org/10.1145/3542929.3563482), [PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2022/09/3542929.3563482.pdf)):
  - Code bugs were split into code change or buggy feature (24.4%), flags and constants (24.4%), code dependency
    (19.5%), and further classes.
  - A flag bug "passed the testing due to inadequate scenario testing" (§3).
  - Postmortem automation suggestions (Fig. 7) were Unclear 32.2%, Manual Test 25.7%, None 15.1%, Auto Alert/Triage
    15.1%, Config Test 5.9% and Auto Deployment 5.9%.
  - Within the testing suggestions: scenario test 30.8%, system/"end-to-end" test 23.1%, performance 12.8%,
    validation 12.8%, unit 12.8% and integration 7.7%.
  - "70% of incidents with no monitors were root caused to code bugs ... we should improve testing rather than
    relying on monitoring" (Finding 11).
  - "47% of configuration bugs mitigated with a rollback", so they "can be identified by rigorous configuration
    testing" (Finding 13).
  - VERIFIED (`check_r2_e2e.py`): the six testing shares sum to 100.0, and scenario plus system make 53.9% against 12.8%
    for unit.
  - Caveat (BELIEVED): these are engineers' opinions in postmortems, not measured catch rates.
- **Azure, 112 software-bug incidents** (SOURCED, secondary summary only, [Colyer 2019](https://blog.acolyer.org/2019/06/21/what-bugs-cause-cloud-production-incidents/),
  of [Liu, Lu, Musuvathi, Nath, HotOS 2019](https://doi.org/10.1145/3317550.3321438); the PDF fetch failed on TLS):
  - Bugs were about 40% of the incidents.
  - Fault and error handling 31%, data format 21%, timing 13%, constant values 7%.
  - Data-format bugs were almost all cross-process: 40% were differing assumptions about shared files or tables, and
    60% were changed message interfaces.
  - VERIFIED (script): about 24 data-format incidents and about 280 incidents in total. Those are exactly what contract
    and schema tests target.
- These agree with round 1's distributed-systems studies (Yuan 2014: 92% of catastrophic failures were in
  error handling; Alquraan 2018: partitions; see r1-against §C).

A4. **Net (BELIEVED):** no test *shape* predicts escapes. The data is consistent with a narrower rule: a suite needs at
least one test that crosses each boundary where escapes cluster in its project type. Those boundaries are interfaces
and data formats, configuration and flags, error paths, the real database, the real model, and the real simulator.
Beyond that, fast tests win on cost (r1). This is the basis for section C.

## B. The four E2E kinds: what they catch and what they cost

| Kind | Catches | Cost and failure mode | Evidence |
|---|---|---|---|
| **Smoke** (build, install, start, one trivial path) | packaging, wiring, missing assets, startup config | seconds; low flake risk; misses logic | Memon & Xie: short GUI smoke tests "with certain test oracles are effective at detecting a large number of faults" and "execute a large percentage of the code", but "there are classes of faults that our smoke test cannot detect"; 5,000-8,000 smoke tests per app ran overnight. SOURCED (author abstract pages): [ICSM 2004](https://www.cs.umd.edu/~atif/pubs/MemonICSM2004-abstract.html), [TSE 2005](https://www.cs.umd.edu/~atif/pubs/MemonTSE2005-abstract.html) ([doi](https://doi.org/10.1109/tse.2005.117)). Packaging: PyConf found 183,864 PyPI library releases with potential configuration issues, 68% detectable only at source level. SOURCED (abstract): [Peng et al., ICSE 2024](https://arxiv.org/abs/2310.12598) ([doi](https://doi.org/10.1145/3597503.3639077)). |
| **Golden master / snapshot / characterization** (record the output, diff it later) | any unintended change in a serialised output; refactors and ports | cheap to write; maintenance is triage of intended against unintended diffs; the risk is blind re-recording | Jest: snapshots "should be committed alongside code changes, and reviewed"; it warns against "regenerating snapshots when test suites fail instead of examining the root causes"; snapshots must be deterministic, "focused, short". SOURCED: [Jest docs](https://jestjs.io/docs/snapshot-testing). Snapshot maintenance is "substantial ... overhead" (SOURCED, abstract: [LLMShot, arXiv 2507.10062](https://arxiv.org/abs/2507.10062)). The only adoption study is preliminary, with no numbers in its abstract (SOURCED: [ICSME 2023](https://doi.org/10.1109/icsme58846.2023.00041)). No fault-detection data found. |
| **Contract / schema** (each side checked against a shared interface) | data-format and interface drift between processes (A3: 21% of Azure bug incidents); backward compatibility | cheap if schema-based; Pact-style needs a broker; failure points at the interface | r1-theory-shapes F8 (Fowler, Pact; qualitative studies only); A3 above. |
| **Scenario / journey** (a realistic multi-step run through the real stack) | multi-event and configuration-dependent faults, flags, cross-component flows | slowest; large tests are about 28× as flaky as small (r1-empirical-by-size F7); needs seeding and tolerant oracles | SoCC 2022 scenario plus system tests were 53.9% of testing suggestions (A3). In ArduPilot, 35.5% of bugs were configuration-dependent and most could be reproduced in software-in-the-loop simulation (r1-against §13). |

BELIEVED: in an agent-run repo, golden files have one extra risk. An agent can make a red test green by
re-recording the golden file, which is a form of test weakening (r1-llm-tests-tools). A diff to a golden file should
therefore be a review item, and a weakening detector should flag it. L already has a fixture for this
(`tests_engine/fixtures/weakening/w12-golden-edit.toml`, VERIFIED by `git ls-files`).

## C. Necessary E2E by project type (BELIEVED synthesis; each row cites its evidence)

### C1. CLI tool / library
- **Necessary:**
  1. **Installed-artifact smoke:** build the wheel, install it into a clean env, run `--help` or import the public
     API, plus one real command. Basis: PyPI configuration issues are common (B, Peng 2024), and a source-tree test
     run cannot see them.
  2. **Golden tests of user-visible and machine-read output:** CLI text and JSON. Machine-read output is a contract
     with its consumers (A3, data-format escapes).
  3. **Public-API break check** on each release.
- **Optional:** property tests on parsers (r1 F7).
- **Not needed:** browser or journey tests.
- **Cost:** seconds to about 30 s.
- **Tools:**
  - [syrupy](https://github.com/syrupy-project/syrupy): MIT, 891 stars, pushed 2026-10-04, PyPI 6.1.1.
  - [inline-snapshot](https://github.com/15r10nk/inline-snapshot): MIT, 756 stars, pushed 2026-10-12, PyPI 0.36.1.
  - [pytest-regressions](https://github.com/ESSS/pytest-regressions): MIT, 220 stars, 2.11.0. It does data and
    numeric regression with tolerances.
  - [griffe](https://github.com/mkdocstrings/griffe): ISC, 699 stars, 2.3.2. Its API-breakage check is BELIEVED from
    its docs reputation and was not fetched this round.
  - All of these were checked through the GitHub and PyPI APIs on 2026-10-12. Best free default: syrupy, or
    inline-snapshot for small outputs.

### C2. Web service with a database
- **Necessary:**
  1. **Run the DB-touching tests on the production engine** at least once per merge. 84.2% of 423 database-access bugs
     in 7 Java applications were SQL-query, schema or API bugs: SQL queries were 54% with JDBC and API bugs 38.7% with
     Hibernate. SOURCED (abstract): [Liu, Mondal, Chen, TOSEM 2024](https://arxiv.org/abs/2405.15008) ([doi](https://doi.org/10.1145/3672449)).
     A substitute engine (SQLite for PostgreSQL) or a mock cannot show dialect and schema bugs (BELIEVED). DB code is
     often untested: 33% of 72 OSS projects did not cover database code at all, and 46% covered under half of their
     database access methods. SOURCED (abstract): [Information Systems 2022](https://doi.org/10.1016/j.is.2022.102105).
  2. **Migration test:** upgrade the chain from empty and from the previous release's schema, then check the models
     against the schema.
  3. **API contract/schema test** on every endpoint (A3, data format).
  4. **Configuration and flag test** for every shipped configuration (A3, SoCC Finding 13).
  5. **1-5 critical-journey E2E tests,** with browser tests only if there is a UI, run pre-merge or scheduled (r1
     guidance).
- **Tools:**
  - [testcontainers-python](https://github.com/testcontainers/testcontainers-python): Apache-2.0, 2,313 stars, pushed
    2026-10-09, 4.15.0. This is the best free option for a throwaway real database.
  - [pytest-postgresql](https://github.com/dbfixtures/pytest-postgresql): LGPL-3.0, 532 stars, 9.1.1.
  - schemathesis (MIT, r1).
- **Cost:** a real-database tier adds container start-up and I/O. In L, io-kind tests were 51.7% of the suite but
  80.6% of red tests (r1-sibling-suites F6). So this tier must be the flake-tracked one.

### C3. LLM-agent application (evals replace some tests)
- **Necessary in the gate (deterministic):**
  1. **Stub-model pipeline E2E:** the real pipeline with a seeded fake model that answers every schema. Add injected
     faults to prove the oracles fire.
  2. **Golden or recorded-response tests for prompts and parsers:** prompt regression. Prompts appear in about 1% of
     tests across 39 agent frameworks and 439 agentic apps. Deterministic tools and workflows take over 70% of testing
     effort, and the model-driven plan under 5%. Agent-specific methods such as DeepEval are used in about 1%. SOURCED
     (abstract): [Hasan et al., arXiv 2509.19185](https://arxiv.org/abs/2509.19185), published as
     [EMSE 2026](https://doi.org/10.1007/s10664-026-10857-9) (that DOI's abstract was not returned; the identity of the
     two is BELIEVED). Tangent finds that tests are "dominated by narrowly scoped unit tests" with "heavy mocking, and
     shallow validation" (SOURCED, abstract: [arXiv 2608.08413](https://arxiv.org/abs/2608.08413)).
  3. **Treat model output as untrusted input:** schema-fuzz it into the executor, with property-based tests.
- **Evals, outside the per-change gate:**
  - Start with "20-50 simple tasks drawn from real failures". Regression evals "should have a nearly 100% pass rate".
    Grade "what the agent produced, not the path it took". Model graders are "Non-deterministic" and need "calibration
    with human graders". Evals belong "pre-launch and in CI/CD" on every agent change and model upgrade. SOURCED:
    [Anthropic, 2026](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
  - Why not per-case asserts in the gate? VERIFIED (`check_r2_e2e.py`):
    - With 50 eval cases each passing 99% of runs, a gate that requires all to pass is green only 60.5% of the time
      (81.8% at 20 cases). Each case needs 99.9% to reach 95.1% green at 50 cases.
    - Rerunning a case k times does not fix this. An any-failure rule at k=3 raises false alarms on a 95% case 14.3% of
      the time and catches a drop to 80% only 48.8% of the time.
    - A suite-level threshold does work. With 50 cases, alarm when 44 or fewer pass. False alarms at a 95% baseline are
      3.8%, and detection of a drop to 85% is 78.1%. With 100 cases and alarm at 89 or fewer, the figures are 1.1% and
      90.1%.
    - So the rule is: evals are gated on a pass rate with a recorded baseline, run on prompt or model changes and on a
      schedule, and record model and date (owner memory: model-dated evidence).
- **Tools:**
  - [inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai): MIT, 2,990 stars, pushed 2026-10-13, 0.3.279.
  - [promptfoo](https://github.com/promptfoo/promptfoo): MIT, 25,944 stars.
  - [vcrpy](https://github.com/kevin1024/vcrpy) and [pytest-recording](https://github.com/kiwicom/pytest-recording):
    MIT, 3,020 and 615 stars. They record and replay HTTP to the model.
  - All checked on 2026-10-12. Best free default for Python: inspect_ai for evals and pytest-recording for replay.
- **Criteria drift** means an eval set needs periodic human recalibration (r1-against §15, Shankar 2024).

### C4. Simulation / robotics
- **Necessary:**
  1. **Seeded scenario tests in simulation with tolerance oracles,** for example "task completes, tilt < 1°", not
     exact trajectories. Simulation is used for testing by 85% of 82 surveyed robotics developers (84% in the ICST
     version). The named challenges include "the gap between simulation and reality, a lack of reproducibility, and
     considerable resource costs". SOURCED (abstracts): [Afzal et al., arXiv 2004.07368](https://arxiv.org/abs/2004.07368)
     and [ICST 2021](https://doi.org/10.1109/icst49551.2021.00036).
  2. **One regression scenario for each field or sim bug.** Most ArduPilot bugs reproduce in SIL simulation, and 35.5%
     are configuration-dependent (r1-against §13).
  3. **A configuration matrix** over shipped configurations (same source).
  4. **A fresh-clone run** with assets fetched or declared. The oracle problem is the main testing challenge for
     scientific software, alongside cultural factors. SOURCED (abstract, 62 studies): [Kanewala & Bieman, IST 2014](https://doi.org/10.1016/j.infsof.2014.05.006)
     ([arXiv](https://arxiv.org/abs/1804.01954)). BELIEVED: this is why golden numeric outputs need tolerances
     (pytest-regressions) rather than exact snapshots.
- **Scheduled, not per change:** long soaks and sim-to-real checks.

## D. Mapping onto the siblings (VERIFIED facts by `git ls-files`, `git grep` and reads at the round-1 heads; recommendations BELIEVED)

| Repo (type) | E2E kinds present | Missing against C | Highest-value action |
|---|---|---|---|
| **L**: game server, PostgreSQL in production, LLM agents (C2+C3) | stub-model synthetic playtest (`playtest_stub.py`: deterministic stub "so the same seed gives the same report", with injected canary and invented-number faults); golden-master port equivalence (29 gzipped pre-port DB dumps compared row for row); golden model packets and a scenario `agent_eval`/`model_eval` against any OpenAI-compatible server, evidence-only per model and host; schema plus recorded-plan fuzzing of model plans; marathon soak with checkpoints; opt-in GPU, PostgreSQL and chat smokes; migration test; Playwright folio test | production refuses SQLite (`Production requires postgresql+asyncpg; SQLite requires explicit test_mode`, adapters module), but the engine fixture defaults to SQLite unless a PostgreSQL test URL is set; only 3 tests carry `@pytest.mark.postgres`; the only CI job that set the URL failed 92/92 at container start (r1 F6); the scenario, marathon and playtest tiers carry no marker (r1 F4) | make the PostgreSQL run of the engine-fixture suite a required, recorded stage (its workflow already has a guard against "a SQLite URL ... report[ed] as a PostgreSQL pass"); mark E2E tiers and schedule them; keep evals pass-rate-gated with a model/date record |
| **R**: robot simulation research (C4) | `tests/regression/cases.json`: 8 bug-derived sim scenarios with tolerance oracles (for example `print_complete`, `tilt_max_deg_lt: 1.0`, each with a `found` note); numeric fixtures under `tests/data` | no CI; 14% of items fail in a fresh clone on one git-ignored asset (r1 F5); no config matrix | a fresh-clone CI job that fetches or skips-with-reason the asset; run `cases.json` scenarios in it; add one scenario per future sim bug (already the pattern) |
| **H**: CLI and library plus copier template (C1) | template E2E: render and run its gate, `copier update` without conflicts, fresh clone to green gate following README (`tests/test_template.py`); `live`-marked network tests | no build-wheel-install smoke (no `uv build` or wheel step in `tests/` or `.github/workflows`, grep 2026-10-12); 0 golden or snapshot tests of `dev` CLI or JSON output (grep shows 2 incidental matches in `test_board.py`), although siblings consume that output | add one installed-artifact smoke job and golden tests (syrupy or inline-snapshot) for the machine-read `dev` outputs and the hook protocol |
| **E**: research-first LLM-agent product, no product code yet (C3 later) | one smoke test (r1 F1) | nothing yet needed | when the first model-calling code lands: a stub-model pipeline E2E in the gate plus a 20-50 case eval set built from synthetic users, pass-rate-gated, scheduled, with model and date recorded |

BELIEVED, for the #114 review component: the component can check presence rather than count. For the project's
declared type, it asks whether at least one test of each necessary kind in C exists and runs somewhere enforced: the
gate, a scheduled job, or a pre-release job. A kind that exists only as an opt-in local command counts as absent. That
is how L's PostgreSQL and E2E tiers went unenforced.

## Gaps seen, not closed
- **Still no study of suite shape against escapes** (A1). A possible proxy would be to mine OSS repos for test-level
  markers against post-release issue counts. That is a research project, not a lookup.
- **Liu et al., HotOS 2019** was read only through a secondary summary. The PDF fetch failed TLS verification.
- **Mockus 2009, Kochhar 2017, the EMSE 2021 Apache study, Spadini 2018 and Williams 2009** are abstracts only. Effect
  sizes and threats were not read.
- **Snapshot and golden tests** have no fault-detection or maintenance-cost data beyond a preliminary ICSME 2023 study
  and an iOS-only LLMShot dataset.
- **Whether L's local gate sets the PostgreSQL URL on every run** was not determined; I read the code path, not its
  gate records. Nor was it determined whether its evals are ever gated.
- **griffe's breaking-change check** was not verified against its docs (context7 was not queried).
- **The Hasan et al. EMSE 2026 version** returned no abstract, so equivalence with arXiv 2509.19185 is assumed.

## Sources fetched (2026-10-12)
- Papers, abstracts unless noted:
  - Cotroneo et al. arXiv 2604.26672.
  - Mockus et al. ESEM 2009.
  - Kochhar et al. TR 2017, via the Microsoft Research page.
  - EMSE 2021 (doi:10.1007/s10664-020-09891-y).
  - Spadini et al. ICSME 2018.
  - Williams et al. ISSRE 2009.
  - Ghosh et al. SoCC 2022 (full text PDF).
  - Liu et al. HotOS 2019, via Colyer's summary.
  - Memon & Xie ICSM 2004 and TSE 2005 (author abstract pages).
  - Peng et al. ICSE 2024 / arXiv 2310.12598.
  - Snapshot testing study, ICSME 2023.
  - LLMShot, arXiv 2507.10062.
  - Liu, Mondal, Chen TOSEM 2024 / arXiv 2405.15008.
  - Testing database manipulation code, Information Systems 2022.
  - Hasan et al. arXiv 2509.19185.
  - Tangent, arXiv 2608.08413.
  - Afzal et al. arXiv 2004.07368 / ICST 2021.
  - Kanewala & Bieman arXiv 1804.01954.
  - Timperley et al. ROBUST arXiv 2404.03629 was fetched but not used: its abstract has no numbers.
- Docs: [Jest snapshot testing](https://jestjs.io/docs/snapshot-testing); [Anthropic, Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- Tools: GitHub API and PyPI JSON for syrupy, inline-snapshot, pytest-regressions, ApprovalTests.Python (Apache-2.0, 212
  stars), testcontainers-python, pytest-postgresql, vcrpy, pytest-recording, inspect_ai, promptfoo, griffe and
  pytest-console-scripts (MIT, 86 stars, last pushed 2026-01-09, so stale for a default).
- Siblings: read-only `git ls-files` and `git grep` and file heads in L, R and H at the round-1 heads; E from round 1.
- Script: `scripts/check_r2_e2e.py` (run with `python3 -I`). Output:
  ```
  SoCC22 sum of testing-suggestion shares: 100.0
  SoCC22 system+scenario share: 53.9 unit: 12.8
  HotOS19 implied total incidents: 280
  HotOS19 data-format incidents ~ 24 ; fault-handling ~ 35
  DB-access bugs in SQL/Schema/API: 356
  evals in gate: p=0.99 N=20 P(all pass)=0.818
  evals in gate: p=0.99 N=50 P(all pass)=0.605
  evals in gate: p=0.999 N=20 P(all pass)=0.980
  evals in gate: p=0.999 N=50 P(all pass)=0.951
  k=3: false alarm at p=0.95 0.143; detect drop to p=0.80 0.488
  N=50 alarm if passes<=44: false alarm at p=0.95 0.038; detect p=0.85 0.781
  N=100 alarm if passes<=89: false alarm at p=0.95 0.011; detect p=0.85 0.901
  ```
