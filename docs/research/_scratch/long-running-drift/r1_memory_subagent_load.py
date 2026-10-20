"""R1 memory: does a subagent's own context carry the parent project's auto-memory index? Tally by Claude Code version.

usage: python3 -I r1_memory_subagent_load.py PROJECT_DIR [PROJECT_DIR ...]
PROJECT_DIR = a folder under ~/.claude/projects. Scans <dir>/*/subagents/agent-*.jsonl; a transcript "loads" the index when
an attachment or user record carries the auto-memory marker. Agent type comes from the sibling .meta.json when present.
"""

import collections
import json
import sys
from pathlib import Path

MARK = "auto-memory, persists across conversations"
tally = collections.Counter()
types = collections.Counter()
for d in sys.argv[1:]:
    for f in Path(d).expanduser().glob("*/subagents/agent-*.jsonl"):
        ver, hit = None, False
        with open(f, errors="replace") as fh:
            for line in fh:
                if ver is None and '"version":"' in line:
                    ver = line.split('"version":"', 1)[1].split('"', 1)[0]
                if MARK in line:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") in ("attachment", "user"):
                        hit = True
                        break
        meta = f.with_suffix(".meta.json")
        at = json.loads(meta.read_text()).get("agentType", "?") if meta.exists() else "?"
        minor = ".".join((ver or "?").split(".")[:3])
        tally[(minor, hit)] += 1
        types[(at.split(":")[0] if ":" in at else at, hit)] += 1
vers = sorted({v for v, _ in tally}, key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
for v in vers:
    print(f"{v:10} loads={tally[(v, True)]:4} no={tally[(v, False)]:4}")
print("by agent type (namespace):", dict(types))
print("total loads", sum(n for (v, h), n in tally.items() if h), "of", sum(tally.values()))
