# r1-benchmark: an outside-gold recall benchmark for `dev reuse`, its frozen split and snapshot, and which free sources add recall

Search coverage: `uv run dev lit status` 2026-10-13 reported openalex, crossref, europepmc and arxiv up and s2 DOWN
(rate limited, keyless pool). Also used: GitHub REST search through `gh api`, packages.ecosyste.ms, context7 (keyless),
PyPI JSON, the Sourcegraph stream API, and WebSearch and WebFetch for docs. All runs 2026-10-13.

Scripts are in [scripts/](scripts/) and data in `scripts/data/`. Every number below labelled VERIFIED names the script
that produced it. Arm names follow [PROTOCOL.md](PROTOCOL.md): B0/B1/A1-A3/P1-P4 are protocol arms, R0-R7 are the
ranking configs in `configs.json`, and S0-S15/C0-C5/M0-M5 are this benchmark's recall strategies. S6 is the pilot's
S6.

## Findings

### F1. The benchmark, the split and the snapshot exist and follow PROTOCOL.md (VERIFIED)
- **Strata.** Gold for every stratum except P comes from a source neither the lead nor I wrote.
  - **A**: 120 needs, one per [awesome-python](https://github.com/vinta/awesome-python) subcategory, pinned at
    `ba49ec7`. There are 159 eligible subcategories; `build_gold.py` draws 120 of them with seed 169, in two nested
    batches of 60. Each need's gold is every GitHub repo the list files under that subcategory.
  - **P**: the lead's 16 pilot needs. They are TUNE only.
  - **M**: 40 PyMigBench sources ([ualberta-smr/PyMigBench](https://github.com/ualberta-smr/PyMigBench) `d8ffb98`,
    MIT, 134 migration pairs). The gold is the libraries that real projects migrated to. The only query is the source
    package's own PyPI summary. They are TEST only.
  - **C**: 10 hand-written helpers at pinned commits, TEST only:
    - pip's `retry`; pip's NEWS says it replaced the vendored tenacity.
    - devkit's `backoff`, token bucket, `.env` parser, XML guard and frontmatter split.
    - CPython 3.13's `_levenshtein_distance`.
    - Django's `slugify`.
    - tqdm's `format_sizeof`.
    - Home Assistant's `parse_datetime`.
  - Who named each C answer is recorded in `recall_bench.py` (`gold_by`): ruff's S314 docs, pip's NEWS or an
    awesome-python entry for 6 cases, and me for 4. Those 4 are BELIEVED mappings.
- **Paraphrases.** Each A need has 2 paraphrases, written once by me from the label alone, before any result for that
  need was seen. They name no library and are frozen in `paraphrases.json`.
- **Split.** `make_split.py` built `split.json` with SHA-256 `0a3a85c5…7ba04` (commit 5d07214).
  - TUNE has 64 needs: 48 from A and 16 from P. TEST has 112 needs (72 from A and 40 from M), plus 10 code cases.
  - Three A needs share gold with a pilot need and were forced into TUNE: Data Validation, Testing / Frameworks and
    Fuzzy Matching.
  - `overlaps_tune` lists 13 TEST needs or code cases whose gold also appears in TUNE, so TEST can be read with and
    without them. 6 of them are code cases: pip retry, backoff, token bucket, levenshtein, .env and slugify.
- **Configs.** `configs.json` has SHA-256 `f6b2376a…ba69` (commit afc5a1c). It declares 8 ranking configs (R0 is PR #171
  as built) plus the 4 index arms, 12 in all.
- **TEST discipline.** `recall_bench.py --mode test` refuses to run without `--confirm-test-run` and appends every
  run to `data/TEST_RUNS.log`. `replay.py` and `headroom.py` exit if a snapshot holds a TEST need.
  - Disclosure: set C was run once (arms C0-C4, hit@10) before PROTOCOL.md existed, and I saw that result. It is
    logged as TEST run #0 for C. No C arm or constant changed after it.
  - No TEST metric for A or M was printed or read. The pre-protocol A+M run was stopped and its output deleted unread.
- **Snapshot (PROTOCOL step 6).** The snapshot is stored machine-local, outside git, at
  `<git-common-dir>/devkit/bench/snapshot-2026-10-13.tar.gz`. It is 60 MB, SHA-256 `1064cc00…a4aa`. It holds three
  things:
  - The `recall_bench.py` API cache: 4,754 answers that cover every TUNE, TEST and C need (`--mode warm`). Transient
    failures are never cached.
  - The `run_reuse_snapshot.py` snapshot of PR #171 (`4d61af0`) on all 64 TUNE needs. It holds every recalled
    candidate. Each candidate that any config could show at n=10 is enriched, verified and gated: the 20 best by prior,
    the 10 best by R, and all within 0.20 of the best R.
  - The PyPI top-30k index (F5).

### F2. TUNE baselines (VERIFIED, `replay.py --configs R0`, `recall_bench.py --mode tune`, n = 64)
| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR@10 | other |
|---|---|---|---|---|---|
| A1 = R0 replayed (PR #171 as built) | 0.45 [0.33, 0.58] | 0.30 | 0.69 | 0.40 | 41% of top-3 results are HOLD; gold is anywhere in its pool for 0.83 |
| S0 one query, GitHub order | 0.14 [0.06, 0.23] | 0.11 | 0.16 | 0.13 | |
| S1 3 queries, round-robin | 0.30 [0.19, 0.41] | 0.14 | 0.33 | 0.21 | |
| S6 pilot best | 0.47 [0.34, 0.59] | 0.31 | 0.64 | 0.42 | pool hit 0.91 |
| S7 the S6 pool, BM25+stars | 0.25 [0.16, 0.36] | 0.16 | 0.59 | 0.25 | |
| S13 PyPI top-30k index alone | 0.56 [0.44, 0.69] | 0.30 | 0.80 | 0.45 | |
| S14 S6 + PyPI index | 0.44 [0.31, 0.56] | 0.30 | 0.69 | 0.41 | pool hit 0.95 |

Paired hit@3 differences [95% CI]:
- S1 vs S0: +0.16 [+0.08, +0.25].
- S6 vs S0: +0.33 [+0.20, +0.45].
- S7 vs S6: −0.22 [−0.34, −0.08], so BM25 ranks worse than embeddings.
- S12 vs S6 (outside text only, no agent paraphrase): −0.16 [−0.30, −0.03], so the agent's paraphrases carry real
  signal.
- S11 vs S6 (no `language:python` filter): +0.05 [−0.05, +0.14]. Not significant.

The full per-stratum tables are in `data/tune-report-2026-10-13.md`, the A1 replay in `data/tune-a1-r0-2026-10-13.md` and the headroom list in `data/tune-headroom-proxy-2026-10-13.md`.

Not yet run:
- **B0/B1 need a fresh agent.** I have read the gold lists, so my picks would be contaminated. The fixed prompt is
  `data/b0_prompt.md` and the input is `data/b0_tune_input.json` (64 needs). `replay.py --b0` scores the answers.
- **A2 needs the B0 names**, enriched live and added to the pool.

### F3. The pilot's numbers were optimistic, and ranking, not recall, is the bottleneck (VERIFIED, `--mode tune`)
- **S6 is much better on the pilot stratum.** On P it scores hit@3 0.88 and hit@10 0.94. On the 48 TUNE needs from A it
  scores hit@3 0.33 [0.21, 0.48] and hit@10 0.54. The method was designed on P, as PROTOCOL step 1 expected.
- **Gold is usually in the pool but rarely ranked into the top 3.** Some gold is in the pool for 0.83 of needs in
  PR #171's pool, 0.91 in the S5/S6 pool and 0.95 in the S14 pool. Yet hit@3 is only 0.44-0.47. This agrees with the
  live failure in [gaps-r1](gaps-r1.md).
- **More sources help recall but hurt the top of the list.** Adding the index pool to S6 (S14) raised hit@10 by +0.05
  [−0.05, +0.14] but lowered hit@3 by −0.03 [−0.11, +0.05]. With a mixed-source pool, a wider pool does not help the
  top 3 unless the ranking changes (BELIEVED).

### F4. Which free sources add recall
- **context7: no gain, and its keyless quota is too small (VERIFIED).**
  - The keyless v1 search answers with HTTP 429 after 200 requests. The headers read `ratelimit-limit: 200` and
    `retry-after` about 18.7 days, which is a reset on 2026-11-01T00:00Z. That is a monthly quota per IP.
  - Its docs call keyless use "for testing only". The documented free plan is 1,000 calls a month, and the newer v3
    endpoint returns documentation snippets, not libraries ([search API](https://context7.com/docs/search-api),
    SOURCED).
  - On the 27 TUNE needs whose answers are cached: S8 (S6 + context7) vs S6 is −0.04 [−0.11, +0.00] hit@3, and
    context7 alone scores 0.11 hit@3.
  - The v1 search endpoint is undocumented (BELIEVED risk).
- **ecosyste.ms keyword pages.** They raised pool hit from 0.59 to 0.91 (S4 vs S5; S5 also relaxes the query, so the
  two effects are mixed). The anonymous tier allows 5,000 requests an hour, per the response headers (VERIFIED).
- **ecosyste.ms `related_packages` is unsafe as a source.** For `requests`, all 20 entries were typosquats or test
  packages that claim `github.com/psf/requests` (`rewuests`, `requst` and others). A package's repository URL is a
  claim, not proof of identity. The hallucination guard must check the reverse direction, from repo to canonical
  package (VERIFIED, one probe).
- **libraries.io needs a free key.** Without one it answers HTTP 401. Its docs give a 60 requests per minute limit per
  key ([libraries.io API](https://libraries.io/api), SOURCED). It is not measured here because no key is set up.
- **PyPI's own search page is unusable for scripts.** It returns a JavaScript "Client Challenge" page to scripts
  (VERIFIED), which matches PyPI having no search API.
- **Sourcegraph.com anonymous search.**
  - The stream API answers. A repo-scoped query returns matches and a `done` event. Global queries returned 0 matches
    (`lang:python "def levenshtein"`; `"import tenacity" select:repo`) (VERIFIED, 2 probes).
  - It is not usable for global code-to-library search without an account (BELIEVED).
- **deps.dev `v3alpha/query`** takes a package or version key, not free text (VERIFIED: one probe, 404 on a name-only
  key). It suits enrichment, not discovery.

### F5. PyPI-side arm and feasibility of an offline semantic index of all PyPI summaries
- **The top-30k index beats every live arm on TUNE (VERIFIED, `recall_bench.py --mode tune`, S13).**
  - The index holds the 30,000 PyPI packages with the most downloads, embedded with potion-base-8M (dense only, no
    BM25). Searched alone and ranked by embedding plus a stars prior, it scores hit@3 0.56 and hit@10 0.80.
  - Against S6 the paired differences are hit@3 +0.09 [−0.03, +0.22] and hit@10 +0.16 [+0.03, +0.28].
  - On the 48 TUNE needs from A it scores hit@10 0.77, against 0.54 for S6. This is TUNE only and not a decision.
- **Headroom check (PROTOCOL item 16), proxy (VERIFIED, `headroom.py`).**
  - 26 of 64 TUNE needs (0.41) have a gold library in the index's top 100 that is absent from PR #171's pool.
  - For 9 of 64 (0.14), the index is the only place any gold appears.
  - Both are above the 10% threshold.
  - Proxy caveats: it uses the top-30k potion-base-8M index, not P1. The A2 pool lacks the B0 names, which would likely
    cover famous misses such as scipy, ipython and psycopg (BELIEVED).
- **Data source: the packages.ecosyste.ms registry pages (VERIFIED).**
  - The registry lists 941,619 PyPI packages. `per_page=1000&sort=downloads` works.
  - The top 30k took 228 s and compress to a 6.2 MB JSONL file: name, summary, keywords, repo, downloads, stars.
  - 26,169 of the 30k have a GitHub repo and 29,356 have a summary.
  - Deep pages are 22 MB (page 100), 11.6 MB (page 500) and 4.0 MB (page 900), at 3-4 s each, against about 124 MB per
    page at the top.
  - The full catalogue is therefore about 942 requests, roughly 1 h. The transfer is on the order of 10-15 GB (BELIEVED:
    extrapolated from 4 sampled pages). Both fit the 5,000/h anonymous limit.
- **Embedding cost (VERIFIED, `embed_time.py`).** potion-base-8M embedded 72,866 and 84,110 texts/s on CPU in two runs, so all
  941,619 summaries take about 13 s. The vectors take 964 MB as 256-d float32, or about 241 MB as int8 (BELIEVED).
- **Other free sources, not measured.**
  - The PyPI BigQuery `distribution_metadata` table is free up to 1 TB of queries a month without a credit card, but
    it needs a Google account ([packaging guide](https://packaging.python.org/en/latest/guides/analyzing-pypi-package-downloads/),
    SOURCED). The table is "based on the core metadata specifications" ([docs.pypi.org](https://docs.pypi.org/api/bigquery/),
    SOURCED). That it has a `summary` column is BELIEVED, not checked.
  - [sethmlarson/pypi-data](https://github.com/sethmlarson/pypi-data) (Apache-2.0, 409 MB gzipped SQLite, release
    2026-07-21) has URLs, downloads and Scorecard results but no summary column (VERIFIED from its README schema). It
    is useful for adoption and Scorecard columns, not for the text.
  - The full-index design (open-pypi corpus, P1-P4 models) is in [r2-pipeline](r2-pipeline.md); this note does not
    repeat it.

### F6. Benchmark limits (BELIEVED unless marked)
- **Hit rates are lower bounds.** Gold lists are incomplete, so a good library absent from the awesome-python
  subcategory counts as a miss.
- **Matching is on canonical GitHub `owner/repo` names only.** A PyPI-only candidate cannot match.
- **The paraphrases were written by the same model family the agent uses.** That is realistic for `dev reuse`, but they
  are one author's style.
- **Small strata.** The P stratum is 16 needs and the context7 subset 27, so their intervals are wide.
- **Head versus long tail.** `head_flags.json` (`head_flags.py`) marks needs whose gold includes a top-1,000 PyPI
  package: 44 of 64 in TUNE, 64 of 112 in TEST and 9 of 10 in C (VERIFIED). TEST therefore has a long-tail stratum of
  48 needs for amendment 14.

## Gaps seen, not closed
- **B0, B1 and A2 on TUNE.** They need a fresh agent run with `data/b0_prompt.md`. B1 also needs PR #171's gates
  applied to the named packages, and A2 needs the B0 names enriched live.
- **The TEST snapshot of `dev reuse` is not taken.** The command is
  `PYTHONPATH=<PR #171 devkit> uv run --with model2vec python scripts/run_reuse_snapshot.py <dir> --split test --bench-cache <cache>`.
  It covers 72 A and 40 M needs and takes about 2 h at 60-70 s per need. The code cases need a `--from` runner.
- **P1 itself is not built.** The headroom figure is a proxy.
- **The context7 arms cover only 27 of 64 TUNE needs** until the quota resets on 2026-11-01.
- **Not probed:** libraries.io keyword search with a free key; whether the BigQuery `distribution_metadata` table has
  `summary`; conda-forge; GitHub code search (10/min) for `--from`.

## Sources fetched (2026-10-13)
- Gold:
  - [vinta/awesome-python](https://github.com/vinta/awesome-python) README @ ba49ec7.
  - [ml-tooling/best-of-python](https://github.com/ml-tooling/best-of-python) `projects.yaml` @ 00a6272. Read for code
    gold; its categories proved too coarse to use as needs.
  - [ualberta-smr/PyMigBench](https://github.com/ualberta-smr/PyMigBench) @ d8ffb98.
  - LibEval ([arXiv 2508.09791](https://arxiv.org/abs/2508.09791)). Its abstract was fetched, but no data link was found
    in its HTML, so it is not used.
  - pip `NEWS.rst` @ fb1d166.
  - [ruff S314](https://docs.astral.sh/ruff/rules/suspicious-xml-element-tree-usage/).
- Source docs:
  - [GitHub search REST docs](https://docs.github.com/en/rest/search/search): 30 requests a minute authenticated, 10
    for code search, 1,000 results, 256 characters, 5 operators (SOURCED).
  - [libraries.io API](https://libraries.io/api).
  - [context7 search API](https://context7.com/docs/search-api).
  - [PyPI BigQuery docs](https://docs.pypi.org/api/bigquery/).
  - [packaging guide on BigQuery](https://packaging.python.org/en/latest/guides/analyzing-pypi-package-downloads/).
  - [sethmlarson/pypi-data](https://github.com/sethmlarson/pypi-data) README and releases.
