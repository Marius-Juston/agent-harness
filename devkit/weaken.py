"""Test-weakening check: a branch may not delete, loosen or swallow asserts, delete tests or cases, add skips, or edit
test config, conftest patches and golden files, unless its issue records approval.

Agents given a failing test often edit the test instead of the code: ImpossibleBench (docs/research/agent-harness.md,
section 4) measured it, and an explicit way out (`uv run dev board block`) cut it sharply. This check makes the
shortcut visible: every finding is a file:line in the diff against main. The owner approves a deliberate change with
a `weakening-ok: <reason>` line in the issue body or an issue comment. The kinds follow a sibling project's detector
(#112, round 2 finding F5); each is a line-level heuristic, so a multi-line construct can escape it.
"""

import ast
import json
import os
import re
from collections import Counter
from collections.abc import Callable
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
PARAM = re.compile(r"\bpytest\.param\s*\(")
PARAMETRIZE = re.compile(r"\bparametrize\s*\(")
SWALLOW = re.compile(
    r"^\s*except\s*:|^\s*except\b[^:]*\b(?:AssertionError|BaseException)\b"
    r"|^\s*(?:async\s+)?with\b.*\bsuppress\s*\([^)]*\b(?:AssertionError|BaseException)\b"
)
# A conftest line that changes what runs or what it sees: autouse fixtures, patches and collection or run hooks.
CONFTEST = re.compile(
    r"\bautouse\s*=\s*True|\bmonkeypatch\.|\bpatch(?:\.object)?\s*\(|\bsetattr\s*\(|\bcollect_ignore"
    r"|\bdef\s+pytest_(?:collection\w*|ignore_collect|deselected|runtest\w*|pycollect\w*|report\w*|configure)\b"
)
CONFIG_FILES = {"pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", ".coveragerc"}
# Pytest and coverage options that select, deselect or relax tests; edited anywhere in a config file.
CONFIG_KEY = re.compile(
    r"^\s*[\"']?(?:addopts|testpaths|norecursedirs|python_files|python_classes|python_functions|xfail_strict"
    r"|filterwarnings|required_plugins|fail_under|omit|exclude_lines|exclude_also)\b"
    r"|--deselect|--ignore|\s-k\s"
)
# The name a patch replaces: setattr(obj, "name"), patch.object(obj, "name"), or the last part of a dotted target.
PATCH = re.compile(
    r"\b(?:setattr|patch\.object)\s*\(\s*[\w.]+\s*,\s*[\"'](\w+)[\"']"
    r"|\b(?:setattr|patch)\s*\(\s*[\"'][\w.]*?(\w+)[\"']"
)
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


def is_config(path: str) -> bool:
    return PurePosixPath(path).name.removesuffix(".jinja") in CONFIG_FILES


def _code(text: str) -> str:
    """The line without a trailing comment, so a commented-out skip is not one (a `#` inside a string is rare)."""
    return text.split("#", 1)[0] if not text.lstrip().startswith("#") else ""


def _assert(text: str) -> ast.expr | None:
    """The tested expression of a one-line assert, or None for anything else (a multi-line assert does not parse)."""
    try:
        node = ast.parse(_code(text).strip()).body[0]
    except (SyntaxError, IndexError):
        return None
    return node.test if isinstance(node, ast.Assert) else None


def _strength(test: ast.expr) -> tuple[str, int]:
    """(subject, strength): an exact `==`/`is` check is 2, any other comparison 1, plain truthiness 0."""
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        return ast.dump(test.left), 2 if isinstance(test.ops[0], (ast.Eq, ast.Is)) else 1
    return ast.dump(test), 0


def _trivial(test: ast.expr) -> bool:
    """An assert that cannot fail: a truthy literal, a non-empty tuple, `x == x`, `... or True`, `not <falsy>`."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, (ast.Tuple, ast.List)):
        return bool(test.elts)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Constant):
        return not test.operand.value
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(_trivial(v) for v in test.values)
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.Eq, ast.Is, ast.LtE, ast.GtE))
        and ast.dump(test.left) == ast.dump(test.comparators[0])
    )


def _cases(text: str) -> tuple[str, int] | None:
    """(argnames, case count) of a one-line `parametrize("x", [...])`, or None when it spans lines."""
    try:
        call = ast.parse(_code(text).strip().lstrip("@"), mode="eval").body
    except SyntaxError:
        return None
    if not (isinstance(call, ast.Call) and len(call.args) >= 2 and isinstance(call.args[1], (ast.List, ast.Tuple))):
        return None
    return ast.dump(call.args[0]), len(call.args[1].elts)


def _removals(files: list[FileDiff]) -> list[str]:
    """Asserts, test functions and parametrize cases removed from a test file beyond those added back in that file.
    One moved verbatim to another file is not a removal."""
    out: list[str] = []
    for kind, pattern in (("assert", ASSERT), ("test", TEST_DEF), ("parametrize case", PARAM)):
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
    return out


def _test_file(f: FileDiff, read: Callable[[str], str]) -> list[str]:
    """Loosened, trivial and swallowed asserts, fewer parametrize cases, conftest edits and patches of the function
    an assert calls, among the added lines of one test file."""
    out: list[str] = []
    was = {}
    for ln in f.removed:
        if (test := _assert(ln.text)) is not None:
            subject, strength = _strength(test)
            was[subject] = max(was.get(subject, 0), strength)
    cases = {c[0]: c[1] for ln in f.removed if PARAMETRIZE.search(ln.text) and (c := _cases(ln.text))}
    asserted = [ln.text for ln in f.added if ASSERT.search(_code(ln.text))]
    asserted += [t for t in read(f.new).splitlines() if ASSERT.search(_code(t))]
    conftest = PurePosixPath(f.new).name == "conftest.py"
    for ln in f.added:
        code, kind = _code(ln.text), ""
        if (test := _assert(ln.text)) is not None:
            subject, strength = _strength(test)
            kind = "trivial assert" if _trivial(test) else "loosened assert" if strength < was.get(subject, -1) else ""
        elif SWALLOW.search(code):
            kind = "swallowed assert"
        elif PARAMETRIZE.search(code) and (c := _cases(ln.text)) and c[1] < cases.get(c[0], 0):
            kind = "fewer parametrize cases"
        elif conftest and CONFTEST.search(code):
            kind = "conftest edit"
        elif (m := PATCH.search(code)) and any(re.search(rf"\b{m[1] or m[2]}\s*\(", t) for t in asserted):
            kind = "patch of code under test"
        if kind:
            out.append(f"{ln.path}:{ln.number}: {kind}: {ln.text.strip()}")
    return out


def _config(f: FileDiff) -> list[str]:
    """Changed test-selection or coverage options: the added lines, or the removed ones when none was added."""
    lines = [ln for ln in f.added if CONFIG_KEY.search(_code(ln.text))]
    lines = lines or [ln for ln in f.removed if CONFIG_KEY.search(_code(ln.text))]
    return [f"{ln.path}:{ln.number}: test config edit: {ln.text.strip()}" for ln in lines]


def findings(diff: str, read: Callable[[str], str] = lambda path: "") -> list[str]:
    """file:line of each weakening in a diff: removed asserts, tests and cases (see `_removals`), weakened lines in a
    test file, skips added anywhere in Python, edited test config, and the first removed line of an edited non-Python
    file under a test directory (a golden file). `read(path)` gives a test file's new text, to see its asserts."""
    files = [f for f in parse(diff) if (f.old or f.new).endswith(".py")]
    out = _removals(files)
    for f in files:
        if is_test(f.new or f.old):
            out += _test_file(f, read)
        out += [
            f"{ln.path}:{ln.number}: added skip/xfail: {ln.text.strip()}"
            for ln in f.added
            if SKIP.search(_code(ln.text))
        ]
    for f in parse(diff):
        path = f.old or f.new
        if is_config(path):
            out += _config(f)
        elif not path.endswith(".py") and is_test(path) and f.removed:
            ln = f.removed[0]
            out.append(f"{ln.path}:{ln.number}: golden file edit: {ln.text.strip()}")
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


def _read(path: str) -> str:
    try:
        return (root() / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def check_weakening(base: str | None) -> Ran:
    ran = Ran("test weakening (asserts, tests, skips, test config, goldens)", allow_empty=True)
    if not base:
        ran.warnings.append("no merge base with main: test changes not compared")
        return ran
    paths = ["*.py", "*tests/*", "*test/*", *(f"*{name}{ext}" for name in CONFIG_FILES for ext in ("", ".jinja"))]
    diff = git("diff", "-M", "-U0", "--no-color", "--no-ext-diff", base, "--", *paths, cwd=root())
    ran.checked = len(parse(diff))
    found = findings(diff, _read)
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
