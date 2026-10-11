"""Contracts of the dev harness: empty checks fail, parsing is robust, commands named in docs exist."""

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, checks, citecheck, code, frontmatter
from devkit.cli import command_map
from devkit.ran import Ran


def test_a_check_that_checked_nothing_fails():
    assert not Ran("x").ok
    assert Ran("x", allow_empty=True).ok
    assert Ran("x", checked=3).ok
    assert not Ran("x", checked=3, problems=["p"]).ok
    assert "checked nothing" in Ran("x").line()


def test_frontmatter_split():
    meta, body = frontmatter.split("---\nname: a\nx: [1, 2]\n---\nbody\n")
    assert meta == {"name": "a", "x": [1, 2]} and body == "body\n"
    assert frontmatter.split("no frontmatter")[0] is None
    with pytest.raises(ValueError):
        frontmatter.split("---\nname: a\n")
    with pytest.raises(ValueError):
        frontmatter.split("---\n: : :\n---\n")


@given(st.text())
def test_frontmatter_never_crashes_unexpectedly(text):
    with contextlib.suppress(ValueError):
        frontmatter.split(text)


def test_sections_and_blockers():
    body = board.body_from("ctx", ["a test passes"], ["devkit/"], [3, 4], ["docs/x.md"], 1)
    parts = board.sections(body)
    assert parts["Context"] == "ctx" and "- [ ] a test passes" in parts["DONE WHEN"]
    assert board.issue_numbers(parts["Blocked by"]) == [3, 4]
    assert parts["Parent"] == "#1"


def _issue(comments):
    return board.Issue(1, "t", ["status:in-progress"], "", "OPEN", "2026-10-11T00:00:00Z", comments)


def test_claim_earliest_live_wins_and_release_clears():
    a = {"body": "<!-- devkit:claim host=h1 agent=a branch=1-x -->", "createdAt": "2026-10-11T01:00:00Z"}
    b = {"body": "<!-- devkit:claim host=h2 agent=b branch=1-y -->", "createdAt": "2026-10-11T01:00:01Z"}
    rel = {"body": "<!-- devkit:release -->\nreleased", "createdAt": "2026-10-11T02:00:00Z"}
    assert _issue([a, b]).claim()[:2] == ("h1", "a")
    assert _issue([a, rel]).claim() is None
    assert _issue([a, rel, b]).claim()[:2] == ("h2", "b")


def test_startable_orders_by_priority_then_unblocks():
    def mk(n, labels, body=""):
        return board.Issue(n, f"t{n}", labels, body, "OPEN", "2026-10-11T00:00:00Z")

    issues = [
        mk(1, ["status:ready", "P2"]),
        mk(2, ["status:ready", "P1"]),
        mk(3, ["status:ready", "P2"]),
        mk(4, ["status:blocked", "P0"], "## Blocked by\n#3"),
        mk(5, ["status:ready", "P0"], "## Blocked by\n#1"),  # blocker still open: not startable
    ]
    # P1 first; #1 and #3 each unblock one item, so the tie falls to the lower number; #5 waits on open #1
    assert [i.number for i in board.startable(issues)] == [2, 1, 3]


def test_title_similarity():
    t = "SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks"
    assert citecheck.similar("SkillsBench", t) == 1.0
    assert citecheck.similar("A Completely Different Paper About Protein Folding", t) < citecheck.MATCH_FLOOR
    assert not citecheck.looks_like_title("arXiv 2602.12670")
    assert citecheck.looks_like_title("How many instructions can LLMs follow")


def test_outline_python_and_markdown():
    lines = code.outline_python(
        '"""Doc."""\nX = 1\nclass A(B):\n    """Cls."""\n    def m(self, y: int) -> str: ...\nasync def f(): ...\n'
    )
    text = "\n".join(lines)
    assert (
        "class A(B)" in text and "def m(self, y: int) -> str" in text and "async def f()" in text and "X = ..." in text
    )
    md = code.outline_markdown("# T\n```\n# not a heading\n```\n## S\n")
    assert len(md) == 2


def test_documented_core_commands_exist():
    cmds = command_map()
    for c in ("orient", "check", "test", "docs", "code", "board", "pr", "integrate", "cite-check", "setup", "doctor"):
        assert c in cmds
    assert {"claim", "brief", "next", "new"} <= cmds["board"]


@pytest.mark.parametrize(
    ("meta", "lines", "bad"),
    [
        ({"name": "lead", "description": "x"}, 10, None),
        ({"name": "Lead", "description": "x"}, 10, "spec"),
        ({"name": "-lead", "description": "x"}, 10, "spec"),
        ({"name": "le--ad", "description": "x"}, 10, "spec"),
        ({"name": "lead", "description": ""}, 10, "1-1024"),
        ({"name": "lead", "description": "x" * 1025}, 10, "1-1024"),
        ({"name": "lead", "description": "x", "compatibility": "y" * 501}, 10, "compatibility"),
        ({"name": "lead", "description": "x"}, 600, "500"),
        ({"name": "other", "description": "x"}, 10, "match its directory"),
    ],
)
def test_skill_spec(meta, lines, bad):
    problems = checks.skill_spec_problems(meta, "line\n" * (lines - 1), "lead")
    assert (not problems) if bad is None else any(bad in p for p in problems)
