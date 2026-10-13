# Lead measurement: cost of a full offline PyPI semantic index (2026-10-13)

VERIFIED by commands run on this machine (32 cores, WSL2), one sample each; rates depend on the network and on
PyPI/ecosyste.ms load. Scripts were inline; re-run them before relying on the numbers.

| Step | Measurement | Full PyPI (912,487 projects) |
|---|---|---|
| Bulk metadata via packages.ecosyste.ms `/registries/pypi.org/packages?per_page=1000` | 13.5 s and 152 MB per 1,000 (records carry full repo metadata); anonymous limit 5,000 req/h | ~140 GB, ~3.5 h: too heavy |
| PyPI JSON API `/pypi/<name>/json`, 8 threads, 200 random projects | 24.3 projects/s; median 11 kB, mean 23 kB; 186/200 have a summary | ~10.4 h, ~21 GB, one time |
| sethmlarson/pypi-data `pypi.db.gz` (Apache-2.0) | 409 MB, last release 2026-07-21; tracks names, versions, classifiers, URLs, scorecard, not summaries | names/URLs/classifiers only |
| Embedding with model2vec potion-base-8M | 88,239 texts/s, 256 dims | ~10 s; 467 MB as float16 (233 MB int8) |
| Updates: XML-RPC `changelog_since_serial` | 19,979 events over 12.1 h = ~39,700 events/day, ~7,200 distinct projects/day | ~5 min/day of fetching at 24/s, <1 s re-embedding |

Not measured: the PyPI BigQuery public dataset (summaries in one query; free tier but needs a Google Cloud account),
and the cost of a stronger embedding model (bge-small is roughly 100x slower than potion on CPU: BELIEVED, unmeasured).
