"""r2 (#111): for the named requirements, statements unique to each by app area (same inputs as r2_coverage.py)."""

import collections
import sys
import tomllib
from pathlib import Path

import coverage

root = Path(sys.argv[1]).resolve()
app = sys.argv[2]
want = sys.argv[3].split(",")
cov = coverage.Coverage(data_file=str(root / ".coverage"))
cov.load()
data = cov.get_data()
reqs = tomllib.loads((root / "docs/requirements.toml").read_text())["requirements"]
ev = collections.defaultdict(set)
for r in reqs:
    for e in r.get("evidence", []):
        ev[e.split("[")[0]].add(r["id"])
out = collections.defaultdict(collections.Counter)
for f in data.measured_files():
    rel = Path(f).relative_to(root).as_posix()
    if not rel.startswith(app + "/"):
        continue
    parts = rel.split("/")
    area = "/".join(parts[1:3]) if parts[1] in ("presentation", "domain") and len(parts) > 3 else parts[1]
    for _ln, ctxs in data.contexts_by_lineno(f).items():
        rs = set().union(*(ev.get(c.split("|")[0].split("[")[0], set()) for c in ctxs if c))
        if len(rs) == 1 and (r := next(iter(rs))) in want:
            out[r][area] += 1
for r in want:
    print(r, sum(out[r].values()), out[r].most_common(8))
    print(
        "  summary:",
        next(x["summary"] for x in reqs if x["id"] == r),
        "| evidence ids:",
        len(next(x["evidence"] for x in reqs if x["id"] == r)),
    )
