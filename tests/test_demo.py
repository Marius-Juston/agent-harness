"""The README demos (#368): each committed cast is its storyboard's, every `uv run dev` command shown parses, no line
is wider than the terminal (the SVG clips rather than wraps), and the README embeds each rendered SVG."""

import importlib.util
import re
import shlex
import unicodedata

import pytest

from devkit import cli
from devkit.paths import root

spec = importlib.util.spec_from_file_location("demo_cast", root() / "scripts/demo_cast.py")
assert spec and spec.loader
demo_cast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_cast)

STORYBOARDS = sorted(demo_cast.DEMOS.glob("*.toml"))
BOARDS = {p.stem: demo_cast.load(p) for p in STORYBOARDS}


def steps(board: dict) -> list[dict]:
    return [step for scene in board["scene"] for step in scene["step"]]


def shown_dev_commands() -> list[str]:
    out = []
    for board in BOARDS.values():
        for s in steps(board):
            typed = " ".join(s["cmd"].splitlines())
            out += [c.strip() for c in re.split(r"&&|\|", typed)]
            out += re.findall(r"Bash\((uv run dev [^)]*)\)", s["output"])
    return sorted({c for c in out if c.startswith("uv run dev ")})


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in demo_cast.TAG.sub("", text))


def test_there_is_a_demo_for_a_new_project_an_existing_one_and_an_update():
    assert set(BOARDS) == {"new-project", "existing-project", "update"}


@pytest.mark.parametrize("path", STORYBOARDS, ids=lambda p: p.stem)
def test_committed_cast_is_current(path):
    cast = path.with_suffix(".cast").read_text(encoding="utf-8")
    assert cast == demo_cast.cast(demo_cast.load(path)), "run: uv run python scripts/demo_cast.py --render"


@pytest.mark.parametrize("name", BOARDS)
def test_cast_is_asciicast_v2_with_rising_time_and_time_to_read(name):
    header, *events = demo_cast.cast(BOARDS[name]).splitlines()
    assert '"version": 2' in header
    times = [float(e.split(",")[0].lstrip("[")) for e in events]
    assert times == sorted(times)
    assert 20 < times[-1] < 120, "a demo people watch to the end"
    assert times[-1] - times[-2] >= 5, "the last frame holds before the loop restarts"


def test_output_is_held_longer_the_more_lines_it_has():
    board = {**demo_cast.DEFAULTS, "scene": [{"caption": "c", "step": [{"cmd": "x", "output": "a\n" * 12}]}]}
    *_, last_line, blank, _end = demo_cast.events(board)
    assert blank[0] - last_line[0] == pytest.approx(12 * demo_cast.DEFAULTS["read_s"])


def test_the_new_project_demo_shows_the_whole_loop():
    dev = shown_dev_commands()
    for needed in ("setup", "board new", "integrate", "check"):
        assert any(c.startswith(f"uv run dev {needed}") for c in dev), needed
    assert any(s["cmd"].startswith("/goal ") for s in steps(BOARDS["new-project"]))


@pytest.mark.parametrize("command", shown_dev_commands())
def test_every_shown_dev_command_parses(command):
    cli.parse(shlex.split(command)[3:])  # argparse exits on an unknown command or flag


@pytest.mark.parametrize("name", BOARDS)
def test_no_line_is_wider_than_the_terminal(name):
    board = BOARDS[name]
    scenes = board["scene"]
    lines = [f"{n}/{len(scenes)} ▸ {s['caption']}" for n, s in enumerate(scenes, 1)]
    for s in steps(board):
        prompt = s.get("prompt", board["prompt"])
        lines += [prompt + line for line in s["cmd"].splitlines()] + s["output"].splitlines()
    assert [line for line in lines if width(line) > board["width"]] == []


@pytest.mark.parametrize("path", STORYBOARDS, ids=lambda p: p.stem)
def test_readme_embeds_each_rendered_demo(path):
    svg = demo_cast.svg_path(path)
    assert f'<img src="{svg.relative_to(root())}"' in (root() / "README.md").read_text(encoding="utf-8")
    assert svg.stat().st_size < 400_000
