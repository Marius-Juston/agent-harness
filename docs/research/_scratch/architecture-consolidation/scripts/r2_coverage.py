"""r2 (#111): feature-to-code map of repo-L from per-test coverage (software reconnaissance over requirement evidence).

Usage (repo-L's interpreter, which has coverage 7.x): python -I r2_coverage.py <archive copy> <app dir name>
Needs <archive copy>/.coverage written by `pytest --cov=<app> --cov-context=test`. Reads docs/requirements.toml
(requirement -> pytest node ids). Prints statement counts per area: not executed by any test; executed only by tests
that are no requirement's evidence; executed by evidence tests; and lines unique to one requirement."""

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
reqs = tomllib.loads((root / "docs/requirements.toml").read_text())["requirements"]
ev = collections.defaultdict(set)  # node id -> requirement ids
for r in reqs:
    for e in r.get("evidence", []):
        ev[e.split("[")[0]].add(r["id"])


def node(ctx):  # pytest-cov context: "path::test[param]|run"
    return ctx.split("|")[0].split("[")[0]


area_stats = collections.defaultdict(collections.Counter)
per_req = collections.Counter()
uniq = collections.Counter()
file_rows = []
ctx_seen = set()
for f in sorted(data.measured_files()):
    rel = Path(f).relative_to(root).as_posix()
    if not rel.startswith(app + "/"):
        continue
    parts = rel.split("/")
    area = parts[1] if len(parts) > 2 else "(top)"
    if area == "presentation" and "scenes" in parts:
        area = "presentation/dice scenes"
    _, stmts, _, missing, _ = cov.analysis2(f)
    by_line = data.contexts_by_lineno(f)
    missing_set = set(missing)
    c = area_stats[area]
    fc = collections.Counter()
    for ln in stmts:
        ctxs = {node(x) for x in by_line.get(ln, []) if x}
        ctx_seen |= ctxs
        rs = set().union(*(ev.get(x, set()) for x in ctxs)) if ctxs else set()
        k = "none" if ln in missing_set else ("import only" if not ctxs else ("evidence" if rs else "test only"))
        c[k] += 1
        c["stmts"] += 1
        fc[k] += 1
        for r in rs:
            per_req[r] += 1
        if len(rs) == 1:
            uniq[next(iter(rs))] += 1
    file_rows.append((fc["none"], len(stmts), rel))
tot = collections.Counter()
for a, c in sorted(area_stats.items(), key=lambda kv: -kv[1]["stmts"]):
    tot.update(c)
    print(
        f"{a:28s} stmts {c['stmts']:6d}  none {c['none']:6d} ({c['none'] / c['stmts']:.0%})  import-only {c['import only']:6d}  test-only {c['test only']:6d}  evidence {c['evidence']:6d}"
    )
print(
    f"{'TOTAL':28s} stmts {tot['stmts']:6d}  none {tot['none']:6d} ({tot['none'] / tot['stmts']:.1%})  import-only {tot['import only']:6d} ({tot['import only'] / tot['stmts']:.1%})  test-only {tot['test only']:6d} ({tot['test only'] / tot['stmts']:.1%})  evidence {tot['evidence']:6d}"
)
print("contexts seen", len(ctx_seen), "evidence ids", len(ev), "evidence ids that ran", len(set(ev) & ctx_seen))
print("files with most unexecuted statements:")
[print(" ", r) for r in sorted(file_rows, reverse=True)[:15]]
print("requirements by statements unique to them:", uniq.most_common(12))
print("requirements with 0 unique statements:", sum(1 for r in reqs if uniq[r["id"]] == 0), "of", len(reqs))
