"""R2b check 5: replay "orient lists auto-memory entries newer than the last HANDOFF commit" at every lead session
start in project B's transcripts on this machine (machine-local data; read-only).

usage: python3 r2_memory_replay.py
Entry time = file birth time (stat %W); HANDOFF times = first-parent commits touching docs/HANDOFF.md; session start =
first timestamp in each ~/.claude/projects/<project-b>/*.jsonl. An entry is "unpromoted" while it has no home in the
repository; r1-taxonomy F6 found all 5 entries unpromoted on 2026-10-13, so every entry counts as unpromoted here.
"""

import datetime
import os
import re
import subprocess
from pathlib import Path

import r2_common as c

proj = Path(os.environ.get("PROJECT_B_TRANSCRIPTS", "~/.claude/projects/project-b")).expanduser()
mem = {
    p.name: int(os.stat(p).st_birthtime)
    if hasattr(os.stat(p), "st_birthtime")
    else int(subprocess.run(["stat", "-c", "%W", str(p)], capture_output=True, text=True).stdout)
    for p in (proj / "memory").glob("*.md")
    if p.name != "MEMORY.md"
}
ho = [
    int(t) for t in c.git(c.PB, "log", "--first-parent", "--format=%ct", "origin/main", "--", "docs/HANDOFF.md").split()
]
starts = []
for f in proj.glob("*.jsonl"):
    with open(f, errors="replace") as fh:
        for line in fh:
            m = re.search(r'"timestamp":"([^"]+)"', line)
            if m:
                starts.append(
                    (int(datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp()), f.name[:8])
                )
                break
fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")  # noqa: E731 - research script, kept as run
print("memory entries (birth):", {k: fmt(v) for k, v in sorted(mem.items(), key=lambda kv: kv[1])})
listed = missed = 0
for s, name in sorted(starts) + [(int(datetime.datetime.now().timestamp()), "NEXT")]:  # noqa: RUF005 - research script, kept as run
    h = max([t for t in ho if t <= s], default=None)
    exist = [k for k, b in mem.items() if b <= s]
    new = [k for k in exist if h is None or mem[k] > h]
    old = [k for k in exist if k not in new]
    listed += len(new)
    missed += len(old)
    print(
        f"session {name} start {fmt(s)} | last HANDOFF {fmt(h) if h else '-'} | listed {new} | unpromoted but not listed {old}"
    )
print(f"entry-sessions listed {listed}, unpromoted entry-sessions not listed {missed}")
