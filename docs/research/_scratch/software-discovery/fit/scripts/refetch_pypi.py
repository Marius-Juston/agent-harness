"""Rebuild the frozen F1 PyPI snapshot from version-pinned PyPI URLs and check its SHA-256 (#196).

Usage: uv run --no-project python refetch_pypi.py <versions.json> <out.json> <expected sha256>
The snapshot itself holds third-party README text, so it is not committed; this script re-fetches
https://pypi.org/pypi/<name>/<version>/json for the version recorded in versions.json (the same fields and the same
12,000-character cut as snapshot.py) and exits non-zero if the hash differs.
"""

import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

VERS, OUT, EXPECT = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
DESC = 12000


def norm(s: str) -> str:
    return re.sub(r"[-_.]+", "-", s).lower()


snap = {}
for p, dists in sorted(json.loads(VERS.read_text()).items()):
    ver = next(d.rsplit("-", 1)[1] for d in dists if norm(d.rsplit("-", 1)[0]) == norm(p))
    url = f"https://pypi.org/pypi/{p}/{ver}/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        info = json.load(r)["info"]
    snap[p] = {k: info.get(k) for k in ("name", "version", "summary", "requires_python")}
    snap[p]["description"] = (info.get("description") or "")[:DESC]
    time.sleep(0.5)
OUT.write_text(json.dumps(snap, indent=1))
got = hashlib.sha256(OUT.read_bytes()).hexdigest()
print("sha256", got, "OK" if got == EXPECT else f"MISMATCH (expected {EXPECT})")
sys.exit(0 if got == EXPECT else 1)
