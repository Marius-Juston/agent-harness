"""Check that gold scripts share no README text with the package's PyPI description (#196, licence scrub).

Usage: uv run --no-project python readme_overlap.py <pypi_snapshot_n.json> <cases_n.json> <gold_dir> [ids...]
Reports, per script, lines of 20+ characters that appear verbatim in the README and shared runs of 6+ tokens.
"""

import json
import re
import sys
from pathlib import Path

snap = json.loads(Path(sys.argv[1]).read_text())
pkg = {c["id"]: c["package"] for c in json.loads(Path(sys.argv[2]).read_text())}
gold = Path(sys.argv[3])
ids = sys.argv[4:] or sorted(pkg)
bad = 0
for i in ids:
    readme = snap[pkg[i]]["description"]
    src = (gold / f"{i}.txt").read_text()
    lines = [ln.strip() for ln in src.splitlines() if len(ln.strip()) >= 20 and ln.strip() in readme]
    tok = lambda s: re.findall(r"\w+|[^\w\s]", s)  # noqa: E731
    rt, st = " ".join(tok(readme)), tok(src)
    runs = {" ".join(st[j : j + 6]) for j in range(len(st) - 5) if " ".join(st[j : j + 6]) in rt}
    bad += bool(lines)
    print(i, pkg[i], "verbatim lines:", lines, "| shared 6-token runs:", len(runs), sorted(runs)[:4])
sys.exit(1 if bad else 0)
