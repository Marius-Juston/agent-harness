"""Build a compact offline index of the most-downloaded PyPI packages from packages.ecosyste.ms (#169).

Usage: uv run python pypi_index.py <out.jsonl> [pages]   (1,000 packages per page, sorted by downloads, default 30)
Each line: name, summary, keywords, repo (github owner/repo lower case or ""), downloads, stars. The raw pages (about
125 MB each at the top of the ranking, mostly repo_metadata) are parsed while streaming and not kept.
"""

import json
import re
import sys
import time
import urllib.request

URL = "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages?per_page=1000&page={}&sort=downloads&order=desc"
UA = {"User-Agent": "agent-harness-recall-bench/0.1 (research; github.com/Marius-Juston/agent-harness)"}


def main() -> None:
    out, pages = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30
    n = 0
    t0 = time.time()
    with open(out, "w", encoding="utf-8") as fh:
        for page in range(1, pages + 1):
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(urllib.request.Request(URL.format(page), headers=UA), timeout=300) as r:  # noqa: S310
                        body = json.loads(r.read())
                    break
                except Exception as exc:  # retry any network or decode failure
                    print(f"page {page} attempt {attempt}: {exc}", file=sys.stderr)
                    time.sleep(30 * (attempt + 1))
            else:
                sys.exit(f"page {page} failed")
            for p in body:
                m = re.match(r"https://github.com/([^/]+/[^/#?]+)", (p.get("repository_url") or "").lower())
                fh.write(
                    json.dumps(
                        {
                            "name": p["name"],
                            "summary": p.get("description") or "",
                            "keywords": p.get("keywords_array") or [],
                            "repo": m.group(1).removesuffix(".git") if m else "",
                            "downloads": p.get("downloads") or 0,
                            "stars": (p.get("repo_metadata") or {}).get("stargazers_count") or 0,
                        }
                    )
                    + "\n"
                )
                n += 1
            print(f"page {page}: {n} packages, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
