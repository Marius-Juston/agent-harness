"""Check r1 claims: large churny files per repo, and repo-R lazy imports by source dir. Run from the worktree root."""

import importlib.util
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from codenames import path

spec = importlib.util.spec_from_file_location("rm", "scripts/repo_metrics.py")
rm = importlib.util.module_from_spec(spec)
sys.modules["rm"] = rm
spec.loader.exec_module(rm)
for name in ["repo-L", "repo-R", "H"]:
    repo = path(name)
    r = rm.BlobReader(repo)
    loc = {p: r.loc(s, p) for s, p in rm.tree(repo, "HEAD", []) if rm.kind_of(p) == "code"}
    h = rm.hotspots(repo, loc, [])
    rows = [x for x in [{"path": p, "loc": n} for p, n in loc.items()]]
    # recompute commit counts for all files via full list
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--no-merges", "-M", "--numstat", "--format=@"], capture_output=True, text=True
    ).stdout
    print(
        name,
        "big+frequent (>1000 LOC, >=30 commits):",
        [(t["path"], t["loc"], t["commits"]) for t in h["top"] if t["loc"] > 1000 and t["commits"] >= 30],
    )
# repo-R: lazy edges by source dir
pr = path("repo-R")
r = rm.BlobReader(pr)
py = {p: r.text(s) for s, p in rm.tree(pr, "HEAD", []) if p.endswith(".py")}
mods = {rm.module_name(p): p for p in py}
lazy = Counter()
for m, p in mods.items():
    info = rm.analyse_py(py[p], m, p.endswith("__init__.py"))
    for t, top in info.imports:
        if not top and rm.resolve(t, set(mods)) not in (None, m):
            lazy[rm.top(p)] += 1
print("repo-R function-level import statements by source dir:", dict(lazy))
