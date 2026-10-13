"""Shared helpers for the round-2 replays (#220): first-parent history of both repositories and cached trees.

Read-only: trees are extracted with `git archive` into .scratch/r2/trees/, so no worktree, branch or ref changes.
Data dumps (issues, PRs) are read from .scratch/r2/data/ (made by dump_issues.py / dump_prs.py).
"""

import io
import os
import subprocess
import tarfile
from pathlib import Path

AH = Path(__file__).resolve().parents[5]  # this agent-harness checkout
# Project B is a private downstream project; its checkout and GitHub name come from the environment.
PB = Path(os.environ.get("PROJECT_B", "../project-b")).expanduser()
PB_NAME = os.environ.get("PROJECT_B_NAME", "project-b")
PB_SLUG = os.environ.get("PROJECT_B_REPO", "owner/" + PB_NAME)  # owner/name on GitHub
AH_SLUG = "Marius-Juston/agent-harness"
REPOS = {"AH": (AH, "main"), "PB": (PB, "origin/main")}
SCR = AH / ".scratch" / "r2"
DATA = SCR / "data"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def history(name: str) -> list[tuple[str, int, str]]:
    """(sha, unix time, subject) of each first-parent commit on main, oldest first."""
    repo, ref = REPOS[name]
    out = git(repo, "log", "--first-parent", "--reverse", "--format=%H%x09%ct%x09%s", ref)
    return [(s, int(t), subj) for s, t, subj in (ln.split("\t", 2) for ln in out.splitlines())]


def tree(name: str, sha: str) -> Path:
    """Directory holding the commit's tree (extracted once)."""
    repo = REPOS[name][0] if name in REPOS else AH
    d = SCR / "trees" / name / sha[:12]
    if not d.exists():
        raw = subprocess.run(["git", "-C", str(repo), "archive", sha], capture_output=True, check=True).stdout
        tmp = d.with_suffix(".tmp")
        tarfile.open(fileobj=io.BytesIO(raw)).extractall(tmp, filter="data")  # noqa: SIM115 - research script, kept as run
        tmp.rename(d)
    return d


def files(name: str, sha: str) -> list[str]:
    repo = REPOS[name][0]
    return git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
