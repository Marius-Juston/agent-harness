"""Second-pass transcript stats for #254 (stdlib + numpy). Usage: python3 extra_stats.py <claude-project-dir>

Prints: per-token price fit from cost-state records and each cost share; subagent first-turn context; end-of-run context
and tool-result share by agent type; cd-prefix targets vs the agent's own cwd; gaps between API calls (cache TTL);
worktree-isolation refusals.
"""

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np

P = sys.argv[1]
SUB = glob.glob(os.path.join(P, "*", "subagents", "*.jsonl"))
MAIN = glob.glob(os.path.join(P, "*.jsonl"))


def records(f):
    with open(f, errors="ignore") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def agent_type(f):
    try:
        return json.load(open(f[:-6] + ".meta.json")).get("agentType", "?").replace("harness-ops:", "")
    except OSError:
        return "?"


def rlen(c):
    if isinstance(c, str):
        return len(c)
    return sum(len(x.get("text", "")) + (6000 if x.get("type") == "image" else 0) for x in c or [])


rows, costs = [], []
first_ctx, end_ctx = [], defaultdict(list)
by_tool = defaultdict(Counter)
cd = Counter()
gaps = {"sub": Counter(), "main": Counter()}
isolation = 0
for f in MAIN + SUB:
    sub = f in SUB
    at = agent_type(f) if sub else "main"
    pend, seen, prev, first, last = {}, set(), None, True, 0
    for d in records(f):
        t = d.get("type")
        if t == "cost-state":
            m = d["modelUsage"].get("claude-opus-5-5")
            if m:
                rows.append(
                    [m["inputTokens"], m["outputTokens"], m["cacheReadInputTokens"], m["cacheCreationInputTokens"]]
                )
                costs.append(m["costUSD"])
        if t == "assistant":
            msg = d["message"]
            u = msg.get("usage")
            if u and msg.get("id") not in seen:
                seen.add(msg.get("id"))
                ctx = u["cache_read_input_tokens"] + u["cache_creation_input_tokens"] + u["input_tokens"]
                if first and sub:
                    first_ctx.append(ctx)
                first, last = False, ctx
                ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                if prev:
                    g = (ts - prev).total_seconds()
                    gaps["sub" if sub else "main"][
                        "<1m" if g < 60 else "1-5m" if g < 300 else "5-60m" if g < 3600 else ">1h"
                    ] += 1
                prev = ts
            for b in msg.get("content", []):
                if b.get("type") != "tool_use":
                    continue
                name = b["name"]
                if name == "Bash":
                    c = b["input"].get("command", "").strip()
                    m = re.match(r'cd\s+("?)([^"&;\s]+)\1\s*(&&|;)', c)
                    if m:
                        tgt, cwd = m.group(2).rstrip("/"), d.get("cwd", "").rstrip("/")
                        cd["own cwd" if tgt == cwd else "another worktree" if "worktrees" in tgt else "elsewhere"] += 1
                    else:
                        cd["no cd"] += 1
                    m = re.search(r"dev\s+(lit|code|board|cite-check|check|reuse)", c)
                    name = (
                        "dev " + m.group(1)
                        if m
                        else "gh"
                        if re.search(r"\bgh ", c)
                        else "curl"
                        if "curl" in c
                        else "Bash-other"
                    )
                pend[b["id"]] = name
        if t == "user" and isinstance(d["message"].get("content"), list):
            for b in d["message"]["content"]:
                if b.get("type") == "tool_result":
                    by_tool[at][pend.get(b["tool_use_id"], "?")] += rlen(b.get("content"))
                    if b.get("is_error") and "This agent is isolated in the worktree" in str(b.get("content")):
                        isolation += 1
    if sub:
        end_ctx[at].append(last)

A, y = np.array(rows, float), np.array(costs)
x, *_ = np.linalg.lstsq(A, y, rcond=None)
tot = A.sum(0) * x
print("fitted $/M tokens (input, output, cache read, cache write):", (x * 1e6).round(2))
print(
    "cost share:", dict(zip(["input", "output", "cache read", "cache write"], (tot / tot.sum()).round(3), strict=True))
)
first_ctx.sort()
print("subagent first-turn context: median", first_ctx[len(first_ctx) // 2])
for at, v in sorted(end_ctx.items(), key=lambda kv: -len(kv[1])):
    v.sort()
    s = sum(by_tool[at].values()) or 1
    top = [(k, round(n / s, 2)) for k, n in by_tool[at].most_common(5)]
    print(f"{at}: n={len(v)} end-context median {v[len(v) // 2]} p90 {v[int(len(v) * 0.9)]}; result share {top}")
print("cd prefix targets:", dict(cd))
print("API-call gaps:", {k: dict(v) for k, v in gaps.items()})
print("worktree-isolation refusals:", isolation)
