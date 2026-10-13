"""Option flags written after `uv run dev <cmd> [<sub>]` in tracked text that the repo's own CLI parser does not
define. Run inside a repo with its own environment:  uv run python <this file>  (read-only).

The span checked is the backticked code span holding the command, else the rest of the line up to | && ; or #.
Prints every unknown flag with file:line, then a count line. Exit 0 always (measurement, not a gate).
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from devkit import cli

VERBOSE = "-v" in sys.argv


def opts(p: argparse.ArgumentParser) -> tuple[set[str], dict]:
    own, subs = set(), {}
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            subs.update(a.choices)
        else:
            own.update(a.option_strings)
    return own, subs


root_p = cli.build()
_, top = opts(root_p)
CMD = re.compile(r"uv run dev ([a-z][a-z-]*)((?: [^`|;&#\n]*)?)")
FLAG = re.compile(r"(?<![\w<\[/-])(--?[a-zA-Z][\w-]*)")
files = subprocess.run(
    ["git", "ls-files", "*.md", "*.jinja", "*.sh", "*.yml", "*.toml", "*.py"], capture_output=True, text=True
).stdout.split()
checked = bad = 0
for f in files:
    if f.startswith(("docs/generated/", "tests/")) or "/_scratch/" in f:
        continue
    for ln, line in enumerate(Path(f).read_text(errors="replace").splitlines(), 1):
        for m in CMD.finditer(line):
            cmd, rest = m.group(1), m.group(2)
            if cmd not in top:
                continue
            p = top[cmd]
            own, subs = opts(p)
            words = rest.split()
            if words and words[0] in subs:
                own2, _ = opts(subs[words[0]])
                own = own | own2
            # stop at prose: a span ends at a closing backtick if the command was backticked
            before = line[: m.start()]
            if before.count("`") % 2 == 1 and "`" in line[m.end() :] + "`":
                rest = rest.split("`")[0]
            for fl in FLAG.findall(rest):
                if fl.startswith("--") or len(fl) == 2:
                    checked += 1
                    if VERBOSE:
                        print("ok?", fl in own, f, ln, cmd, fl)
                    if fl not in own:
                        bad += 1
                        print(
                            f"{f}:{ln}: uv run dev {cmd}{(' ' + words[0]) if words and words[0] in subs else ''}: unknown {fl}   | {line.strip()[:140]}"
                        )
print(f"RESULT: {bad} unknown of {checked} flags checked in {len(files)} files")
