Search coverage: discovery 2026-11-20: OK, partial (s2 down; openalex answered) (worst line, on one of 11 `dev lit search`
calls; the other 10 printed OK). Also used: arxiv MCP abstracts, WebFetch of arXiv HTML full text (2511.04427v3,
2601.13597v2, 2606.13298v1) and of one vendor page (GitClear). note: arxiv `download_paper` and `search_paper_text`
were denied, and the Juergens et al. PDF could not be text-extracted (pdftotext needs approval), so that paper is cited
from its abstract only.

# r1: code growth, duplication, churn and erosion in AI-written code; signals that consolidation pays off

Round 1 sub-question: empirical evidence on code growth, duplication, churn and architectural erosion, especially in
AI- or agent-written code, and measurable signals that consolidating a codebase pays off.

## Findings

### F1. The causal evidence says agent adoption raises complexity and warnings persistently; velocity gains fade
- SOURCED. [Speed at the Cost of Quality: How Cursor AI Increases Short-Term Velocity and Long-Term Complexity in Open-Source Projects](https://arxiv.org/abs/2511.04427)
  (He, Miller, Agarwal, Kästner, Vasilescu; MSR 2026). Staggered difference-in-differences, 806 Cursor-adopting GitHub
  repositories against 1,380 matched controls. §4.1.2: "static analysis warnings increase significantly by 30.3%" and
  "code complexity increases by 41.6%" (Table 2: β 0.2644 and 0.3481). §4.1.1: lines added rise "281.3% … in the
  first month", and "The only significant development velocity gain is in the first two months post Cursor adoption."
  After controlling for codebase size, complexity still has "a 9% baseline increase" (§4.2, Table 3: 0.086, SE 0.030).
- SOURCED. [AI IDEs or Autonomous Agents? Measuring the Impact of Coding Agents on Software Development](https://arxiv.org/abs/2601.13597)
  (Agarwal, He, Vasilescu; MSR 2026). Same design, adoption defined as the first agent-authored PR (AIDev dataset):
  401 agent-first repositories against 606 controls, and 117 IDE-first against 73 (§3.1). Abstract: "static-analysis
  warnings and cognitive complexity rising by roughly 18% and 39%". Table 2, agent-first: warnings β 0.163 (SE 0.048,
  +17.7 %), complexity β 0.299 (SE 0.059, +34.9 %); IDE-first complexity β 0.357 (SE 0.114, +42.9 %). LOC is a covariate (§3.2).
- SOURCED (hypothesis-generating, observational strata). [A Few Pages of Markdown: Committed AI Configuration and Lower Quality Cost after Coding-Agent Adoption](https://arxiv.org/abs/2608.25241)
  (Denisov-Blanch et al.; ASE 2026), 441 repositories. Abstract: among agent-first repositories, "those without
  committed AI configuration show roughly twice the increase in cognitive complexity (+53% versus +27%) and 1.7x the
  increase in static-analysis warnings." The authors say discipline or model capability "may explain part of the gap".
- These two DiD papers share authors and data lineage (He, Agarwal, Vasilescu), so they are not independent
  replications of each other (BELIEVED). The GMM link from complexity to later slowdown in He et al. is reported as
  "a 64.5% and 50.3% decrease in development velocity" (§4.2), but the Table 3 coefficients (−0.718, −0.588 on
  log lines added) imply −51 % and −44 % if read as log-point effects (BELIEVED arithmetic, computed by hand; the script
  [scripts/r1_effects.py](scripts/r1_effects.py) was written but its run needs approval). Treat the size of that
  slowdown as unsettled.

### F2. Duplication is NOT where the causal studies find the damage; the duplication alarm comes from a vendor
- SOURCED. He et al. §4.1.2: "The effect on duplicate line density is insignificant." (Table 2 prints 0.0679 with
  stars, +7.0 %, which conflicts with the text; flagged, unresolved.) Agarwal et al. §4: "Duplication effects are small
  and inconsistent, suggesting quality risks stem from structural complexity rather than copy–paste proliferation"
  (agent-first +7.9 %, IDE-first −0.9 %, both not significant).
- SOURCED, vendor, methods undisclosed. [The Maintainability Gap: AI Code Quality in 2026](https://www.gitclear.com/the_ai_code_quality_maintainability_gap)
  (GitClear, 2026): "623 million analyzed changes from 2023-2026"; moved code (their refactoring proxy) fell from 21 %
  of changed lines in 2022 to 3.8 % in 2026 YTD; "copy/paste climbed from 9.4% (in 2022) to 15.7% in the first half of
  2026"; duplicated blocks ("five or more consecutive repeated meaningful lines") 40.3 → 73.0 per unit of changes; two-week
  churn up 15 %. The page does not say how moved or copied lines are detected, nor how AI authorship is determined, and
  the units of the block metric disagree between text and chart. It is a secular trend, not a causal estimate
  (BELIEVED). Use it as a hypothesis, not as evidence.
- Pre-AI evidence on whether clones cause defects is split. SOURCED: [Do Code Clones Matter?](https://arxiv.org/abs/1701.05472)
  (Juergens, Deissenboeck, Hummel, Wagner; ICSE 2009; abstract only): inconsistent changes to clones "are very frequent"
  and "a significant number of faults" were induced by them. Contrary, SOURCED via search snippet of the abstract (not
  fetched in full): [Clones: what is that smell?](https://doi.org/10.1007/s10664-011-9195-3) (Rahman, Bird, Devanbu;
  EMSE 2012; four OSS projects) finds most bugs have no significant link to clones and cloned code may be *less*
  defect-prone. So the risk is in clones that must change together and drift, not in duplication per se (BELIEVED).

### F3. Architecture-level erosion: no causal evidence yet that agents raise it; density metrics mislead
- SOURCED. [Mining Architectural Quality Under Agentic AI Adoption: A Causal Study of Java Repositories](https://arxiv.org/abs/2606.13298)
  (Larsen, Moghaddam; Euromicro SEAA 2026). 151 Java repositories (74 adopters, 77 matched), 1,811 monthly Arcan
  snapshots. Abstract: "Total smell counts are essentially unchanged (+1.1%, p = 0.82) while lines of code grow +12.8%
  (p = 0.003); the resulting 6.7% ASD decline (p = 0.004) is therefore a denominator effect rather than an architectural
  improvement." Only hub-like density survives Holm correction (−5.0 %); raw unstable-dependency counts +5.8 % (Holm p
  0.128) (§4.3). The lesson is methodological: "Density-normalized outcomes can mislead when treatment affects system
  size: raw counts and explicit decomposition are required" (abstract).
- SOURCED, lab and generated systems, not field. [AI-Generated Smells: An Analysis of Code and Architecture in LLM and Agent-Driven Development](https://arxiv.org/abs/2605.02741)
  (Zhu, Tsantalis, Rigby; arXiv 2026): claims "code volume is a near perfect predictor of structural degradation" and
  "neither functional correctness nor detailed prompting mitigates this decay" (abstract). Effect sizes not read.
- SOURCED. [Understanding software architecture erosion: A systematic mapping study](https://doi.org/10.1002/smr.2423)
  (Li, Liang, Soliman, Avgeriou; JSEP 2022; 73 studies): erosion shows as violations, structural issues and quality
  problems; "More empirical studies are required … in industrial settings." No agreed erosion metric (BELIEVED from abstract).

### F4. Persistence: AI-introduced issues are mostly smells, and about a fifth survive
- SOURCED. [Debt Behind the AI Boom: A Large-Scale Empirical Study of AI-Generated Code in the Wild](https://arxiv.org/abs/2603.28592)
  (Liu, Widyasari, Zhao, Irsan, Chen, Lo; arXiv 2026): 302.6k AI-authored commits in 6,299 repositories; "code smells
  are by far the most common type, accounting for 89.3% of all issues"; "Between 7.5% and 14.8% of each assistant's
  commits introduce at least one issue"; "22.7% of tracked AI-introduced issues still survive at the latest version".
- SOURCED. [Agentic Refactoring: An Empirical Study of AI Coding Agents](https://arxiv.org/abs/2511.04824) (Horikawa
  et al.; TOSEM 2026): agents target refactoring in "26.1% of commits", but favour low-level edits (Rename Variable
  7.6 %, Change Variable Type 6.8 %) whose "measured metric improvements … remain limited"; overall "small but
  statistically significant improvements" in class size and complexity. Agents refactor, but not the structural kind
  that would cut complexity (BELIEVED).

### F5. Signals that consolidation pays off (mostly pre-AI, correlational, vendor-adjacent)
- SOURCED. [Code Red: The Business Impact of Code Quality](https://arxiv.org/abs/2203.04374) (Tornhill, Borg; TechDebt
  2022; 39 proprietary codebases, 30,737 files, CodeScene Code Health): "low quality code contains 15 times more
  defects than high quality code"; issue resolution "takes on average 124% more time"; "9 times longer maximum cycle
  times". Correlational; authors are from the vendor of the metric.
- SOURCED. [Increasing, not Diminishing: Investigating the Returns of Highly Maintainable Code](https://arxiv.org/abs/2401.13407)
  (Borg, Pruvost, Mones, Tornhill; TechDebt 2024): "strong non-linearities at the extremes"; recommends "diligently
  prevent the introduction of code smells in files with high churn". Same group, so not independent of Code Red.
- SOURCED. [Use of relative code churn measures to predict system defect density](https://doi.org/10.1145/1062455.1062514)
  (Nagappan, Ball; ICSE 2005; Windows Server 2003): "absolute measures of code churn are poor predictors of defect
  density" but relative ones discriminate fault-prone binaries "with an accuracy of 89.0 percent". So the target signal
  is churn relative to size, concentrated in files (hotspots), not total growth.
- SOURCED. [An Empirical Study of Refactoring Challenges and Benefits at Microsoft](https://doi.org/10.1109/tse.2014.2318734)
  (Kim, Zimmermann, Nagappan; TSE 2014): the top 5 % most refactored Windows 7 modules saw "higher reduction in the
  number of inter-module dependencies and several complexity measures but increase size more than the bottom 95%";
  developers perceive "substantial cost and risks". A consolidation does not have to shrink LOC to pay off.
- Causal (in a slowdown sense, not a consolidation trial): He et al.'s GMM attributes later velocity loss to
  accumulated warnings and complexity (F1). No study found here randomises or quasi-experimentally tests a
  consolidation and measures the payoff (gap).

### F6. What this implies for a harness (BELIEVED unless marked)
- Track **raw counts and size together**, never a density alone (single-source: Larsen and Moghaddam abstract,
  "raw counts and explicit decomposition are required").
- The measurable signals with evidence behind them, in order: (1) cognitive complexity and static-analysis warning
  **counts** per month, which rise ~18-42 % after agent adoption (two DiD papers, shared authors); (2) relative churn on
  hotspot files (Nagappan and Ball; Borg et al.); (3) clone groups that change inconsistently (Juergens et al.), not
  overall duplication rate (null in both DiD papers; GitClear's rise is undisclosed-method vendor data).
- A consolidation pays off when it targets high-churn, low-health files; payoff is visible as fewer defects and
  shorter, less variable cycle time on those files (Code Red: 15x defects, +124 % time), and as lower dependency counts
  rather than fewer lines (Kim et al.). These are correlational magnitudes; a project would need its own before/after
  on matched files to claim a causal gain.

## Unclosed gaps
- No experiment or quasi-experiment on consolidation payoff (refactor vs not, matched files, defect and cycle-time outcomes).
- He et al.: text vs Table 2 conflict on duplication significance; GMM percentages vs coefficients. Needs the paper's
  model specification (§3-4 beyond the first 100k characters of the HTML were not read).
- Juergens et al. numbers (share of inconsistent clone changes that are faults) not extracted: PDF text extraction
  needs approval. Rahman et al. read only via a search snippet of the abstract.
- GitClear methodology (how "moved", "copy/paste" and AI authorship are detected) undisclosed on the page.
- Coppola et al. ([AI Writes Code, Humans Pay the Debt](https://arxiv.org/abs/2609.04208), 2026) is a registered
  report with no results yet; worth re-checking later.
- `scripts/r1_effects.py` recomputes every percentage from the coefficients; running it needs approval, so the
  arithmetic above is by hand (BELIEVED, not VERIFIED).

## Sources fetched
- He et al. 2025/2026, arXiv 2511.04427 (abstract; HTML v3 first 100k characters).
- Agarwal, He, Vasilescu 2026, arXiv 2601.13597 (abstract; HTML v2 §3-4, Table 2).
- Denisov-Blanch et al. 2026, arXiv 2608.25241 (abstract).
- Larsen, Moghaddam 2026, arXiv 2606.13298 (abstract; HTML v1 §4, §6).
- Zhu, Tsantalis, Rigby 2026, arXiv 2605.02741 (abstract).
- Liu et al. 2026, arXiv 2603.28592 (abstract).
- Horikawa et al. 2026, arXiv 2511.04824 (abstract).
- Coppola et al. 2026, arXiv 2609.04208 (abstract; registered report).
- Tornhill, Borg 2022, arXiv 2203.04374 (abstract). Borg et al. 2024, arXiv 2401.13407 (abstract).
- Nagappan, Ball 2005; Kim, Zimmermann, Nagappan 2014; Li et al. 2022; Rahman, Bird, Devanbu 2012 (metadata and
  abstracts via `dev lit get`, Rahman via web search snippet). Juergens et al. 2009, arXiv 1701.05472 (abstract).
- GitClear, "The Maintainability Gap: AI Code Quality in 2026" (web page).
