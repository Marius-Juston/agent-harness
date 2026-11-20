"""A wrong `uv run dev` call names the invoked subcommand's usage and the nearest option in at most 3 lines (#524)."""

import pytest

from devkit import cli


def _error(argv: list[str], capsys) -> list[str]:
    with pytest.raises(SystemExit) as exc:
        cli.parse(argv)
    assert exc.value.code == 2
    lines = capsys.readouterr().err.splitlines()
    assert 2 <= len(lines) <= 3, lines
    return lines


# docs/research/docs-current.md §2.2: the guesses agents made, and the usage line their first error should show
HISTORICAL = [
    ("board wave -n 8", "usage: dev board wave [-h] n", "-n: did you mean the positional n?"),
    ("board show 5", "usage: dev board [-h] {init,", None),
    (
        "worktree --dry-run",
        "usage: dev worktree [-h] <sub> ...",
        "--dry-run: did you mean dev worktree prune --dry-run?",
    ),
    ("test -k foo", "usage: dev test [-h] [--affected] [--slow] [-n N] [paths ...]", None),
    ("board claim 5", "usage: dev board claim [-h] --agent AGENT [--branch BRANCH] [--force] number", None),
    ("code refs a b", "usage: dev code refs [-h] symbol", None),
    ("board beat 5 --note x", "usage: dev board beat [-h] number message", None),
    ("board new --priority P1 --title t --type build", "usage: dev board new [-h] --title TITLE", "--prio"),
    ("board new --priority P1", "usage: dev board new [-h] --title TITLE", "--priority: did you mean --prio?"),
    ("board block 5 because", "usage: dev board block [-h] --reason REASON number", None),
]


@pytest.mark.parametrize(("command", "usage", "hint"), HISTORICAL)
def test_historical_guess_shows_the_subcommand_usage(command, usage, hint, capsys):
    lines = _error(command.split(), capsys)
    assert lines[0].startswith(usage), lines
    if hint:
        assert hint in "\n".join(lines[1:]), lines


def test_unknown_flag_suggests_the_nearest_option(capsys):
    lines = _error(["check", "--no-cash"], capsys)
    assert lines[0] == "usage: dev check [-h] [--full] [--no-cache]"
    assert lines[1] == "dev check: error: unrecognized arguments: --no-cash"
    assert lines[2] == "--no-cash: did you mean --no-cache?"


def test_flag_with_value_is_matched_by_name(capsys):
    lines = _error(["board", "list", "--stats=ready"], capsys)
    assert lines[-1] == "--stats: did you mean --status?"


def test_no_hint_below_the_cutoff(capsys):
    lines = _error(["check", "--zzzzzz"], capsys)
    assert len(lines) == 2
    assert lines[1] == "dev check: error: unrecognized arguments: --zzzzzz"


def test_missing_argument_keeps_argparse_message(capsys):
    lines = _error(["board", "claim", "5"], capsys)
    assert lines[1] == "dev board claim: error: the following arguments are required: --agent"


def test_misspelled_subcommand_suggests_the_nearest(capsys):
    lines = _error(["board", "brie", "5"], capsys)
    assert lines[0].startswith("usage: dev board [-h]")
    assert lines[-1] == "did you mean brief?"


def test_long_usage_is_one_line(capsys):
    lines = _error(["board", "new", "--title", "t"], capsys)
    assert lines[0].startswith("usage: dev board new [-h] --title TITLE --type {")
    assert lines[0].endswith("[--parent PARENT]")


def test_values_after_double_dash_are_not_flags(capsys):
    lines = _error(["board", "claim", "5", "--", "--agnt"], capsys)
    assert len(lines) == 2


def test_help_still_wraps(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse(["board", "new", "--help"])
    assert exc.value.code == 0
    assert all(len(line) < 200 for line in capsys.readouterr().out.splitlines())
