"""Fetch Claude Code docs pages as markdown for the #498 gap survey (read-only, not committed output).

Usage: uv run python docs/research/_scratch/gap-survey/scripts/fetch_docs.py <out-dir> <page>...
Each page is fetched from https://code.claude.com/docs/en/<page>.md and saved as <out-dir>/<page>.md.
"""

import sys
import urllib.request
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for page in sys.argv[2:]:
        url = f"https://code.claude.com/docs/en/{page}.md"
        req = urllib.request.Request(url, headers={"User-Agent": "docs-reader/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https prefix
                body = r.read()
            (out / f"{page.replace('/', '_')}.md").write_bytes(body)
            print(f"{page:24} {r.status} {len(body)}")
        except Exception as e:  # report and continue
            print(f"{page:24} ERROR {e}")


if __name__ == "__main__":
    main()
