"""Test-weakening check: a branch may not delete asserts or tests, or add skips, unless its issue records approval.

Agents given a failing test often edit the test instead of the code: ImpossibleBench (docs/research/agent-harness.md,
section 4) measured it, and an explicit way out (`uv run dev board block`) cut it sharply. This check makes the
shortcut visible: every finding is a file:line in the diff against main. The owner approves a deliberate change with
a `weakening-ok: <reason>` line in the issue body or an issue comment.
"""

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from devkit import board
from devkit.checks import repo_owner
from devkit.paths import git, root
from devkit.ran import Ran

MARKER = re.compile(r"^\s*weakening-ok:\s*\S", re.M)
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
ASSERT = re.compile(r"^\s*assert\b|\bself\.assert\w*\s*\(|\bpytest\.raises\s*\(")
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(")
SKIP = re.compile(
    r"\bpytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail)\s*\("
    r"|\bunittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\.skipTest\s*\("
)


@dataclass(frozen=True)
class Line:
    path: str
    number: int
    text: str


@dataclass
class FileDiff:
    old: str
    new: str
    removed: list[Line]
    added: list[Line]


def parse(diff: str) -> list[FileDiff]:
    """Removed lines (old path, old line number) and added lines (new path, new number) of a `git diff -U0`."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    old_no = new_no = 0
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            cur = FileDiff("", "", [], [])
            files.append(cur)
        elif cur is None:
            continue
        elif raw.startswith("--- "):
            cur.old = raw[4:].removeprefix("a/") if raw[4:] != "/dev/null" else ""
        elif raw.startswith("+++ "):
            cur.new = raw[4:].removeprefix("b/") if raw[4:] != "/dev/null" else ""
        elif m := HUNK.match(raw):
            old_no, new_no = int(m.group(1)), int(m.group(2))
        elif raw.startswith("-"):
            cur.removed.append(Line(cur.old, old_no, raw[1:]))
            old_no += 1
        elif raw.startswith("+"):
            cur.added.append(Line(cur.new, new_no, raw[1:]))
            new_no += 1
    return files


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or p.name == "conftest.py"
        or bool({"tests", "test"} & set(p.parts[:-1]))
    )


def _code(text: str) -> str:
    """The line without a trailing comment, so a commented-out skip is not one (a `#` inside a string is rare)."""
    return text.split("#", 1)[0] if not text.lstrip().startswith("#") else ""


def findings(diff: str) -> list[str]:
    """file:line of each assert or test function a diff removes from a test file beyond those it adds back in that
    file, and of each skip/xfail it adds anywhere. An assert moved verbatim to another file is not a removal."""
    files = [f for f in parse(diff) if (f.old or f.new).endswith(".py")]
    out: list[str] = []
    for kind, pattern in (("assert", ASSERT), ("test", TEST_DEF)):
        gone = {id(f): [ln for ln in f.removed if is_test(ln.path) and pattern.search(_code(ln.text))] for f in files}
        came = {id(f): [ln for ln in f.added if pattern.search(_code(ln.text))] for f in files}
        moved = Counter(ln.text.strip() for lines in came.values() for ln in lines)
        for f in files:
            kept = []
            for ln in gone[id(f)]:
                if moved[ln.text.strip()] > 0:
                    moved[ln.text.strip()] -= 1
                else:
                    kept.append(ln)
            if len(kept) > len(came[id(f)]):
                out += [f"{ln.path}:{ln.number}: removed {kind}: {ln.text.strip()}" for ln in kept]
    for f in files:
        out += [
            f"{ln.path}:{ln.number}: added skip/xfail: {ln.text.strip()}"
            for ln in f.added
            if SKIP.search(_code(ln.text))
        ]
    return out


def approved(issue: dict, owner: str | None) -> bool:
    """True when the repository owner wrote a `weakening-ok: <reason>` line in the issue body or a comment."""
    if not owner:
        return False
    posts = [issue, *issue.get("comments", [])]
    return any(
        (p.get("author") or {}).get("login", "").lower() == owner.lower() and MARKER.search(p.get("body") or "")
        for p in posts
    )


def issue_number() -> int | None:
    """The issue of this branch, from its `<n>-slug` name (a PR's head branch in CI)."""
    branch = os.environ.get("GITHUB_HEAD_REF") or git("branch", "--show-current", cwd=root(), check=False)
    m = re.match(r"(\d+)-", branch.strip())
    return int(m.group(1)) if m else None


def check_weakening(base: str | None) -> Ran:
    ran = Ran("test weakening (asserts, tests, skips)", allow_empty=True)
    if not base:
        ran.warnings.append("no merge base with main: test changes not compared")
        return ran
    diff = git("diff", "-M", "-U0", "--no-color", "--no-ext-diff", base, "--", "*.py", cwd=root())
    ran.checked = len(parse(diff))
    found = findings(diff)
    if not found:
        return ran
    number = issue_number()
    reason = "no `<n>-slug` branch names the issue"
    if number is not None:
        try:
            issue = json.loads(
                board.gh("issue", "view", str(number), "-R", board.repo(), "--json", "body,author,comments")
            )
            if approved(issue, repo_owner()):
                ran.warnings += [f"{f} (approved in #{number})" for f in found]
                return ran
            reason = f"#{number} records no owner `weakening-ok:` line"
        except (board.BoardError, json.JSONDecodeError) as e:
            reason = f"#{number} could not be read ({e})"
    ran.problems += found
    ran.problems.append(
        f'{reason}. Fix the code, not the test; if the test is wrong, `uv run dev board block <n> "<why>"` and ask'
        " the owner to approve the change in the issue"
    )
    return ran
