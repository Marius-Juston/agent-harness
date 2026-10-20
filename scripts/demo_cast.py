#!/usr/bin/env python3
"""Build the README demos: each docs/assets/demo/<name>.toml becomes docs/assets/demo/<name>.cast (asciicast v2) and,
with --render, docs/assets/demo-<name>.svg (needs Node: a pinned svg-term-cli through npx).

  uv run python scripts/demo_cast.py --render

Timing comes only from the storyboard, so a cast is the same on every run and tests/test_demo.py can check that the
committed one is current. A storyboard is `[[scene]]`s, one per cleared screen, each with a `caption` and
`[[scene.step]]`s:
  cmd      typed after the prompt, a word at a time (no frame holds a cut word; the scrub test reads the SVG's
           text); a newline types as a shell line continuation
  output   printed a line at a time, then held long enough to read
  prompt   overrides the storyboard's prompt ("" for none, e.g. a Claude Code transcript)
  run_s    how long the command visibly runs before its output (default DEFAULTS["run_s"])
Top-level keys override DEFAULTS. Tags in `cmd` and `output`: {dim} {ok} {del} {warn} {issue} {agent} {cmd} {b},
closed by {/}; a line that starts with a devkit status word (`ok`, `WARN`, `FIX`, `FAIL`) has that word coloured.
Keep lines under `width` columns: the SVG clips, it does not wrap.
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "docs/assets/demo"
SVG_TERM = "svg-term-cli@2.1.1"

DEFAULTS = {
    "width": 100,
    "height": 26,
    "prompt": "{cmd}~/my-project{/} $ ",
    "type_s": 0.05,  # per typed character
    "run_s": 0.5,  # before the output appears; a slow command sets its own
    "line_s": 0.08,  # per output line
    "read_s": 0.35,  # held per output line once it is all on screen ...
    "read_min_s": 1.5,  # ... and at least this long
    "scene_pause_s": 2.5,  # before the next screen clears
    "end_pause_s": 8.0,  # on the last frame before the loop restarts
}
TAGS = {
    "dim": "\x1b[90m",  # bright black: svg-term draws no faint (SGR 2) text
    "ok": "\x1b[32m",
    "del": "\x1b[31m",
    "warn": "\x1b[33m",
    "issue": "\x1b[1;33m",
    "agent": "\x1b[35m",
    "cmd": "\x1b[36m",
    "b": "\x1b[1m",
    "step": "\x1b[1;34m",
    "/": "\x1b[0m",
}
STATUS = {"ok": "ok", "WARN": "warn", "FIX": "warn", "FAIL": "warn"}  # devkit's status words, coloured on sight
TAG = re.compile(r"\{(" + "|".join(map(re.escape, TAGS)) + r")\}")
CLEAR = "\x1b[2J\x1b[H"
RUNNING = "{dim}⋯ running{/}"
ERASE_LINE = "\r\x1b[2K"


def style(text: str) -> str:
    """Expand `{tag}` markers to ANSI codes and colour a leading devkit status word."""
    if (m := re.match(r"(ok|WARN|FIX|FAIL)(\s{1,3}\S)", text)) and m.group(1) in STATUS:
        text = f"{{{STATUS[m.group(1)]}}}{m.group(1)}{{/}}{text[len(m.group(1)) :]}"
    return TAG.sub(lambda t: TAGS[t.group(1)], text)


def load(path: Path) -> dict:
    return {**DEFAULTS, **tomllib.loads(path.read_text(encoding="utf-8"))}


def events(board: dict) -> list[list]:
    """The cast's output events, [seconds, "o", text], one scene per cleared screen."""
    out: list[list] = []
    t = 0.0

    def emit(text: str, wait: float) -> None:
        nonlocal t
        t = round(t + wait, 3)
        out.append([t, "o", text])

    scenes = board["scene"]
    for n, scene in enumerate(scenes, 1):
        caption = f"{{step}}{n}/{len(scenes)} ▸{{/}} {{b}}{scene['caption']}{{/}}"
        emit(CLEAR + style(caption) + "\r\n\r\n", board["scene_pause_s"] if out else 0.3)
        for step in scene["step"]:
            prompt = step.get("prompt", board["prompt"])
            lines = step["output"].strip("\n").splitlines()
            if prompt or step["cmd"]:
                emit(style(prompt), 0.5)
                for word in re.findall(r"\S+\s*", step["cmd"].replace("\n", " \\\r\n    ")):
                    emit(word, board["type_s"] * len(word.strip()))
                emit("\r\n", 0.5)
            if (run := step.get("run_s", board["run_s"])) >= 1.5:  # long enough to show that something runs
                emit(style(RUNNING), 0.1)
                emit(ERASE_LINE, run)
            elif lines:
                t = round(t + run, 3)
            for line in lines:
                emit(style(line) + "\r\n", board["line_s"])
            if lines:
                emit("\r\n", max(board["read_min_s"], board["read_s"] * len(lines)))
    emit("", board["end_pause_s"])
    return out


def cast(board: dict) -> str:
    header = {"version": 2, "width": board["width"], "height": board["height"], "env": {"TERM": "xterm-256color"}}
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in [header, *events(board)]) + "\n"


def svg_path(storyboard: Path) -> Path:
    return ROOT / f"docs/assets/demo-{storyboard.stem}.svg"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--render", action="store_true", help=f"also render each SVG with npx {SVG_TERM}")
    a = p.parse_args()
    for storyboard in sorted(DEMOS.glob("*.toml")):
        target = storyboard.with_suffix(".cast")
        target.write_text(cast(load(storyboard)), encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")
        if a.render:
            svg = svg_path(storyboard)
            cmd = ["npx", "--yes", SVG_TERM, "--in", str(target), "--out", str(svg), "--window", "--no-cursor"]
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"wrote {svg.relative_to(ROOT)} ({svg.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
