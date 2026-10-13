#!/usr/bin/env python3
"""How agents touch files in recorded sessions, to bound how often a path-scoped `.claude/rules/` file would load (#220 r2).

Usage: python3 -I r2_rule_triggers.py [~/.claude/projects/<project-b>]

A path-scoped rule loads on Read, Write or Edit of a matching file, and on a Bash command that counts as a read
(`cat`, `head`, `sed -n` on one file). It does not load on Grep, on a Python subprocess that opens the file
(`uv run dev code show` is one), or on a Read a PreToolUse hook denies (probed 2026-10-13, Claude Code 2.1.296;
see r2-mechanisms.md M2). This script counts, over every main and subagent transcript in the directory:
- file reads by trigger class: Read tool / Bash read verb (cat, head, tail, sed -n) / non-trigger (dev code, grep, rg);
- file writes: Edit, Write, MultiEdit, NotebookEdit (trigger) vs Bash writes (heuristic: sed -i, perl -i, tee,
  `>`/`>>` redirection to a path that is not /dev/null, `cat <<` heredoc into a file, write_text/open(...,'w')).
The Bash-write count needs a write verb and a repository path (REPO_PATH) in the same command, so a command that
reads one repo file and writes scratch counts too: an upper bound. `--sample N` prints N random matches to hand-check.
"""

import json
import os
import pathlib
import re
import sys
from collections import Counter

READ_VERB = re.compile(r"(?:^|&&\s*|;\s*)(?:cat|head|tail|sed\s+-n)\s+(?!.*\|)")
NOTRIG_READ = re.compile(r"\b(?:dev\s+code\s+(?:show|outline|refs|find)|grep|rg)\b")
BASH_WRITE = re.compile(
    r"sed\s+-i|perl\s+-i|\btee\b|(?<![0-9&])>>?\s*(?!/dev/null|&)[\w./~\"'$-]+|write_text\(|open\([^)]*['\"][wa]['\"]"
)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# A repository path an agent could scope a rule to (not scratch, not /tmp). Used to keep only Bash writes of tracked files.
REPO_PATH = re.compile(
    r"(?<![/\w.-])(?:(?:devkit|plugins|tests|template|scripts|src|\.claude|\.devkit)/[\w./-]+"
    r"|docs/(?!research/_scratch)[\w./-]+|(?:CLAUDE|README|CONTRIBUTING|CHANGELOG)\.md|pyproject\.toml)"
)


SAMPLE: list[str] = []


def main() -> None:
    root = pathlib.Path(
        sys.argv[1]
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
        else os.environ.get("PROJECT_B_TRANSCRIPTS", "~/.claude/projects/project-b")
    ).expanduser()
    c, files = Counter(), 0
    for f in root.rglob("*.jsonl"):
        files += 1
        sub = "subagents" in f.parts
        for line in f.open(errors="replace"):
            try:
                msg = json.loads(line).get("message") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                who = "sub" if sub else "main"
                name, inp = b.get("name"), b.get("input") or {}
                if name == "Read":
                    c[who, "read", "Read tool (trigger)"] += 1
                elif name in EDIT_TOOLS:
                    c[who, "write", "Edit/Write tool (trigger)"] += 1
                elif name in ("Grep", "Glob"):
                    c[who, "read", "Grep/Glob (no trigger)"] += 1
                elif name == "Bash":
                    cmd = str(inp.get("command", ""))
                    if BASH_WRITE.search(cmd) and REPO_PATH.search(cmd):
                        c[who, "write", "Bash write of a repo path (no trigger, heuristic)"] += 1
                        SAMPLE.append(cmd)
                    if NOTRIG_READ.search(cmd):
                        c[who, "read", "dev code / grep in Bash (no trigger)"] += 1
                    elif READ_VERB.search(cmd):
                        c[who, "read", "Bash cat/head/tail/sed -n (trigger)"] += 1
    print(f"transcripts: {files} under {root}")
    if "--sample" in sys.argv:
        import random

        random.seed(220)
        for cmd in random.sample(SAMPLE, int(sys.argv[sys.argv.index("--sample") + 1])):
            print("SAMPLE |", cmd[:220].replace("\n", " "))
    for kind in ("read", "write"):
        for who in ("main", "sub", None):
            rows: Counter = Counter()
            for k, v in c.items():
                if k[1] == kind and (who is None or k[0] == who):
                    rows[k[2]] += v
            tot = sum(rows.values())
            trig = sum(v for k, v in rows.items() if "(trigger)" in k)
            label = who or "all"
            print(
                f"{kind:5} {label:4} total {tot:6}  trigger {trig:6} ({100 * trig / tot if tot else 0:.1f}%)  "
                + "; ".join(f"{k}: {v}" for k, v in sorted(rows.items()))
            )


if __name__ == "__main__":
    main()


# --- Per write: was a rule scoped to the written file already loaded? -------------------------------------------
# Target of a Bash write, when it can be read off the command: `sed -i ... <path>`, `>`/`>>` <path>,
# Path("<path>") or p='<path>' with write_text/open(p,'w'). Writes whose target cannot be extracted are skipped.
TARGETS = [
    re.compile(r"sed\s+-i\S*\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)(?:\s+-e\s+(?:'[^']*'|\"[^\"]*\"))*\s+([\w./-]+)"),
    re.compile(r"(?<![0-9&<>])>>?\s*([\w./-]+\.\w+)"),
    re.compile(r"Path\(\s*['\"]([\w./-]+)['\"]\s*\)[^\n]*\n?(?:.|\n){0,4000}?write_text\("),
    re.compile(
        r"\b(?:p|F|path|f)\s*=\s*['\"]([\w./-]+)['\"](?:.|\n){0,4000}?(?:open\(\s*(?:p|F|path|f)\s*,\s*['\"]w|write_text\()"
    ),
]


def _norm(p: str) -> str:
    """Path relative to a checkout: drop everything up to a worktree root, keep repo-relative tail."""
    m = re.search(
        r"(?:agent-harness|"
        + re.escape(os.environ.get("PROJECT_B_NAME", "project-b"))
        + r")(?:-worktrees/[^/]+)?/(.+)$",
        p,
    )
    return m.group(1) if m else p.lstrip("./")


COLD: list[tuple[str, str]] = []


def per_write(root: pathlib.Path) -> None:
    pre, cold, n_tool = 0, 0, 0
    for f in root.rglob("*.jsonl"):
        loaded: set[str] = set()
        for line in f.open(errors="replace"):
            try:
                msg = json.loads(line).get("message") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name"), b.get("input") or {}
                if name in ("Read",) or name in EDIT_TOOLS:
                    p = str(inp.get("file_path") or inp.get("notebook_path") or "")
                    if p:
                        loaded.add(_norm(p))
                        n_tool += name in EDIT_TOOLS
                elif name == "Bash":
                    cmd = str(inp.get("command", ""))
                    rds = re.finditer(
                        r"(?:^|&&\s*|;\s*|\n)\s*(?:cat|head(?:\s+-n\s*\d+|\s+-\d+)?|tail(?:\s+-n\s*\d+)?|sed\s+-n\s+(?:'[^']*'|\"[^\"]*\"|\S+))\s+([\w./-]+)\s*(?=$|&&|;|\n|\|)",
                        cmd,
                    )
                    tgt = None
                    for rx in TARGETS:
                        m = rx.search(cmd)
                        if m and REPO_PATH.fullmatch(_norm(m.group(1))) and "/_scratch/" not in m.group(1):
                            tgt = _norm(m.group(1))
                            break
                    if tgt:
                        if tgt in loaded:
                            pre += 1
                        else:
                            cold += 1
                            COLD.append((tgt, cmd[:160].replace("\n", " ")))
                    for rd in rds:
                        loaded.add(_norm(rd.group(1)))
    tot = pre + cold
    print(f"writes via Edit/Write tools (rule loaded by the required prior Read, or by the Write itself): {n_tool}")
    print(
        f"Bash writes of a repo file with an extractable target: {tot}; target already loaded in that session "
        f"(Read/Edit/Write tool or cat/head/tail/sed -n): {pre} ({100 * pre / tot:.1f}%); never loaded before the write: {cold}"
    )
    if "--sample" in sys.argv:
        import random

        random.seed(220)
        for t, c in random.sample(COLD, int(sys.argv[sys.argv.index("--sample") + 1])):
            print("COLD |", t, "|", c)


if __name__ == "__main__" and "--per-write" in sys.argv:
    per_write(pathlib.Path(sys.argv[1]).expanduser())
