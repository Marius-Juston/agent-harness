"""Freeze the inputs of the fit eval (#196): installed versions per package env, and the PyPI text arm F1 reads.

Usage: uv run --no-project python snapshot.py <envroot> <versions.json> <pypi_snapshot.json> <cases.json>...
The PyPI snapshot holds name, version, summary, requires_python and the first 12,000 characters of the project
description from https://pypi.org/pypi/<name>/json. It stays outside git (third-party README text); its SHA-256 goes
in PROTOCOL.md.
"""

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT, VERS, SNAP = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
pkgs = sorted({c["package"] for f in sys.argv[4:] for c in json.loads(Path(f).read_text())})
DESC = 12000
versions, snap = {}, {}
for p in pkgs:
    site = next((ROOT / p / ".venv" / "lib").glob("python3*/site-packages"))
    versions[p] = sorted(d.name.removesuffix(".dist-info") for d in site.glob("*.dist-info"))
    with urllib.request.urlopen(f"https://pypi.org/pypi/{p}/json", timeout=30) as r:
        info = json.load(r)["info"]
    snap[p] = {k: info.get(k) for k in ("name", "version", "summary", "requires_python")}
    snap[p]["description"] = (info.get("description") or "")[:DESC]
    time.sleep(0.5)
VERS.write_text(json.dumps(versions, indent=1))
SNAP.write_text(json.dumps(snap, indent=1))
print("pypi snapshot sha256", hashlib.sha256(SNAP.read_bytes()).hexdigest())
