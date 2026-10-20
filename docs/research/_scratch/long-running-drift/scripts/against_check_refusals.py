"""Count how often a check's refusal message appears in tool results, against how often its command ran.

Usage: python3 against_check_refusals.py <transcript_dir> <command_regex> <refusal_regex>
Counts Bash tool_use commands matching command_regex and tool_result texts matching refusal_regex
(top level and subagents), with first and last timestamps of refusals.
"""

import json
import pathlib
import re
import sys


def main(d, cmd_re, ref_re):
    cmd_re, ref_re = re.compile(cmd_re), re.compile(ref_re)
    runs = refusals = 0
    ts = []
    for f in pathlib.Path(d).expanduser().rglob("*.jsonl"):
        for line in f.open(errors="replace"):
            try:
                o = json.loads(line)
            except Exception:  # noqa: S112
                continue
            c = o.get("message", {}).get("content")
            if not isinstance(c, list):
                continue
            for x in c:
                if not isinstance(x, dict):
                    continue
                if x.get("type") == "tool_use" and x.get("name") == "Bash":
                    if cmd_re.search(x.get("input", {}).get("command", "")):
                        runs += 1
                elif x.get("type") == "tool_result":
                    t = x.get("content")
                    if isinstance(t, list):
                        t = " ".join(y.get("text", "") for y in t if isinstance(y, dict))
                    if isinstance(t, str) and ref_re.search(t):
                        refusals += 1
                        ts.append(o.get("timestamp") or "")
    ts.sort()
    print(f"runs={runs} refusals={refusals} first={ts[0] if ts else '-'} last={ts[-1] if ts else '-'}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
