#!/usr/bin/env python3
"""Build the README demo's asciicast (docs/assets/demo/demo.cast) from docs/assets/demo/storyboard.toml.

Timing comes only from the storyboard, so the cast is the same on every run and tests/test_demo.py can check that the
committed one is current. Render it to the SVG the README embeds (needs Node):

  uv run python scripts/demo_cast.py
  npx --yes svg-term-cli@2.1.1 --in docs/assets/demo/demo.cast --out docs/assets/demo.svg --window --no-cursor
"""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORYBOARD = ROOT / "docs/assets/demo/storyboard.toml"
CAST = ROOT / "docs/assets/demo/demo.cast"

TAGS = {
    "dim": "\x1b[90m",  # bright black: svg-term draws no faint (SGR 2) text
    "ok": "\x1b[32m",
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


def style(text: str) -> str:
    """Expand `{tag}` markers to ANSI codes and colour a leading devkit status word."""
    if (m := re.match(r"(ok|WARN|FIX|FAIL)(\s{1,3}\S)", text)) and m.group(1) in STATUS:
        text = f"{{{STATUS[m.group(1)]}}}{m.group(1)}{{/}}{text[len(m.group(1)) :]}"
    return TAG.sub(lambda t: TAGS[t.group(1)], text)


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
            if prompt or step["cmd"]:
                emit(style(prompt), 0.4)
                # a word at a time, so no frame holds a cut word (the scrub test reads the SVG's text); a newline
                # types as a line continuation
                for word in re.findall(r"\S+\s*", step["cmd"].replace("\n", " \\\r\n    ")):
                    emit(word, board["type_s"] * len(word.strip()))
                emit("\r\n", 0.35)
            for line in step["output"].strip("\n").splitlines():
                emit(style(line) + "\r\n", board["line_s"])
            if step["output"].strip():
                emit("\r\n", 0.6)
    emit("", board["end_pause_s"])  # hold the last frame before the loop restarts
    return out


def cast(board: dict) -> str:
    header = {"version": 2, "width": board["width"], "height": board["height"], "env": {"TERM": "xterm-256color"}}
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in [header, *events(board)]) + "\n"


def main() -> int:
    CAST.write_text(cast(tomllib.loads(STORYBOARD.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"wrote {CAST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
