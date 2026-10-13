# Lead measurement: what the open data covers for indexing (2026-10-13)

VERIFIED (Hugging Face datasets-server statistics, HF API file sizes, `gh api search/repositories` total_count).

## open-index/open-pypi `packages` (945,204 rows, ODC-By, snapshot 2026-04-11, 4 parquet files, ~1.07 GB)
| Field | Present | Note |
|---|---|---|
| summary | 94.0% | median 47 chars |
| description (README) | 83.3% | median 1,947 chars |
| project_urls_json | 76.5% | repository link source |
| keywords | 34.4% | |
| docs_url | 0.5% | |
| release_count | 100% | releases table gives dates, so recency and cadence are computable |
Not in it: downloads, GitHub stars and activity, dependents (computable from the `dependencies` table), OSV
advisories, OpenSSF Scorecard. These come from pypistats/BigQuery, the GitHub API, the OSV bulk export, and
deps.dev or the sethmlarson/pypi-data scorecard table.

## GitHub Python repositories (total_count, 2026-10-13)
- stars >= 50: 169,159; stars >= 100: 96,223; stars >= 1,000: 11,853
- stars >= 20 and pushed in the last year: 114,991
Search returns at most 1,000 results per query, so enumeration has to slice by star ranges or creation dates
(BELIEVED feasible in well under a day at 30 searches/min; not run). READMEs and topics for ~100-170k repos would
then come from GraphQL (batched) or raw.githubusercontent.com (rate limits unmeasured).
