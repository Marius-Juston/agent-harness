# r2-pipeline: a free, open, easy-to-update pipeline that indexes all of PyPI and retrieves packages for a need or a snippet

Round 2, sub-question for #169 (researcher, 2026-10-13). Scope: data source and refresh, package representation,
retrieval models (dense, sparse, late interaction, rerankers, query expansion), index store, throughput, hosting
on the owner's lab server, publishing a Hugging Face snapshot, and at most 4 pipeline arms fixed before tuning.
The benchmark TEST set was not run or read.

Search coverage: `dev lit status` 2026-10-13: openalex, crossref, europepmc, arxiv OK; s2 DOWN (HTTP 429, no
key). Also used: arxiv MCP, the Hugging Face Hub API (model cards, dataset commits), the GitHub REST and GraphQL APIs,
the PyPI docs, the embeddings-benchmark/results repository, and the ClickHouse public SQL endpoint. context7 was
over its monthly quota, so LanceDB and Qdrant docs were read directly from their sites.

Scripts written for this round live outside the repository, in the session scratchpad
(`.../scratchpad/bench/bench_embed.py`, `.../scratchpad/bench/search_cost.py`). Copy them into `scripts/` if the
numbers below are to be re-run. Every VERIFIED number is one run on one machine (32 cores, WSL2, RTX 4090).

## Answer in one paragraph

The answer is a two-stage pipeline: a recall stage, then the existing `dev reuse` gates and Q ranking. Nothing
fancier is justified yet. The index only has to put the gold package in the top 100. `dev reuse` already orders the
shortlist by gates, adoption and the agent, so the index has to supply recall plus an adoption column, not a polished
top 3.

- **Retrieval.** Use BM25 over name, summary, keywords and the head of the README, plus one 0.6B open dense embedder,
  fused with RRF.
- **Embedder.** Qwen3-Embedding-0.6B (Apache-2.0) reproduces its card's MTEB(Code) score of 75.41 from the public
  per-task results (VERIFIED). Its CPU query encode takes 0.21-0.24 s, and an exact search over all of PyPI takes
  54 ms (VERIFIED).
- **Build and refresh cost.** A full build is 353M tokens at 512 tokens per package: 1.5 h on one RTX 4090
  (VERIFIED), so about 0.4-0.6 h on the 4x A6000 (BELIEVED). Daily refresh is about 4,000 changed projects: under 1
  GPU-minute, plus about 35 minutes of polite PyPI fetching.
- **Data.** open-index/open-pypi is a one-day snapshot (2026-04-11) that nobody maintains (VERIFIED). Catching up
  means 228k changed projects. Detect changes with the PEP 691 simple index (one 45 MB GET listing every project's
  `_last-serial`, VERIFIED), not XML-RPC, which PyPI says is "planned for deprecation".
- **Adoption.** 30-day download counts for all of PyPI come from the keyless ClickHouse public endpoint in 2.3 s
  (VERIFIED). That fixes the shortlist failure from round 1 (gaps-r1 cause 3) without live calls.
- **Optional stages, each an arm to be won on TEST.**
  - A GPU cross-encoder: only the 4B Qwen3 reranker helps code retrieval (75.41 → 81.20). The 0.6B one hurts
    (73.42), and CPU reranking is infeasible (134 s for 50 documents, VERIFIED proxy).
  - LLM document expansion: Tool-DE ([Lu et al. 2025](https://arxiv.org/abs/2510.22670)).
- **Evidence against complexity.** ColBERT-style late interaction lost to BM25 on tool retrieval
  ([ToolRet, ACL Findings 2025](https://doi.org/10.18653/v1/2025.findings-acl.1258)), and a ColBERT index of PyPI
  would be 11-90 GB. Learned sparse models are either NC-licensed (SPLADE-v3) or unproven here. LLM query expansion
  is already done for free by the calling agent (`--also` paraphrases).

## Findings

### F1. Data source and refresh: what is fresh, what is sanctioned (VERIFIED unless marked)

- **open-index/open-pypi is a snapshot, not a feed.** Every commit on the dataset is dated 2026-04-11, and
  `lastModified` is 2026-04-11T16:25 (HF API, VERIFIED). Its card says 712,894 packages. lead-data-coverage.md counted
  945,204 rows in `packages`. Reconcile the two before relying on either.
- **Serial at the snapshot date.** PyPI serial 36,000,000 corresponds to uploads on 2026-04-11 around 13:00 UTC: the
  latest uploads of projects whose `_last-serial` is just above it were made then (5 projects probed through the JSON
  API).
  - 228,154 of 912,555 projects (25%) have a `_last-serial` at or above 36.0M, so they changed since the snapshot.
  - Serials advanced about 33,300 per day since then (arithmetic in this round).
  - A catch-up therefore takes 2.6 h at the lead's 24 requests/s, or 32 h at a polite 2 requests/s.
- **Change detection without XML-RPC.** PyPI's API page says "No new integrations should use the XML-RPC APIs as they
  are planned for deprecation" and points to RSS for periodic checks ([docs.pypi.org/api](https://docs.pypi.org/api/),
  SOURCED). The RSS update feed is not enough: it holds 100 items, which covered about 9 minutes at 07:35-07:44 UTC
  (VERIFIED, `curl https://pypi.org/rss/updates.xml`).
  - The PEP 691 JSON simple index root (`Accept: application/vnd.pypi.simple.v1+json`) lists all 912,555 projects with
    `_last-serial` in one 44.8 MB response. It took 22 s, carries `cache-control: max-age=600`, and is at API version
    1.4 (VERIFIED).
  - Diffing two days of this file gives the exact set of changed projects. This should replace
    `changelog_since_serial` in lead-index-cost.md.
- **Fetch etiquette.** PyPI asks consumers to "not make a lot of requests (thousands) in a short amount of time
  (minutes)" and prefers "requests in serial over a longer amount of time". It honours `ETag`
  ([docs.pypi.org/api](https://docs.pypi.org/api/), SOURCED).
  - The lead's 24 requests/s with 8 threads is about 1,440 requests a minute, which reads as outside that guidance
    (BELIEVED).
  - Recommended: a serial client at 1-2 requests/s. That makes the one-time catch-up an overnight background job and
    the daily refresh about 35 minutes.
- **Daily volume.** Distinct projects with an upload, per UTC day, 2026-10-05 to 2026-10-12: 3,149, 4,028, 3,796,
  4,297, 4,265, 2,563, 2,524, 3,988 (ClickHouse query below, VERIFIED).
  - Only uploads change the README, so about 2.5-4.3k projects a day need re-embedding.
  - lead-index-cost.md counted about 7,200 projects a day by changelog events. Those also count yanks, ownership and
    metadata events, which do not change the text.
- **A keyless, near-live mirror of PyPI metadata exists.** ClickHouse's public endpoint (`sql-clickhouse.clickhouse.com`,
  user `demo`, documented in [ClickHouse/clickpy](https://github.com/ClickHouse/clickpy), Apache-2.0, pushed
  2026-09-28) has `pypi.projects`: one row per release file with `summary`, `description` (README), `keywords`,
  `project_urls` and `requires_dist`.
  - It held 18.7M rows and 817,027 distinct names, with the latest upload at 2026-10-13 00:29 UTC, a few hours stale
    when queried (VERIFIED).
  - It also has `pypi_downloads_per_day`: 30-day downloads for all 856,661 projects in one 2.3 s query (VERIFIED).
  - Limit: a 9.54 MiB cap per result (`TOO_MANY_ROWS_OR_BYTES`, VERIFIED). It suits daily deltas (a few queries) and
    adoption counts, not a bulk mirror. It is a demo endpoint with no stated SLA or terms (BELIEVED risk), so the
    PyPI JSON API stays the fallback.
  - Upstream licence: PyPI's BigQuery tables are CC BY 4.0
    ([docs.pypi.org/api/bigquery](https://docs.pypi.org/api/bigquery/), SOURCED).
- **README length** over the latest release per project (ClickHouse aggregate, VERIFIED; 677,367 names returned,
  which is fewer than the 817,027 distinct names, so the demo may cap GROUP BY; treat as a sample):
  - median 1,465 characters, p75 4,318, p90 8,850, p99 29,200;
  - 14% of projects have no README;
  - truncating at 2,048 characters keeps 34% of all README characters.

### F2. Dense embedders: candidates by licence and fetched scores

The [HF Hub API](https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B) gave licence tags and parameter counts
on 2026-10-13. Scores are self-reported unless marked.

| Model | Licence | Params | Evidence |
|---|---|---|---|
| Qwen/Qwen3-Embedding-0.6B | Apache-2.0 | 0.60B | MTEB(eng v2) 70.70, retrieval 61.83; MMTEB 64.33; MTEB(Code) 75.41 ([card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [paper](https://arxiv.org/abs/2506.05176)). The 12-task MTEB(Code) mean recomputed from [embeddings-benchmark/results](https://github.com/embeddings-benchmark/results) is 75.41 (VERIFIED, matches). 8.5M downloads; MRL down to small dims; 32k context |
| perplexity-ai/pplx-embed-v1-0.6b | MIT | 0.60B | MTEB(Code) 12-task mean 75.87 from the results repo (VERIFIED). Needs `trust_remote_code`; emits unnormalised int8 ([card](https://huggingface.co/perplexity-ai/pplx-embed-v1-0.6b); [paper, ACL 2026](https://arxiv.org/abs/2602.11151)) |
| microsoft/harrier-oss-v1-0.6b | MIT | 0.60B | MTEB v2 multilingual 69.0 claimed ([card](https://huggingface.co/microsoft/harrier-oss-v1-0.6b), 2026-03-30). The results repo has no code tasks for it (5 of my 21 tasks found), so its code retrieval is unverified |
| google/embeddinggemma-300m | Gemma terms, gated | 0.30B | MTEB(Code) 12-task mean 68.75 (VERIFIED from the results repo). Licence is not OSI, so excluded |
| ibm-granite/granite-embedding-small-english-r2 | Apache-2.0 | 47M | CoIR 53.8, BEIR 50.9; 199 docs/s on an H100 at 512-token windows ([card](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2)) |
| nomic-ai/CodeRankEmbed | MIT | 137M | CoIR 60.1 ([card](https://huggingface.co/nomic-ai/CodeRankEmbed)) |
| nomic-ai/nomic-embed-code | Apache-2.0 | 7.1B | CodeSearchNet Python 81.7 ([card](https://huggingface.co/nomic-ai/nomic-embed-code)). Code-to-code and text-to-code; 12x the 0.6B cost |
| minishlab/potion-retrieval-32M | MIT | 32M static | No transformer at query time ([card](https://huggingface.co/minishlab/potion-retrieval-32M)); the lead measured potion-base-8M at 88k texts/s |
| jina-embeddings-v3, jina-code-embeddings-0.5b/1.5b, Salesforce SFR-Embedding-Code-400M_R | CC-BY-NC-4.0 | | Excluded: non-commercial is not open source (HF tags, VERIFIED) |

Reading:
- Qwen3-Embedding-0.6B and pplx-embed-v1-0.6b tie on code retrieval (75.4 vs 75.9).
- Qwen3 is the safer default: Apache-2.0, plain sentence-transformers loading, 8.5M downloads, and a matching
  reranker family (BELIEVED).
- No public score measures "package README for a developer need" directly.
- The closest benchmark is tool retrieval. On ToolRet (43k tools, 7.6k tasks, with instructions), BM25s scores an
  average nDCG@10 of 36.46. Older embedders score from 25.53 (all-MiniLM-L6-v2) to 45.96 (gte-Qwen2-1.5B-instruct).
  NV-Embed-v1 (7B) scores 42.71 ([Shi et al., ACL Findings 2025](https://arxiv.org/abs/2503.01763), Table 5, SOURCED).
  The authors find tool retrieval scores correlate poorly with MTEB.
- So a small 2022-era embedder may lose to BM25 here, and a 2025 LLM-based embedder probably beats it, by about 10
  points (BELIEVED, extrapolated from 2024 models). That is why BM25 stays in every arm.

### F3. Lexical, learned sparse, hybrid fusion

- **BM25.** [bm25s](https://github.com/xhluca/bm25s) (MIT, 1.8k stars, pushed 2026-10-08) is pure numpy/scipy, with
  "up to a 500x speedup" over rank-bm25 ([Lù 2024](https://arxiv.org/abs/2407.03618), SOURCED). ToolRet used it.
  Package names and keywords are exact tokens ("yaml", "retry"), which is where BM25 is strong (BELIEVED).
- **Learned sparse.** naver/splade-v3 is CC-BY-NC-SA-4.0 and gated (HF tag, VERIFIED), so excluded.
  opensearch-neural-sparse-encoding-doc-v3-gte is Apache-2.0 and 133M params, with an "inference-free" query side
  (tokenizer plus IDF lookup). It reports an average nDCG@10 of 0.546 on 13 BEIR sets, with no BM25 row in the same
  table ([card](https://huggingface.co/opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte), SOURCED).
  - No evidence was found on tool or package retrieval. It would add a third index for an unmeasured gain, so it is
    not an arm (BELIEVED).
- **Fusion.** Convex combination beats RRF in and out of domain. RRF is "sensitive to its parameters". Convex
  combination needs only a small set of examples to tune its single weight
  ([Bruch et al., TOIS 2023](https://doi.org/10.1145/3596512), SOURCED).
  - PROTOCOL prefers constants with a prior over fitted ones. So all arms use RRF with k=60, untuned. A convex weight
    in {0.3, 0.5, 0.7} is the only fusion constant allowed in the TUNE search space, if the lead wants one.
- **Stores with built-in hybrid search.**
  - LanceDB defaults to `RRFReranker()` ([docs](https://docs.lancedb.com/search/hybrid-search), SOURCED).
  - Qdrant fuses with `rrf` or `dbsf` and can re-score with ColBERT multivectors
    ([docs](https://qdrant.tech/documentation/concepts/hybrid-queries/), SOURCED).

### F4. Rerankers: small ones can hurt, and the agent already reranks

- Qwen3 rerankers re-scored the top 100 from Qwen3-Embedding-0.6B on MTEB(Code)
  ([Qwen3-Reranker-0.6B card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B), SOURCED):
  - the 0.6B reranker lowers the score from 75.41 to 73.42;
  - the 4B reranker raises it to 81.20, and the 8B to 81.22.
  - On English retrieval (MTEB-R), 0.6B raises it from 61.82 to 65.80, and 4B to 69.76.
- ToolRet, reranking NV-Embed-v1's candidates (Table 5, SOURCED):
  - bge-reranker-v2-m3 changes nothing (42.68 vs 42.71);
  - jina-reranker-v2 (NC licence) reaches 47.17, and bge-reranker-v2-gemma 47.52;
  - RankGPT listwise with GPT-3.5 or Mixtral-8x22B scores below the first stage (29.60-38.77).
  - These are 2024 models, so re-measure on current ones.
- Tool-DE: Qwen3-Reranker-4B adds +1.20 nDCG@10 on its benchmark
  ([Lu et al. 2025](https://arxiv.org/abs/2510.22670), SOURCED).
- A 22-method study finds LLM rerankers lead on familiar queries but generalise unevenly to novel ones, while
  lightweight models stay competitive ([Abdallah et al. 2025](https://arxiv.org/abs/2508.16757), SOURCED, abstract
  only).
- CPU cost: a 0.6B forward pass over 50 documents of up to 512 tokens took 134 s at 32 threads and 438 s at 4
  threads (VERIFIED proxy for a 0.6B cross-encoder, `bench_embed.py`). Reranking is therefore a GPU-server-only
  stage, never part of the HF fallback.
- Arm A3 is already an LLM listwise reranker: the agent reorders a shortlist of 20. A cross-encoder arm therefore
  competes with the agent, not with nothing (BELIEVED).

### F5. Late interaction (ColBERT-type): not worth it here

- GTE-ModernColBERT-v1 (Apache-2.0, 149M) averages 54.67 nDCG@10 on BEIR
  ([card](https://huggingface.co/lightonai/GTE-ModernColBERT-v1), SOURCED). Its library is
  [PyLate](https://github.com/lightonai/pylate) (MIT, pushed 2026-07-23).
- On ToolRet, ColBERTv2 scored below BM25 (23.79 vs 36.46 average nDCG@10, with instructions, Table 5; 19.46 vs
  22.32 without, SOURCED).
- Storage: 817k packages x 432 tokens x 128 dims is 90 GB at fp16, or about 11 GB at 2 bits per dimension (VERIFIED
  arithmetic). The HF fallback could not ship that, and the 1-vector index is 0.2-3.3 GB.
- Not an arm (BELIEVED).

### F6. Query and document expansion

- LLM query or document expansion helps weak retrievers but "generally harms stronger models". This held across 11
  expansion methods, 12 datasets and 24 retrievers
  ([Weller et al. 2023/EACL 2024](https://arxiv.org/abs/2309.08541), SOURCED).
- A 2026 controlled study finds generated query expansion still lifts SPLADE-v3 by 4.8-9.5% relative nDCG@10 on
  scientific collections, if the original query keeps at least 30% weight
  ([Barron et al. 2026](https://arxiv.org/abs/2609.37911), SOURCED, abstract).
- In `dev reuse` the calling agent already writes paraphrases (`--also`), so server-side query expansion adds an LLM
  call for what the caller does for free. No arm adds it (BELIEVED).
- **Document expansion is the stronger lever for this domain.**
  - Tool-DE generates structured fields per tool with Qwen3-32B: `function_description`, `when_to_use`,
    `limitations`, `tags`, `example_usage`.
  - An audit flags 41.6% of tool documents as incomplete, falling to 23.5% after expansion.
  - In ablations (BM25 and a dense retriever), `function_description` and `tags` help or are neutral, while
    `example_usage` adds the least and can hurt. The authors drop it.
  - Expansion-trained embedders add +4.97 and +6.69 nDCG@10 over Qwen3-Embedding-0.6B's 43.13
    ([Lu et al. 2025](https://arxiv.org/abs/2510.22670), §3.2, §4.1, App. A.4, SOURCED).
  - Inference-time gains appear only in a figure, so their size was not read.
- PyPI fits the problem: 6% of projects lack a summary, 17% a README, and 66% keywords (lead-data-coverage.md).

### F7. Package representation (what text gets embedded)

- One vector per package, built from: `name`, `summary`, keywords, the classifier topic leaves, the
  first-paragraph-weighted head of the README with badges, HTML and install blocks stripped, and GitHub topics where
  known. Truncate at 512 tokens.
- Reasons:
  1. The answer unit is a package, not a passage. Chunking would need max-pooling across chunks and a larger index
     for long-tail READMEs that are mostly short: median 1,465 characters, about 370 tokens (F1).
  2. Tool-DE's ablation favours functional description and tags over usage examples (F6).
  3. 512 tokens embeds 2.8x faster than 2,048 (F8).
- All of the above is BELIEVED. 512 vs 2,048 tokens is the one representation constant worth sweeping on TUNE.
- BM25 indexes the same fields. The name field is boosted by repeating it twice, fixed now (ASSUMED).
- **`--from` code snippets.** No separate code model. Qwen3-Embedding-0.6B is trained for code retrieval
  (MTEB(Code) 75.41), so the raw snippet is a valid query. Imports in the snippet also map to distributions
  (`yaml` → PyYAML).
  - The import-to-distribution map is not in open-pypi or `pypi.projects`. It needs wheel `top_level.txt` or a
    maintained mapping (gap G3).
  - Set C is TEST-only (PROTOCOL 7), so nothing here may be tuned on it.

### F8. Throughput and latency (VERIFIED, `bench_embed.py` and `search_cost.py`)

Sample: 3,000 real READMEs uploaded 2026-10-08 (ClickHouse), text = name + summary + README, bf16,
sentence-transformers 6.1.0, torch 2.14.1+cu130.

| Measure | Result |
|---|---|
| Qwen3-Embedding-0.6B docs, RTX 4090, max 512 tok | 149 docs/s, 64,357 tok/s, mean 432 tok/doc |
| same, max 2,048 tok | 39 docs/s, 47,905 tok/s, mean 1,223 tok/doc |
| Full PyPI (817,027) at 512 tok | 353M tokens: 1.5 h on one 4090; 0.38 h on 4 GPUs at 4090 speed |
| Full PyPI at 2,048 tok | 999M tokens: 5.8 h on one 4090; 1.45 h on 4 GPUs at 4090 speed |
| Daily 4,300 changed projects | 29 s (512) / 110 s (2,048) on one GPU |
| CPU query encode (batch 1), Qwen3-Embedding-0.6B | median 208 ms (32 threads), 239 ms (4 threads) |
| CPU query encode, bge-small-en-v1.5 / potion-base-8M | 5-10 ms / <0.1 ms |
| CLI cold start, Qwen3-0.6B on CPU (warm disk cache) | 2.8-2.9 s, of which 2.2-2.4 s is importing torch/sentence-transformers |
| Exact search, 817k x 1,024 float32, numpy | 54 ms/query (3.35 GB); 256 dims: 18 ms (0.84 GB); float16 matmul in numpy is 1.2 s, so keep float32 or use a SIMD library |

- **A6000 scaling.** The A6000 has less memory bandwidth than the 4090, and its dense bf16 tensor throughput is
  similar or lower. I expect 0.6-1.0x the 4090 rate, so a full build at 512 tokens takes 0.4-0.6 h on 4x A6000
  (BELIEVED, not measured on an A6000).
- **No ANN index is needed.** At 1M vectors, exact search takes 54 ms. Brute force also makes upserts trivial: an
  array row per package, replaced in place. Add HNSW only past about 10M vectors (BELIEVED).

### F9. Index store

Repositories checked through the GitHub API on 2026-10-13:

| Option | Licence | Stars, pushed | Upsert | Filters | Hybrid |
|---|---|---|---|---|---|
| numpy matrix + Parquet + bm25s | BSD/MIT | n/a | replace a row; rebuild BM25 nightly (minutes, BELIEVED) | pandas | RRF by hand (10 lines) |
| [FAISS](https://github.com/facebookresearch/faiss) | MIT | 41.1k, 2026-10-13 | `IndexIDMap` add/remove on flat indexes (BELIEVED) | no (post-filter) | no |
| [LanceDB](https://github.com/lancedb/lancedb) | Apache-2.0 | 11.7k, 2026-10-13 | merge-insert (BELIEVED; not on the fetched page) | SQL `where` | FTS + vector, RRF default (SOURCED) |
| [Qdrant](https://github.com/qdrant/qdrant) | Apache-2.0 | 35.1k, 2026-10-13 | upsert by id | payload filters | dense + sparse, RRF or DBSF, multivector rescoring (SOURCED); a server process |
| [usearch](https://github.com/unum-cloud/usearch) | Apache-2.0 | 4.3k, 2026-10-05 | add/remove | no | no |
| [sqlite-vec](https://github.com/asg017/sqlite-vec) | Apache-2.0 | 8.2k, 2026-05-18 | SQL | SQL | with FTS5 by hand |

- Recommendation: one Parquet file per snapshot (name, version, serial, summary, downloads_30d, dependents, vector),
  a numpy float32 matrix, and a bm25s index. This is the smallest correct store. One file is published to HF, and
  the server and the fallback run the same code path. LanceDB is the upgrade if filtering or size grows (BELIEVED).
- Serving: [text-embeddings-inference](https://github.com/huggingface/text-embeddings-inference) (Apache-2.0, 5.1k
  stars) or [vLLM](https://github.com/vllm-project/vllm) (Apache-2.0) are optional. A plain FastAPI process that holds
  the model and matrix is enough at agent query rates (BELIEVED).

### F10. Publishing a Hugging Face snapshot: licences

- **ODC-By** (open-pypi) covers the database, not its contents. It "does not cover any rights … in individual
  Contents". A public Derivative Database must carry the licence or its URI and keep existing notices
  ([ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/), SOURCED). So derived embeddings and fields can be
  published under ODC-By with attribution to open-index/open-pypi, but the README text keeps its authors' rights.
- **PyPI Terms of Service** (since 2025-02-25): for content without an included licence, uploaders grant "the PSF …
  and all other users of the Service" a licence to "reproduce, distribute, transmit, display, perform … and publish
  the Content". No clause bans bulk use or ML use. The API terms forbid "excessively frequent requests" and
  downloading "for spamming purposes … including … selling PyPI users' personal information"
  ([Terms of Service](https://policies.python.org/pypi.org/Terms-of-Service/), SOURCED). So: no author or maintainer
  emails or names in the published set.
- **Other sources.**
  - BigQuery and ClickHouse-derived fields (downloads, metadata) are CC BY 4.0 upstream. Attribute them.
  - ecosyste.ms dumps are CC BY-SA 4.0 ([open data page](https://packages.ecosyste.ms/open-data), SOURCED; latest
    dump 2026-02-05). Mixing them in would force share-alike on the published set, so keep ecosyste.ms out of it.
  - OSV's PyPI advisories are CC-BY 4.0 ([OSV data](https://google.github.io/osv.dev/data/), SOURCED).
- **Embeddings are not anonymised text.** vec2text recovers 92% of 32-token inputs exactly from their embeddings
  ([Morris et al. 2023](https://arxiv.org/abs/2310.06816), SOURCED). Published README vectors should be treated as
  carrying README content.
  - The PyPI ToS grant to "all other users" covers republishing the content itself. Whether vectors are a derivative
    work is legally unsettled (BELIEVED). This is an owner decision (gap G5).
  - Default: publish the vectors, name, version, summary and adoption columns. Do not publish raw README text, emails
    or author names.
- **Model licences.** Qwen3 and pplx are Apache-2.0 and MIT, and neither restricts outputs. Do not use NC-licensed
  models (jina, SPLADE-v3, SFR-Code) for a published index.
- **HF limits.** Free public storage is "best-effort": "Beyond the first few gigabytes, please use this resource
  responsibly". The Hub "starts to degrade after a few thousand commits"; `super_squash_history` resets history
  ([HF storage limits](https://huggingface.co/docs/hub/storage-limits), SOURCED).
  - Size: 817k x 1,024 float32 is 3.35 GB; with MRL at 256 dims and int8 it is 0.21 GB (VERIFIED arithmetic).
  - Plan: publish weekly, squash monthly. Ship the 256-dim int8 view for the fallback only if TUNE shows it within
    noise of full dims.

### F11. Extra free sources each arm needs, and how each stays fresh

| Source | Gives | Licence | Bootstrap | Refresh |
|---|---|---|---|---|
| open-index/open-pypi | name, summary, README, keywords, classifiers, URLs, dependencies | ODC-By | 1.07 GB download | none (frozen 2026-04-11) |
| PEP 691 simple root | project list + `_last-serial` | PyPI ToS | 1 GET, 45 MB | daily diff, 22 s |
| PyPI JSON API | changed projects' metadata + README + `requires_dist` | PyPI ToS | 228k catch-up, overnight at 2/s | ~4k/day, ~35 min at 2/s, ETag |
| ClickHouse `pypi_downloads_per_day` | 30-day downloads, all projects | CC BY 4.0 upstream | 1 query, 2.3 s | weekly |
| ClickHouse `pypi.projects` | alternative source for daily deltas | CC BY 4.0 upstream | not for bulk (9.5 MiB cap) | daily, a few queries |
| open-pypi `dependencies` + JSON `requires_dist` | reverse-dependency counts | ODC-By / PyPI | computed | with each JSON fetch |
| GitHub GraphQL | stars, pushed_at, archived, licence, topics, README for PyPI packages with GitHub URLs | GitHub ToS | 50 repos for 1 point (VERIFIED; 30 of 50 had `README.md`, so fall back to `.rst` and others); ~560k linked repos is about 11k points, under 3 h at 5,000 points/h ([limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api), SOURCED) | weekly for projects updated in the last 2 years |
| OSV `PyPI/all.zip` | advisories | CC-BY 4.0 | 1 download | daily |
| GitHub repos not on PyPI (96k at ≥100 stars) | non-packaged prior art | GitHub ToS | search slices + GraphQL, ~1 h (BELIEVED) | monthly |

- Needed by arm:
  - P1-P4 need rows 1-6.
  - GitHub README fill-in (row 7) matters only for the 17% of projects with no PyPI README. It is a field fill, not
    an arm.
  - Non-PyPI GitHub repos (row 9) cannot raise hit@3 when gold names are PyPI libraries (awesome-python, PyMigBench).
    A2 already searches GitHub live. Defer them until a gold set includes non-PyPI targets (BELIEVED).

## Candidate PROTOCOL arms (fixed now, before tuning; cheapest that is near-SoTA first)

All index arms share the following:
- **Corpus:** the open-pypi bootstrap plus serial-diff catch-up, frozen with the API snapshot (PROTOCOL 6, 12).
- **Representation:** F7, at 512 tokens.
- **Lexical side:** BM25 via bm25s, defaults k1=1.5, b=0.75, name repeated twice.
- **Fusion:** RRF with k=60.
- **Output:** top 100, with `downloads_30d` and `dependents` columns, passed into `dev reuse`. The shortlist rule
  then takes top-k by R together with top-k by adoption among R ≥ floor (gaps-r1). Gates and Q follow, as in A2.
  Each arm is unioned with the A2 pool, per PROTOCOL 11.

| Arm | Retrieval | Build / upkeep beyond A2-A3 | Runs where |
|---|---|---|---|
| **P1 = lexical + static** | BM25 ∪ minishlab/potion-retrieval-32M (MIT) dense, RRF | Data pipeline only (overnight catch-up, ~35 min a day); embedding takes seconds on CPU; no GPU; artefact about 0.3 GB | anywhere; HF fallback is complete |
| **P2 = A4** | BM25 ∪ Qwen3-Embedding-0.6B (Apache-2.0), fixed query instruction "Given a description of a programming need or a code snippet, retrieve Python packages that provide it", 1,024 dims float32, exact search, RRF | P1 + GPU 0.4-0.6 h one-time, <1 min a day; client needs torch and 1.2 GB of weights, or the SSH tunnel; ~3 s cold CLI call | server, or HF fallback with local query encode |
| **P3 = A5** | P2, then top 50 re-scored by Qwen3-Reranker-4B (Apache-2.0) on the GPU | P2 + the server must be up for every query; GPU latency for 50 pairs unmeasured (BELIEVED under 1 s on an A6000) | server only |
| **P4 = expansion** | P2 with each package text = F7 fields + an LLM profile (`function_description`, `when_to_use`, `tags`; no `example_usage`, per Tool-DE) generated offline by Qwen3-30B-A3B-Instruct-2507 or Qwen3-8B (both Apache-2.0, HF tags VERIFIED) with vLLM | P2 + LLM generation over ~0.8M packages one-time (unmeasured; BELIEVED a few hours to a day on 4x A6000) + ~4k a day; generated text adds a hallucination risk to the index | server builds; HF fallback gets the vectors |

**Amendment 3 view: what would justify each arm.**
- P1 is adopted only if it beats A3 on TEST hit@3 (paired 95% CI above 0) with no higher harm share. The likely place
  for this is the long-tail stratum (PROTOCOL 14), where the agent's memory and GitHub search are weakest.
- P2 must then beat P1. P3 and P4 must each beat P2.
- **Cheap TUNE-only pre-check** that fits PROTOCOL: on TUNE, measure *headroom*, the share of needs whose gold is in
  P1's top 100 but not in the A2 pool. If headroom is under about 10% of TUNE needs, the TEST interval cannot clear 0
  at N≈60, so stop at A3 without building P2-P4 (BELIEVED threshold; set it before running).
- **Prior expectation (BELIEVED).**
  - P2 over P1 is likely: ToolRet shows about 10 points between strong LLM-based embedders and BM25 or small
    embedders.
  - P3 over P2 is uncertain, because A3's agent rerank already captures most of what a cross-encoder adds.
  - P4 is the most speculative and the most expensive to keep fresh.

**Against over-engineering.** Do not build these until TUNE shows a reason:
- ColBERT (F5), SPLADE (F3), server-side query expansion (F6), an ANN index (F8), a vector database server (F9);
- chunked READMEs (F7);
- a 4B or 8B embedder: the 4B gains +3.9 on the MTEB(eng v2) mean and +6.6 on retrieval for 6.7x the parameters
  (Qwen3 card), and recall@100 rather than nDCG@10 is what this stage is judged on;
- a GitHub-wide non-PyPI corpus (F11).

## Gaps seen, not closed

- G1. No A6000 measurement. The A6000 rates are extrapolated from one RTX 4090 run. The owner's first server script
  should print docs/s at 512 and 2,048 tokens.
- G2. The open-pypi row-count mismatch (712,894 on the card vs 945,204 from datasets-server) and the ClickHouse
  GROUP BY count (677k vs 817k names) were not reconciled.
- G3. No source for import name to distribution (`top_level.txt`) was found in the open datasets. `--from` needs one.
  Candidates: wheel METADATA/RECORD via the JSON API's file URLs (heavy), or a maintained mapping list (unchecked).
- G4. Inference-time effect size of document expansion for packages: Tool-DE gives it only in a figure. P4's LLM
  throughput on 4x A6000 is unmeasured.
- G5. Legal status of publishing embeddings of README text (F10). Owner decision; the default excludes raw text.
- G6. Terms or rate policy of the ClickHouse `demo` user are unknown. Do not make it a hard dependency.
- G7. Qwen3-Reranker-4B latency for 50 pairs on an A6000 is unmeasured.
- G8. harrier-oss-v1-0.6b (MIT, 2026-03) may beat Qwen3-0.6B, but its code-task results are not in the MTEB results
  repo.

## Sources fetched (2026-10-13)

- Qwen3 Embedding: [arXiv 2506.05176](https://arxiv.org/abs/2506.05176) (abstract via `dev lit`); model cards
  [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) and
  [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B).
- [pplx-embed-v1-0.6b card](https://huggingface.co/perplexity-ai/pplx-embed-v1-0.6b) and
  [arXiv 2602.11151](https://arxiv.org/abs/2602.11151) (abstract);
  [harrier-oss-v1-0.6b card](https://huggingface.co/microsoft/harrier-oss-v1-0.6b);
  [granite-embedding-small-english-r2](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2);
  [CodeRankEmbed](https://huggingface.co/nomic-ai/CodeRankEmbed);
  [nomic-embed-code](https://huggingface.co/nomic-ai/nomic-embed-code);
  [GTE-ModernColBERT-v1](https://huggingface.co/lightonai/GTE-ModernColBERT-v1);
  [opensearch doc-v3-gte](https://huggingface.co/opensearch-project/opensearch-neural-sparse-encoding-doc-v3-gte);
  [potion-retrieval-32M](https://huggingface.co/minishlab/potion-retrieval-32M). Licences and sizes for these and
  for the excluded models came from the HF API.
- MTEB per-task results: [embeddings-benchmark/results](https://github.com/embeddings-benchmark/results) (21 task
  files for 5 models).
- [ToolRet, Shi et al. 2025](https://arxiv.org/abs/2503.01763)
  ([ACL Findings DOI](https://doi.org/10.18653/v1/2025.findings-acl.1258)), Tables 5 and 9 and §6.1.
- [Tool-DE, Lu et al. 2025](https://arxiv.org/abs/2510.22670), §3.2, §4.1, §5.2, App. A.4.
- [Bruch et al. 2023, TOIS](https://doi.org/10.1145/3596512) (abstract).
- [BM25S, Lù 2024](https://arxiv.org/abs/2407.03618) (abstract).
- [Weller et al. 2023](https://arxiv.org/abs/2309.08541) (abstract); [Barron et al. 2026](https://arxiv.org/abs/2609.37911)
  (abstract); [Abdallah et al. 2025](https://arxiv.org/abs/2508.16757) (abstract).
- [Morris et al. 2023, vec2text](https://arxiv.org/abs/2310.06816) (abstract).
- PyPI: [API overview](https://docs.pypi.org/api/), [feeds](https://docs.pypi.org/api/feeds/),
  [BigQuery](https://docs.pypi.org/api/bigquery/),
  [Terms of Service](https://policies.python.org/pypi.org/Terms-of-Service/), the simple index root, the RSS feeds,
  and the JSON API (probed).
- [ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/);
  [ecosyste.ms open data](https://packages.ecosyste.ms/open-data); [OSV data](https://google.github.io/osv.dev/data/);
  [HF storage limits](https://huggingface.co/docs/hub/storage-limits);
  [GitHub GraphQL limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api).
- [LanceDB hybrid search](https://docs.lancedb.com/search/hybrid-search);
  [Qdrant hybrid queries](https://qdrant.tech/documentation/concepts/hybrid-queries/);
  [ClickHouse/clickpy README](https://github.com/ClickHouse/clickpy); the open-pypi dataset card and its commit list.
- GitHub API repository metadata: faiss, lancedb, qdrant, usearch, sqlite-vec, bm25s, tantivy-py, pylate,
  sentence-transformers, text-embeddings-inference, vllm, infinity, model2vec, pyserini, hnswlib, ColBERT, rank_llm,
  duckdb-vss.
