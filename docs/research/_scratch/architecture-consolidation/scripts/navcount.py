"""Count code-navigation tool calls in Claude Code transcripts (jsonl on stdin): `dev code` subcommands vs Bash
commands containing grep/rg, plus Read/Write/Edit/Grep/LSP and MCP calls. Usage: cat *.jsonl | navcount.py"""

import json
import re
import sys
from collections import Counter

c = Counter()
for line in sys.stdin:
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    m = d.get("message") or {}
    if d.get("type") != "assistant" or not isinstance(m.get("content"), list):
        continue
    for b in m["content"]:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        name, inp = b["name"], b.get("input", {})
        if name == "Bash":
            cmd = inp.get("command", "")
            mm = re.search(r"dev code (outline|show|refs|find)", cmd)
            if mm:
                c["dev code " + mm.group(1)] += 1
            elif re.search(r"(^|[|;&( ])(rg|grep|git grep)\b", cmd):
                c["bash grep/rg"] += 1
        elif name in ("Grep", "Glob", "Read", "Write", "Edit", "LSP"):
            c[name] += 1
        elif name.startswith("mcp__"):
            c["mcp:" + name.split("__")[1]] += 1
print(dict(c.most_common(14)))
