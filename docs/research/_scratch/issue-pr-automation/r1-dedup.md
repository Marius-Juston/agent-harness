# r1 (c): duplicate and related issue detection for `board new`

Search coverage: discovery 2026-10-20: OK, partial (s2 down; openalex answered) (worst of 8 `dev lit search` runs; the others were OK with both indexes).

Method. Every issue of agent-harness (235) and repo-E (556), read-only through `gh issue list --state all`, on 2026-10-20. The raw text stays in `.scratch/dedup/` (gitignored), because repo-E is private. Only issue numbers are committed ([dedup_labels.json](dedup_labels.json)). Each issue is a query, ranked against every earlier issue in the same repo, open or closed. That is the "realistic" setting of [Rakha et al.](https://doi.org/10.1109/TSE.2017.2755005). There are two label tiers ([dedup_labels.py](dedup_labels.py)):
- **Tier A.** 12 pairs over 10 queries, curated from closing comments: "Duplicate of", "Folded into", "Covered by", "Supersedes". It is never used for tuning.
- **Tier B.** 1,245 pairs over 672 queries. A pair exists when the query's body names an earlier issue as `#N`. Tier B is split by query hash and was frozen before any arm ran: DEV has 476 queries, TEST has 196 queries and 348 pairs.

The pre-registration, written before the first run, is at the end of this file.

## Findings

**F1. Duplicates are rare. About 1.6 % of filings were closed as a duplicate or folded into another issue, and none carries GitHub's `duplicate` reason (VERIFIED, [dedup_explore.py](dedup_explore.py) `volume`, plus a hand read of every NOT_PLANNED closing comment).**
- Of the 791 issues, 782 have the `board new` body (Context and DONE WHEN), and all were opened by the owner's account. So almost every issue was filed through `board new` by an agent. That is BELIEVED, because the owner can run `board new` too.
- 24 issues were closed NOT_PLANNED. Of those, 10 are duplicate, superseded or folded:
  - agent-harness: #10 and #37 (duplicates), #275 and #308 (folded into #338).
  - repo-E: 3 duplicates, 1 duplicate of an open question, 1 superseded, 1 tracked upstream.
  - The other 14 are 11 issues moved to agent-harness and 3 test issues.
- 3 more overlap without a NOT_PLANNED close: repo-E has 2 closed COMPLETED as "covered by" or "folded in", and agent-harness #66 is open but folded into #255.
- Total: 13 of 791 (1.6 %). 0 issues have stateReason DUPLICATE. `gh` 2.102 has `gh issue close --duplicate-of N` and `--reason duplicate` (VERIFIED, `gh issue close --help`). The harness does not use them, so the labels have to be recovered from prose.
- **Detection latency is bimodal.**
  - The 3 repo-E duplicates were filed 0.1-1.3 h after their target and closed within 0.1 h. Parallel work filed the same item twice, and the duplicate was closed at once.
  - agent-harness #10 and #37 stayed open 212 h and 204 h until a lead board sweep found them ([#77 sweep comment](https://github.com/Marius-Juston/agent-harness/issues/77): "Duplicates closed: #10 (to #104), #37 (to #240)").
  - Only 11 of 791 bodies mention "duplicat" in their Context. These are hand "not to duplicate" lists, as in #346.

**F2. On real duplicates (Tier A), every arm puts the target in the top 3 for 8-10 of the 10 queries (VERIFIED, [dedup_eval.py](dedup_eval.py) `test`, output [dedup_test_output.txt](dedup_test_output.txt)).** Recall@3 by arm:
- 0.9 for TF-IDF on title + DONE WHEN (A2);
- 0.8 for BM25 on the same fields (A3);
- 0.9 for BM25 with Context added (A4);
- 1.0 for potion-base-8M (A5).

The bootstrap 95% CIs span about 0.5 to 1.0, because n is 10, so the data does not rank the arms on duplicates. For agent-harness #338, which collects the folded #275 and #308, the first target is at rank 2 (A5), 3 (A6), 4 (A4), 5 (A2) or 6-7 (A3, A7, A8), and is missing from A1's top 10. The reason: "folded into" is a scope relation, not a textual one. [Jahan & Rahman](https://arxiv.org/abs/2212.09976) report the same failure mode for textually dissimilar duplicates (SOURCED).

**F3. On related issues (Tier B TEST, 196 queries), BM25 over title + Context + DONE WHEN is best: recall@3 0.536 [0.464, 0.602]. It beats TF-IDF title + DONE WHEN at 0.464 by +0.071 [0.020, 0.122] (VERIFIED, [dedup_test_output.txt](dedup_test_output.txt)).**

| arm | R@1 | R@3 [95% CI] | R@5 | P@3 | Δ R@3 vs A4 [paired 95% CI] |
|---|---|---|---|---|---|
| A1 TF-IDF title | 0.230 | 0.449 [0.378, 0.520] | 0.536 | 0.158 | −0.087 [−0.163, −0.015] |
| A2 TF-IDF title+DONE WHEN | 0.296 | 0.464 [0.393, 0.531] | 0.526 | 0.167 | −0.071 [−0.122, −0.020] |
| A3 BM25 title+DONE WHEN | 0.270 | 0.480 [0.408, 0.551] | 0.526 | 0.170 | −0.056 [−0.107, −0.005] |
| **A4 BM25 title+Context+DONE WHEN** | 0.327 | **0.536 [0.464, 0.602]** | 0.607 | 0.204 | — |
| A5 potion-base-8M | 0.265 | 0.372 [0.306, 0.444] | 0.480 | 0.134 | −0.163 [−0.230, −0.097] |
| A6 potion-retrieval-32M | 0.265 | 0.429 [0.357, 0.500] | 0.500 | 0.153 | −0.107 [−0.168, −0.041] |
| A7 bge-small-en-v1.5 (fastembed) | 0.219 | 0.398 [0.332, 0.464] | 0.474 | 0.143 | −0.138 [−0.204, −0.077] |
| A8 RRF(A3, A6) | 0.291 | 0.480 [0.408, 0.551] | 0.541 | 0.175 | −0.056 [−0.112, +0.000] |

- The pre-registered rule picks A4. It is also the DEV pick, with DEV R@3 0.567 ([dedup_dev_output.txt](dedup_dev_output.txt)).
- The three local embedding models all rank below lexical BM25: potion-base-8M is the model `dev reuse` already loads, then potion-retrieval-32M, then bge-small. Fusing them with BM25 (A8) adds nothing over BM25 alone.
- This repeats [software-discovery.md](../../software-discovery.md) §P1, where BM25 + potion fusion tied the lexical arm (SOURCED, in-repo). It also matches the DBRD literature (F6).
- P@3 is at most 1/3 on single-target queries, so read it as relative.
- A re-run after renaming the private repository key reproduced every point estimate exactly. The CIs moved by up to ±0.005, because bootstrap order follows the query order.

**F4. Context earns part of A4's gain honestly, but about half of it on TEST is label leakage (VERIFIED, exploratory, [dedup_explore.py](dedup_explore.py) `leak`).** Tier B labels come from `#N` mentions, and the Context sentence that holds a mention often describes the target in words. The check removes every Context sentence that contains `#N` (arm A4x):
- TEST, A4 − A3: +0.056 [+0.005, +0.107]. A4x − A3: +0.026 [−0.015, +0.066].
- DEV, A4 − A3: +0.105. A4x − A3: +0.069 [+0.038, +0.101].

So Context still helps on DEV with the leakage removed, but on TEST the advantage is not significant. On Tier A, which has no such leakage, A4 ties A2 (0.9 each) and A4x scores 0.8. Indexing Context costs nothing, because `board new` already holds it. Still, A4 should be described as "at least as good as" A2/A3, not as a proven +7 points.

**F5. At the DEV-tuned alert threshold, A4 shows 1.0 suggestion per filing and alerts on 48 % of filings. The precision of those alerts is 0.29 against labels alone (a lower bound). Including a manual judgement it is 0.64 on agent-harness. That judgement found 0 unflagged duplicates (VERIFIED for the counts; the judgement is BELIEVED, one rater, [dedup_judged.json](dedup_judged.json)).**
- A4 made 80 suggestions on agent-harness TEST. 24 are labelled. Of the other 56, I judged 27 related (same component or overlapping scope) and 0 duplicates. That gives 51/80 = 0.64. Examples: #241 → #23 (both about Semantic Scholar key cooldowns) and #259 → #153 (both about agent frontmatter effort).
- The remaining false alarms are siblings by template. Release issues match earlier releases, and "Experiment: ..." issues match other experiments.
- No latent duplicate shows among open issues:
  - agent-harness has 118 open issues. No open pair reaches TF-IDF cosine 0.4, and the top 15 pairs are related siblings.
  - repo-E has 347 open issues. 8 pairs reach 0.4, and all 8 are parallel siblings (same template, different scope), judged by hand. No titles are printed, because the repo is private.
- The threshold for BM25 is a raw score (25.8 for A4, [dedup_thresholds.json](dedup_thresholds.json)). It depends on corpus size and query length, so it will drift as the board grows (BELIEVED).

**F6. The literature agrees: lexical retrieval is a strong baseline that recent deep and LLM methods beat only modestly, and GitHub is the hardest setting (SOURCED).**
- [Duplicate Bug Report Detection: How Far Are We?](https://arxiv.org/abs/2212.00548) (Zhang et al., 2022, TOSEM):
  - REP, a BM25F-based method from 2011, outperforms recent deep learning methods on most projects, and Bugzilla's full-text search is a strong baseline.
  - The techniques "do not perform as well on GitHub as they do on Bugzilla and Jira". The issue-tracker effect is significant with a large Cliff's delta for REP on GitHub (|d| 0.66, p < 0.001).
  - RR@k is the standard metric. The authors justify k ≤ 10 by VSCodeBot's 5 recommendations and by practitioners' unwillingness to inspect more than 10 items.
- [Towards more accurate retrieval of duplicate bug reports](https://doi.org/10.1109/ASE.2011.6100061) (Sun et al., 2011, ASE): REP extends BM25F with categorical fields and reaches recall rate@k of 37-71 % on 209,058 Eclipse reports.
- [Revisiting the Performance Evaluation of Automated Approaches for the Retrieval of Duplicate Issue Reports](https://doi.org/10.1109/TSE.2017.2755005) (Rakha, Bezemer & Hassan, 2017, TSE): evaluation on a short window overestimates performance. Using the resolution field gives a median relative gain of 7-21.5 %. Here, candidates are every earlier issue in any state.
- [Cupid: Leveraging ChatGPT for More Accurate Duplicate Bug Report Detection](https://arxiv.org/abs/2308.10022) (Zhang et al., 2023, arXiv): on repositories of around 10k reports, deep learning methods do worse than traditional ones. REP + ChatGPT (2023 model, not comparable to current models) reaches RR@10 of 0.602-0.654, which is 5-8 % over the prior best.
- [Combining Retrieval and Classification](https://arxiv.org/abs/2404.14877) (Meng et al., 2024, arXiv): Sentence-BERT was the best retriever among the models they compared. That was a trained setting on large public datasets, not our 235-556-issue boards (abstract only).

**F7. A dependency-free BM25 reproduces bm25s exactly, and ranking is cheap. The cost is fetching the issues (VERIFIED, [dedup_explore.py](dedup_explore.py) `purepy`).**
- A stdlib BM25 of about 15 lines (Lucene idf, k1 1.5, b 0.75) gives the same top 3 as bm25s on 789/789 queries, with a maximum score difference of 4.3e-05. devkit needs neither numpy nor bm25s (MIT, 1,796 stars, `dev reuse check` PASS).
- bm25s indexing plus one query over 555 issues takes 20 ms. `gh issue list --state all` with bodies takes 5.9 s for repo-E, so `board new` should reuse the board's existing issue fetch or a cache (VERIFIED, one timing each).

**F8. A related-issue list is worth more than a duplicate gate (BELIEVED, from F1 + F3 + F5).**
- With a 1.6 % duplicate rate and precision far below 1, a blocking duplicate check would interrupt about half of all filings to catch about 1 in 60. The [Zhang et al.](https://arxiv.org/abs/2212.00548) pre-submission scenario is also advisory (Mozilla's suggestions as the reporter types).
- An advisory "Related: #a #b #c" line costs nothing to ignore. Agents already list related issues by hand (672 of 791 bodies reference an earlier issue). The same list can pre-fill `## Refs`.

## Recommendation for the note (candidate 4)
- **Do this.** `board new` prints up to 3 "Related:" issues, open or closed in the last 30 days (window ASSUMED; the measurement used all earlier issues). Rank with stdlib BM25 over title + Context + DONE WHEN, excluding `#N` tokens. Show only when the score is at least a threshold, and never block.
- **Re-tune that threshold.** Use a corpus-independent form, for example the score divided by the query's self-score. Re-tune it on DEV before shipping, because raw BM25 25.8 will drift.
- **Label future duplicates in a machine-readable way.** `board` should close duplicates with `gh issue close --duplicate-of N` (stateReason DUPLICATE). The labels then stop depending on prose.
- **Expected yield.**
  - About 1 suggestion per filing, alerting on about half of all filings.
  - About 64 % of suggestions are related (one-rater judgement).
  - Recall@3 is 0.9 on the 10 known duplicates [0.7, 1.0] and 0.54 on body-referenced related issues.
  - Saving: the lead's duplicate sweep (#10 and #37 each stayed open more than 200 h) and the immediate close-as-duplicate of parallel follow-ups (3 in repo-E). Both are small in absolute terms (13 issues in about 10 days across both repos).

## Gaps not closed
- Tier A has only 10 queries. Duplicate-detection recall cannot separate the arms; CIs span 0.5-1.0.
- Titles were edited after filing ([#77](https://github.com/Marius-Juston/agent-harness/issues/77) "Titles rewritten to the conventions on 58 items"), and DONE WHEN and Owns were added later to some issues. The query text is the current text, not the text at filing time. `userContentEdits` (GraphQL) could rebuild it, as [r2-replay](../adr-drift/r2-replay.md) did; that was not done here.
- Precision beyond the labels rests on one rater (me), on agent-harness only.
- Owns overlap as a non-textual feature (the REP idea of categorical fields) was not tested.
- How often agents search the board by hand before `board new` (`gh issue list --search`, `board list`) belongs to the transcripts sub-question. It is not measured here.

## Pre-registration (written 2026-10-20 before any method was run)

- Labels and split: [dedup_labels.json](dedup_labels.json), sha256 prefix `59861d36e2d62065` at freeze. After the TEST run, the private repository's key was renamed to `repo-E`, and the prefix is now `f39799ffa6093e2f`. The pairs and the split are identical (checked by a script diff), and the split hash still uses the repository's own name. Tier A (12 curated duplicate/superseded/folded pairs) is held out whole and never used for tuning. Tier B (body cross-references to an earlier issue) is split DEV/TEST by query hash, 30 % TEST.
- Query: the later-filed issue's title + DONE WHEN (what `board new` has). Candidates: every same-repo issue filed before it, in any state. `#N` tokens are stripped from all text.
- Arms (fixed now):
  - A1: TF-IDF, title only.
  - A2: TF-IDF, title + DONE WHEN. This is the issue's proposal and the reference arm.
  - A3: BM25 (bm25s, k1 1.5, b 0.75), title + DONE WHEN.
  - A4: BM25, title + Context + DONE WHEN.
  - A5: model2vec potion-base-8M (the model `dev reuse` already loads), title + DONE WHEN.
  - A6: model2vec potion-retrieval-32M.
  - A7: fastembed BAAI/bge-small-en-v1.5.
  - A8: RRF (k 60) of A3 and A6.
- Metrics:
  - recall@k for k 1, 3 and 5. A query is a hit if any gold target is in the top k: the RR@k of the DBRD literature.
  - precision@3 over gold queries.
  - alert precision, recall and rate at a score threshold.
- Only tuning: the alert threshold per arm, on Tier B DEV (max F1 of alert precision and alert recall@3). TEST is scored once.
- Decision rule: take the cheapest arm, in the order A1 < A2 < A3 < A4 < A5 < A6 < A7 < A8, whose Tier B TEST recall@3 is not below the best arm's with a paired bootstrap 95% CI that excludes zero. Compare it with the DEV pick.

## Sources fetched
- Zhang, Han, Vinayakarao, Irsan, Xu, Thung, Lo, Jiang. [Duplicate Bug Report Detection: How Far Are We?](https://arxiv.org/abs/2212.00548) TOSEM 2022 (sections 2.2, 4.2, 5.1 and 9 read).
- Sun, Lo, Khoo, Jiang. [Towards more accurate retrieval of duplicate bug reports](https://doi.org/10.1109/ASE.2011.6100061). ASE 2011 (abstract).
- Rakha, Bezemer, Hassan. [Revisiting the Performance Evaluation of Automated Approaches for the Retrieval of Duplicate Issue Reports](https://doi.org/10.1109/TSE.2017.2755005). TSE 2017 (abstract).
- Zhang, Irsan, Thung, Lo. [Cupid: Leveraging ChatGPT for More Accurate Duplicate Bug Report Detection](https://arxiv.org/abs/2308.10022). arXiv 2023 (abstract).
- Jahan, Rahman. [Towards Understanding the Impacts of Textual Dissimilarity on Duplicate Bug Report Detection](https://arxiv.org/abs/2212.09976). SANER 2023 (abstract).
- Meng, Zhang, Ramackers, Visser. [Combining Retrieval and Classification: Balancing Efficiency and Accuracy in Duplicate Bug Report Detection](https://arxiv.org/abs/2404.14877). arXiv 2024 (abstract).
- Tools: [bm25s](https://github.com/xhluca/bm25s) (MIT), [model2vec](https://github.com/minishlab/model2vec) (MIT), [fastembed](https://github.com/qdrant/fastembed) (Apache-2.0), scikit-learn (BSD-3-Clause). Each was checked with `uv run dev reuse check` and run ephemerally with `uv run --no-project --with`, not added to the project.
