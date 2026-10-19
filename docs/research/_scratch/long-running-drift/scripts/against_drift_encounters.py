"""Find assistant text in Claude Code transcripts (top level and subagents) where an agent notices that
instructions, memory or docs disagree with the repository, i.e. drift met at use time.

Usage: python3 against_drift_encounters.py <project_transcript_dir> [...] [--dump OUT.jsonl]
Prints per directory: transcript files, assistant text blocks, encounter hits. Text goes only to --dump.
The pattern requires a doc-like subject AND a staleness word in the same sentence; hits are candidates for hand coding.
"""

import json
import pathlib
import re
import sys

SUBJ = re.compile(
    r"\b(CLAUDE\.md|AGENTS\.md|HANDOFF|memory|MEMORY\.md|README|the (doc|docs|spec|note|skill|prompt|ticket|issue body)|"
    r"skill|agent (body|prompt|definition)|docstring|research note|instructions?)\b",
    re.I,
)
STALE = re.compile(
    r"\b(stale|outdated|out of date|out-of-date|no longer (exists?|true|accurate|valid|matches|applies)|"
    r"(is|are|was) (wrong|incorrect|obsolete)|contradict\w*|drift\w*|(doesn't|does not|didn't) (exist|match)|"
    r"(says|claims|states) .{0,80}\bbut\b)",
    re.I,
)
SENT = re.compile(r"(?<=[.!?\n])\s+")


def blocks(o):
    if o.get("type") != "assistant":
        return
    c = o.get("message", {}).get("content")
    if isinstance(c, list):
        for x in c:
            if isinstance(x, dict) and x.get("type") == "text":
                yield x.get("text", "")


def main(argv):
    dump = None
    if "--dump" in argv:
        i = argv.index("--dump")
        dump = open(argv[i + 1], "w")  # noqa: SIM115
        argv = argv[:i] + argv[i + 2 :]
    for d in argv:
        p = pathlib.Path(d).expanduser()
        files = sorted(p.rglob("*.jsonl"))
        nb = hits = 0
        sessions = set()
        for f in files:
            for line in f.open(errors="replace"):
                try:
                    o = json.loads(line)
                except Exception:  # noqa: S112
                    continue
                for t in blocks(o):
                    nb += 1
                    for s in SENT.split(t):
                        if SUBJ.search(s) and STALE.search(s):
                            hits += 1
                            sessions.add(f.name)
                            if dump:
                                dump.write(
                                    json.dumps(
                                        {
                                            "dir": p.name,
                                            "file": str(f.relative_to(p)),
                                            "ts": o.get("timestamp"),
                                            "s": s[:600],
                                        }
                                    )
                                    + "\n"
                                )
                            break
        print(f"{p.name}\tfiles={len(files)}\ttext_blocks={nb}\tencounters={hits}\tfiles_with={len(sessions)}")


if __name__ == "__main__":
    main(sys.argv[1:])
