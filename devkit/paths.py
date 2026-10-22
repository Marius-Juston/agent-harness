"""Repository locations. Everything is resolved from git, so the same code works in the main checkout and in
every agent worktree (each worktree has its own top level; they share one git common dir)."""

import functools
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

# git settings that change output devkit parses (colour codes, a/ b/ prefixes, relative or quoted paths, an external
# diff, signature lines in `git log`), pinned to git's defaults for every git devkit starts (#374). An external diff
# (diff.external) cannot be unset through config: git() passes --no-ext-diff instead.
GIT_PINNED = {
    "color.ui": "never",
    "core.quotePath": "true",
    "diff.noprefix": "false",
    "diff.mnemonicPrefix": "false",
    "diff.relative": "false",
    "log.showSignature": "false",
}


def pin_git_output(env: Mapping[str, str]) -> dict[str, str]:
    """`env` plus GIT_PINNED as GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n> entries (git 2.31+, the precedence of
    `git -c`), after any the environment already has."""
    try:
        n = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        n = 0
    out = dict(env)
    for i, (key, value) in enumerate(GIT_PINNED.items(), start=n):
        out[f"GIT_CONFIG_KEY_{i}"], out[f"GIT_CONFIG_VALUE_{i}"] = key, value
    out["GIT_CONFIG_COUNT"] = str(n + len(GIT_PINNED))
    return out


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    if args[:1] in (("diff",), ("log",), ("show",)):
        args = (args[0], "--no-ext-diff", *args[1:])
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
