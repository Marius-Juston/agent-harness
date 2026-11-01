"""`dev handoff` (#394): the board-derived sections are rebuilt, everything hand-written is kept byte for byte."""

import subprocess
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from devkit import board, cli, handoff

T0 = "2026-10-11T00:00:00Z"
OWNER = "## Owner decisions\n- keep #12 or drop it?  \n\ttabbed — ünïcode, no final newline"


def mk(n, labels, title=None, comments=None, body=""):
    return board.Issue(n, title or f"t{n}", labels, body, "OPEN", T0, comments or [])


def claim(agent, branch):
    return {"body": f"<!-- devkit:claim host=h agent={agent} branch={branch} id=x -->", "createdAt": T0, "url": "u#1"}


def fixture_board():
    return [
        mk(1, ["status:in-progress", "type:build"], "build the thing", [claim("builder", "1-thing")]),
        mk(2, ["status:review", "type:bug"], "fix the bug", [claim("tester", "2-bug")]),
        mk(3, ["status:ready", "P1", "type:build"], "next one"),
        mk(4, ["status:ready", "P3", "type:research"], "unblocks five"),
        mk(5, ["status:ready", "P0", "type:build"], "waits on 4", body="## Blocked by\n#4\n"),
        mk(6, ["status:ready", "type:epic"], "an epic"),
    ]


OLD = (
    "# Handoff\n\nThe lead keeps this current.\n\n## In flight\n- #99 day-old work\n\n## Next\n- stale next\n\n" + OWNER
)


def test_regenerates_from_a_fixture_board_keeping_the_owner_section_byte_identical():
    new = handoff.render(OLD, handoff.sections(fixture_board(), set(), ["feat: x (#7)", "fix: y (#8)"]))
    assert new.endswith("\n\n" + OWNER)  # byte-identical, still last, its missing final newline kept
    assert new.startswith("# Handoff\n\nThe lead keeps this current.\n\n## In flight\n")
    assert "#99" not in new and "stale next" not in new
    flight = new.split("## In flight\n")[1].split("\n\n")[0].splitlines()
    assert flight == [
        "- #1 in-progress builder on `1-thing`: build the thing",
        "- #2 review tester on `2-bug`: fix the bug",
    ]
    merged = new.split("## Just merged\n")[1].split("\n\n")[0].splitlines()
    assert merged == ["- feat: x (#7)", "- fix: y (#8)"]
    nxt = new.split("## Next\n")[1].split("\n\n")[0].splitlines()
    # #4 inherits P0 from #5, which it blocks; #5 waits; the epic never starts
    assert nxt == ["- #4 P0 research, unblocks 1: unblocks five", "- #3 P1 build: next one"]
    assert len(new.splitlines()) <= 40
    assert handoff.render(new, handoff.sections(fixture_board(), set(), ["feat: x (#7)", "fix: y (#8)"])) == new


def test_sections_are_added_after_the_preamble_when_missing():
    text = "# Handoff\nintro\n" + OWNER
    new = handoff.render(text, handoff.sections([], set(), []))
    assert new.startswith("# Handoff\nintro\n\n## In flight\n- nothing\n\n## Just merged\n- nothing\n\n## Next\n")
    assert new.endswith("\n\n" + OWNER)


def test_the_template_handoff_renders_and_stays_stable():
    text = (Path(__file__).parents[1] / "template/docs/HANDOFF.md").read_text()
    gen = handoff.sections(fixture_board(), set(), ["x"])
    once = handoff.render(text, gen)
    assert once.startswith(text.split("## In flight")[0])
    assert once.endswith("- #3 P1 build: next one\n")
    assert handoff.render(once, gen) == once


line = st.text(st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n\x0b\x0c\x1c\x1d\x1e\x85  "))


@given(
    pre=st.lists(line.filter(lambda s: not s.startswith("## ")), max_size=4),
    hand=st.lists(
        st.tuples(st.sampled_from(["Owner decisions", "Notes", "Risks"]), st.lists(line, max_size=3)), max_size=3
    ),
    gen_first=st.booleans(),
)
def test_render_keeps_every_hand_written_chunk_and_is_idempotent(pre, hand, gen_first):
    hand_chunks = ["## " + h + "\n" + "".join(x.replace("## ", "") + "\n" for x in body) for h, body in hand]
    stale = ["## In flight\n- old\n", "## next\n- old\n"]
    text = "".join(x + "\n" for x in pre) + "".join(stale + hand_chunks if gen_first else hand_chunks + stale)
    gen = handoff.sections(fixture_board(), set(), ["m"])
    once = handoff.render(text, gen)
    for chunk in hand_chunks:
        assert chunk in once
    assert "- old" not in once
    assert handoff.render(once, gen) == once


def test_dev_handoff_is_a_no_op_without_a_handoff_file(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(handoff, "root", lambda: tmp_path)
    monkeypatch.setattr(board, "fetch", lambda **k: (_ for _ in ()).throw(AssertionError("board read")))
    assert cli.run(cli.build().parse_args(["handoff"])) in (0, None)
    assert "no handoff file at docs/HANDOFF.md" in capsys.readouterr().out
    assert not (tmp_path / "docs").exists()


def test_dev_handoff_rewrites_the_file(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/HANDOFF.md").write_text(OLD)
    monkeypatch.setattr(handoff, "root", lambda: tmp_path)
    monkeypatch.setattr(board, "fetch", lambda **k: fixture_board())
    monkeypatch.setattr(board, "closed_numbers", set)
    monkeypatch.setattr(handoff, "merged_subjects", lambda: ["feat: x (#7)"])
    assert handoff.refresh().startswith("refreshed docs/HANDOFF.md")
    text = (tmp_path / "docs/HANDOFF.md").read_text()
    assert text.endswith("\n\n" + OWNER) and "- feat: x (#7)" in text and "#99" not in text
    assert handoff.refresh() == "docs/HANDOFF.md already current"


def test_after_integrate_leaves_the_default_branch_checkout_clean(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/HANDOFF.md").write_text(OLD)
    monkeypatch.setattr(handoff, "root", lambda: tmp_path)
    monkeypatch.setattr(handoff, "git", lambda *a, **k: "main")
    monkeypatch.setattr(board, "fetch", lambda **k: fixture_board())
    monkeypatch.setattr(board, "closed_numbers", set)
    assert "not refreshed" in handoff.after_integrate()
    assert (tmp_path / "docs/HANDOFF.md").read_text() == OLD


def test_after_integrate_never_raises(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/HANDOFF.md").write_text(OLD)
    monkeypatch.setattr(handoff, "root", lambda: tmp_path)
    monkeypatch.setattr(handoff, "git", lambda *a, **k: "394-x")

    def boom(**k):
        raise RuntimeError("gh crashed")

    monkeypatch.setattr(board, "fetch", boom)
    assert handoff.after_integrate() == "handoff not refreshed: gh crashed"
    assert (tmp_path / "docs/HANDOFF.md").read_text() == OLD
