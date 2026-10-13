# Gaps after round 1 (lead, 2026-10-13)

Round 1: [r1-surface](r1-surface.md), [r1-ranking](r1-ranking.md), [lead-coverage](lead-coverage.md); r1-benchmark
still running when this was written. Build: agent-harness PR #171 (`dev reuse`, review fixes in 4d61af0).

## Findings that change the recommendation
- Surface: keep a CLI; the rule needs enforcement, not a prompt line (builders searched before writing a new module
  in 2/6 runs; skills untriggered in 56% in Vercel's eval). Proposed: `uv add` PreToolUse guard (G1/G2/G5), a
  `Reuse:` line in the PR summary checked by the completion gate, an optional user-invoked `/reuse` front door. No MCP.
- Coverage: neither index contains the other (30% of random PyPI projects have no repo URL; 22-56% of active GitHub
  Python repos are on PyPI). PyPI has no search API.

## Live failure of the built ranking (VERIFIED, `uv run --extra semantic dev reuse "retry a function call with
exponential backoff" --also "retrying decorator" -n 12`, 2026-10-13 02:55)
Top 12 were retry-decorator, near-duplicate personal repos and new tiny packages; tenacity absent. Three causes:
1. HOLD candidates rank among passes (8 of the top 12 were HOLD: dormant or no licence).
2. R spread among near-duplicates (0.60-0.78) exceeds 0.15*Q, so adoption (dependents 2,186 for `retry`, tenacity
   far more) barely moves the order.
3. The 2n shortlist is chosen by R (+ a stars bonus) before enrichment, so a library with lower description
   similarity (tenacity R 0.50) is never scored.

## Questions for round 2
- Tune the ranking end to end on the r1 benchmark (call `devkit.reuse` with overridden constants): candidates include
  PASS before HOLD; relevance as a tier (within delta of the best R, or R >= floor) then order by Q; shortlist =
  union of top-k by R and top-k by adoption among R >= floor; lambda in {0.15, 0.5, 1}. Report hit@1/@3/@10.
- Offline PyPI semantic index: data source, size, embed time, licence, refresh; does it add recall on the benchmark?
- Which further free sources add recall (libraries.io, conda-forge, awesome lists, code search for --from)?

## Added after the owner's GPU server (2026-10-13)
- Which free open-weight retrieval embedding and re-ranker models to use for package READMEs (licence, size,
  retrieval benchmark scores fetched, 2026 models), and their measured throughput on an A6000.
- Serving: a private HTTP service on the owner's server that `dev reuse` calls when configured, local fallback
  otherwise; access over SSH or a private network only; index refresh from PyPI's changelog serial.
- Whether bulk fetching the PyPI JSON API at ~24 projects/s is acceptable use, or BigQuery is the better source.
- Build from open-index/open-pypi (ODC-By) + changelog catch-up instead of a 10 h fetch (lead-prior-art-index.md).
- Publishing: if the index passes the PROTOCOL decision rule, publish vectors + name/summary (no README text, no
  author emails) as a Hugging Face dataset with ODC-By attribution, so consumers download it and embed only the query
  locally; the owner's server is then only the builder. Check: licence of derived embeddings from ODC-By and PyPI
  metadata; CPU query-encode latency of the chosen model; HF free storage limits; staleness shown by `dev reuse`.

## Owner 2026-10-13 (later): state-of-the-art pipeline, easy updates, hosting on the lab GPU server
- Requirement: if built, the index must use the state-of-the-art indexing and retrieval pipeline and be easy to update.
- Hosting: the owner can host a service on the UIUC lab GPU server (exclusive to the lab), reached over SSH.
  Lead default: private only, through an SSH tunnel, no public endpoint (university acceptable-use rules unchecked).
  A published Hugging Face snapshot stays the fallback for people without access.
