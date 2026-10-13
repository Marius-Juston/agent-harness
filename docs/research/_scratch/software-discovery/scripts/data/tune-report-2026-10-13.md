PyPI index: 30000 packages, 26169 with a GitHub repo, embedded in 0.4s

## TUNE, all (n = 64)

| strategy | hit@3 [95% CI] (primary) | hit@1 | hit@10 [95% CI] | MRR@10 | pool hit | median pool |
|---|---|---|---|---|---|---|
| S0 one query, GitHub order | 0.14 [0.06, 0.23] | 0.11 | 0.16 [0.08, 0.25] | 0.13 | 0.17 | 30 |
| S1 3 queries, round-robin | 0.30 [0.19, 0.41] | 0.14 | 0.33 [0.22, 0.45] | 0.21 | 0.36 | 36 |
| S2 3q + topics, stars | 0.20 [0.11, 0.31] | 0.08 | 0.36 [0.25, 0.48] | 0.16 | 0.59 | 90 |
| S3 3q + topics, embed | 0.17 [0.09, 0.27] | 0.05 | 0.33 [0.22, 0.45] | 0.13 | 0.59 | 90 |
| S4 3q + topics, embed+stars | 0.25 [0.14, 0.36] | 0.09 | 0.41 [0.28, 0.53] | 0.19 | 0.59 | 90 |
| S5 S4 + relaxed + ecosyste.ms | 0.31 [0.20, 0.42] | 0.12 | 0.53 [0.41, 0.66] | 0.25 | 0.91 | 134 |
| S6 S5, mean query vector (pilot S6) | 0.47 [0.34, 0.59] | 0.31 | 0.64 [0.52, 0.75] | 0.42 | 0.91 | 134 |
| S7 S5 pool, BM25+stars | 0.25 [0.16, 0.36] | 0.16 | 0.59 [0.47, 0.70] | 0.25 | 0.91 | 134 |
| S11 S6 without language:python | 0.52 [0.39, 0.64] | 0.36 | 0.61 [0.48, 0.72] | 0.45 | 0.84 | 137 |
| S12 S6 on outside text only (label + section blurb) | 0.31 [0.20, 0.42] | 0.16 | 0.45 [0.33, 0.58] | 0.25 | 0.70 | 106 |
| S13 PyPI summary index only (top 30k), embed+stars | 0.56 [0.44, 0.69] | 0.30 | 0.80 [0.69, 0.89] | 0.45 | 0.84 | 30 |
| S14 S6 + PyPI summary index | 0.44 [0.31, 0.56] | 0.30 | 0.69 [0.58, 0.80] | 0.41 | 0.95 | 158 |

paired differences, B minus A, hit@3 and hit@10 [95% CI]:
- S1 3 queries, round-robin vs S0 one query, GitHub order: hit@3 +0.16 [+0.08, +0.25]; hit@10 +0.17 [+0.08, +0.28]
- S6 S5, mean query vector (pilot S6) vs S0 one query, GitHub order: hit@3 +0.33 [+0.20, +0.45]; hit@10 +0.48 [+0.36, +0.61]
- S7 S5 pool, BM25+stars vs S6 S5, mean query vector (pilot S6): hit@3 -0.22 [-0.34, -0.08]; hit@10 -0.05 [-0.17, +0.08]
- S11 S6 without language:python vs S6 S5, mean query vector (pilot S6): hit@3 +0.05 [-0.05, +0.14]; hit@10 -0.03 [-0.14, +0.08]
- S12 S6 on outside text only (label + section blurb) vs S6 S5, mean query vector (pilot S6): hit@3 -0.16 [-0.30, -0.03]; hit@10 -0.19 [-0.31, -0.06]
- S13 PyPI summary index only (top 30k), embed+stars vs S6 S5, mean query vector (pilot S6): hit@3 +0.09 [-0.03, +0.22]; hit@10 +0.16 [+0.03, +0.28]
- S14 S6 + PyPI summary index vs S6 S5, mean query vector (pilot S6): hit@3 -0.03 [-0.11, +0.05]; hit@10 +0.05 [-0.05, +0.14]

context7 answers cached for 27/64 needs (keyless quota); context7 arms on those only:

## TUNE, all, context7 subset (n = 27)

| strategy | hit@3 [95% CI] (primary) | hit@1 | hit@10 [95% CI] | MRR@10 | pool hit | median pool |
|---|---|---|---|---|---|---|
| S6 S5, mean query vector (pilot S6) | 0.37 [0.19, 0.56] | 0.26 | 0.59 [0.41, 0.78] | 0.36 | 0.89 | 127 |
| S8 S6 + context7 | 0.33 [0.15, 0.52] | 0.19 | 0.56 [0.37, 0.74] | 0.30 | 0.81 | 172 |
| S9 context7 only, round-robin | 0.11 [0.00, 0.26] | 0.04 | 0.19 [0.04, 0.33] | 0.08 | 0.30 | 57 |
| S10 context7 only, embed+stars | 0.11 [0.00, 0.22] | 0.11 | 0.22 [0.07, 0.37] | 0.13 | 0.30 | 57 |
| S15 S6 + context7 + PyPI index | 0.37 [0.19, 0.56] | 0.11 | 0.63 [0.44, 0.81] | 0.28 | 0.93 | 199 |

paired differences, B minus A, hit@3 and hit@10 [95% CI]:
- S8 S6 + context7 vs S6 S5, mean query vector (pilot S6): hit@3 -0.04 [-0.11, +0.00]; hit@10 -0.04 [-0.11, +0.00]

## TUNE, stratum A (awesome-python) (n = 48)

| strategy | hit@3 [95% CI] (primary) | hit@1 | hit@10 [95% CI] | MRR@10 | pool hit | median pool |
|---|---|---|---|---|---|---|
| S0 one query, GitHub order | 0.10 [0.02, 0.19] | 0.10 | 0.12 [0.04, 0.23] | 0.11 | 0.15 | 30 |
| S1 3 queries, round-robin | 0.21 [0.10, 0.33] | 0.10 | 0.25 [0.12, 0.38] | 0.15 | 0.27 | 38 |
| S2 3q + topics, stars | 0.15 [0.06, 0.25] | 0.04 | 0.29 [0.17, 0.42] | 0.11 | 0.50 | 92 |
| S3 3q + topics, embed | 0.08 [0.02, 0.17] | 0.02 | 0.21 [0.10, 0.33] | 0.07 | 0.50 | 92 |
| S4 3q + topics, embed+stars | 0.17 [0.06, 0.27] | 0.04 | 0.29 [0.17, 0.42] | 0.12 | 0.50 | 92 |
| S5 S4 + relaxed + ecosyste.ms | 0.23 [0.12, 0.35] | 0.10 | 0.44 [0.29, 0.58] | 0.20 | 0.88 | 136 |
| S6 S5, mean query vector (pilot S6) | 0.33 [0.21, 0.48] | 0.23 | 0.54 [0.40, 0.69] | 0.32 | 0.88 | 136 |
| S7 S5 pool, BM25+stars | 0.23 [0.12, 0.35] | 0.19 | 0.54 [0.40, 0.69] | 0.25 | 0.88 | 136 |
| S11 S6 without language:python | 0.40 [0.27, 0.54] | 0.27 | 0.52 [0.38, 0.67] | 0.35 | 0.83 | 137 |
| S12 S6 on outside text only (label + section blurb) | 0.21 [0.10, 0.33] | 0.15 | 0.33 [0.21, 0.48] | 0.19 | 0.60 | 109 |
| S13 PyPI summary index only (top 30k), embed+stars | 0.48 [0.33, 0.62] | 0.29 | 0.77 [0.65, 0.88] | 0.42 | 0.81 | 30 |
| S14 S6 + PyPI summary index | 0.35 [0.23, 0.48] | 0.21 | 0.60 [0.46, 0.73] | 0.32 | 0.94 | 161 |

context7 answers cached for 27/48 needs (keyless quota); context7 arms on those only:

## TUNE, stratum A (awesome-python), context7 subset (n = 27)

| strategy | hit@3 [95% CI] (primary) | hit@1 | hit@10 [95% CI] | MRR@10 | pool hit | median pool |
|---|---|---|---|---|---|---|
| S6 S5, mean query vector (pilot S6) | 0.37 [0.19, 0.56] | 0.26 | 0.59 [0.41, 0.78] | 0.36 | 0.89 | 127 |
| S8 S6 + context7 | 0.33 [0.15, 0.52] | 0.19 | 0.56 [0.37, 0.74] | 0.30 | 0.81 | 172 |
| S9 context7 only, round-robin | 0.11 [0.00, 0.26] | 0.04 | 0.19 [0.04, 0.33] | 0.08 | 0.30 | 57 |
| S10 context7 only, embed+stars | 0.11 [0.00, 0.22] | 0.11 | 0.22 [0.07, 0.37] | 0.13 | 0.30 | 57 |
| S15 S6 + context7 + PyPI index | 0.37 [0.19, 0.56] | 0.11 | 0.63 [0.44, 0.81] | 0.28 | 0.93 | 199 |

## TUNE, stratum P (lead pilot) (n = 16)

| strategy | hit@3 [95% CI] (primary) | hit@1 | hit@10 [95% CI] | MRR@10 | pool hit | median pool |
|---|---|---|---|---|---|---|
| S0 one query, GitHub order | 0.25 [0.06, 0.50] | 0.12 | 0.25 [0.06, 0.50] | 0.19 | 0.25 | 3 |
| S1 3 queries, round-robin | 0.56 [0.31, 0.81] | 0.25 | 0.56 [0.31, 0.81] | 0.39 | 0.62 | 34 |
| S2 3q + topics, stars | 0.38 [0.12, 0.62] | 0.19 | 0.56 [0.31, 0.81] | 0.30 | 0.88 | 71 |
| S3 3q + topics, embed | 0.44 [0.19, 0.69] | 0.12 | 0.69 [0.44, 0.88] | 0.30 | 0.88 | 71 |
| S4 3q + topics, embed+stars | 0.50 [0.25, 0.75] | 0.25 | 0.75 [0.50, 0.94] | 0.41 | 0.88 | 71 |
| S5 S4 + relaxed + ecosyste.ms | 0.56 [0.31, 0.81] | 0.19 | 0.81 [0.62, 1.00] | 0.41 | 1.00 | 129 |
| S6 S5, mean query vector (pilot S6) | 0.88 [0.69, 1.00] | 0.56 | 0.94 [0.81, 1.00] | 0.71 | 1.00 | 129 |
| S7 S5 pool, BM25+stars | 0.31 [0.12, 0.56] | 0.06 | 0.75 [0.50, 0.94] | 0.25 | 1.00 | 129 |
| S11 S6 without language:python | 0.88 [0.69, 1.00] | 0.62 | 0.88 [0.69, 1.00] | 0.74 | 0.88 | 134 |
| S12 S6 on outside text only (label + section blurb) | 0.62 [0.38, 0.88] | 0.19 | 0.81 [0.62, 1.00] | 0.43 | 1.00 | 104 |
| S13 PyPI summary index only (top 30k), embed+stars | 0.81 [0.62, 1.00] | 0.31 | 0.88 [0.69, 1.00] | 0.52 | 0.94 | 30 |
| S14 S6 + PyPI summary index | 0.69 [0.44, 0.88] | 0.56 | 0.94 [0.81, 1.00] | 0.68 | 1.00 | 153 |

context7 answers cached for 0/16 needs (keyless quota); context7 arms on those only:

per-need detail: <scratchpad>/rbcache/results-tune.json
