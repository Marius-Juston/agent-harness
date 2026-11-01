"""Branches: worktree listing and local branch pruning (`dev worktree prune`, and integrate's branch cleanup).

A squash merge never makes a branch 'merged' for `git branch -d`; `drop_branch` deletes a local branch only when its
tip is contained in a merged PR head this clone has, and no worktree holds it.
"""

import contextlib
import json
import re
import subprocess
from pathlib import Path

from devkit import board
from devkit.paths import default_branch, git


def worktrees() -> list[dict]:
    rows = []
    for block in git("worktree", "list", "--porcelain").split("\n\n"):
        path = re.search(r"^worktree (.+)$", block, re.M)
        branch = re.search(r"^branch refs/heads/(.+)$", block, re.M)
        if not path:
            continue
        p = Path(path.group(1))
        b = branch.group(1) if branch else "(detached)"
        ahead = git("rev-list", "--count", f"main..{b}", check=False) if branch and b != "main" else "0"
        dirty = len(git("-C", str(p), "status", "--porcelain", check=False).splitlines()) if p.exists() else 0
        last = git("-C", str(p), "log", "-1", "--format=%cr", check=False) if p.exists() else "?"
        rows.append({"path": str(p), "branch": b, "ahead": int(ahead or 0), "dirty": dirty, "last": last})
    return rows


SHA = re.compile(r"[0-9a-f]{40}")


# Where a worktree stopped mid-rebase or mid-bisect keeps the branch it will return to: `worktree list` shows such a
# worktree as detached, yet `rebase --continue` / `bisect reset` fail once that branch is gone.
IN_USE = ("rebase-merge/head-name", "rebase-apply/head-name", "BISECT_START")


def checked_out() -> dict[str, str]:
    """Branch -> path of the worktree (the main checkout included) that has it checked out, or is rebasing or
    bisecting it (`IN_USE`)."""
    rows = {}
    for block in git("worktree", "list", "--porcelain").split("\n\n"):
        path, branch = re.search(r"^worktree (.+)$", block, re.M), re.search(r"^branch refs/heads/(.+)$", block, re.M)
        if not path:
            continue
        if branch:
            rows[branch.group(1)] = path.group(1)
        gitdir = git("-C", path.group(1), "rev-parse", "--absolute-git-dir", check=False)
        for name in IN_USE if gitdir else ():
            with contextlib.suppress(OSError):
                held = (Path(gitdir) / name).read_text().strip().removeprefix("refs/heads/")
                if held:
                    rows.setdefault(held, path.group(1))
    return rows


def open_prs_on(branch: str) -> list[int] | None:
    """Open same-repository PRs whose head is `branch`; None when GitHub cannot say (callers keep the branch)."""
    try:
        found = board.gh(
            "pr", "list", "-R", board.repo(), "--head", branch, "--state", "open", "--json", "number,isCrossRepository"
        )
        return [p["number"] for p in json.loads(found or "[]") if not p.get("isCrossRepository")]
    except (board.BoardError, OSError, ValueError):
        return None


def drop_branch(branch: str, heads: list[str], dry_run: bool = False) -> str:
    """Delete the local `branch` when its tip is contained in `heads` (a merged PR's head, or an ancestor of it) and
    no worktree has it checked out; one line saying what happened and why. A squash merge never makes a branch
    'merged' for `git branch -d`, so agents would need `-D` (which guard_bash refuses) without this (#340). The ref is
    deleted only if it still points at the tip checked (`update-ref -d <ref> <old>`), so a concurrent push keeps it."""
    tip = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    if not tip:
        return f"no local branch {branch}"
    if path := checked_out().get(branch):
        return f"kept branch {branch}: checked out in {path}"
    if not (heads := [h for h in heads if SHA.fullmatch(h)]):
        return f"kept branch {branch}: no merged head in this clone to compare it with"
    # commits of the tip that no head reaches; a head this clone lacks is a git error: "" (fail closed)
    ahead = git("rev-list", "--count", tip, *(f"^{h}" for h in heads), check=False)
    if not ahead:
        return f"kept branch {branch}: this clone lacks the merged head, so nothing proves {tip[:10]} is in it"
    if ahead != "0":
        return f"kept branch {branch}: its tip {tip[:10]} has {ahead} commit(s) the merged head does not"
    if dry_run:
        return f"would delete branch {branch} (at {tip[:10]})"
    try:
        git("update-ref", "-d", f"refs/heads/{branch}", tip)
    except RuntimeError as exc:
        return f"kept branch {branch}: {exc}"
    git("config", "--remove-section", f"branch.{branch}", check=False)
    return f"deleted branch {branch} (was {tip[:10]})"


PRUNE_LIMIT = 1000  # PRs read per prune: plenty for one repository's local branches


def prune_branches(dry_run: bool = False) -> list[str]:
    """`dev worktree prune`: local branches whose PR is merged, or closed (superseded) with no open PR on the branch.
    Each is deleted when `drop_branch` finds its tip contained in the merged PR heads this clone has (its own, or for
    a superseded one another's); otherwise kept with the reason. One line per such branch."""
    fields, limit = "number,state,headRefName,headRefOid,isCrossRepository", str(PRUNE_LIMIT)
    prs = json.loads(board.gh("pr", "list", "-R", board.repo(), "--state", "all", "--limit", limit, "--json", fields))
    if len(prs) >= PRUNE_LIMIT:  # an older open PR on a local branch could be past the limit: delete nothing
        raise board.BoardError(
            f"worktree prune read {PRUNE_LIMIT} PRs (PRUNE_LIMIT), so an older open PR may be missing; nothing deleted"
        )
    prs = [p for p in prs if not p.get("isCrossRepository")]
    merged = [p["headRefOid"] for p in prs if p["state"] == "MERGED" and SHA.fullmatch(p["headRefOid"] or "")]
    check = subprocess.run(
        ["git", "cat-file", "--batch-check"], input="\n".join(merged), capture_output=True, text=True, check=False
    )
    heads = [line.split()[0] for line in check.stdout.splitlines() if not line.endswith(" missing")]
    local = git("for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads/").splitlines()
    lines = []
    for branch in local:
        if branch == default_branch():
            continue
        own = [p for p in prs if p["headRefName"] == branch]
        if not own or any(p["state"] == "OPEN" for p in own):
            continue
        which = ", ".join(f"#{p['number']} {p['state'].lower()}" for p in own)
        lines.append(f"{drop_branch(branch, heads, dry_run)} (PR {which})")
    return lines
