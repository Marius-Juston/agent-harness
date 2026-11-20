"""Fisher-z 95% intervals for the Spearman coefficients analyse.py prints (n = modules at the snapshot), and the
bug-fix touch counts per module over the whole history (#496). Standard library only.

Run from the worktree root: ``python3 docs/research/_scratch/architecture-grading/scripts/ci.py``.
"""

import json
import math
from collections import Counter
from pathlib import Path


def ci(r: float, n: int) -> tuple[float, float]:
    z, se = math.atanh(r), 1 / math.sqrt(n - 3)
    return round(math.tanh(z - 1.96 * se), 2), round(math.tanh(z + 1.96 * se), 2)


for label, r, n in [
    ("churn30 at 100", 0.70, 23),
    ("hotspot_loc at 100", 0.63, 23),
    ("loc at 100", 0.35, 23),
    ("cog_sum at 100", 0.24, 23),
    ("cog_sum size-partialled at 100", -0.44, 23),
    ("fan_out at 100", 0.57, 23),
    ("median churn30", 0.36, 23),
    ("median hotspot_loc", 0.42, 23),
]:
    print(f"{label:34} r={r:+.2f}  95% CI {ci(r, n)}")
# Spearman with ties is only approximately normal under Fisher z; treat these as rough.

D = json.loads(Path(".scratch/arch_history.json").read_text())
bugs = Counter(p for r in D["rows"] if r["bug"] for p in r["changed"])
allc = Counter(p for r in D["rows"] for p in r["changed"])
print("\nbug-fix commits", sum(r["bug"] for r in D["rows"]), "of", len(D["rows"]))
for p, n in bugs.most_common(8):
    print(f"  {p:28} bug-fix touches {n:>2} of {allc[p]:>2} commits")
wf = [i for i, r in enumerate(D["rows"]) if "devkit/workflow.py" in r["changed"] and 80 <= i < 144]
print("\ncommits touching workflow.py in [80, 144):", len(wf), wf)
