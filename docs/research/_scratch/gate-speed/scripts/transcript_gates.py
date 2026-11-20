"""Wall time of `dev check` and `dev integrate` calls in Claude Code transcripts (#520).

For every Bash tool call whose command runs `dev check` or `dev integrate`, print one JSON line: when the call was
made, when its result came back (wall seconds), whether it ran in the background, the gate's own RESULT line
(stage seconds, cached or failed), and whether the output says the gate waited for a slot.

Usage: python transcript_gates.py <transcript dir> [...] > calls.jsonl
Only timings and result lines are printed; no transcript text beyond those lines leaves this script.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

RESULT = re.compile(r"RESULT: (passed|failed)[^\n]*?\((?:(\d+) stage\(s\), )?(\d+) s\)")
CACHED = re.compile(r"RESULT: passed \(cached")
WAIT = "every gate slot on this machine is busy"
KIND = re.compile(r"\bdev (check|integrate)\b")


def ts(s: str | None) -> float | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def calls(path: Path):
    uses: dict[str, dict] = {}
    for line in path.open(errors="replace"):
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
                if (k := KIND.search(cmd)) and "--help" not in cmd and "dev check-" not in cmd:
                    uses[b["id"]] = {
                        "kind": k.group(1),
                        "pr": (re.search(r"dev integrate\s+(\d+)", cmd) or [None, None])[1],
                        "train": "--train" in cmd,
                        "local_ci": "--local-ci" in cmd,
                        "bg": bool(b.get("input", {}).get("run_in_background")),
                        "t0": ts(d.get("timestamp")),
                    }
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                u = uses.pop(b["tool_use_id"])
                out = text_of(b.get("content"))
                r = RESULT.findall(out)
                u.update(
                    t1=ts(d.get("timestamp")),
                    file=path.name,
                    result=("cached" if CACHED.search(out) else r[0][0] if r else None),
                    stage_s=int(r[0][2]) if r else None,
                    waited=WAIT in out,
                    integrate_result=(re.findall(r"^RESULT: (\S+)", out, re.M) or [None])[-1],
                )
                if u["t0"] and u["t1"]:
                    u["wall_s"] = round(u["t1"] - u["t0"], 1)
                yield u


def main() -> None:
    for top in sys.argv[1:]:
        for path in sorted(Path(top).rglob("*.jsonl")):
            for u in calls(path):
                print(json.dumps(u))


if __name__ == "__main__":
    main()
