"""Summarise install cost per package from install_all.py output (#196): cold and warm seconds, venv and cache size.

Usage: uv run --no-project python install_costs.py <installs.json>...
"""

import json
import statistics as st
import sys
from pathlib import Path

rows = {}
for f in sys.argv[1:]:
    rows.update(json.loads(Path(f).read_text()))
ok = [r for r in rows.values() if r["cold"]["ok"]]
print(f"packages {len(rows)}, installed with --no-build {len(ok)}")
for key, label, scale in (
    ("seconds", "cold s", 1),
    ("venv_bytes", "venv MB", 1e6),
    ("cache_bytes", "cold cache MB", 1e6),
):
    v = sorted(r["cold"][key] / scale for r in ok)
    q = st.quantiles(v, n=10)
    print(f"{label}: median {st.median(v):.1f}, p90 {q[8]:.1f}, max {v[-1]:.1f}")
w = sorted(r["warm"]["seconds"] for r in ok)
print(f"warm s: median {st.median(w):.2f}, max {w[-1]:.2f}")
big = sorted(ok, key=lambda r: -r["cold"]["seconds"])[:4]
print("slowest:", [(r["cold"]["package"], r["cold"]["seconds"], round(r["cold"]["venv_bytes"] / 1e6)) for r in big])
