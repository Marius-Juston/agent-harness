"""devkit.board is split into parts (#625): a test patches the defining part, and the patch must reach every caller."""

import ast
import re
from pathlib import Path

import pytest

from devkit import board, board_brief, board_gh, board_issue, board_lint, board_related, board_state

ROOT = Path(__file__).resolve().parents[1]
PARTS = {
    m.__name__.rpartition(".")[2]: m
    for m in (board_gh, board_issue, board_related, board_state, board_lint, board_brief)
}
# a patch of the facade, plain or through another module's import of it (`mod.board`), on one line or several
BOARD_PATCH = re.compile(r"setattr\(\s*(?:\w+\.)*board,")
PART_PATCH = re.compile(r"setattr\(\s*(board_\w+),\s*\"(\w+)\"")


def test_parts_import_each_other_and_paths_as_modules():
    """`from devkit.board_gh import gh` inside a part binds a copy that a patch of board_gh never reaches."""
    found = []
    for path in sorted((ROOT / "devkit").glob("board_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and re.fullmatch(r"devkit\.(board\w*|paths)", node.module or ""):
                found.append(f"{path.name}:{node.lineno}: from {node.module} import ...")
    assert found == [], "import the module (`from devkit import board_gh`) and call `board_gh.gh(...)`"


def test_no_test_patches_the_board_facade():
    """A patch of `board.X` replaces only the facade's copy: the parts keep calling the original."""
    found = [
        f"{path.name}:{text.count(chr(10), 0, m.start()) + 1}"
        for path in sorted(ROOT.glob("tests/**/*.py"))
        if path.name != Path(__file__).name
        for text in [path.read_text()]
        for m in BOARD_PATCH.finditer(text)
    ]
    assert found == [], "patch the defining part instead (`uv run dev code show <name>` names it)"


def patched_part_functions() -> set[tuple[str, str]]:
    """(part, name) of every function another test file patches on the part that defines it."""
    found = set()
    for path in sorted(ROOT.glob("tests/**/*.py")):
        if path.name == Path(__file__).name:
            continue
        for part, name in PART_PATCH.findall(path.read_text()):
            value = vars(PARTS[part]).get(name) if part in PARTS else None
            if callable(value) and not isinstance(value, type) and value.__module__ == PARTS[part].__name__:
                found.add((part, name))
    return found


@pytest.mark.parametrize(("part", "name"), sorted(patched_part_functions()))
def test_a_function_tests_patch_is_late_bound_in_the_facade(monkeypatch, part, name):
    """Code outside the parts calls `board.X`: that call must reach a test's patch of the defining part."""
    monkeypatch.setattr(PARTS[part], name, lambda *a, **k: ("patched", a, k))
    assert getattr(board, name)(1, k=2) == ("patched", (1,), {"k": 2})


def test_a_patch_of_the_defining_part_reaches_the_other_parts(monkeypatch):
    """The review's probe: board_lint reads the board through board_state.fetch, so it sees the patch."""
    issue = board_issue.Issue(
        1, "x: y", ["type:build", "P2", "status:ready"], "## DONE WHEN\n- a\n## Owns\n- `x`", "OPEN", ""
    )
    monkeypatch.setattr(board_state, "fetch", lambda **k: [issue])
    monkeypatch.setattr(board_state, "resolved", lambda issues: set())
    monkeypatch.setattr(board_lint, "merged_closes", lambda: {})
    monkeypatch.setattr(board_state, "path_history", lambda: None)
    issues, _ = board_lint._findings()
    assert issues == [issue]
    assert board.fetch() == [issue]
