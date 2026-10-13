# r3-tuning: TUNE scores for every pre-registered arm, the headroom check, and the configuration chosen for TEST

## Declared before the first round-3 score was computed (2026-10-13 ~10:00 UTC)
Where PROTOCOL.md leaves a detail open, round 3 fixes it here, before any round-3 number was seen.
- **Choice rule.** The ranking configuration (R0-R7) is chosen by TUNE hit@3 on the **A2 pool**, since A2 is what it
  will order on TEST; ties go to the lower `complexity`, then the lower id. R0-R7 on the A1 pool are reported too.
- **A2.** The pool is the PR #171 snapshot plus the B0 names. A name the pool already holds (same PyPI name or same
  GitHub repository) is tagged, not duplicated. Every B0 name is enriched, gated and always kept in the shortlist,
  whatever the shortlist rule, because a deployed tool verifies the names the agent passes. Order: the config.
- **B0 harm** = share of B0's names that PyPI and ecosyste.ms do not know, or that the gates mark FAIL, HOLD or
  UNVERIFIED. **B1** = B0 without its FAIL, UNVERIFIED and unknown names (as `dev reuse` drops them); HOLD names stay
  in place and count as harm, as in every A arm.
- **Gold match.** Every shown repository name is canonicalised through the GitHub API (renames), as replay.py already
  did for B0, so all arms are matched the same way. R0 on the A1 pool is also shown uncanonicalised, to check this
  replay against round 1 (hit@3 0.45).
- **A3.** The agent sees only the need, its two descriptions and A2's shortlist under the chosen config (the n = 10
  rule: 10 by R, 10 by adoption, plus the B0 names; FAIL dropped; at most 20 lines, in A2's order; 14 lines on
  average, at least 7), in the fixed format of `scripts/data/a3_prompt.md`, and returns up to 10 of those names, best
  first. (Amended before any A3 output: the first draft said "top 20 at n = 20", which the n = 10 snapshot cannot
  serve.) Same method as B0:
  `claude -p --tools '' --strict-mcp-config --setting-sources ''` from an empty directory, batches of 16 needs.
  A name not on the shortlist is dropped (the agent may only re-rank).
- **Index arms.** P1 top 100 per need, then the same `add_named.py` step as A2 (index names join the A2 pool; they
  are not forced into the shortlist: the index is a recall stage), ordered by the chosen config. Headroom (PROTOCOL
  item 16) = share of TUNE needs with a gold repository in P1's top 100 that is absent from the whole A2 pool
  (recalled candidates of every status plus the B0 names).

Search coverage: no literature search this round (all evidence is measured here). `dev lit status` at dispatch,
2026-10-13: discovery OK, partial (s2 down). Live sources used: ecosyste.ms, deps.dev, PyPI JSON, GitHub REST
(through PR #171's client and `gh api`), the PyPI PEP 691 simple index, open-index/open-pypi on Hugging Face, and
ClickHouse's public `pypi.projects`. All 2026-10-13.

Snapshot (PROTOCOL 6): `<git-common-dir>/devkit/bench/snapshot-2026-10-13-r3.tar.gz`, 176 MB, SHA-256
`bbfed5dd…9625`. It holds the A2 snapshot, the recall_bench cache (6,447 answers), the ClickHouse delta, P1's top 100,
the reports and `manifest.sha256` (corpus, simple index, open-pypi file, P1 vectors). Every number below is
replayed offline from it with `scripts/r3_score.py`. The choice is in `scripts/data/chosen.json`.

## Findings

### F1. On TUNE, the agent's own picks score highest, and search adds nothing to them (VERIFIED, `r3_score.py score`, n = 64)
| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR | harm share of top 3 | head (44) | tail (20) |
|---|---|---|---|---|---|---|---|
| A1 R0 (PR #171 as built) | 0.45 [0.33, 0.58] | 0.30 | 0.69 | 0.40 | 0.41 | 0.52 | 0.30 |
| A1 R4 | 0.61 [0.48, 0.72] | 0.52 | 0.69 | 0.58 | 0.05 | 0.68 | 0.45 |
| A2 R4 (A1 pool + B0 names) | 0.88 [0.80, 0.95] | 0.70 | 0.97 | 0.80 | 0.03 | 0.86 | 0.90 |
| B0 (agent memory) | 0.97 [0.92, 1.00] | 0.86 | 0.97 | 0.91 | 0.21 | 0.98 | 0.95 |
| B1 (B0 + gates) | 0.97 [0.92, 1.00] | 0.88 | 0.97 | 0.92 | 0.13 | 0.98 | 0.95 |
| A3 (agent re-rank of A2 R4) | 0.97 [0.92, 1.00] | 0.89 | 0.97 | 0.93 | 0.11 | 0.98 | 0.95 |

- The full table (R0-R7 on both pools, strata A and P) is `scripts/data/tune-r3-2026-10-13.md`.
- **Check of the replay.** R0 on the A1 pool, uncanonicalised, gives hit@3 0.45, hit@10 0.69 and HOLD share 0.41,
  as in round 1 (r1-benchmark F2). Canonicalising names through GitHub changes no R0 number.
- **Paired hit@3 differences [95% CI]:**
  - A2 R4 minus B1: −0.09 [−0.17, −0.03]. A2 loses 6 needs and wins none.
  - A3 minus B1: +0.00. A3 and B1 hit the same 62 needs. Both miss "AI and Agents / Agent Skills" (B0 gave no
    name) and "Testing / Test Runners".
  - A3 minus A2 R4: +0.09 [+0.03, +0.17] (6 wins, 0 losses). The agent's re-rank recovers exactly what A2's order lost.
  - A2 R4 minus A1 R4: +0.27 [+0.16, +0.38] (17 wins, 0 losses). Adding the agent's names is the biggest single gain.
- **Why A2 loses to B1.** In A2, gated ordering puts PASS packages above famous HOLD ones, and λ = 1 still lets a
  closely matching small package outrank the famous one. Examples: python-pptx is HOLD (dormant, 797 days) and falls
  to 4th, behind office-oxide and two personal repos. panda3d is HOLD (licence "Zed", Blue Oak Lead) and falls to 4th.
  pydantic falls out of the top 3 under "Data Validation" (shortlist lines in `data/a3_tune_input.json`).
- **Harm is policy, not hallucination.** All 175 B0 names exist on PyPI or ecosyste.ms (0 unknown). The 174 candidates they map to (two names map to one) include 16 FAIL and 22 HOLD:
  - FAIL: 13 strong-copyleft licences (mutagen, PyQt6, ansible and others) and 3 archived repositories (backoff,
    ward, supermemo2).
  - HOLD: mostly G4 dormancy (for example sortedcontainers, schedule, python-pptx, thefuzz).
  - So B0's 0.21 "harm" is the gates' default policy (permissive only; a release within 730 days), not invented
    names. B1 and A3 cut it to 0.13 and 0.11 at no hit@3 cost on TUNE.
- **Ceiling (BELIEVED).** B0 is at 0.97 on TUNE. If TEST's A stratum behaves the same, no arm can beat B1 there by an
  interval above 0. The decision (PROTOCOL 10, 13) will be settled by the M stratum (migration targets, a different
  gold source) and the long tail. awesome-python is very likely in the model's training data, which favours B0 on
  strata A and P.

### F2. Choice: R4, by TUNE hit@3 on the A2 pool (VERIFIED; written to `scripts/data/chosen.json`)
- Order on the A2 pool: R4 0.88, R3 0.83, R6 0.75, R2 0.70, R1 0.69, R5 0.64, R0 0.58, R7 0.55.
- R4 is PASS before HOLD, the union shortlist and λ = 1.0, with no tier.
- R4 minus R3 (λ 0.5) is +0.05 [−0.02, +0.11]: 4 wins, 1 loss. That is within noise, but the declared rule picks the top
  mean. λ = 1.0 is **fitted** on TUNE; the cited prior is 0.15 (r1-ranking F1).
- On the A1 pool, R3 (0.62) and R4 (0.61) are level.
- PASS before HOLD is the large effect: R1 minus R0 on A1 is +0.09 [+0.02, +0.17], and the HOLD share falls from 0.41
  to 0.02. Then λ: R3 minus R2 is +0.09 on the A1 pool and +0.13 on the A2 pool (means).
- The tier rules (R5, R6) and the tier without PASS-first (R7) are worse than plain λ.

### F3. Headroom for an index is 4.7% once the agent's names are in the pool: no GPU index (VERIFIED, `r3_score.py headroom`)
- **P1 as built.** BM25 (bm25s, k1 = 1.5, b = 0.75, name twice) plus potion-retrieval-32M, fused with
  RRF k = 60, over 777,561 packages. Build time 98 s wall on 32 CPU cores, 14 GB peak RAM. Query time for 64 needs 7 s
  (`p_index.py`).
- **Headroom (PROTOCOL 16).** 3 of 64 TUNE needs have a gold repository in P1's top 100 that is absent from the A2 pool:
  0.047, below the 10% threshold. The three are:
  - Cloud Providers: googleapis/google-cloud-python;
  - Configuration Management: ansible/ansible (FAIL under the default licence policy anyway);
  - Parser: python-parsy/parsy.
- The index is the only source of gold for 0 of 64 needs.
- Against the A1 pool (no B0 names), the same index has headroom 18/64 = 0.28, with 6/64 index-only. The round-1 proxy
  gave 0.41. The agent's names close almost all of the recall gap that an index would close.
- **Decision under PROTOCOL 16.** Stop at A3. P2, P3 and P4 were not built, so the RTX 4090 was not used. P1 is not
  a TEST arm (BELIEVED reading of "stop at A3"; the lead may overrule).
- **Pool recall.** Gold is somewhere in the A2 pool for 63/64 needs (0.98), against 0.83 for A1. Ranking, not recall,
  is what is left.

### F4. P1 corpus, and two data facts that reconcile round-2 gaps (VERIFIED, `p_corpus.py`)
- **open-pypi.** `packages/0000.parquet` alone holds all 712,894 unique names. Files 0001-0003 (232,310 rows) are
  duplicates of names already in 0000. So the 945,204 rows that lead-data-coverage.md counted are 712,894 packages
  plus re-fetched rows, which closes r2 gap G2.
- **Drift since the snapshot.** The PEP 691 simple root on 2026-10-13 lists 912,643 projects (serial 42,171,628).
  - 203,378 of them are not in open-pypi; 62,414 of those have a serial below 36.0M, so they existed but were
    missed or had no release.
  - 87,388 changed since the snapshot, and 3,628 open-pypi names are gone.
- **Catch-up.** It came from ClickHouse `pypi.projects`: the latest upload of the 128,599 names uploaded since
  2026-04-11, fetched as 64 hash partitions with READMEs cut at 2,500 characters.
  - The demo user has a read quota of 500 GB per hour (QUOTA_EXCEEDED after 36 partitions; each partition scans
    about 14 GB). The rest ran after the hourly reset. Treat this source as unsuitable for a scheduled job.
  - The PEP 691 + JSON API catch-up planned in r2 would take about 40 h at 2 requests/s, so it was not done. This is a
    deviation; it affects only the headroom figure.
- **Corpus.** 777,561 packages (63,931 updated, 64,668 new), 508,502 of them with a GitHub repository, SHA-256
  `b7893f8a…3354`. About 135k projects in the simple index are still missing; most have no release (BELIEVED).
- **Gold matching for the index** is on GitHub owner/repo, with canonicalisation through GitHub for any repository
  whose name part equals a gold name part. A rename that also changes the repository name would be missed, so
  headroom is a slight lower bound (BELIEVED small).

## Gaps seen, not closed
- **TEST inputs not built.**
  - B0 on TEST needs `b0_test_input.json` with the 72 A and 40 M needs, in the same format and batches of 16.
  - The A1 TEST snapshot (`run_reuse_snapshot.py --split test`, about 2 h) is not taken.
  - A2 then needs `add_named.py --tag agent` on it.
  - For M needs, the source package itself must not count as a hit or appear in B0 (recall_bench has `exclude`;
    `r3_score.py` does not yet). This must be fixed before TEST, or B0 scores the source package as an answer.
- **B0's batching on TUNE is not recorded.** b0_tune_answers `_how` names the flags but not whether the 64 needs went
  in one call. `run_agent.py` sends batches of 16, and A3 used that. For B0 on TEST, the lead should fix the batch size
  in chosen.json before running.
- **Harm policy.** 13 of 16 B0 FAILs are copyleft. Whether a copyleft name counts as "harm" for PROTOCOL 10's harm
  comparison is a policy question for the owner.
- **Small TUNE strata.** P is 16 needs and tail is 20, so their intervals are wide.
- **Not measured:** P1's own hit@3 on TUNE (the protocol stops before it); the Qwen3 arms (not built by rule).

## Scripts and data added (all under `scripts/`)
- `add_named.py`: B0 or index names into a snapshot, enriched with PR #171's functions.
- `r3_score.py`: every arm, headroom and the A3 input.
- `run_agent.py`: the fixed tool-less `claude -p` runner.
- `p_corpus.py` and `p_index.py`: the P1 corpus and index.
- Data: `data/a3_prompt.md`, `data/a3_tune_input.json`, `data/a3_tune_answers.json` (claude-opus-5-5,
  2026-10-13), `data/chosen.json`, `data/tune-r3-2026-10-13.md`, `data/tune-r3-headroom-2026-10-13.txt`.

## Sources fetched (2026-10-13)
- [open-index/open-pypi](https://huggingface.co/datasets/open-index/open-pypi): card and `packages`, `project_urls`
  parquet. ODC-By.
- PyPI [simple index](https://pypi.org/simple/) (PEP 691 JSON). ClickHouse public endpoint (`sql-clickhouse.clickhouse.com`,
  user `demo`) `pypi.projects`.
- Models: [minishlab/potion-retrieval-32M](https://huggingface.co/minishlab/potion-retrieval-32M) (MIT),
  [bm25s](https://github.com/xhluca/bm25s) (MIT). Licences are as recorded in r2-pipeline.md.

## TEST (one shot, PROTOCOL 5 and amendment 5; run 2026-10-13)
The choice in `chosen.json` and amendment 5 were committed (8f97ea3) before any TEST score. The execution code and the
agent answers were committed next (c84e764, 121271c, bdb57aa). Then came one scoring run of A and M. The first
attempt crashed before printing anything: once the M source was excluded, an A1 shortlist reached an unenriched
candidate. No number was seen. It is logged in `scripts/data/TEST_RUNS.log`, and the A1 snapshot was re-enriched
under the exclusion (a45c30f).
- **Set C** was scored once (TEST run #1 for C; run #0 predates the protocol).
- **Snapshot.** `<git-common-dir>/devkit/bench/snapshot-2026-10-13-r3-test.tar.gz`, 71 MB, SHA-256 `492206a0…7eb`.
- **Reports.** `scripts/data/test-r3-2026-10-13.md` and `scripts/data/test-r3-code-2026-10-13.md`.
- **Inputs** (VERIFIED, `r3_score.py score --split test`, n = 112: 72 A + 40 M).
  - B0 ran with `claude -p --model claude-opus-5-5`, 2 batches of 56. A3 ran in 7 batches of 16.
  - M ids were opaque to the agent (`b0_test_ids.json`).
  - B0 named 328 packages: 4 do not exist (renpy, python-scrapinghub, leon-ai, pyobfuscate), 1 is unverified
    (erpnext), 25 are FAIL and 61 HOLD.

### T1. Arms on TEST (VERIFIED)
| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR | harm | harm without licence | A (72) | M (40) | top-1,000 (64) | long tail (48) |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 R0 (PR #171) | 0.33 [0.25, 0.42] | 0.22 | 0.47 | 0.29 | 0.45 | 0.35 | 0.36 | 0.28 | 0.38 | 0.27 |
| A1 R4 | 0.42 [0.33, 0.51] | 0.30 | 0.50 | 0.37 | 0.06 | 0.04 | 0.47 | 0.33 | 0.47 | 0.35 |
| A2 R4 | 0.71 [0.63, 0.79] | 0.49 | 0.85 | 0.62 | 0.04 | 0.02 | 0.79 | 0.57 | 0.84 | 0.54 |
| B0 | 0.84 [0.77, 0.90] | 0.71 | 0.84 | 0.77 | 0.27 | 0.20 | 0.90 | 0.72 | 0.92 | 0.73 |
| B1 | 0.83 [0.76, 0.90] | 0.71 | 0.83 | 0.77 | 0.19 | 0.16 | 0.90 | 0.70 | 0.91 | 0.73 |
| A3 | 0.83 [0.76, 0.89] | 0.67 | 0.86 | 0.75 | 0.11 | 0.10 | 0.90 | 0.70 | 0.92 | 0.71 |
| P1 R4 (exploratory, not a decision arm) | 0.71 [0.63, 0.79] | 0.45 | 0.84 | 0.58 | 0.02 | 0.01 | 0.79 | 0.57 | 0.84 | 0.54 |

**Paired hit@3 differences [95% CI]** (10,000 resamples over needs, seed 169):

| comparison | all (112) | A (72) | M (40) | top-1,000 (64) | long tail (48) |
|---|---|---|---|---|---|
| A2 R4 − B1 | −0.12 [−0.19, −0.05] | −0.11 [−0.19, −0.04] | −0.12 [−0.28, +0.03] | −0.06 [−0.14, +0.02] | −0.19 [−0.31, −0.08] |
| A3 − B1 | +0.00 [−0.05, +0.05] | +0.00 [−0.04, +0.04] | +0.00 [−0.12, +0.12] | +0.02 [−0.05, +0.08] | −0.02 [−0.08, +0.04] |
| A3 − A2 R4 | +0.12 [+0.04, +0.19] | +0.11 [+0.03, +0.19] | +0.12 [−0.03, +0.28] | +0.08 [−0.02, +0.17] | +0.17 [+0.04, +0.29] |
| A1 R4 − A1 R0 | +0.09 [+0.02, +0.16] | +0.11 [+0.03, +0.21] | +0.05 [−0.07, +0.17] | +0.09 [+0.00, +0.20] | +0.08 [+0.00, +0.19] |

- **Without the 7 needs that overlap TUNE (n = 105)** the result is the same: A2 − B1 −0.12, A3 − B1 +0.00.
- **Harm, paired differences [95% CI]:**
  - A2 − B1: −0.15 [−0.20, −0.10].
  - A3 − B1: −0.07 [−0.11, −0.04].
  - Without licence outcomes: A2 − B1 −0.14, A3 − B1 −0.07, both intervals below 0.
- **P1** is exactly level with A2 (+0.00 [−0.05, +0.05]). The index adds nothing on TEST, as the TUNE headroom
  predicted.
- **Set C** (`dev reuse --from` alone, n = 10): R0 hit@3 0.20 [0.00, 0.50], R4 0.40 [0.10, 0.70], difference +0.20
  [+0.00, +0.50].
  - R4 hits: pip retry → tenacity, frontmatter, levenshtein → rapidfuzz, slugify.
  - Not recalled: backoff, token bucket, .env, XML, tqdm.
  - Ranked low: parse_datetime (dateparser).
  - The --from need text is noisy, with module names and comment words (`test-r3-code-2026-10-13.md`).

### T2. What each pre-registered rule decides
- **PROTOCOL 10 (search as the default).** Not met.
  - A2 − B1 is −0.12 [−0.19, −0.05]: worse.
  - A3 − B1 is +0.00 [−0.05, +0.05]: the interval does not clear 0.
  - So the gates ship as a verifier for named packages, and search stays an optional recall aid.
- **PROTOCOL 13 (simplest sufficient arm).** B1 wins. Neither A2 nor A3 beats B1, the cheapest arm. No index is built
  or published; P1 confirms this.
- **PROTOCOL 5 (chosen config vs PR #171).**
  - R4 beats R0 on the same recall: +0.09 [+0.02, +0.16]. The HOLD share of the top 3 falls from 0.45 to 0.06.
  - Ship R4's ordering (PASS before HOLD, union shortlist, λ = 1) in `dev reuse` whatever its role. λ is fitted.
  - S6 was dropped from the TEST arms by amendment 5 and was not run.
- **PROTOCOL 14 (head vs tail).** Agent memory leads in both groups: B1 scores 0.91 on top-1,000 gold and 0.73 on the
  long tail.
  - The tail is where search was expected to help, but A2 is further behind B1 there: −0.19 [−0.31, −0.08].
  - A3 is level with B1 in both groups.
- **Harm.** Every search arm has a lower harm share than B1 (intervals below 0). A3 matches B1's hit@3 at 0.11 harm
  against 0.19.
  - This trade-off is outside the hit@3 rule. Whether "same hit@3, 42% less harm" justifies A3 is an owner decision
    (BELIEVED: B1 is cheaper and already removes FAIL names; A3 additionally moves HOLD names out of the top 3).

### T3. Misses with causes (PROTOCOL 8; full lists in `test-r3-2026-10-13.md`)
- **B1, 19 misses.**
  - 18 are "not named": the agent's names are not in the gold. This includes migrations where it named a sensible
    alternative outside the gold, such as requests → httpx/urllib3 and pyyaml → ruamel.yaml.
  - 1 is gated out: chardet → cchardet is GPL, so FAIL.
- **A2 R4, 32 misses.**
  - 10 not recalled.
  - 21 recalled but ranked low. Gold is often HOLD (pyserial, biopython, ldap3, gpiozero) and sinks under PASS before
    HOLD, or is a smaller PASS package below a more adopted one.
  - 1 gated out (cchardet).
- **A3, 19 misses.**
  - 15 not on the shortlist, so recall- or order-limited upstream.
  - 4 on the shortlist but not picked: pyarmor, quart/bottle for flask, celery for huey, pcapy.
- **Benchmark limits** (BELIEVED). Gold lists are incomplete: renpy is not on PyPI, odoo is a repo only, and Qt gold
  is outside GitHub. So all hit rates are lower bounds, and they are lower bounds for every arm alike.
