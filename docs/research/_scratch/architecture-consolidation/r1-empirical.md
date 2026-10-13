Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc, arxiv OK; s2 DOWN (HTTP 429, shared pool). arXiv MCP was rate-limited, so arXiv pages were read via WebFetch/curl of arxiv.org. Springer/ScienceDirect full texts were paywalled; their abstracts came from OpenAlex, publisher repositories or author PDFs.

# R1: empirical evidence on growth, duplication, churn and erosion (esp. AI/agent code), and signals that a consolidation pays off

## Findings

### A. AI and agent code: growth, duplication, debt

A1. **Cursor adoption: a quick velocity spike, then lasting complexity.** He, Miller, Agarwal, Kästner, Vasilescu ([arXiv:2511.04427](https://arxiv.org/abs/2511.04427), MSR 2026). This is a difference-in-differences study: 806 Cursor-adopting GitHub repos and 1,380 matched controls, observed Jan 2024 to Aug 2025. Adoption is proxied by the first commit of `.cursorrules` or `.cursor/`. The model and Cursor version are **not recorded** (the paper lists this as a threat). Results:
- Lines added rose +281.3% in month 1 and +48.4% in month 2. No later month showed a significant gain. Averaged over the whole period, lines added rose +28.58% (±13.7%). Commits rose +2.63%, which is not significant.
- Static-analysis warnings rose +30.26% (±6.66%) and cognitive complexity rose +41.64% (±7.62%). Both effects persisted.
- Duplicated-line density rose +7.03% (±4.79%), the smallest of the three quality effects. Its significance stars in the HTML table are ambiguous. Duplication was measured with SonarQube, which needs about 10 duplicated statements or 100 tokens to flag a block, so the measure is coarse.
- A dynamic panel GMM model links the quality loss back to speed. A 100% rise in complexity predicts a 64.5% drop in lines added, and a 100% rise in warnings predicts a 50.3% drop. The authors say the velocity gain is cancelled by about a 5x rise in warnings or about a 3x rise in complexity.
- Label: SOURCED (paper HTML read; numbers grepped from the text). I could not re-derive the 3x and 5x break-even figures from the quoted coefficients: a naive log-linear reading gives about 1.5x and 1.8x (script `r1_numbers.py`, see Sources). Treat the break-even numbers as the authors' own. The causal claim rests on matching quality (PS AUC 0.83 to 0.91) and on contamination: 382 treated repos also use Copilot or Claude Code.

A2. **AI-authored commits in the wild leave lasting debt, but they also fix about as many smells as they add.** Liu, Widyasari, Zhao, Irsan, Chen, Lo ([arXiv:2603.28592](https://arxiv.org/abs/2603.28592), v2 Apr 2026). The dataset is 302.6k AI-authored commits from 6,299 repos. Attribution uses explicit git metadata (bot logins, `noreply@anthropic.com`, Co-authored-by trailers). The tools covered are GitHub Copilot, Claude, Cursor, Gemini and Devin; **model versions are not given**.
- The commits introduced 484,366 distinct issues: 89.3% code smells, 6.0% correctness issues and 4.7% security issues.
- More than 15% of commits from every tool introduced at least one issue.
- 22.7% of tracked issues survive at HEAD (105,364 of 464,900).
- The authors also report that AI co-authored commits fix "a similar number of code smell issues as they introduce".
- There is no human baseline, by design.
- Label: SOURCED. The 22.7% survival rate is VERIFIED from the table counts (`r1_numbers.py`).

A3. **Agents stop reusing code as a session goes on; pass rates do not show it.** Ma et al. ([arXiv:2609.35357](https://arxiv.org/abs/2609.35357), 28 Sep 2026), RepoReuse benchmark, 3,000 audited turns. The models are GPT-5.6 Terra, DeepSeek-v4.1-flash, Qwen3.7-plus and GLM-5.3, each run under mini-SWE-agent and OpenCode (2026 versions per the paper).
- The share of task chains with a cross-turn re-implementation rose from 13.8% at turn 1 to 50.8% at turn 5. Per configuration, turn-5 redundancy ranged from 25.3% to 69.3%.
- Repo reuse ranged from 48.9% to 75.7% across configurations.
- Interface memory (OpenCode with Qwen3.7-plus) means giving the agent the signatures and behaviour of functions written in earlier turns. Compared with no memory, it raised self-reuse from 30.0% to 67.8% and cut redundancy from 65.1% to 46.7%. Lines fell from 878 to 576 (−34%, VERIFIED by `r1_numbers.py`). Correction at synthesis (audit): the paper defines this column as "the number of distinct source lines viewed", not lines written. Pass rate stayed flat (72.4% vs 71.9%).
- Giving the agent full source memory did **not** help: self-reuse was 29.2% and redundancy 70.7%.
- This is a benchmark, not a production repo, and only one memory ablation was run.
- Label: SOURCED (model names, the 13.8% and 50.8% figures and Table 5 checked in the paper HTML).

A4. **Agent PRs carry more redundancy than human PRs.** Huang et al. ([arXiv:2601.21276](https://arxiv.org/abs/2601.21276), MSR 2026), built on AIDev.
- Average max redundancy was 0.2867 for agent PRs vs 0.1532 for human PRs (1.87x; Mann-Whitney p<0.001; no effect size reported).
- The redundancy result comes from **one repo** (crewAI, 617 PRs). The lines-added comparison on 3,858 Python PRs showed almost no difference (23.78 vs 23.01).
- Reviewers' sentiment toward agent PRs was more neutral or positive.
- The agents are not named.
- Label: SOURCED (via WebFetch of the HTML). The paper's tables contain internal inconsistencies, so treat this as weak evidence.

A5. **Industry telemetry (not peer-reviewed): copy/paste up, moved code down.** GitClear 2025 report ([gitclear.com](https://www.gitclear.com/ai_assistant_code_quality_2025_research)) analysed 211M changed lines from 2020 to 2024.
- Copy/pasted lines rose from 8.3% (2021) to 12.3% (2024), a 1.48x relative rise (VERIFIED).
- Moved (refactored) lines fell from 25% (2021) to under 10% (2024).
- The page does **not** say how AI-assisted lines are identified, and it is a correlation over time with no model named.
- Its headline "4x cloning" figure has no stated baseline on the page.
- Label: SOURCED, low weight. Secondary press mentions a 2026 report (refactoring at 3.8% of changed lines); I did not fetch it.

A6. **Survey-level delivery effects (DORA 2024, Google).** Per a 25% increase in AI adoption, the report estimates delivery throughput −1.5% and delivery stability −7.2%. Over the same increase, documentation quality rose +7.5%, code quality +3.4% and review speed +3.1%. These are correlational estimates from a survey ([Google Cloud blog](https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report)). No model is named. Label: SOURCED.

A7. **Contrary evidence: an RCT found no downstream maintainability penalty.** Borg et al., "Echoes of AI" ([arXiv:2507.00788](https://arxiv.org/abs/2507.00788), ICSME 2025 registered report, v3 Feb 2026). N=151, 95% professionals.
- Phase 1: participants added a feature with or without an AI assistant.
- Phase 2: randomised new developers evolved that code without AI. There was no significant difference in completion time or code quality, and Bayesian estimates put any effect at small at most.
- Phase 1 (observational) showed a 30.7% median time reduction with AI.
- Assistants and models are not stated in the abstract.
- This is one feature in one Java app, so it says little about multi-month accumulation.
- Label: SOURCED.

A8. **Agents refactor locally, not architecturally.** Two studies:
- Horikawa et al. ([arXiv:2511.04824](https://arxiv.org/abs/2511.04824), Nov 2025; AIDev Java subset): 15,451 refactorings in 12,256 PRs. Agents explicitly targeted refactoring in 26.1% of commits. The most common types were Change Variable Type (11.8%), Rename Parameter (10.4%) and Rename Variable (8.5%). Structural gains were "small but statistically significant", with a median class-LOC change of −15.25. The authors say agents favour localized edits over the high-level design changes that humans make.
- Watanabe et al. ([TOSEM 2026, doi:10.1145/3798166](https://doi.org/10.1145/3798166)) studied 567 Claude Code PRs in 157 projects (model version not in the abstract). Refactoring was among the commonest tasks; 83.8% of the PRs were merged and 54.9% of merged PRs needed no change.
- Label: SOURCED. BELIEVED: agents will not consolidate architecture unprompted. It has to be triggered and scoped explicitly.

A9. **Unrequested refactoring hurts patch compilability.** Tian et al. ([arXiv:2605.22526](https://arxiv.org/abs/2605.22526), May 2026). The study covers 3,691 Multi-SWE-bench patches from 3 agent frameworks and 12 LLMs (the abstract does not list them).
- Agents tangle refactoring into fixes less often than humans (21.43% vs 36.72% of patches).
- Tangled refactoring is associated with lower compilability but not with lower functional correctness.
- A refinement step that checks each tangled refactoring raised compilability from 19.34% to 38.33%.
- Label: SOURCED. Implication (BELIEVED): keep consolidation separate from feature work, so it gets its own PRs and gate.

### B. Classic evidence: where cost concentrates, and which signals predict it

B1. **Process metrics (churn, change history) predict defects better than static code metrics.**
- Nagappan & Ball ([ICSE 2005, doi:10.1145/1062455.1062514](https://doi.org/10.1145/1062455.1062514)), Windows Server 2003: absolute churn predicts defect density poorly. *Relative* churn (churn normalised by size and by time span) predicts it well and separates fault-prone binaries with 89.0% accuracy.
- Moser, Pedrycz, Succi ([ICSE 2008, doi:10.1145/1368088.1368114](https://doi.org/10.1145/1368088.1368114)), Eclipse: process metrics beat code metrics, with >75% correctly classified, recall >80% and FPR <30%.
- Hassan ([ICSE 2009, doi:10.1109/ICSE.2009.5070510](https://doi.org/10.1109/ICSE.2009.5070510)): the entropy (scatter) of changes beats prior modifications and prior faults as a predictor.
- Nagappan, Murphy, Basili ([ICSE 2008, doi:10.1145/1368088.1368160](https://doi.org/10.1145/1368088.1368160)), Windows Vista: organisational metrics beat churn, complexity, coverage and dependencies.
- Label: SOURCED (abstracts). These are pre-LLM, single-company or single-project results.

B2. **Maintenance cost is concentrated in a few architecturally connected file groups.**
- Xiao, Cai, Kazman ([ICSE 2016, doi:10.1145/2884781.2884822](https://doi.org/10.1145/2884781.2884822)), 7 OSS projects: the top 5 architectural debts cover 8–25% of error-prone files but 20–61% of maintenance effort.
- Mo et al. ([WICSA 2015, doi:10.1109/WICSA.2015.12](https://doi.org/10.1109/WICSA.2015.12)): hotspot patterns that combine structure and history (Unstable Interface, Implicit Cross-module Dependency) explain error- and change-proneness better than other patterns. The more patterns a file is involved in, the more error-prone it is.
- ICSA 2021 ([doi:10.1109/icsa51549.2021.00017](https://doi.org/10.1109/icsa51549.2021.00017)), 10 OSS systems: current architectural-smell data predicts future issue- and change-proneness, even when trained on other systems.
- Label: SOURCED (abstracts).

B3. **Low "code health" carries a large measured cost.**
- Tornhill & Borg, "Code Red" ([arXiv:2203.04374](https://arxiv.org/abs/2203.04374), TechDebt 2022), 39 proprietary codebases and 30,737 files. Compared with healthy code, low-quality code has 15x more defects, takes 124% longer to resolve issues, and has up to 9x longer maximum cycle time. The authors are the CodeScene founder and staff, which is a conflict of interest.
- Borg, Pruvost, Mones, Tornhill ([arXiv:2401.13407](https://arxiv.org/abs/2401.13407), TechDebt 2024): returns from code quality are nonlinear and largest at the high-quality end. The authors recommend preventing smells in **high-churn files**.
- Palomba et al. ([EMSE 2017, doi:10.1007/s10664-017-9535-z](https://doi.org/10.1007/s10664-017-9535-z)), 395 releases of 30 projects and 17,350 validated smells: smelly classes are more change- and fault-prone. Long or complex-code smells are the most widespread.
- Label: SOURCED.

B4. **Duplication: what matters is clones that are changed inconsistently.**
- Juergens et al. ([ICSE 2009, doi:10.1109/ICSE.2009.5070547](https://doi.org/10.1109/ICSE.2009.5070547); [arXiv:1701.05472](https://arxiv.org/abs/1701.05472)) studied 5 systems and 1,427 clone groups. 52% of clone groups were inconsistent (mean of per-system ratios; the pooled figure is 50.7%). 28% of those inconsistencies were unintentional. 15% of inconsistent groups, and 50–53% of unintentional ones, contained a fault: 107 faults in total. Pooled ratios are VERIFIED from Table 2 (`r1_numbers.py`).
- Counter-evidence: Rahman, Bird, Devanbu ([MSR 2010 PDF](https://microsoft.com/en-us/research/wp-content/uploads/2016/02/rahman2010cws.pdf); [EMSE 2012](https://doi.org/10.1007/s10664-011-9195-3)): "the great majority of bugs are not significantly associated with clones", cloned code "may be less defect prone", and more copies do not mean more errors. Kapser & Godfrey ([EMSE 2008](https://doi.org/10.1007/s10664-008-9076-6)) argue that much cloning is deliberate; I fetched only the bibliographic record, not the abstract text.
- Label: SOURCED. BELIEVED synthesis: raw duplicate percentage is a weak trigger. Clones that **co-change** (or have diverged) are the target.

B5. **Refactoring does not pay off automatically. It has to be aimed, and LOC is the wrong scoreboard.**
- Bavota et al. ([JSS 2015, doi:10.1016/j.jss.2015.05.024](https://doi.org/10.1016/j.jss.2015.05.024); abstract via [IRIS Unisa](https://www.iris.unisa.it/handle/11386/4655580)), 3 Java projects: 42% of refactorings touch smelly code, and only 7% remove the smell.
- Kim, Zimmermann, Nagappan ([TSE 2014, doi:10.1109/TSE.2014.2318734](https://doi.org/10.1109/TSE.2014.2318734)), Windows 7: the top 5% of preferentially refactored modules lost more inter-module dependencies and complexity, but **grew more in size** than the rest. Developers see refactoring as costly and risky. The authors conclude that payoff "requires multi-dimensional assessment".
- Label: SOURCED (abstracts). The earlier FSE 2012 version reportedly also found fewer post-release defects; I did not fetch it, so this is not verified.

B6. **Defect-prediction thresholds do not transfer between projects.** Zimmermann et al. ([ESEC/FSE 2009, doi:10.1145/1595696.1595713](https://doi.org/10.1145/1595696.1595713)) ran 622 cross-project predictions and found transfer "a serious challenge". Label: SOURCED. Implication (BELIEVED): calibrate triggers against each repo's own history, not against fixed cross-repo cut-offs.

### C. Signals that predict a consolidation will pay off (synthesis, BELIEVED unless marked)

1. **Hotspot = relative churn × complexity, aggregated over co-change groups** (B1, B2, B3). Consolidate where change effort concentrates, not where LOC is largest. Xiao et al.'s 8–25% of files carrying 20–61% of effort is the size of prize to look for.
2. **A rising trend in complexity or warnings while the feature count is flat** (A1). In He et al., complexity growth is what drags later velocity down, so a complexity-per-feature trend is a better trigger than absolute LOC.
3. **Duplicated logic across agent sessions that co-changes or has diverged** (A3, A4, B4). Raw clone percentage has mixed defect evidence (B4).
4. **Surviving static-analysis debt from agent commits** (A2): the 22.7% of issues that are still alive at HEAD.
5. **Payoff metrics should be multi-dimensional:** dependencies, complexity, co-change coupling and defect or rework rate, not LOC alone (B5).
6. **Prevention is cheaper than consolidation.** Giving agents a map of existing interfaces cut redundant code and lines by about a third with no loss in pass rate (A3). Free tools exist for history-based hotspot signals: [code-maat](https://github.com/adamtornhill/code-maat) (2,640 stars, last push 2025-07-03; the API reports no SPDX license) and [wily](https://github.com/tonybaloney/wily) (Python complexity over git history; 1,332 stars, pushed 2026-10-07, Apache-2.0). Both were checked with the GitHub API on 2026-10-12.

## Gaps not closed
- No longitudinal study that measures **net LOC reduction while preserving behaviour** in an agent-run repo, which is the "100k → 20k" case. No study gives a size threshold at which consolidation pays off.
- The Nagappan organisational-structure result: I did not fetch replication attempts.
- Kapser & Godfrey 2008, the full text of Bavota 2015, and Kim et al. FSE 2012 (defect reduction): abstracts or metadata only.
- GitClear 2026 report (refactoring at 3.8%): seen in press only.
- The full DORA 2024 report (confidence intervals) and DORA 2025 (the reported throughput flip) were not fetched.
- The 3x and 5x break-even figures in He et al. could not be reproduced from the quoted coefficients.
- "Where do AI coding agents fail?" ([arXiv:2601.15195](https://arxiv.org/abs/2601.15195)) and the AIDev longitudinal study ([arXiv:2607.21832](https://arxiv.org/abs/2607.21832)) were found but not read past the abstract.

## Sources fetched
- He et al. 2026, arXiv:2511.04427 (abs page and HTML v3)
- Liu et al. 2026, arXiv:2603.28592 (abs page and HTML)
- Ma et al. 2026, arXiv:2609.35357 (abs page and HTML)
- Huang et al. 2026, arXiv:2601.21276 (abs page and HTML)
- Horikawa et al. 2025, arXiv:2511.04824 (abs page)
- Tian et al. 2026, arXiv:2605.22526 (abs page)
- Watanabe et al. 2026, doi:10.1145/3798166 (OpenAlex abstract; Crossref authors)
- Li et al. 2025, AIDev, arXiv:2507.15003 (abstract)
- Borg et al. 2025/26, arXiv:2507.00788
- Tornhill & Borg 2022, arXiv:2203.04374
- Borg et al. 2024, arXiv:2401.13407
- GitClear 2025 page
- Google Cloud DORA 2024 blog
- Nagappan & Ball 2005; Moser et al. 2008; Hassan 2009; Nagappan et al. 2008; Kamei et al. 2013 (doi:10.1109/TSE.2012.70; abstract seen, not used); Zimmermann et al. 2009; Xiao et al. 2016; Mo et al. 2015; ICSA 2021 decay paper; Palomba et al. 2017; Kim et al. 2014 (all OpenAlex abstracts)
- Juergens et al. 2009 (arXiv PDF; Table 2 read)
- Rahman et al. 2010 (MSR PDF; abstract read)
- Bavota et al. 2015 (IRIS abstract)
- GitHub API: adamtornhill/code-maat, tonybaloney/wily
- Verification script: `scripts/r1_numbers.py` (in this folder)
