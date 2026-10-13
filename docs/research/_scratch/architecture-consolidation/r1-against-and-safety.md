Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc, arxiv OK; s2 DOWN (rate limited, skipped by every search). arXiv MCP was rate limited (HTTP 429) once and recovered. WebFetch was used for practitioner sources (Spolsky, Fowler bliki, DORA, Google SWE book, Hyrum's law) and for Wikipedia (orientation only).

# r1: the case against periodic big re-architecture, and what makes a restructuring safe

Devil's advocate round for #111. Model-dated evidence names the model and the date. Older-model numbers (GPT-4o era)
are upper bounds on risk for current models, not current measurements.

## Findings

### F1. Rewrites from scratch lose the bug fixes that live in old code (practitioner evidence, no statistics)
- SOURCED: Spolsky calls rewriting from scratch "the single worst strategic mistake that any software company can make".
  His reason is that odd-looking old code is often bug fixes, and "when you throw away code and start from scratch, you are
  throwing away all that knowledge". He says that "fairly major architectural changes can be done without throwing away
  the code", by "carefully moving code, refactoring, changing interfaces"
  ([Things You Should Never Do, Part I](https://www.joelonsoftware.com/2000/04/06/things-you-should-never-do-part-i/), 2000).
  This is an opinion essay about one case, not a study.
- SOURCED (orientation): Netscape 6 shipped on 14 Nov 2000 on the Mozilla/Gecko code, which was rewritten from late 1998.
  Netscape 5 was dropped, and 6.0 "was considered too unstable for production use"
  ([Wikipedia: Netscape 6](https://en.wikipedia.org/wiki/Netscape_6)). Counterpoint (BELIEVED, not fetched): the rewritten
  codebase later became Firefox, so the rewrite failed the company but not the code.
- SOURCED (orientation): Brooks' second-system effect says a successful first system tends to be followed by an
  "over-engineered or bloated" second one, with deferred ideas added all at once. OS/360 is the example
  ([Wikipedia: Second-system effect](https://en.wikipedia.org/wiki/Second-system_effect), citing *The Mythical Man-Month*,
  1975). The primary book was not fetched.
- SOURCED: Fowler says of the "simple-sounding" replace-it-all plan: "we've seen this simple-sounding plan go down in flames
  most of the time". He prefers gradual replacement because "the reduced risk and earlier value from the gradual approach
  outweigh its costs" ([Strangler Fig](https://martinfowler.com/bliki/StranglerFigApplication.html), 2024). He gives no
  failure rate.
- Gap: I found no peer-reviewed failure rate for big-bang rewrites. The case rests on practitioner reports.

### F2. Refactoring is not reliably behaviour-preserving in practice, and it is under-tested
- SOURCED: Bavota et al. mined 15,008 manually validated refactorings of 52 kinds in Ant, Xerces and ArgoUML and linked
  them to bugs with SZZ. Most kinds are rarely harmful. Hierarchy refactorings such as pull-up method "tend to induce
  faults very frequently" ([When Does a Refactoring Induce Bugs?](https://doi.org/10.1109/scam.2012.20), SCAM 2012).
- SOURCED, replication: Di Penta et al. repeated this on 103 systems instead of 3, and the quantitative result held. Their
  manual inspection "partially demystifies" refactoring's role in introducing bugs: SZZ links show co-occurrence, not cause
  ([differentiated replication](https://doi.org/10.1145/3368089.3409695), ESEC/FSE 2020).
- SOURCED: On the SmartSHARK dataset, 21% of bug-fixing commits also contain a refactoring, and 54% of bug-inducing
  commits contain refactoring changes ([Is refactoring always a good egg?](https://doi.org/10.1145/3524842.3528034), MSR
  2022). This is a base-rate statistic. Large commits contain everything, so it does not show cause.
- SOURCED: In JMeter, XMLSecurity and Ant, only 22% of refactored methods and fields were covered by existing regression
  tests. Refactorings were 8% of atomic changes but were involved in "almost half of the failed test cases"
  ([Rachatasumrit & Kim](https://doi.org/10.1109/icsm.2012.6405293), ICSM 2012). This is the main reason the existing test
  suite alone cannot guard a consolidation.
- SOURCED, contrary evidence: At Microsoft, developers "perceive that refactoring involves substantial cost and risks".
  A designated team ran a multi-year, centralized refactoring of Windows, and the binary modules it refactored had
  significantly fewer inter-module dependencies and post-release defects
  ([Kim, Zimmermann, Nagappan](https://doi.org/10.1145/2393596.2393655), FSE 2012). The journal version adds that the top 5%
  of refactored modules "increase size more than the bottom 95%"
  ([TSE 2014](https://doi.org/10.1109/tse.2014.2318734)). So a dedicated periodic effort can pay off, but it did not shrink
  the code. That works against the 100k-to-20k goal.

### F3. LLM and agent refactoring: semantic drift is common, multi-file restructuring is weak, and tests miss part of it
| study (model, date) | setting | result |
|---|---|---|
| Liu et al. (ChatGPT, Gemini; arXiv Nov 2024) | 180 real Java refactorings | 13/176 ChatGPT and 9/137 Gemini solutions were unsafe (changed behaviour or broke syntax), i.e. 7.4% and 6.6% (VERIFIED, `refactor_risk.py`). Fix: re-apply the detected refactoring with a tested engine (RefactoringMirror) ([2411.04444](https://arxiv.org/abs/2411.04444)) |
| Differential fuzzing (CodeLlama, Codestral, StarChat2, Qwen-2.5, Olmo-3, GPT-4o; arXiv Feb 2026) | 3 datasets, 2 refactoring kinds | 19-35% of refactorings were functionally non-equivalent, and ~21% of those passed the existing tests ([2602.15761](https://arxiv.org/abs/2602.15761)). So 4.0-7.4% of refactorings ship silently broken (VERIFIED arithmetic) |
| RefactorBench (SWE-agent + GPT-4o; arXiv Mar 2025) | 100 handcrafted multi-file refactors in 9 Python repos | The agent solved 22% with base instructions, against 87% for a time-limited human developer. SWE-agent 1.0 re-run: 21% base, 31% descriptive. State tracking gave +43.9% relative ([2503.07832](https://arxiv.org/abs/2503.07832)) |
| TOSEM 2026 (StarCoder2, LLaMA 3, GPT-4o-mini, GPT-4o, DeepSeek-v3) | 30 Java projects | GPT-4o and DeepSeek-v3 passed unit tests above 90% at pass@5 on multi-file refactorings. Developers beat the models on "complex, context-sensitive refactorings" ([10.1145/3801158](https://doi.org/10.1145/3801158)) |
| RefactorAssist (unnamed LLMs in abstract; arXiv Aug 2026) | 10 Java projects | Failure causes: context misunderstanding/hallucination 24.3%, inconsistent renaming 15.3%, "adding new functionality or variables" 13.7%. Static repair plus test-log-guided repair reached a 94.2% cumulative pass rate ([2608.00924](https://arxiv.org/abs/2608.00924)) |
| Peitek, Hess, Apel (GPT-5.1; arXiv Feb 2026) | 230 Java snippets, 5 readability iterations | Unit tests (59,489 runs on 154 snippets) showed "only a slight drop (<1%) in functional correctness with each iteration". Five iterations at 1% each compound to at most 4.9% (VERIFIED arithmetic). Edits converge: "restructuring, then stabilization" ([2602.21833](https://arxiv.org/abs/2602.21833)) |
| Self-refactoring (CodeLlama-13B, DeepSeekCoder-6.7B, StarCoder2-15B, Qwen2.5-Coder-14B; 2026) | 1,404 BigCodeBench snippets | Behavioural preservation 96.0% (95% CI 93.3-97.7%) on test-passing snippets ([10.3390/app16157733](https://doi.org/10.3390/app16157733)) |
| Agentic refactoring in the wild (Codex, Claude Code, Cursor PRs in AIDev; arXiv Nov 2025) | 15,451 refactorings in 12,256 Java PRs | Agents target refactoring in 26.1% of commits. The edits are mostly low-level (change variable type 11.8%, rename parameter 10.4%, rename variable 8.5%) rather than design-level. Class LOC median delta was -15.25 ([2511.04824](https://arxiv.org/abs/2511.04824)) |
| Tangled refactoring by agents (3 frameworks, 12 LLMs on Multi-SWE-bench; arXiv May 2026) | 3,691 patches | Agents tangle refactorings into fixes less often than humans (21.43% vs 36.72%). Tangling is "strongly associated with reduced compilability", but not with functional correctness ([2605.22526](https://arxiv.org/abs/2605.22526)) |
| Agentic Python refactoring PRs (AIDev; arXiv May 2026) | Python refactoring PRs | 24.17% of modified files gained new Pylint issues and 4.7% new Bandit findings, yet 73.5% of the PRs were merged ([2605.21453](https://arxiv.org/abs/2605.21453)) |

BELIEVED synthesis: small, local, single-kind refactorings are now fairly safe, at about 1-4% silent drift for current
models. Multi-file, stateful restructuring is where agents fail, at 22% solved on RefactorBench (GPT-4o, 2025). No
fetched benchmark measures current frontier agents on repository-scale consolidation. Name that gap rather than assume either way.

### F4. Risk compounds with the number of transformations, not with how they are batched
- VERIFIED (`refactor_risk.py`): with an independent per-step silent-breakage rate p, the chance that at least one break
  slips through is 1-(1-p)^N. At p = 4% (the low end of F3), 10 steps give 33.5%, 20 give 55.8% and 50 give 87%. At
  p = 1% (the GPT-5.1 bound), 50 steps give 39.5%.
- BELIEVED: this applies equally to continuous tidying. Splitting the work does not lower the total risk. What small steps
  change is detection and repair. Each break shows up within one PR of its cause, can be reverted on its own, and can be
  bisected. A consolidation PR hides N steps behind one green check. Google gives the same reason for sharding large
  changes: finding one failure in a 10,000-file change "is like the proverbial needle in a haystack"
  ([SWE at Google ch. 22](https://abseil.io/resources/swe-book/html/ch22.html), SOURCED).
- So an agent harness needs a stronger oracle than the existing tests (F2, F3), whichever cadence it picks.

### F5. Protocols that make a restructuring safe and keep progress
1. **Feature inventory of observable behaviour, not documented features.** Hyrum's law: "all observable behaviors of
   your system will be depended on by somebody" ([hyrumslaw.com](https://www.hyrumslaw.com/), SOURCED). BELIEVED: in a
   Python repo, generate the inventory mechanically and diff it at every step. Sources are the CLI `--help` tree, the
   public API (griffe can dump and diff an API: [mkdocstrings/griffe](https://github.com/mkdocstrings/griffe), ISC, 699
   stars, pushed 2026-10-10, v2.3.2), config keys and the hook/skill list. Removing an item needs explicit approval.
2. **Characterisation (golden-master) tests before the first move.** These are "change detectors" that record actual,
   not correct, behaviour (Feathers' term; [Wikipedia](https://en.wikipedia.org/wiki/Characterization_test), orientation).
   Free tools: [syrupy](https://github.com/syrupy-project/syrupy) (MIT, 891 stars, pushed 2026-10-04, v6.1.1),
   [pytest-regressions](https://github.com/ESSS/pytest-regressions) (MIT, 220 stars, pushed 2026-10-12) and
   [ApprovalTests.Python](https://github.com/approvals/ApprovalTests.Python) (Apache-2.0, 212 stars, pushed 2026-10-12,
   v19.1.1). Checked via the GitHub and PyPI APIs on 2026-10-12.
3. **Differential testing of old against new while both exist.** Differential fuzzing caught non-equivalences that 21%
   of the time had passed the suite (F3). BELIEVED: during a parallel change, a Hypothesis property `new(x) == old(x)`
   is the cheapest version of this ([hypothesis](https://github.com/HypothesisWorks/hypothesis), MPL-2.0 per PyPI, 9,077
   stars, pushed 2026-10-11).
4. **Mutation testing as a guard on the touched code only.** SOURCED: mutant detection correlates with real-fault
   detection independently of coverage, on 357 real faults in 321k LOC
   ([Just et al.](https://doi.org/10.1145/2635868.2635929), FSE 2014). At Google, mutating only changed code during review
   scales to more than 24,000 developers ([TSE 2021](https://doi.org/10.1109/tse.2021.3107634)). In a 15M-mutant dataset,
   developers exposed to mutants wrote more and better tests, and mutants were coupled to real high-priority faults
   ([ICSE 2021](https://doi.org/10.1109/icse43902.2021.00087)). Free tools: [mutmut](https://github.com/boxed/mutmut)
   (BSD-3-Clause, 1,476 stars, pushed 2026-09-12, v3.8.0, best fit) and
   [cosmic-ray](https://github.com/sixty-north/cosmic-ray) (MIT, 660 stars, pushed 2026-08-09).
5. **Incremental structure: strangler fig, branch by abstraction, parallel change.** All three keep the system green and
   releasable at every step. Branch by abstraction: "the system builds and runs correctly at all times", and you delete
   the old supplier once nothing uses it ([Fowler 2014](https://martinfowler.com/bliki/BranchByAbstraction.html),
   SOURCED). Parallel change runs expand, migrate, contract, which "lowers the risk of change by allowing you to migrate
   clients and to test the new version incrementally" ([Sato 2014](https://martinfowler.com/bliki/ParallelChange.html),
   SOURCED). Google shards each large change into independently committable pieces along ownership boundaries, at up to
   700+ changes and 15,000+ files a day ([SWE at Google ch. 22](https://abseil.io/resources/swe-book/html/ch22.html),
   SOURCED).
6. **Mechanical edits through a refactoring engine, not free-form LLM rewriting.** RefactoringMirror re-applies the
   refactoring the LLM chose through a tested engine (F3, SOURCED). Python engines: [rope](https://github.com/python-rope/rope)
   (LGPL-3.0, 2,240 stars, pushed 2026-10-08) and [LibCST](https://github.com/Instagram/LibCST) codemods (1,962 stars,
   pushed 2026-08-11; the API reports license NOASSERTION, believed MIT but not verified). Engines are not bug-free either
   (BELIEVED; a 2024 study of refactoring-engine bugs appeared in search but was not read, so it is not cited).
7. **Refactor-only PRs, never tangled with behaviour change.** Evidence: the 54% figure in F2 and reduced compilability
   for agents in F3. For Java, [RefactoringMiner](https://github.com/tsantalis/RefactoringMiner) (MIT, 512 stars, pushed
   2026-10-12) detects refactorings in a diff. I did not check whether it supports Python.
8. **Feature flags: use them sparingly.** Toggles are "inventory which comes with a carrying cost". Keep that inventory "as
   low as possible" and set expiry dates ([Hodgson 2017](https://martinfowler.com/articles/feature-toggles.html),
   SOURCED). BELIEVED: in a dev-tooling repo with no live users, parallel change does the same job without flag debt. Use
   a short-lived release toggle only when old and new must coexist across many PRs.

### F6. When a small continuous tidy beats a periodic consolidation (BELIEVED, from F1-F5)
Continuous tidying should be the default. Choose it when **any** of these holds:
- The change is local: one module, or one abstraction plus its callers. Agents do this kind well and already do it in
  26% of commits (F3).
- The code under change is already covered by characterisation tests or a mutation-checked suite. That oracle is the
  real constraint (F2: 22% coverage of refactored code).
- The tidy can ride just before a feature PR that touches the same code, as its own refactor-only PR (F5.7).
- The batch would take more than about a week to complete and check. DORA says any batch that takes "longer than a week
  to complete and check is too big", and that small batches counter the instability that comes with AI adoption
  ([DORA, Working in small batches](https://dora.dev/capabilities/working-in-small-batches/), SOURCED; no effect sizes
  on that page).

A **periodic consolidation** is justified only when all of these hold:
- the duplication or bad boundary crosses modules and no local tidy can remove it, for example two parallel subsystems
  or a wrong layer split;
- a feature inventory and characterisation tests for the affected surface exist **before** step 1;
- it runs as a planned sequence of small, independently mergeable, green steps (strangler, parallel change), never as one
  rewrite PR;
- it has a measured success condition. Microsoft's centralized effort paid off on dependencies and defects but grew the
  code (F2), so "fewer LOC" has to be a stated, ratcheted target with an inventory diff that stays empty.

Never: a from-scratch rewrite, or one agent PR that restructures many files at once. RefactorBench measured 22% success
(GPT-4o, 2025), and no newer fetched number shows that gap has closed.

## Gaps seen, not closed
- No peer-reviewed base rate for big-bang rewrite failure. Spolsky and Fowler are practitioner opinion.
- No benchmark of current frontier agents (Claude Opus/Sonnet 4.x-5.x, GPT-5.x) on repository-scale, multi-file
  consolidation with behaviour checking. RefactorBench is GPT-4o-era. A measurement on this harness's own repos would
  close this.
- Bavota 2012 per-refactoring-kind fault rates: I read only the abstract, so no per-kind numbers are given.
- Not checked: whether mutmut v3 runs fast enough on the changed-lines-only scope for a per-PR gate here.
  test-floors-and-coverage.md marks mutation testing as too slow for per-change use (BELIEVED there).
- Microsoft FSE 2012: the effect size of the defect reduction was not in the abstract.
- Kent Beck's "Tidy First?" (2023) is the obvious practitioner source for continuous tidying. Not fetched, not cited.

## Sources fetched
- Spolsky 2000, joelonsoftware.com (WebFetch). Fowler, Strangler Fig 2024; Fowler, Branch by Abstraction 2014;
  Sato, Parallel Change 2014; Hodgson, Feature Toggles 2017 (martinfowler.com, WebFetch).
- Wright, *Software Engineering at Google* ch. 22 (abseil.io, WebFetch). DORA small batches (dora.dev, WebFetch).
  hyrumslaw.com (WebFetch).
- Wikipedia (orientation): Netscape 6, Second-system effect, Characterization test.
- Abstracts via `uv run dev lit get`: 10.1109/scam.2012.20, 10.1145/3368089.3409695, 10.1145/3524842.3528034,
  10.1109/icsm.2012.6405293, 10.1145/2393596.2393655, 10.1109/tse.2014.2318734, 10.1145/3801158, 10.3390/app16157733,
  10.1145/2635868.2635929, 10.1109/icse43902.2021.00087, 10.1109/tse.2021.3107634, arXiv 2511.04824, 2602.15761,
  2605.22526, 2605.21453, 2608.00924.
- arXiv MCP: 2411.04444 (abstract), 2503.07832 (abstract + passages on gpt-4o and the SWE-agent 1.0 re-run),
  2602.21833 (passages on functional correctness).
- GitHub API and PyPI JSON on 2026-10-12: mutmut, cosmic-ray, ApprovalTests.Python, syrupy, pytest-regressions,
  hypothesis, griffe, rope, LibCST, RefactoringMiner (microsoft/RefactorBench repo is archived).
- Script: `scripts/refactor_risk.py`
  (moved into this folder at synthesis).
