#!/usr/bin/env python3
"""PreToolUse(Read|Bash): a long source file or note asked for whole is answered with its outline instead.

What an agent reads stays in its context and is re-sent on every later call, so an early whole-file dump is the most
expensive thing it does (in one measured repository, whole-file `cat` of modules over 300 lines was 58 % of all
`cat` output).
The call is rewritten (updatedInput), not refused, so it answers in the same turn:
- Read of a .py over PY_LINES lines or a .md over MD_LINES without `limit`: limit WINDOW plus a note;
- Read of a Bash output persisted to `.claude/projects/.../tool-results/*.txt` over PY_LINES lines or SIZE_CAP bytes:
  the first WINDOW lines, fewer if they pass SIZE_CAP bytes, plus a Grep hint (#265; its lines can be very long);
- a plain `cat <file>`, or a `head -n N <file>` with N over the cap, of such a file: `uv run dev code outline <file>`
  plus the note, in a project that has devkit (a `.venv/bin/dev`). A shorter head is already a window and passes.
Escape: add `# whole-file` to the command, or pass an explicit limit. Stdlib only; any doubt allows the call.
"""

import json
import os
import re
import shlex
import sys

PY_LINES, MD_LINES, WINDOW = 300, 400, 250
SIZE_CAP = 40_000  # bytes of a persisted output read at once (#265: one 122-line output held 1.06 MB)
PERSISTED = re.compile(r"/\.claude/projects/.+/tool-results/[^/]+\.txt$")
ESCAPE = "# whole-file"
HEAD_N = re.compile(r"^(?:-n|--lines=|-)(\d+)$")


def whole_read(cmd: str) -> tuple[str, int | None] | None:
    """(path, lines asked for; None = all) for a lone `cat <file>` or `head [-n N] <file>`, else None."""
    if any(ch in cmd for ch in "|;&<>()$`\n#*?"):
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    if len(argv) == 2 and argv[0] == "cat":
        return argv[1], None
    if argv[:1] != ["head"]:
        return None
    opts, path = argv[1:-1], argv[-1]
    if opts[:1] == ["-n"] and len(opts) == 2:
        opts = ["-n" + opts[1]]
    if not opts:
        return path, 10
    m = HEAD_N.match(opts[0]) if len(opts) == 1 else None
    return (path, int(m.group(1))) if m else None


def lines_of(path: str) -> int:
    try:
        if not os.path.isfile(path):
            return 0
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def persisted(path: str) -> bool:
    """A Bash output too large for the context, which Claude Code saved to tool-results/ for a later Read."""
    return bool(PERSISTED.search(path.replace(os.sep, "/")))


def persisted_window(path: str) -> tuple[int, int, int]:
    """(lines, bytes, window): window is the first WINDOW lines, cut to those within SIZE_CAP bytes (at least 1)."""
    n = size = window = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                n += 1
                size += len(line)
                if n <= WINDOW and size <= SIZE_CAP:
                    window = n
    except OSError:
        return 0, 0, 0
    return n, size, max(window, 1)


def limit_for(path: str) -> int | None:
    if path.endswith((".py", ".ts", ".tsx", ".js", ".rs", ".go")):
        return PY_LINES
    if path.endswith(".md") and not path.endswith(("SKILL.md", "CLAUDE.md")) and "/.claude/agents/" not in path:
        return MD_LINES
    return None


def persisted_note(path: str, n: int, size: int, window: int) -> str:
    return (
        f"read-guard: {path} is a persisted tool output of {n} lines ({size // 1000} KB), and what you read stays in "
        f"context for every later call. Shown: its first {window} lines. Next: Grep this file for what you need, or a "
        f"Read with offset+limit for another window."
    )


def note(path: str, n: int) -> str:
    return (
        f"read-guard: {path} has {n} lines (~{n * 12 // 1000}k tokens), and what you read stays in context for every "
        f"later call. Shown: its outline / first {WINDOW} lines. Next: `uv run dev code show <Symbol>`, a Read with "
        f"offset+limit, or add `{ESCAPE}` to a cat command to read it whole."
    )


def out(updated: dict, context: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": updated,
                    "additionalContext": context,
                }
            }
        )
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict):
        return 0
    tool, inp = data.get("tool_name"), data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    if tool == "Read":
        path = inp.get("file_path", "")
        cap = limit_for(path)
        if persisted(path):
            n, size, window = persisted_window(path)
            if not inp.get("limit") and not inp.get("offset") and (n > PY_LINES or size > SIZE_CAP):
                out({**inp, "limit": window}, persisted_note(path, n, size, window))
        elif cap and not inp.get("limit") and not inp.get("offset") and (n := lines_of(path)) > cap:
            out({**inp, "limit": WINDOW}, note(path, n))
    elif tool == "Bash":
        cmd = inp.get("command", "")
        if isinstance(cmd, str) and ESCAPE not in cmd and (hit := whole_read(cmd)):
            path, asked = hit
            full = path if os.path.isabs(path) else os.path.join(cwd, path)
            cap = limit_for(full)  # None for a persisted .txt output: an outline of it shows nothing
            project = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
            has_devkit = os.path.exists(os.path.join(project, ".venv/bin/dev"))
            if has_devkit and cap and (n := lines_of(full)) > cap and (asked is None or asked > cap):
                out({**inp, "command": f"uv run --quiet dev code outline {shlex.quote(path)}"}, note(path, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
