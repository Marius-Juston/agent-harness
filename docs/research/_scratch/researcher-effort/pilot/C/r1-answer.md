# Round 1 answer: code growth, duplication, churn and erosion in AI-written code; signals that consolidation pays off

Search coverage: discovery 2026-11-20: OK (openalex, s2 answered). Six further `lit search` calls by the search helper were denied by permissions, so it fell back to WebSearch, which has lower recall.

## Findings

1. **AI-tool adoption gives a short burst of velocity, and the complexity it adds stays.**
   - He et al. 2025 ran a staggered difference-in-differences study of 806 Cursor-adopting GitHub repositories against 1,380 matched controls ([Speed at the Cost of Quality: How Cursor AI Increases Short-Term Velocity and Long-Term Complexity in Open-Source Projects](https://arxiv.org/abs/2511.04427), He, Miller, Agarwal, Kästner and Vasilescu 2025, arXiv; the venue MSR is unverified). The abstract reports "a statistically significant, large, but transient increase in project-level development velocity, along with a substantial and persistent increase in static analysis warnings and code complexity", and adds that "increases in static analysis warnings and code complexity are major factors driving long-term velocity slowdown" (SOURCED).
   - Table 2 of the HTML v3, log-scale ATT (SOURCED, via WebFetch):
     - lines added +28.58% (±13.7%)
     - static analysis warnings +30.26% (±6.66%)
     - code complexity +41.64% (±7.62%)
     - commits +2.63% (±4.40%)
   - **Duplication did not rise significantly.** The duplicated-lines-density row is +7.03% (estimate 0.0679, SE 0.0448). That is z ≈ 1.5, so the 95% CI includes 0 (BELIEVED: hand arithmetic; [he_att.py](scripts/he_att.py) is written but was not run because running it needed approval). Section 4.1.2 calls the effect "insignificant", but the table prints \*\*\* against it.
   - Single-source.

2. **Autonomous agents repeat the pattern.** Agarwal, He and Vasilescu ran the same design on the AIDev repositories, defining adoption as the first agent-written PR ([AI IDEs or Autonomous Agents? Measuring the Impact of Coding Agents on Software Development](https://arxiv.org/abs/2601.13597), 2026, arXiv). The abstract reports "static-analysis warnings and cognitive complexity rising by roughly 18% and 39%". It also finds velocity gains "only when agents are the first observable AI tool in a project" (SOURCED). Duplication was one of the outcomes, but the abstract gives no figure (gap). This is the same research group as finding 1, so it is not independent.

3. **Redundancy in agent-written code: one study finds it, one benchmark contradicts it.**
   - For: "LLM Agents frequently disregard code reuse opportunities, resulting in higher levels of redundancy compared to human developers". Reviewers were more neutral or positive towards AI PRs ([More Code, Less Reuse: Investigating Code Quality and Reviewer Sentiment towards AI-generated Pull Requests](https://arxiv.org/abs/2601.21276), Huang et al. 2026, arXiv, abstract; N not read; SOURCED).
   - For: GitClear's vendor report, an observational trend with no AI attribution, covers 211 million changed lines from 2020-2024. Copy/pasted lines "rose from 8.3% to 12.3%", and refactoring-associated (moved) lines "sunk from 25% of changed lines in 2021, to less than 10% in 2024" ([AI Copilot Code Quality 2025](https://www.gitclear.com/ai_assistant_code_quality_2025_research), GitClear 2025; SOURCED).
   - The page gives no churn definition or churn figure. The widely repeated "8x duplicated blocks" and "two-week churn 3.1%→5.7%" numbers come only from secondary press; treat them as unverified.
   - Against, in a benchmark: AI code was "generally simpler and more repetitive", while human code had "a higher concentration of maintainability issues" across more than 500k samples ([Human-Written vs. AI-Generated Code: A Large-Scale Study of Defects, Vulnerabilities, and Complexity](https://arxiv.org/abs/2508.21634), Cotroneo, Improta and Liguori 2025, ISSRE; SOURCED).

4. **AI-introduced issues persist.**
   - Liu et al. 2026 studied 302.6k AI-authored commits in 6,299 repositories ([Debt Behind the AI Boom: A Large-Scale Empirical Study of AI-Generated Code in the Wild](https://arxiv.org/abs/2603.28592), Liu, Widyasari, Zhao, Irsan, Chen and Lo 2026, arXiv; SOURCED):
     - code smells were "89.3% of all issues"
     - "Between 7.5% and 14.8% of each assistant's commits introduce at least one issue"
     - "22.7% of tracked AI-introduced issues still survive"
   - There is no human baseline in the abstract.

5. **Agents do refactor, but the effect on quality is small.**
   - Agents "explicitly target[] refactoring in 26.1% of commits". That is 51,412 instances in 12,256 PRs ([Agentic Refactoring: An Empirical Study of AI Coding Agents](https://arxiv.org/abs/2511.04824), Horikawa et al. 2026, TOSEM; SOURCED).
   - The effect: "small but statistically significant improvements in structural metrics … reducing class size and complexity", while the low-level ones bring "limited" metric gains.
   - Takeaway: a consolidation pass by an agent should target structure (merges, removals), not renames.

6. **Measured quality predicts cost, but the evidence is correlational.**
   - Across 39 proprietary codebases and 30,737 files, "low quality code contains 15 times more defects than high quality code" and "resolving issues in low quality code takes on average 124% more time in development", with "9 times longer maximum cycle times" ([Code Red: The Business Impact of Code Quality -- A Quantitative Study of 39 Proprietary Production Codebases](https://arxiv.org/abs/2203.04374), Tornhill and Borg 2022, TechDebt; SOURCED, abstract re-read).
   - The authors' vendor tool CodeScene defines the quality score. Confounders such as size and activity are not checked (gap).

7. **Classic signals behind a consolidation trigger.** These are BELIEVED: they rest on search summaries only, and I opened no full text.
   - Relative churn (churn normalised by size) predicts defect-prone binaries, while absolute churn does not (Nagappan and Ball 2005, ICSE, *Use of Relative Code Churn Measures to Predict System Defect Density*).
   - Architecture anti-patterns, such as Unstable Interface and Crossing, correlate with bug- and change-proneness (Mo, Cai, Kazman, Xiao et al. 2019, TSE).
   - **Clones are not uniformly harmful.** Kapser and Godfrey 2008 (EMSE, *"Cloning considered harmful" considered harmful*) classify many clone patterns as beneficial. Juergens et al. 2009 (ICSE, *Do code clones matter?*) tie inconsistent changes to clones with faults.
   - **Refactoring has costs.** Some refactoring types induce bugs (Bavota et al. 2012, SCAM; replicated on 103 systems by Di Penta et al. 2020, arXiv:2009.11685). At Microsoft, refactored modules had fewer dependencies and defects (Kim, Zimmermann and Nagappan 2012, FSE).

**What this means for a consolidation trigger (BELIEVED).**
- The best-supported signals in AI-heavy repositories are:
  - growth in complexity and static-analysis warnings, with velocity flattening after an early spike (findings 1-2);
  - churn relative to size;
  - architectural hotspots in files that are both large and changed often.
- Raw duplication alone is weak: its effect is null in finding 1 and contested in finding 7.
- The devkit trigger already uses a growth ratio, large churny files, import cycles and dormant units (`devkit/config.py`). Gaps:
  - **Complexity and warnings per KLOC.** These carry the best causal evidence, and the trigger does not track them as trends.
  - **Velocity.** Nothing compares commits or merged PRs per window against the window before.
  - **Payoff.** No source measures the return of a consolidation pass in agent-written code. Payoff evidence exists only for human code, and it is correlational.

## Unclosed gaps
- The duplication figure in Agarwal et al. 2026 and their full table (replication package: github.com/shyamagarwal13/agentic-coding-impact).
- Primary text for Nagappan and Ball, Mo et al., Juergens et al., Kapser and Godfrey, and Kim et al., to upgrade finding 7 from BELIEVED.
- The GitClear churn method, and its 2026 report (*Write-Only Mode*), not opened.
- No study of architectural erosion in agent-written code, and no before/after study of a consolidation pass in an agent-maintained repository. Both are absences in what this round searched.
- Run `scripts/he_att.py` once permitted.

## Sources fetched
- He et al. 2025, arXiv:2511.04427 (abstract; HTML v3 Table 2 via WebFetch).
- Agarwal, He and Vasilescu 2026, arXiv:2601.13597 (abstract).
- Huang et al. 2026, arXiv:2601.21276 (abstract).
- Liu et al. 2026, arXiv:2603.28592 (abstract).
- Horikawa et al. 2026, arXiv:2511.04824 (abstract).
- Cotroneo et al. 2025, arXiv:2508.21634 (abstract).
- Tornhill and Borg 2022, arXiv:2203.04374 (abstract).
- GitClear 2025 report page (WebFetch).
- Finding 7 sources: search summaries only, via the search helper.
