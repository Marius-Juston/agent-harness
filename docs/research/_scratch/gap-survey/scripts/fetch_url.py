"""Fetch whole pages by URL for the #498 gap survey (saved outside the repository).

Usage: uv run python docs/research/_scratch/gap-survey/scripts/fetch_url.py <out-dir> <name>=<https-url>...
"""

import sys
import urllib.request
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for arg in sys.argv[2:]:
        name, url = arg.split("=", 1)
        if not url.startswith("https://"):
            raise SystemExit(f"https only: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "docs-reader/1.0"})  # noqa: S310 - https only
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - https checked above
                body = r.read()
            (out / name).write_bytes(body)
            print(f"{name:28} {r.status} {len(body)}")
        except Exception as e:  # report and continue
            print(f"{name:28} ERROR {e}")


if __name__ == "__main__":
    main()
