"""r2 (#111): app statements executed only by evidence of one milestone's requirements (statement basis as r2_coverage.py)."""

import collections
import sys
import tomllib
from pathlib import Path

import coverage

root = Path(sys.argv[1]).resolve()
app = sys.argv[2]
cov = coverage.Coverage(data_file=str(root / ".coverage"))
cov.load()
data = cov.get_data()
doc = tomllib.loads((root / "docs/requirements.toml").read_text())
ms = {m["id"]: m["name"] for m in doc["milestones"]}
ev = collections.defaultdict(set)
for r in doc["requirements"]:
    for e in r.get("evidence", []):
        ev[e.split("[")[0]].add(r["milestone"])
only, shared, total = collections.Counter(), 0, 0
for f in data.measured_files():
    rel = Path(f).relative_to(root).as_posix()
    if not rel.startswith(app + "/"):
        continue
    _, stmts, _, _, _ = cov.analysis2(f)
    by = data.contexts_by_lineno(f)
    for ln in stmts:
        m = set().union(*(ev.get(c.split("|")[0].split("[")[0], set()) for c in by.get(ln, []) if c))
        if not m:
            continue
        total += 1
        if len(m) == 1:
            only[next(iter(m))] += 1
        else:
            shared += 1
for k in sorted(ms):
    print(f"{k} {ms[k][:52]:52s} only-this-milestone stmts {only[k]:6d}")
print("statements backed by evidence", total, "shared by 2+ milestones", shared)
