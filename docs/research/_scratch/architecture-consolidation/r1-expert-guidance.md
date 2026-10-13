# r1: expert guidance on keeping a codebase lean and on large restructurings

Sub-question: what do recognised experts prescribe, and when does each technique apply?
Search coverage: `uv run dev lit status` on 2026-10-12 showed openalex, crossref, europepmc and arxiv up and s2 DOWN
(HTTP 429). Primary texts were fetched directly (martinfowler.com, Stanford CS190 notes, Parnas and Lehman PDFs,
the Herraiz review PDF, the Ousterhout/Martin discussion, melconway.com, laputan.org, cognitect.com, shopify.engineering).
Two books were not available as primary text: Feathers (2004) and Beck's *Tidy First?* (2023). Claims about them rest
on Beck's own newsletter plus secondary reviews, and are labelled that way.

## Findings

### F1. The thing to minimise is complexity. LOC is a weak proxy, and reducing complexity can increase size
- SOURCED: Ousterhout defines complexity as "anything related to the structure of a system that makes it hard to work
  on the development of that system". He lists three symptoms: change amplification, cognitive load and unknown
  unknowns. He names two causes: dependencies and obscurity. Complexity is incremental ("No one thing makes a system
  complicated"), so he asks for a "zero-tolerance attitude"
  ([CS190 notes, Complexity, 2018](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=complexity)).
- SOURCED: He wants deep modules ("small interface, lots of functionality") and warns against "classitis" ("small classes
  taken to the extreme"). He says depth matters more than size: classes of 200 to 2000 lines are fine
  ([CS190 notes, Modular Design](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=modularDesign)).
  In his 2024-25 debate with Robert Martin he says over-decomposition makes code harder to understand, and "If two
  pieces of code are tightly related, the solution is to bring them together"
  ([aposd-vs-clean-code](https://github.com/johnousterhout/aposd-vs-clean-code)).
- SOURCED: Microsoft field study (Kim, Zimmermann, Nagappan, TSE 2014; survey, interviews and Windows 7 version
  history). The top 5% most refactored modules lost more inter-module dependencies and complexity than the bottom 95%,
  but they also "increase size more". The authors conclude that the impact of refactoring has to be measured on
  several dimensions. Developers also saw refactoring as carrying "substantial cost and risks"
  ([doi:10.1109/tse.2014.2318734](https://doi.org/10.1109/tse.2014.2318734)).
- BELIEVED: A component that aims at "100k to 20k LOC" should not make LOC its acceptance metric. Better primary
  metrics are interface count, inter-module dependency count and how far typical changes spread (change
  amplification). LOC can be a secondary signal. Real collapses come from deleting duplication and shallow layers,
  which is what Ousterhout's "bring them together" describes, not from compressing code.

### F2. Module boundaries: hide the decisions that are likely to change, and do not split along processing steps
- SOURCED: Parnas (CACM 1972) says "it is almost always incorrect to begin the decomposition of a system into modules on
  the basis of a flowchart". Instead you start from "a list of difficult design decisions or design decisions which are
  likely to change. Each module is then designed to hide such a decision from the others." He gives three expected
  benefits: managerial (separate groups can work "with little need for communication"), product flexibility ("drastic
  changes to one module without a need to change others") and comprehensibility (study the system one module at a time)
  ([Parnas 1972 PDF](https://wstomv.win.tue.nl/edu/2ip30/references/criteria_for_modularization.pdf),
  [doi:10.1145/361598.361623](https://doi.org/10.1145/361598.361623)).
- SOURCED: Ousterhout gives the modern form. "Temporal decomposition" is a common cause of information leakage, and
  when you see leakage you should "bring all the information together in one place"
  ([CS190 Modular Design](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=modularDesign)).
- SOURCED: Foote and Yoder (PLoP 1997), SHEARING LAYERS: "factor your system so that artifacts that change at similar
  rates are together" ([Big Ball of Mud](http://www.laputan.org/mud/mud.html)).
- BELIEVED: For a consolidation pass, these give a concrete test. Group code by the decision it hides and by how fast it
  changes (git co-change), not by pipeline stage. Agent-written code tends to grow one module per step or per feature,
  which is the flowchart decomposition Parnas warns against. That tendency is ASSUMED: this sub-question did not
  measure it.

### F3. Growth is near-certain. Rising complexity is the default but not inevitable, and keeping it down takes deliberate work
- SOURCED: Lehman et al. 1997 restate the eight laws. They include Law II, Increasing Complexity ("As an E-type system
  evolves its complexity increases unless work is done to maintain or reduce it"), Law V, Conservation of Familiarity
  ("Excessive growth diminishes that mastery"), Law VI, Continuing Growth, and Law VII, Declining Quality
  ([Lehman, Ramil, Wernick, Perry, Turski, METRICS 1997](https://www.ifi.uzh.ch/dam/jcr:00000000-5852-3622-0000-00002e60ff8e/lehman-metrics-97.pdf),
  [doi:10.1109/metric.1997.637156](https://doi.org/10.1109/metric.1997.637156)). In their own table, VII had "No data"
  for or against it.
- SOURCED, contrary evidence: The systematic review by Herraiz et al. (ACM CSUR 2013) finds that Laws I and VI hold in
  every study it covers. Laws II, IV and V "have been invalidated for most of the cases", especially in libre software:
  "empirical data shows that further changes are not more difficult". It concludes that the laws' validity "is not
  universal" ([Herraiz et al. 2013 PDF](https://gwern.net/doc/cs/2013-herraiz.pdf),
  [doi:10.1145/2543581.2543595](https://doi.org/10.1145/2543581.2543595)).
- BELIEVED: The defensible design premise is that the codebase will keep growing (Law VI is robust), so a trigger keyed
  on growth will fire repeatedly and needs to be cheap. Do not justify the component by saying "complexity inevitably
  rises" (Law II is contested). Justify it by measured symptoms in this repo. Law V's "familiarity" maps naturally onto
  an agent's context budget, but that is an analogy, not evidence.

### F4. The default mode is continuous small tidying, not a scheduled big refactor
- SOURCED: Fowler (2011) treats refactoring as "an opportunistic activity, done whenever and wherever code needs to
  cleaned up" (the campsite rule). He says there is "a place for planned efforts", for example a day or two on an area
  that has hindered the team for months. A team that refactors well "should hardly ever need to plan refactoring"
  ([OpportunisticRefactoring](https://martinfowler.com/bliki/OpportunisticRefactoring.html)). He defines refactoring as
  a change to internal structure "without changing its observable behavior"
  ([DefinitionOfRefactoring, 2004](https://martinfowler.com/bliki/DefinitionOfRefactoring.html)).
- SOURCED: Beck's newsletter gives four forces for each "tidy first?" decision. Cost ("smaller, later, or less
  likely?"), revenue, coupling ("change fewer elements?") and cohesion ("smaller, more concentrated scope?")
  ([Beck, newsletter, 2022-11-21](https://newsletter.kentbeck.com/p/conclusion)).
- SOURCED (secondary review, not the book text): *Tidy First?* says to keep structural and behavioural changes in
  separate PRs or commits. It weighs time-value of money, which argues against tidying first, against optionality, which
  argues for it. It frames the cost of software as roughly the cost of change, which is dominated by coupling
  ("Constantine's Equivalence") ([Warne review, 2024](https://henrikwarne.com/2024/01/10/tidy-first/)).
- SOURCED: Ousterhout separates tactical programming ("get the next feature or bug fix working") from strategic
  programming ("produce a great design"). On investment his notes say "As much as you can afford ... 10-20% overhead?"
  and on payoff "Investments pay for themselves relatively quickly (6-12 months?)". He wrote both with question marks,
  so they are his estimates, not measurements
  ([CS190 notes, Working Isn't Good Enough](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=working)).
  In the Martin debate he adds: "I have a lot of experience with tactical programming, and I know that it rarely ends
  well" ([aposd-vs-clean-code](https://github.com/johnousterhout/aposd-vs-clean-code)).
- BELIEVED: An agent that merges one issue at a time is tactical by construction. That makes a recurring, budgeted
  strategic pass (for example around 10-20% of merged work, using Ousterhout's hedged figure) a reasonable default. The
  pass should be split into many small behaviour-preserving PRs, as Beck and Fowler advise, and should not be one large
  PR.

### F5. Large restructurings: incremental replacement behind seams, with the system always working
- SOURCED: Strangler Fig (Fowler, 2024) is "a gradual approach to legacy modernization". Fowler says of big-bang
  replacement: "we've seen this simple-sounding plan go down in flames most of the time". The reasons he gives are that
  replacement takes long, existing behaviour is "hard to figure out", and "much of that behavior is stuff that isn't
  really wanted". The method finds seams, replaces small parts first and accepts a temporary "transitional architecture"
  ([StranglerFigApplication](https://martinfowler.com/bliki/StranglerFigApplication.html)).
- SOURCED: Cartwright, Horn and Lewis (2021, rev. 2024) list **Feature Parity** among their legacy-displacement
  patterns. Feature parity is the promise "to give the business exactly what they had today". They report that it
  "led to a plan for a large single 'big bang' cut-over release" and that pinning down current functionality was
  itself a large effort ([Patterns of Legacy Displacement](https://martinfowler.com/articles/patterns-legacy-displacement/)).
- SOURCED: Branch by Abstraction (Fowler, 2014): add an abstraction, move clients onto it, build the new supplier
  behind it, switch clients over, then delete the old supplier. "Ensure that the system builds and runs correctly at
  all times" ([BranchByAbstraction](https://martinfowler.com/bliki/BranchByAbstraction.html)).
- SOURCED: Parallel Change, also called expand/migrate/contract (Sato, 2014). It fits interface, signature and schema
  changes with several clients. "If the contract phase is not executed you might end up in a worse state than you
  started" ([ParallelChange](https://martinfowler.com/bliki/ParallelChange.html)).
- SOURCED: The Mikado Method (Ellnestam and Brolund, Methods & Tools, 2013) cycles through goal, experiment, a
  prerequisite graph and undo, so the code is always "in a known working state". It is aimed at changes that "take
  several weeks, or even months" ([Methods & Tools](https://www.methodsandtools.com/archive/mikado.php)).
- SOURCED (via secondary quotes): Feathers coined *characterization tests*, which are tests that "characterize the
  actual behavior of a piece of code" rather than what it should do. He advises writing them "for the area where you
  will make your changes" and, when extracting or moving functionality, tests that verify its "existence and
  connection" ([InfoQ 2007, quoting Feathers via Savoia](https://www.infoq.com/news/2007/03/characterization-testing)).
  The 2004 book itself was not fetched.
- BELIEVED: The owner's "keep every feature" needs a tested feature inventory. Fowler and the displacement authors also
  warn that literal parity is both a trap and a waste. So the inventory step should sort each behaviour into keep,
  drop with approval, or "nobody uses it". It should not freeze everything.

### F6. Rewrites: rarely, per module, by the people who hold the context
- SOURCED: Spolsky (2000) calls a from-scratch rewrite the "single worst strategic mistake", because old code carries
  hard-won bug fixes. His example is Netscape going about three years without a major release
  ([Things You Should Never Do](https://www.joelonsoftware.com/2000/04/06/things-you-should-never-do-part-i/)).
- SOURCED: Foote and Yoder say RECONSTRUCTION ("throw it away and start over") applies when code is "beyond repair, or
  even comprehension". It risks Brooks's second-system effect and should start with a review of "what it did well, and
  why it failed". Before that, the intermediate option is SWEEPING IT UNDER THE RUG: "at least cordon it off"
  ([Big Ball of Mud](http://www.laputan.org/mud/mud.html)).
- SOURCED: Fowler's sacrificial architecture (2014) quotes Google's rule of thumb from Jeff Dean, "design for ~10X
  growth, but plan to rewrite before ~100X". It says "as a system grows it's more effective to sacrifice individual
  modules", and that the original team should make the call
  ([SacrificialArchitecture](https://martinfowler.com/bliki/SacrificialArchitecture.html)).

### F7. Modular monolith: enforce boundaries inside one deployable, mechanically and in the PR
- SOURCED: Fowler's Monolith First (2015) argues that boundaries are hard to get right early and that a monolith
  should be built "paying attention to modularity". Gradual decomposition worked only where a good modular design
  already existed ([MonolithFirst](https://martinfowler.com/bliki/MonolithFirst.html)).
- SOURCED: Shopify (Westeinde, 2019) evolved into a modular monolith. They organised code by domain instead of by
  technical layer and inventoried about 6,000 classes in a spreadsheet
  ([Deconstructing the Monolith](https://shopify.engineering/deconstructing-monolith-designing-software-maximizes-developer-productivity)).
  The 2020 follow-up (Müller) gives the scale as over 2.8M LOC of Ruby and 37 components, with packwerk's dependency
  and privacy checks applied to "about a third of the components". The earlier runtime tool, Wedge, took over an hour
  and was noisy. Packwerk's static check takes "a few minutes" and blocks violations in PRs. Circular dependencies were
  widespread, and at one point every component depended on over half of the others. Hard rules on interface
  consistency added friction, so the team moved to "more guidance and fewer rules"
  ([Under Deconstruction](https://shopify.engineering/shopify-monolith)).
- VERIFIED (GitHub and PyPI APIs, 2026-10-12): The Python counterpart is `import-linter`: BSD-2-Clause, 1215 stars,
  last pushed 2026-09-16, version 2.15, not archived. `Shopify/packwerk` is MIT, 1923 stars, last pushed 2026-08-26,
  and Ruby-only.

### F8. Conway's law: the communication structure shapes the module structure
- SOURCED: Conway (Datamation, 1968) says organisations are "constrained to produce designs which are copies of the
  communication structures of these organizations". Because "the design which occurs first is almost never the best
  possible", the organisation needs to stay flexible ([Conway 1968](https://www.melconway.com/research/committees.html)).
- SOURCED: Fowler (2022) sets out three responses: ignore, accept, or the inverse Conway maneuver, which changes
  designers' communication "to encourage the desired software architecture"
  ([ConwaysLaw](https://martinfowler.com/bliki/ConwaysLaw.html)). Team Topologies treats team cognitive load as the
  constraint and says boundaries "must adapt as products and technologies evolve"
  ([teamtopologies.com](https://teamtopologies.com/key-concepts)).
- SOURCED, contrary evidence: Colfer and Baldwin (ICC 2016) reviewed 142 empirical studies. They find mirroring
  "prevalent ... but not universal", and the studies of open collaborative projects, mostly software, "were not
  supportive" ([doi:10.1093/icc/dtw027](https://doi.org/10.1093/icc/dtw027), abstract only).
- BELIEVED: In an agent-run repo, the "communication structure" is the decomposition into issues, worktrees and PRs. If
  one issue maps to one new file or module, the result is a module per issue. An inverse-Conway response is to file
  consolidation issues along the target module boundaries.

### F9. Record restructuring decisions as ADRs and supersede them instead of editing them
- SOURCED: Nygard (2011) gives the ADR form: Title, Context, Decision ("We will..."), Status and Consequences. "If a
  decision is reversed, we will keep the old one around, but mark it as superseded". The motivation is to avoid
  "blind acceptance or blind reversal" by later developers
  ([Nygard 2011](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)).
- VERIFIED (GitHub API, 2026-10-12): `npryce/adr-tools` has 5730 stars, was last pushed 2024-04-25 and has licence
  NOASSERTION. `adr/madr` has 2550 stars, was last pushed 2026-08-28 and has licence NOASSERTION. Both licences are
  unclear through the API and need a manual check.
- BELIEVED: This matters most for agents. A consolidation that deletes code removes the evidence of why the code
  existed. An ADR, together with the feature inventory, keeps that rationale, so a later agent does not re-add a
  removed layer by "blind reversal".

### F10. The economic case: cruft slows teams within weeks (a practitioner claim plus one vendor-linked study)
- SOURCED: Fowler (2019, rev. 2024) argues that "high quality software is actually cheaper to produce", and that poor
  internal quality "significantly slows them down within a few weeks"
  ([Is High Quality Software Worth the Cost?](https://martinfowler.com/articles/is-quality-worth-cost.html)).
- SOURCED: Tornhill and Borg (TechDebt 2022) studied 39 proprietary codebases and 30,737 files. Low-quality code had
  15x more defects and took on average 124% more time to resolve issues in. Quality was rated with CodeScene's Code
  Health metric ([arXiv:2203.04374](https://arxiv.org/abs/2203.04374)). BELIEVED: Tornhill founded CodeScene, so this
  is vendor-linked work. It is also observational, so it does not show that refactoring would recover the gap.

## When each technique applies

All entries are BELIEVED syntheses of F1-F10.

| Situation | Technique | Source |
|---|---|---|
| Every change, continuously | Campsite tidying. Keep structure and behaviour in separate commits or PRs. | Fowler 2011; Beck |
| An area has hindered work for a while | Planned refactor, timeboxed (Fowler: "a day or two") | Fowler 2011 |
| Deciding where module lines go | Hide decisions that are likely to change. Group by rate of change. No temporal or flowchart split. | Parnas 1972; Ousterhout; Foote & Yoder |
| Many thin wrappers or shallow modules | Merge into deep modules. Delete pass-through layers. | Ousterhout |
| Changing code whose behaviour is undocumented | Characterisation tests first, on the change area | Feathers |
| Replacing a dependency many callers use | Branch by abstraction | Fowler 2014 |
| Changing an interface or schema with several clients | Parallel change (expand, migrate, contract), and finish the contract phase | Sato 2014 |
| A change that keeps breaking something else | Mikado graph, revert, work from the leaves | Ellnestam & Brolund |
| Replacing a whole subsystem | Strangler fig with transitional architecture, and no feature-parity big bang | Fowler 2024; Cartwright et al. |
| Code beyond comprehension that cannot be strangled | Cordon it off first. Rewrite that one module only, run by the people who hold the context. | Foote & Yoder; Fowler 2014 |
| Keeping the boundaries afterwards | Static boundary checks in the PR gate (import-linter for Python) | Shopify 2020 |
| Any of the above that removes or moves capability | ADR, superseding the old one | Nygard 2011 |

## Gaps (seen, not closed)

- *Tidy First?* and Feathers' book were not fetched as primary text. Beck's tidy first/after/later/never rule and
  Feathers' "legacy change algorithm" steps are known only from reviews. Needs the book text before it is quoted in the
  final note.
- No source gives a numeric trigger (LOC, churn, PR count) for when to start a planned restructuring. Fowler's "hindered
  the team for months", Ousterhout's "10-20%?" and Google's "~10X/~100X" are the closest, and all are heuristics.
  Measuring this is a job for other rounds (repo data, CodeScene-style hotspots).
- Kim et al. 2014: the exact effect sizes for dependency reduction and size increase were not extracted, only the
  abstract.
- MacCormack, Rusnak and Baldwin 2012 (mirroring test) was found but only its metadata came back, so it is not used.
- Parnas's "Software Aging" (ICSE 1994) was not fetched. It is cited only inside Herraiz.
- Licences for adr-tools and MADR show NOASSERTION in the API and need a manual check.

## Sources fetched

- Ousterhout, CS190 notes (Stanford, Winter 2018): [Complexity](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=complexity), [Modular Design](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=modularDesign), [Working Isn't Good Enough](https://web.stanford.edu/~ouster/cgi-bin/cs190-winter18/lecture.php?topic=working); [APOSD book page](https://web.stanford.edu/~ouster/cgi-bin/aposd.php); [aposd-vs-clean-code](https://github.com/johnousterhout/aposd-vs-clean-code)
- Parnas 1972, CACM 15(12): [PDF](https://wstomv.win.tue.nl/edu/2ip30/references/criteria_for_modularization.pdf), [doi:10.1145/361598.361623](https://doi.org/10.1145/361598.361623)
- Lehman et al. 1997, METRICS: [PDF](https://www.ifi.uzh.ch/dam/jcr:00000000-5852-3622-0000-00002e60ff8e/lehman-metrics-97.pdf); Lehman 1980 abstract [doi:10.1109/proc.1980.11805](https://doi.org/10.1109/proc.1980.11805)
- Herraiz, Rodriguez, Robles, Gonzalez-Barahona 2013, ACM CSUR: [PDF](https://gwern.net/doc/cs/2013-herraiz.pdf)
- Fowler: [StranglerFigApplication](https://martinfowler.com/bliki/StranglerFigApplication.html), [Is quality worth the cost](https://martinfowler.com/articles/is-quality-worth-cost.html), [DefinitionOfRefactoring](https://martinfowler.com/bliki/DefinitionOfRefactoring.html), [OpportunisticRefactoring](https://martinfowler.com/bliki/OpportunisticRefactoring.html), [BranchByAbstraction](https://martinfowler.com/bliki/BranchByAbstraction.html), [ParallelChange](https://martinfowler.com/bliki/ParallelChange.html), [MonolithFirst](https://martinfowler.com/bliki/MonolithFirst.html), [SacrificialArchitecture](https://martinfowler.com/bliki/SacrificialArchitecture.html), [ConwaysLaw](https://martinfowler.com/bliki/ConwaysLaw.html), [Patterns of Legacy Displacement](https://martinfowler.com/articles/patterns-legacy-displacement/). [Workflows of Refactoring](https://martinfowler.com/articles/workflowsOfRefactoring/) was fetched, but only the titles were readable, so it is not cited for content.
- Beck: [newsletter conclusion 2022](https://newsletter.kentbeck.com/p/conclusion); review [Warne 2024](https://henrikwarne.com/2024/01/10/tidy-first/)
- Feathers via [InfoQ 2007](https://www.infoq.com/news/2007/03/characterization-testing)
- [Mikado, Methods & Tools 2013](https://www.methodsandtools.com/archive/mikado.php)
- [Foote & Yoder 1997](http://www.laputan.org/mud/mud.html); [Spolsky 2000](https://www.joelonsoftware.com/2000/04/06/things-you-should-never-do-part-i/)
- [Conway 1968](https://www.melconway.com/research/committees.html); [Team Topologies key concepts](https://teamtopologies.com/key-concepts); Colfer & Baldwin 2016 abstract [doi:10.1093/icc/dtw027](https://doi.org/10.1093/icc/dtw027)
- [Nygard 2011](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
- Shopify: [Westeinde 2019](https://shopify.engineering/deconstructing-monolith-designing-software-maximizes-developer-productivity), [Müller 2020](https://shopify.engineering/shopify-monolith)
- Kim, Zimmermann, Nagappan 2014 abstract [doi:10.1109/tse.2014.2318734](https://doi.org/10.1109/tse.2014.2318734); Tornhill & Borg 2022 abstract [arXiv:2203.04374](https://arxiv.org/abs/2203.04374)
- APIs: api.github.com for seddonym/import-linter, Shopify/packwerk, npryce/adr-tools, adr/madr; pypi.org for import-linter (2026-10-12)
