#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["networkx==3.5"]
# ///
"""Lines of output each candidate check would print at the newest replayed commit and at review 2 (#496): the cost
side of 'signal per line an agent must read'. Thresholds are the ones analyse.py tested.

Run from the worktree root: ``uv run docs/research/_scratch/architecture-grading/scripts/head_lines.py``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analyse as A

for idx in (A.REVIEW2, len(A.R) - 1):
    ms = A.metrics(idx)
    hot = sorted(ms, key=lambda p: -ms[p]["hotspot_loc"])[:3]
    big = [p for p, m in ms.items() if m["loc"] > 1000]
    cog = sum(A.B[A.R[idx]["files"][p]]["cog_over15"] for p in ms)
    cc = sum(A.B[A.R[idx]["files"][p]]["cc_over10"] for p in ms)
    comm3 = [p for p, m in ms.items() if m["communities"] >= 3]
    print(f"index {idx} ({A.R[idx]['sha']}), {len(ms)} modules")
    print(f"  hotspot top 3 (churn30 x LOC): {[(p.split('/')[-1], ms[p]['hotspot_loc']) for p in hot]}")
    print(f"  modules over 1,000 LOC: {[p.split('/')[-1] for p in big]}")
    print(f"  functions over cognitive 15: {cog}; over cyclomatic 10: {cc}")
    print(f"  modules with >=3 communities of >=100 LOC: {len(comm3)}")
