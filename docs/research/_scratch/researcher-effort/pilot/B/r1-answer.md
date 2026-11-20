# Round 1 answer: code growth, duplication, churn and erosion in AI-written code; when consolidation pays

Search coverage: discovery 2026-11-20: OK, partial (s2 down; openalex answered)

## Findings

1. **The best causal evidence shows AI and agent adoption causes a lasting rise in complexity and warnings, while the velocity gain fades.** SOURCED.
   - [Speed at the Cost of Quality: How Cursor AI Increases Short-Term Velocity and Long-Term Complexity in Open-Source Projects](https://arxiv.org/html/2511.04427v3) (He, Miller, Agarwal, Kästner, Vasilescu, MSR 2026) is a difference-in-differences study of 806 Cursor adopters against 1,380 matched controls. Main results:
     - Velocity: "281.3% increase in lines added in the first month" and a 55.4% rise in commits. The gain is over after two months. The average effect on lines added is +28.58%, and the effect on commits is not significant.
     - Quality: Section 4.1.2 says "static analysis warnings increase significantly by 30.3%, and code complexity increases by 41.6%". Table 2 gives ±6.66% and ±7.62%.
     - Version drift: Sections 5.1.2 and 5.2 of the same paper give 25% for complexity, and the paper does not reconcile the two figures. A commenter quotes a 9% complexity effect after size controls, but I did not read it in the paper (BELIEVED).
   - [AI IDEs or Autonomous Agents? Measuring the Impact of Coding Agents on Software Development](https://arxiv.org/html/2601.13597v2) (Agarwal, He, Vasilescu, MSR 2026) uses a staggered difference-in-differences design on the AIDev dataset. Its groups are 401 agent-first repositories (with 606 controls) and 117 IDE-first repositories (with 73 controls). The abstract says "static-analysis warnings and cognitive complexity rising by roughly 18% and 39%". These risks persist even where the velocity gain is small. This is an independent second source, but it shares authors and method.
2. **The causal studies do not show that duplication rises; only vendor telemetry claims it.**
   - In the Cursor study, Section 4.1.2 says "The effect on duplicate line density is insignificant" (+7.03% ±4.79%). SOURCED.
   - In the agent study, Table 2 gives +7.92% for duplication in agent-first repositories (β 0.076, SE 0.044, not significant). For IDE-first repositories it gives −0.94%. SOURCED.
   - [AI Copilot Code Quality 2025](https://www.gitclear.com/ai_assistant_code_quality_2025_research) (GitClear, 2025, grey literature) covers 211M changed lines from 2020 to 2024. It reports that refactoring ("moved") lines "sunk from 25% of changed lines in 2021, to less than 10% in 2024", and that copy/paste lines "rose from 8.3% to 12.3%". This is correlational, the vendor does not describe how it attributes code to AI, and GitClear sells analytics. Treat it as a trend signal, not a cause. SOURCED, single-source.
3. **Agent configuration may moderate the harm.** [A Few Pages of Markdown: Committed AI Configuration and Lower Quality Cost after Coding-Agent Adoption](https://doi.org/10.1145/3832783.3837546) (Denisov-Blanch et al., ASE 2026) studies 441 repositories.
   - Agent-first repositories without committed AI configuration show "roughly twice the increase in cognitive complexity (+53% versus +27%) and 1.7× the increase in static-analysis warnings".
   - The authors call this observational and "hypothesis-generating". It is relevant to a harness that ships CLAUDE.md and rules. SOURCED, single-source.
4. **Agents refactor often, but the measured gains are small.** [Agentic Refactoring: An Empirical Study of AI Coding Agents](https://arxiv.org/pdf/2511.04824) (Horikawa et al., TOSEM 2026) analyses 51,412 refactorings from AIDev Java PRs. SOURCED.
   - Agents state refactoring intent in 26.1% of commits.
   - The most common operations are low-level: Rename Variable (7.6%) and Change Variable Type (6.8%).
   - The study reports "small but statistically significant improvements in structural metrics". Effect sizes are not in the abstract.
5. **Poor code quality has a measurable cost, which is the case for paying down debt.** [Code Red: The Business Impact of Code Quality](https://arxiv.org/abs/2203.04374) (Tornhill and Borg, TechDebt 2022) covers 39 proprietary codebases and 30,737 files. It is observational and uses CodeScene's own Code Health score; Tornhill is CodeScene's founder. SOURCED.
   - "low quality code contains 15 times more defects than high quality code".
   - Resolving issues takes "on average 124% more time".
   - Low-quality code shows "9 times longer maximum cycle times".
   - Paired with finding 1, this supports the same claim from a second direction: in the Cursor study's GMM model (Section 4.2), more complexity and more warnings predict lower later velocity. The paper states "A 100% increase in code complexity and static analysis warnings causes a 64.5% and 50.3% decrease" in lines added. Table 3 gives the coefficients −0.718 and −0.588. Hand check: if these are log-log elasticities, a doubling gives decreases of 1−2^b = 39% and 33%. Read as 1−e^b, they give 51% and 44%. Neither reproduces 64.5% and 50.3%, which are probably long-run effects that include the lagged term. The script `scripts/gmm_check.py` exists but was not run because running it needs approval. Status: SOURCED, not VERIFIED.
6. **Duplication is a weaker signal than its reputation suggests. Inconsistent clones are the part that predicts faults.**
   - [Do code clones matter?](https://arxiv.org/pdf/1701.05472) (Juergens et al., ICSE 2009) found that inconsistent changes to clones are "very frequent" and caused "a significant number of faults". SOURCED.
   - [Clones: what is that smell?](https://www.microsoft.com/en-us/research/?p=160137) (Rahman, Bird, Devanbu, EMSE 2012) studied four OSS projects. Most bugs had no significant link to clones, and cloned code may be *less* defect-prone. This contradicts the Juergens result. SOURCED, from the abstract via a search summary.
   - So the useful signal is clones that diverge (co-changed copies edited inconsistently), not the raw duplication percentage. BELIEVED (synthesis).
7. **The evidence that refactoring and consolidation pay off is mixed and depends on what is measured.**
   - [An Empirical Study of Refactoring Challenges and Benefits at Microsoft](https://doi.org/10.1109/tse.2014.2318734) (Kim, Zimmermann, Nagappan, TSE 2014) looked at Windows 7. The top 5% of preferentially refactored modules had a larger "reduction in the number of inter-module dependencies and several complexity measures but increase size more". The authors conclude that measurement must be "multi-dimensional". SOURCED.
   - [An experimental investigation on the innate relationship between quality and refactoring](https://doi.org/10.1016/j.jss.2015.05.024) (Bavota et al., JSS 2015) is reported to find that only 7% of refactorings removed the smells they touched. I read this figure only in a secondary source ([arXiv 1907.04797](https://arxiv.org/pdf/1907.04797)), so it is BELIEVED.
   - Cedrim et al. (from the same secondary source, not fetched) report that 2.24% of refactorings remove smells. BELIEVED.
8. **Erosion shows up in review as "inconsistent" and "violate" wording.** [Warnings: Violation symptoms indicating architecture erosion](https://arxiv.org/abs/2212.12168) (Li et al., IST 2023) studied 606 of 21,583 review comments in OpenStack and Qt. Developers discuss 10 categories of violation symptoms. "Inconsistent" and "violate" are the most common terms, and about 90% of responses are refactoring or removing code. SOURCED.

## Measurable signals that consolidation pays off

These are synthesised from the sources above. Signals marked BELIEVED need a project-level test.
- **Rising complexity and warning count per change while velocity is flat or falling.** This is the causal pattern in finding 1, with 30–42% complexity growth in the Cursor study. SOURCED.
- **Concentration of defects and issue-cycle time in low-health files (hotspots).** Code Red reports 15× the defects and 2× the time in those files. This is the payoff estimate for consolidating hotspots. SOURCED, single-source and vendor-linked.
- **Inconsistent changes to clones, rather than the duplication percentage.** SOURCED (Juergens); contested on raw clones (Rahman).
- **A falling share of refactoring or moved lines relative to added lines.** SOURCED (GitClear, vendor-correlational).
- **Review comments that flag inconsistency or rule violations.** SOURCED (Li et al.).
- **A threshold for when consolidation pays.** No source found gives a numeric threshold or ROI for a consolidation pass. ASSUMED gap.

## Unclosed gaps
- No source gives a causal estimate of the benefit of a deliberate consolidation pass in agent-written code, such as velocity or defects before and after.
- The size-controlled complexity estimate in the Cursor study (reportedly 9%) is unread. The GMM percentages are not reproduced.
- Bavota and Cedrim figures need the primary text.
- The 2026 GitClear follow-up figure (moved code at 3.8% of changes) is unverified.
- No study covers churn in agent code, meaning code rewritten within N days. Only GitClear asserts that it rises.

## Sources fetched
He et al. 2026 (arXiv 2511.04427 v3 HTML); Agarwal et al. 2026 (arXiv 2601.13597 v2 HTML); Denisov-Blanch et al. 2026 (dev lit get abstract); Horikawa et al. 2026 (abstract); Tornhill and Borg 2022 (arXiv abstract); GitClear 2025 page; Juergens et al. 2009 (abstract); Rahman et al. (abstract via search); Kim et al. 2014 (abstract); Li et al. 2023 (arXiv abstract); Bavota 2015 (secondary only).
