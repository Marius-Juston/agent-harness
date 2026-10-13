# Lead check: does an open semantic index of PyPI already exist? (2026-10-13)

Searched: Hugging Face Hub API (datasets: "pypi", "pypi metadata", "python packages", "pypi embeddings"; spaces:
"pypi"), GitHub repository search, one extended web search. Not exhaustive.

- **No open, maintained semantic (embedding) index of all PyPI was found.** "pypi embeddings" returns no dataset;
  no Space offers package search. pyatlas (fpgmaas, Apache-2.0) embeds only the top 10,000 packages as a static
  site with no API (probed earlier). Context7 indexes library docs, closed. libraries.io is keyword search.
- **Raw metadata already exists, so the 10 h fetch can mostly be skipped:**
  - [open-index/open-pypi](https://huggingface.co/datasets/open-index/open-pypi): ODC-By, updated 2026-04-11,
    `packages` table 945,204 rows with summary, description (README), keywords, classifiers, project_urls,
    release_count, yanked; plus releases, files, dependencies, owners tables.
  - [semvec/pypi-packages](https://huggingface.co/datasets/semvec/pypi-packages): Apache-2.0, 2026-02-23, 690,775
    rows of core metadata with summary and description (with downloads).
  - [labofsahil/pypi-packages-metadata-dataset](https://huggingface.co/datasets/labofsahil/pypi-packages-metadata-dataset):
    MIT, 2026-01-18, 19.3M rows (one per release).
  Catch-up from a snapshot uses PyPI's XML-RPC `changelog_since_serial` (~7,200 changed projects/day, lead-index-cost.md).

Owner decisions (2026-10-13): anything run on the owner's GPU server is a script the owner starts, over SSH.
Open: publish the built index on Hugging Face (owner's account) for others; see gaps-r1.md.
