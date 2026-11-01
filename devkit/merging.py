"""Merge machinery: merging main into a PR head as integrate gates it, the conflict checks `dev pr` and integrate run
without a worktree, regenerating generated files in the merged tree, and retargeting stacked PRs. Integrate and pr are
the callers; devkit.workflow re-exports these names (devkit/train.py reads them through it).
"""

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from devkit import board, config
from devkit.paths import git


def main_conflicts() -> list[str]:
    """The paths HEAD conflicts in with origin/main, fetched now, as integrate would merge them (#349: 7 of 19
    conflicting hand-backs already conflicted at `dev pr`). Fails closed: no fetch, no PR."""
    try:
        git("fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
        return merge_conflicts("HEAD", git("rev-parse", "refs/remotes/origin/main"))
    except RuntimeError as exc:
        raise board.BoardError(f"cannot check this branch for conflicts with origin/main: {exc}") from exc


def _add_worktree(path: Path, commit: str, cwd: Path | None = None) -> None:
    """A fresh detached worktree at commit. Prunes first: a registered worktree whose folder is gone blocks `add`."""
    if path.exists():
        git("worktree", "remove", "--force", str(path), cwd=cwd, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", cwd=cwd)
    git("worktree", "add", "--detach", str(path), commit, cwd=cwd)


def _remote_shas(pr_number: int) -> tuple[str, str]:
    """(PR head, main) as GitHub has them now."""
    head = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid"))
    main = git("ls-remote", "origin", "refs/heads/main").split()[0]
    return head["headRefOid"], main


def retarget_stacked(branch: str, tip: str, dry_run: bool = False) -> list[int]:
    """Stacked PRs: point every open PR based on `branch` (about to be squash-merged and deleted, which would close
    them) at main, and say how each stacked PR drops its parent's commits, which the squash commit already carries on
    main under another sha. Returns the retargeted PR numbers. The route it prints carries the stacked PR's own commits
    to a new branch from main: auto mode refuses a rebase plus a force push as destructive (#305)."""
    found = board.gh(
        "pr", "list", "-R", board.repo(), "--base", branch, "--state", "open", "--json", "number,headRefName"
    )
    stacked = json.loads(found or "[]")
    for pr in stacked:
        n, name = pr["number"], pr["headRefName"]
        if dry_run:
            print(f"would retarget stacked PR #{n} ({name}) from {branch} to main")
            continue
        board.gh("pr", "edit", str(n), "-R", board.repo(), "--base", "main")
        print(f"retargeted stacked PR #{n} ({name}) from {branch} to main. {stacked_route(n, name, tip)}")
    return [c["number"] for c in stacked]


def stacked_route(pr_number: int, name: str, tip: str) -> str:
    """How a stacked PR leaves its squash-merged parent's commits behind without a rebase or a force push: its own
    commits (those after the parent's `tip`) cherry-picked onto a new branch from main, a new PR, the old one closed.
    The commits are listed by sha when GitHub can compare them, else by the command that lists them."""
    try:
        shas = board.gh("api", f"repos/{board.repo()}/compare/{tip}...{name}", "--jq", ".commits[].sha").split()
    except board.BoardError:
        shas = []
    own = (
        f"`git cherry-pick {' '.join(s[:12] for s in shas)}`"
        if shas
        else f"`git cherry-pick` each commit `git log --reverse --format=%h {tip[:12]}..origin/{name}` lists"
    )
    issue = m[1] if (m := re.match(r"(\d+)-", name)) else "<issue>"
    return (
        f"Before its integrate, carry its own commits to a new branch (no history rewrite: auto mode refuses that): "
        f"`git fetch origin && git switch -c {name}-v2 origin/main`, then {own}, gate it (`uv run dev check`), open "
        f"the new PR with `uv run dev pr {issue}`, and close the old one: `gh pr close {pr_number}`"
    )


# Generated files never conflict: a merge keeps our side (`.gitattributes` names this driver for them, `dev setup`
# and integrate define it) and `dev docs` regenerates them in the merged tree before the gate.
GENERATED_DRIVER = "devkit-generated"
GENERATED_MERGE = (
    (f"merge.{GENERATED_DRIVER}.name", "generated file: keep one side, `uv run dev docs` regenerates it"),
    (f"merge.{GENERATED_DRIVER}.driver", "true"),
)
REGENERATED = "Regenerate generated files after merging main (dev integrate)"


ATTR_SOURCE_GIT = (2, 40)  # `git --attr-source`


def git_version() -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)", git("--version"))
    return tuple(int(x) for x in m.groups()) if m else (0, 0)


def _merge_rules(main: str) -> list[str]:
    """The git options every merge of main into a PR runs with: main's `.gitattributes` (`--attr-source`; an old
    branch must not bring back a union rule that interleaves rewrites) and the generated-file driver. Raises
    RuntimeError when git is too old for `--attr-source`."""
    if (have := git_version()) < ATTR_SOURCE_GIT:
        need, got = ".".join(map(str, ATTR_SOURCE_GIT)), ".".join(map(str, have))
        raise RuntimeError(f"merging main needs git >= {need} (--attr-source); this is git {got}")
    return [f"--attr-source={main}", *(arg for key, value in GENERATED_MERGE for arg in ("-c", f"{key}={value}"))]


def merge_conflicts(head: str, main: str, cwd: Path | None = None) -> list[str]:
    """The paths that conflict when `main` is merged into `head` as `merge_main` merges it, without a worktree or a
    commit: `git merge-tree --write-tree` (3.7 ms a PR, docs/research/issue-pr-automation.md §3.2). [] when it
    merges cleanly. Raises RuntimeError when git is too old or merge-tree fails (unrelated histories, a bad sha)."""
    rules = _merge_rules(main)
    run = subprocess.run(
        ["git", *rules, "merge-tree", "--write-tree", "--name-only", "--no-messages", head, main],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = run.stdout.splitlines()
    # exit 1 is a conflict, but also "not something we can merge": only a tree id on the first line says it merged
    if run.returncode not in (0, 1) or not lines or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", lines[0]):
        raise RuntimeError(f"git merge-tree {head[:10]} {main[:10]} failed: {run.stderr.strip()[-300:]}")
    return sorted({p for p in lines[1:] if p}) if run.returncode == 1 else []


def conflicting(
    main: str, heads: dict[int, str], cwd: Path | None = None
) -> tuple[dict[int, list[str]], dict[int, str]]:
    """(PR number -> the paths its head (`heads`) conflicts in with `main`, for the PRs that conflict; PR number ->
    why git could not check it). One PR git cannot merge-tree does not stop the check of the others."""
    found, failed = {}, {}
    for n, head in heads.items():
        try:
            if paths := merge_conflicts(head, main, cwd):
                found[n] = paths
        except RuntimeError as exc:
            failed[n] = str(exc)
    return found, failed


def merge_main(tree: Path, main: str) -> list[str]:
    """Merge main into the PR head checked out at `tree`, as GitHub's "Update branch" would, with `_merge_rules`.
    Returns the paths that really conflict (the merge is then aborted), or [] when it merged or was current. Raises
    RuntimeError when git is too old or the merge failed without a conflict."""
    merge = subprocess.run(
        ["git", *_merge_rules(main), "merge", "--no-ff", "--no-edit", main],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge.returncode == 0:
        return []
    paths = git("diff", "--name-only", "--diff-filter=U", cwd=tree, check=False).splitlines()
    git("merge", "--abort", cwd=tree, check=False)
    if not paths:
        raise RuntimeError(
            f"git merge of main failed without a conflict: {(merge.stdout + merge.stderr).strip()[-300:]}"
        )
    return paths


def deleted_owns(tree: Path, main: str, info: dict) -> dict[int, list[str]]:
    """Open issue -> its Owns entries that `main` has and the merged tree at `tree` empties, other than the PR's own
    issues (docs/research/decision-records-and-drift.md check 2). The board is read only when the merge deletes."""
    before = set(git("ls-tree", "-r", "--name-only", main).splitlines())
    after = set(git("ls-tree", "-r", "--name-only", "HEAD", cwd=tree).splitlines())
    if not (deleted := before - after):
        return {}
    return board.deleting_owns(board.fetch(), deleted, after, info["body"] or "", info.get("headRefName", ""))


def regenerate(tree: Path) -> bool:
    """`dev docs` in the merged tree, then each `[tool.devkit] regenerate` command in order (#386: a line hashing
    other files, which `dev docs` does not know), committing what they changed. True when it committed."""
    for argv in [["uv", "run", "--quiet", "dev", "docs"], *config.get().regenerate]:
        ran = subprocess.run(argv, cwd=tree, capture_output=True, text=True, check=False)
        if ran.returncode != 0:
            raise RuntimeError(
                f"`{shlex.join(argv)}` failed in the merged tree: {(ran.stdout + ran.stderr).strip()[-500:]}"
            )
    if not git("status", "--porcelain", cwd=tree):
        return False
    git("add", "-A", cwd=tree)
    git("commit", "--no-verify", "-q", "-m", REGENERATED, cwd=tree)  # the gate runs on this tree next
    return True
