"""Backticked `dev <cmd> ...` spans written WITHOUT the `uv run` prefix: the gate's DEV_CMD regex only matches
`uv run dev`, so these names and flags are never checked. Counts spans, unknown commands and unknown flags.
usage (inside a repo): uv run python bare_dev.py
"""

import argparse
import re
import subprocess
from pathlib import Path

from devkit import cli


def opts(p):
    own, subs = set(), {}
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            subs.update(a.choices)
        else:
            own.update(a.option_strings)
    return own, subs


_, top = opts(cli.build())
SPAN = re.compile(r"`(?<!uv run )dev ([a-z][a-z-]*)([^`]*)`")
FLAG = re.compile(r"(?<![\w<\[/-])(--?[a-zA-Z][\w-]*)")
files = subprocess.run(["git", "ls-files", "*.md", "*.jinja"], capture_output=True, text=True).stdout.split()
spans = badcmd = flags = badflag = 0
for f in files:
    if f.startswith("docs/generated/") or "/_scratch/" in f:
        continue
    for ln, line in enumerate(Path(f).read_text(errors="replace").splitlines(), 1):
        for m in re.finditer(r"`dev ([a-z][a-z-]*)([^`]*)`", line):
            if line[max(0, m.start() - 6) : m.start()].endswith("uv run"):
                continue
            spans += 1
            cmd, rest = m.groups()
            if cmd not in top:
                badcmd += 1
                print(f"{f}:{ln}: `dev {cmd}` is not a command")
                continue
            own, subs = opts(top[cmd])
            w = rest.split()
            if w and w[0] in subs:
                own |= opts(subs[w[0]])[0]
            elif w and subs and not w[0].startswith(("-", "<")) and w[0] not in subs:
                badcmd += 1
                print(f"{f}:{ln}: `dev {cmd} {w[0]}` is not a subcommand")
            for fl in FLAG.findall(rest):
                flags += 1
                if fl not in own:
                    badflag += 1
                    print(f"{f}:{ln}: `dev {cmd}`: unknown {fl}")
print(
    f"RESULT: {spans} bare `dev ...` spans in {len(files)} files; {badcmd} bad command names, {badflag} unknown of {flags} flags"
)
