# r2: BM25 variants for the related-issue line of `board new` (#346, round 2)

Round mode for #346, asked by the owner after round 1: does a bm25s scoring variant, a different k1/b, or a
field-weighted BM25F beat the plain BM25 that #350 ships? Script: [bm25_variants.py](bm25_variants.py).
Data, labels and split are round 1's, unchanged: [dedup_labels.json](dedup_labels.json) (Tier B DEV 476 queries,
TEST 196 queries; Tier A 10 curated duplicate queries), built and frozen by [dedup_labels.py](dedup_labels.py) and
described in [r1-dedup.md](r1-dedup.md).

Search coverage: no `dev lit search` was run for this round; it re-scores round 1's data. Round 1's worst line was
`discovery 2026-10-20: OK, partial (s2 down; openalex answered)`.

## Pre-registration (written and committed 2026-10-20 before any arm below was run)

**Status of TEST before this round.** Round 1 scored TEST once for its eight arms, including the baseline here
(A4: bm25s lucene, k1 1.5, b 0.75, title + Context + DONE WHEN): TEST recall@3 0.536. No arm of this round has been
scored on TEST or DEV.

**Text conditions.** Both use round 1's cleaning (`dedup_eval.clean`: `#N` tokens and checkbox marks stripped) and
tokenizer (lowercase `\w\w+`, scikit-learn English stop words removed).
- `leak-removed` (**primary**): every Context sentence that contains `#N` is dropped, in queries and candidates
  alike, exactly as round 1's `dedup_explore.py leak` (arm A4x). Reason: Tier B labels come from `#N` mentions, and
  the sentence holding the mention often describes the target in words. Round 1 found that about half of A4's gain
  over title + DONE WHEN on TEST came from that sentence. An arm that weights Context up could win on leakage alone.
- `full`: the A4 text, as `board new` would index it. Reported for every arm, never used to select.

**Arms (fixed now).** Each query is ranked against every earlier issue of its repository, in any state, as in
round 1. Query text = title + Context + DONE WHEN of the query issue under the same condition.
- B0, baseline: bm25s `method="lucene"`, k1 1.5, b 0.75. (On `full` this is round 1's A4.)
- Methods, bm25s 0.3.14 defaults otherwise (k1 1.5, b 0.75, delta 0.5): M1 `robertson`, M2 `atire`, M3 `bm25l`,
  M4 `bm25+`.
- Grid on `lucene`: k1 ∈ {0.9, 1.2, 1.5, 2.0} × b ∈ {0.5, 0.75, 1.0}, the 11 cells other than B0, named
  G-k1-b (for example G-1.2-0.5).
- BM25F, stdlib (no numpy), Lucene idf over whole-document document frequency, k1 1.5, b 0.75 per field, each
  field length-normalised by its own average over the candidates, field weights (title, Context, DONE WHEN):
  F1 (1, 1, 1), F2 (2, 1, 1), F3 (3, 1, 1), F4 (2, 0.5, 1). Per document and term,
  tf~ = Σ_f w_f · tf_f / (1 − b + b · len_f / avglen_f), and the score is Σ over query tokens of
  idf · tf~ / (k1 + tf~), each repeated query token counted again, as bm25s does.

That is 19 arms against B0.

**Metric.** recall@3 on Tier B (a query is a hit if any gold target is in the top 3), as in round 1. recall@1 and
recall@5 are reported but not used.

**Selection rule (DEV only).**
1. Score every arm on Tier B DEV, `leak-removed`.
2. The candidate is the arm with the highest mean DEV recall@3; a tie goes to the earlier arm in the order above
   (methods, then grid by k1 then b, then F1-F4).
3. Paired bootstrap of (candidate − B0) per query on DEV: 2,000 resamples, seed 346 (`dedup_eval.boot`), 95 % CI.
4. **Pick = the candidate only if that CI excludes 0 (lower bound > 0); otherwise pick = B0.**
5. TEST is scored once, for the pick and B0 only, both text conditions, with the paired bootstrap of (pick − B0).
   If pick = B0, TEST is scored for B0 on `leak-removed` only (its `full` TEST score is round 1's).
6. Tier A (10 real duplicate queries, never tuned on): top-3 hits for the pick and B0, both conditions.
7. The note recommends the pick over B0 only if its TEST point difference is also above 0; else B0 stays.

**Known biases, stated now.** The DEV maximum over 19 arms is optimistic (winner's curse), and a CI on the same DEV
data does not correct for it; the one-shot TEST is the guard. DEV and TEST share repositories and period. One rater's
judged precision (round 1) is not re-run here.

## Results
(filled in after the run, below this line; the pre-registration above is not edited)

Labels: VERIFIED = measured by [bm25_variants.py](bm25_variants.py); BELIEVED = judgement. Outputs:
[bm25_variants_dev.txt](bm25_variants_dev.txt), [bm25_variants_pick.json](bm25_variants_pick.json),
[bm25_variants_test.txt](bm25_variants_test.txt).

**Implementation checks, run after the commit and before any scoring (VERIFIED).** B0 on `full` gives the same top 3
as round 1's A4 on 673/673 labelled queries. The stdlib BM25F with all text in one field matches round 1's stdlib
BM25 (`dedup_explore.bm25_pure`) to a maximum score difference of 1.1e-14 on 59 agent-harness queries.

**R1. No variant beats plain BM25 on DEV; the pick is B0 (VERIFIED, bm25_variants_dev.txt).**
- On `leak-removed` DEV (476 queries) B0 has recall@3 0.532 [0.487, 0.578]. The 19 arms lie between −0.021 and
  +0.013 of it. No paired CI has a lower bound above 0:
  - the best mean, G-1.2-0.5 (k1 1.2, b 0.5): +0.013 [−0.008, +0.034];
  - ATIRE (M2) and BM25+ (M4): +0.004 [+0.000, +0.011], a lower bound of exactly 0, which the rule does not accept;
  - Robertson (M1) −0.002, BM25L (M3) −0.006, both with CIs spanning 0.
- So by the pre-registered rule the pick is B0, and TEST was scored for B0 alone.
- **b = 1.0 is worse.** G-1.2-1.0 −0.017 [−0.032, −0.004] and G-2.0-1.0 −0.021 [−0.040, −0.002] are below B0 with
  CIs that exclude 0; G-0.9-1.0 and G-1.5-1.0 are below it too, with CIs touching 0. Full length normalisation
  penalises the long Context of related issues (BELIEVED for the cause).
- **Field weighting adds nothing.** Weighting the title up does not help: F1 (1, 1, 1) −0.011, F2 (2, 1, 1) −0.004,
  F3 (3, 1, 1) 0.000. F4, with Context weighted down to 0.5, is +0.011 [−0.011, +0.032].
- The `full` text gives the same picture, 0.025-0.038 higher for every arm (B0 0.567). F4 is again the best mean there,
  +0.011 [−0.011, +0.029], and G-2.0-1.0 is again worse, −0.019 [−0.038, −0.002]. Not used for selection.

**R2. TEST, scored once for the pick (VERIFIED, bm25_variants_test.txt).** B0 on `leak-removed` TEST (196 queries):
recall@1 0.321, recall@3 **0.505 [0.439, 0.571]**, recall@5 0.571. Round 1's `full` TEST score for the same arm is
0.536 [0.464, 0.602]. So the leakage in the Context field is worth about 0.03 of recall@3 on both splits (DEV 0.567
against 0.532).

**R3. Tier A, the 10 real duplicates (VERIFIED).** B0 finds the target in the top 3 for 8 of 10 queries on
`leak-removed` and 9 of 10 on `full`.

**What it means for #350 (BELIEVED).**
- Ship plain BM25 as specified: Lucene idf, k1 1.5, b 0.75, one field of title + Context + DONE WHEN. A 15-line
  stdlib version suffices; no variant, grid cell or field weighting earns its extra code.
- #350's DONE WHEN asks for recall@3 of at least 0.50 on TEST. That is met on `full` text (0.536), but on
  `leak-removed` text, the closer model of a filer who does not already know the related issue, it holds by only
  0.005, inside the CI. Read 0.50 as a regression floor, not as a margin.

## Gaps not closed
- Repositories are pooled; the per-repository split of round 1 (agent-harness alone) was not repeated for the variants.
- Only four BM25F weightings and one per-field b were tried. A finer search would need its own DEV split, because
  this DEV set has now been used to choose.
- The DEV CIs are not corrected for 19 comparisons. That matters only for an arm that passes, and none did.
- One tokenizer (round 1's) for every arm.
