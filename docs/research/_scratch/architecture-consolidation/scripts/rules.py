"""Apply candidate gate rules to backtest output. Usage: rules.py <repo> <bt.jsonl>"""

import json
import subprocess
import sys
from pathlib import Path

repo, bt = sys.argv[1:3]


def first(m, loc):
    f, rng = loc.rsplit(":", 1)
    a = int(rng.split("-")[0])
    t = subprocess.run(["git", "-C", repo, "show", f"{m}:{f}"], capture_output=True, text=True).stdout.splitlines()
    return t[a - 1].strip() if a <= len(t) else ""


R = [json.loads(row) for row in Path(bt).read_text().splitlines()]
R = [r for r in R if r["py_lines_added"] > 0]
rules = {
    "pyscn default (types 1,2,4; sim>=0.65)": lambda p, s: True,
    "types 1-2 only": lambda p, s: p["type"] in (1, 2),
    "types 1-2, not a class body": lambda p, s: p["type"] in (1, 2) and not s.startswith("class "),
    "types 1-2 or type-4 sim>=0.8, not a class body": lambda p, s: (
        (p["type"] in (1, 2) or p["sim"] >= 0.8) and not s.startswith("class ")
    ),
}
for name, rule in rules.items():
    fired, pairs = 0, 0
    for r in R:
        hit = [p for p in r["flagged"] if rule(p, first(r["merge"], p["new"]))]
        fired += bool(hit)
        pairs += len(hit)
    print(f"{name}: merges fired {fired}/{len(R)} ({100 * fired / len(R):.0f}%), pairs {pairs}")
