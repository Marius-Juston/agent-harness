"""Stratum N restricted to the 8 packages whose first PyPI upload is on or after 2026-07-15 (16 cases) (#196).

Usage: uv run --no-project python post_cutoff.py <cases_n.json> <arm>=<result.json>...
The 4 packages first released earlier by PyPI's own upload times (data/n_selection.md) are left out.
"""

import json
import sys
from pathlib import Path

PRE = {"poly-basis-ml", "ezgb", "multiplayer", "camt053-lsp"}  # first PyPI upload before 2026-07-15
cases = [c for c in json.loads(Path(sys.argv[1]).read_text()) if c["package"] not in PRE]
nf = [c for c in cases if c["gold"] == "NOT_FIT"]
print(f"post-cutoff cases {len(cases)} (FIT {len(cases) - len(nf)}, NOT_FIT {len(nf)})")
print("| arm | right | catch | false alarm |\n|---|---|---|---|")
for a in sys.argv[2:]:
    k, f = a.split("=", 1)
    d = json.loads(Path(f).read_text())
    right = sum(d[c["id"]]["verdict"] == c["gold"] for c in cases)
    catch = sum(d[c["id"]]["verdict"] == "NOT_FIT" for c in nf)
    fa = sum(d[c["id"]]["verdict"] == "NOT_FIT" for c in cases if c["gold"] == "FIT")
    print(f"| {k} | {right}/{len(cases)} | {catch}/{len(nf)} | {fa}/{len(cases) - len(nf)} |")
