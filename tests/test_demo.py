"""The README demo (#368): the committed cast is the storyboard's, every `uv run dev` command it shows parses, and no
line is wider than the terminal (the SVG clips rather than wraps)."""

import importlib.util
import re
import shlex
import tomllib
import unicodedata

import pytest

from devkit import cli
from devkit.paths import root

spec = importlib.util.spec_from_file_location("demo_cast", root() / "scripts/demo_cast.py")
assert spec and spec.loader
demo_cast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_cast)

BOARD = tomllib.loads(demo_cast.STORYBOARD.read_text(encoding="utf-8"))
STEPS = [step for scene in BOARD["scene"] for step in scene["step"]]


def shown_dev_commands() -> list[str]:
    typed = [" ".join(s["cmd"].splitlines()) for s in STEPS]
    called = [m for s in STEPS for m in re.findall(r"Bash\((uv run dev [^)]*)\)", s["output"])]
    return [c for c in typed + called if c.startswith("uv run dev ")]


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in demo_cast.TAG.sub("", text))


def test_committed_cast_is_current():
    assert demo_cast.CAST.read_text(encoding="utf-8") == demo_cast.cast(BOARD), (
        "run: uv run python scripts/demo_cast.py"
    )


def test_cast_is_asciicast_v2_with_rising_time():
    header, *events = demo_cast.cast(BOARD).splitlines()
    assert '"version": 2' in header
    times = [float(e.split(",")[0].lstrip("[")) for e in events]
    assert times == sorted(times)
    assert 30 < times[-1] < 90, "a demo people watch to the end"


def test_the_demo_shows_the_whole_loop():
    dev = shown_dev_commands()
    for needed in ("setup", "board new", "integrate"):
        assert any(c.startswith(f"uv run dev {needed}") for c in dev), needed
    assert any(s["cmd"].startswith("/goal ") for s in STEPS)


@pytest.mark.parametrize("command", shown_dev_commands())
def test_every_shown_dev_command_parses(command):
    cli.parse(shlex.split(command)[3:])  # argparse exits on an unknown command or flag


def test_no_line_is_wider_than_the_terminal():
    scenes = BOARD["scene"]
    lines = [f"{n}/{len(scenes)} ▸ {s['caption']}" for n, s in enumerate(scenes, 1)]
    for s in STEPS:
        prompt = s.get("prompt", BOARD["prompt"])
        lines += [prompt + line for line in s["cmd"].splitlines()] + s["output"].splitlines()
    assert [line for line in lines if width(line) > BOARD["width"]] == []


def test_readme_embeds_the_rendered_demo():
    assert '<img src="docs/assets/demo.svg"' in (root() / "README.md").read_text(encoding="utf-8")
    assert (root() / "docs/assets/demo.svg").stat().st_size < 400_000
