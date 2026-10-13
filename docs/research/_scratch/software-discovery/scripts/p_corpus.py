"""Corpus for index arms P1-P4 (PROTOCOL.md amendment 4, r2-pipeline.md F7): open-index/open-pypi (ODC-By, snapshot
2026-04-11) caught up with the latest release metadata of every project uploaded since, from ClickHouse's public
`pypi.projects` mirror (CC BY 4.0 upstream; r2-pipeline F1). The PEP 691 catch-up of r2 (JSON API at 2 requests/s)
would take about 40 h for the ~290k changed or new projects, so this round uses the mirror instead (a deviation, named
in r3-tuning.md).

Usage:
  uv run --no-project --with pyarrow python -I p_corpus.py delta <out_dir>          # 64 ClickHouse queries, ~5 min
  uv run --no-project --with pyarrow python -I p_corpus.py build <open-pypi packages/0000.parquet> <delta_dir> <out.jsonl>

Each corpus row: name, repo (github owner/name, lower case, or ""), text (F7: name, summary, keywords, classifier
topic leaves, README head with badges, HTML, code blocks and links stripped, cut at 2,048 characters), src.
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

CH = "https://sql-clickhouse.clickhouse.com/?user=demo"
SINCE = "2026-04-11 00:00:00"
PARTS = 64
MAX_CHARS = 2048  # ~512 tokens (r2-pipeline F7); the 512 vs 2,048-token sweep is a TUNE-only constant
GH = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
SKIP_OWNERS = {"sponsors", "orgs", "settings", "marketplace", "features", "topics", "apps"}


def delta(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for p in range(PARTS):
        f = out / f"p{p:02d}.jsonl"
        if not f.exists():
            q = (f"SELECT name, argMax(summary, upload_time) AS summary, argMax(keywords, upload_time) AS keywords, "  # noqa: S608 (constants only)
                 f"argMax(home_page, upload_time) AS home_page, argMax(project_urls, upload_time) AS project_urls, "
                 f"argMax(classifiers, upload_time) AS classifiers, leftUTF8(argMax(description, upload_time), 2500) AS "
                 f"description, max(upload_time) AS last_upload FROM pypi.projects WHERE upload_time >= '{SINCE}' "
                 f"AND cityHash64(name) % {PARTS} = {p} GROUP BY name FORMAT JSONEachRow")
            with urllib.request.urlopen(urllib.request.Request(CH, data=q.encode()), timeout=120) as r:
                body = r.read()
            if b"TOO_MANY_ROWS_OR_BYTES" in body[-2000:] or b"Exception" in body[-500:]:
                sys.exit(f"partition {p}: result capped or failed")
            f.write_bytes(body)
            time.sleep(1.0)
        total += sum(1 for _ in f.open(errors="replace"))
    print(f"delta rows: {total}")


def repo_of(urls: list[str]) -> str:
    for u in urls:
        if m := GH.match(u or ""):
            owner, name = m[1].lower(), m[2].lower().removesuffix(".git")
            if owner not in SKIP_OWNERS:
                return f"{owner}/{name}"
    return ""


BADGE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)|!\[[^\]]*\]\([^)]*\)|\.\. \|[^|]+\| image::.*|^\.\. image::.*$",
                   re.M)
HTML = re.compile(r"<[^>]{0,400}>")
FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")


def readme_head(text: str) -> str:
    t = FENCE.sub(" ", text or "")
    t = BADGE.sub(" ", t)
    t = HTML.sub(" ", t)
    t = LINK.sub(r"\1", t)
    t = URL.sub(" ", t)
    t = re.sub(r"(?im)^\s*(\$ )?pip install .*$", " ", t)
    t = re.sub(r"[#=*_`|>~-]{2,}", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def doc_text(name: str, summary: str, keywords: str, classifiers: list[str], readme: str) -> str:
    topics = sorted({c.split(" :: ")[-1] for c in classifiers or [] if c.startswith("Topic ::")})
    head = " ".join(x for x in [name, summary or "", (keywords or "").replace(",", " "), " ".join(topics)] if x)
    return (head + " . " + readme_head(readme))[:MAX_CHARS]


def build(packages: Path, delta_dir: Path, out: Path) -> None:
    import pyarrow.parquet as pq
    norm = lambda s: re.sub(r"[-_.]+", "-", s).lower()  # noqa: E731 (PEP 503)
    rows: dict[str, dict] = {}
    cols = ["name", "summary", "description", "keywords", "classifiers_json", "project_urls_json", "home_page", "yanked"]
    pf = pq.ParquetFile(packages)
    for batch in pf.iter_batches(columns=cols, batch_size=50_000):
        for r in batch.to_pylist():
            urls = [*(json.loads(r["project_urls_json"] or "null") or {}).values(), r["home_page"] or ""]
            cls = json.loads(r["classifiers_json"] or "null") or []
            rows[norm(r["name"])] = {"name": r["name"], "repo": repo_of(urls), "src": "open-pypi",
                                     "text": doc_text(r["name"], r["summary"], r["keywords"], cls, r["description"])}
    base = len(rows)
    upd = new = 0
    for f in sorted(delta_dir.glob("p*.jsonl")):
        for line in f.open(errors="replace"):
            r = json.loads(line)
            urls = [u.split(", ", 1)[-1] for u in r["project_urls"]] + [r["home_page"] or ""]
            k = norm(r["name"])
            new += k not in rows
            upd += k in rows
            rows[k] = {"name": r["name"], "repo": repo_of(urls), "src": "clickhouse",
                       "text": doc_text(r["name"], r["summary"], r["keywords"], r["classifiers"], r["description"])}
    with out.open("w") as fh:
        for r in rows.values():
            fh.write(json.dumps(r) + "\n")
    print(f"open-pypi {base}; delta updated {upd}, new {new}; corpus {len(rows)}; "
          f"with a GitHub repo {sum(bool(r['repo']) for r in rows.values())}")


if __name__ == "__main__":
    if sys.argv[1] == "delta":
        delta(Path(sys.argv[2]))
    else:
        build(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
