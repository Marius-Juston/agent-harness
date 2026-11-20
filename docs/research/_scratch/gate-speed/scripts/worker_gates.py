"""Worker gate wall times (#520): every `dev check` Bash call in the transcripts, foreground or moved to the
background (Claude Code moves a call past its timeout into a task whose output file it names), joined to the gate's
RESULT line.

wall   = call start -> result (foreground) or -> the task output file's last write (background)
stage  = the gate's RESULT seconds: the stages, timed from when the gate held a slot
other  = wall - stage: the wait for a slot plus the re-exec, tree key, collection and (for `dev pr`) the rest

Usage: python worker_gates.py <transcript dir> <tasks dir> > worker_gates.json
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

RESULT = re.compile(r"RESULT: (passed|failed)[^\n]*?\((?:\d+ stage\(s\), )?(\d+) s\)")
TASK = re.compile(r"tasks/(\w+)\.output")
CHECK = re.compile(r"(?:^|[;&|(]\s*|timeout \d+ )uv run dev check\b(?! *-)")


def ts(s: str | None) -> float | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def main() -> None:
    tasks = Path(sys.argv[2])
    rows = []
    for path in sorted(Path(sys.argv[1]).rglob("*.jsonl")):
        uses: dict[str, dict] = {}
        for line in path.read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message")
            if not isinstance(m, dict) or not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmd = b.get("input", {}).get("command", "")
                    if CHECK.search(cmd) and "--full" not in cmd:
                        uses[b["id"]] = {"t0": ts(d.get("timestamp")), "agent": path.stem}
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                    u = uses.pop(b["tool_use_id"])
                    out, t1, how = text_of(b.get("content")), ts(d.get("timestamp")), "fg"
                    if (task := TASK.search(out)) and not RESULT.search(out):
                        f = tasks / f"{task.group(1)}.output"
                        if not f.exists():
                            continue
                        out, t1, how = f.read_text(errors="replace"), os.stat(f).st_mtime, "bg"
                    if not (r := RESULT.findall(out)) or not u["t0"]:
                        continue
                    rows.append(
                        {
                            **u,
                            "how": how,
                            "wall_s": round(t1 - u["t0"]),
                            "stage_s": int(r[-1][1]),
                            "passed": r[-1][0] == "passed",
                            "waited": "every gate slot on this machine is busy" in out,
                            "day": datetime.fromtimestamp(u["t0"]).strftime("%Y-%m-%d"),
                        }
                    )
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
