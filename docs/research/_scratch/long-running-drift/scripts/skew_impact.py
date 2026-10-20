"""What the plugin skew cost: tool calls in a window that a newer harness-ops hook would have seen (#282 r1).

Usage: python skew_impact.py --since <ISO UTC> [--until <ISO UTC>] [--exclude <session-id>]
       [--reloaded <session-id>=<ISO UTC> ...] <transcript dir>

Counts, over every transcript file under the directory (main sessions and subagents), tool_use blocks after --since:
- Bash commands that run `uv add` (uv_add_guard.py, added in v0.1.7, absent from 0.1.4);
- Workflow tool calls (spawn_guard's quota gate matches `Agent|Workflow` from v0.1.5; 0.1.4 matches `Agent` only);
- Agent tool calls with a harness-ops builder (SubagentStop completion_gate.py, added in v0.1.5).
With --reloaded, a call in that session at or after that time (a /reload-plugins that loaded a newer build) is
counted separately as "(after reload)": those calls ran with the newer hooks. Counts only; it prints no command text.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

UV_ADD = re.compile(r"(^|[;&|(\s])uv\s+add\s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    ap.add_argument("--exclude")
    ap.add_argument("--reloaded", action="append", default=[], help="<session-id>=<ISO UTC>")
    ap.add_argument("dir", type=Path)
    a = ap.parse_args()
    reloaded = dict(x.split("=", 1) for x in a.reloaded)
    c: Counter = Counter()
    for path in a.dir.rglob("*.jsonl"):
        for line in path.open(errors="replace"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if a.exclude and r.get("sessionId") == a.exclude:
                break
            ts = r.get("timestamp") or ""
            if r.get("type") != "assistant" or not (a.since <= ts < a.until):
                continue
            sid = r.get("sessionId") or ""
            tag = " (after reload)" if sid in reloaded and ts >= reloaded[sid] else ""
            for p in (r.get("message") or {}).get("content") or []:
                if not isinstance(p, dict) or p.get("type") != "tool_use":
                    continue
                name, inp = p.get("name"), p.get("input") or {}
                if name == "Bash" and UV_ADD.search(str(inp.get("command", ""))):
                    c["bash uv add" + tag] += 1
                elif name == "Workflow":
                    c["workflow call" + tag] += 1
                elif name in ("Agent", "Task") and str(inp.get("subagent_type", "")).endswith("builder"):
                    c["builder spawn" + tag] += 1
    for k, v in sorted(c.items()):
        print(f"{k}\t{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
