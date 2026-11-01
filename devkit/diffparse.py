"""Parse a unified diff (`-U0`) into removed and added lines per file. A leaf module: it imports nothing from
devkit, so the checks that read a diff (devkit.checks, devkit.weaken) share it without importing each other."""

import re
from dataclasses import dataclass

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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
