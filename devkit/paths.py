"""Repository locations. Everything is resolved from git, so the same code works in the main checkout and in
every agent worktree (each worktree has its own top level; they share one git common dir)."""

import functools
import os
import subprocess
from pathlib import Path


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


@functools.cache
def root() -> Path:
    """Top level of the checkout or worktree we run in."""
    return Path(git("rev-parse", "--show-toplevel"))


@functools.cache
def common_dir() -> Path:
    """The git directory shared by every worktree of this repository (locks, caches, records live under it)."""
    return Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))


def state_dir(*parts: str) -> Path:
    """Machine-local state shared by all worktrees: <git-common-dir>/devkit/<parts>. Never committed."""
    path = common_dir().joinpath("devkit", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_state_dir(*parts: str) -> Path:
    """Per-user state shared by every repository on this machine: $XDG_STATE_HOME/devkit/<parts>, else
    ~/.local/state/devkit/<parts>. For what an outside service counts per key or per IP, not per repository. A
    relative XDG_STATE_HOME is ignored, as the XDG spec says: it would split the state by working directory."""
    xdg = Path(os.environ.get("XDG_STATE_HOME", ""))
    path = (xdg if xdg.is_absolute() else Path.home() / ".local" / "state").joinpath("devkit", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def main_checkout() -> Path:
    return common_dir().parent


def default_branch() -> str:
    return "main"


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root()))
    except ValueError:
        return str(path)
