"""Cross-check repo_metrics LOC against grep (run from the worktree root; needs `git archive` copies in .scratch/arch/<dir>)."""

import importlib.util
import subprocess
import sys
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
    files = [(s, p) for s, p in rm.tree(repo, "HEAD", []) if p.endswith(".py")]
    mine = sum(r.loc(s, p) for s, p in files)
    paths = [p for _, p in files]
    out = subprocess.run(
        ["grep", "-cvE", r"^\s*(#[^!]|#$|$)", *paths], cwd=f".scratch/arch/{repo.name}", capture_output=True, text=True
    ).stdout
    theirs = sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines())
    print(name, len(files), "py files; repo_metrics", mine, "grep", theirs)
