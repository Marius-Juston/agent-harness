"""Sandbox run time of every check (#196): gold scripts and agent-written F3/F3r/F4/F4r checks, from committed files.

Usage: uv run --no-project python run_times.py <result.json>...
Counts each sandbox run once (run0 and run of a repaired case both count). Agent call time is not included.
"""

import json
import statistics as st
import sys
from pathlib import Path

secs = []
for f in sys.argv[1:]:
    for k, v in json.loads(Path(f).read_text()).items():
        if k.startswith("_"):
            continue
        if "calls" not in v:
            secs.append((v["seconds"], f"{Path(f).name}:{k}"))
            continue
        for r in (v.get("run0"), v.get("run")):
            if r and r.get("seconds") is not None:
                secs.append((r["seconds"], f"{Path(f).name}:{k}"))
secs = sorted(set(secs))
vals = [s for s, _ in secs]
print(
    f"runs {len(vals)}, median {st.median(vals):.2f} s, p90 {st.quantiles(vals, n=10)[8]:.2f} s, max {vals[-1]:.2f} s"
)
print("slowest:", secs[-5:])
