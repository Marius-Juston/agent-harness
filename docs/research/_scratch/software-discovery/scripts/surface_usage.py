"""Mine local Claude Code transcripts: how harness-ops agents actually use discovery surfaces.

Usage: python3 surface_usage.py [projects_dir]   (default ~/.claude/projects)
Reports, per harness-ops agent type: share of runs using `dev lit`, WebSearch, WebFetch, arxiv MCP, context7,
library-discovery actions, `uv add`; result sizes (chars) per surface; Skill tool calls in all transcripts.
"""

import collections
import json
import re
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".claude/projects")


def load(p):
    out = []
    for line in p.open(errors="ignore"):
        try:  # noqa: SIM105 - research script, kept as run
            out.append(json.loads(line))
        except Exception:  # noqa: S110 - research script, kept as run
            pass
    return out


def surface(name, inp):
    if name == "Bash":
        c = inp.get("command", "")
        if re.search(r"dev lit (search|get)", c):
            return "dev lit search/get"
        if "dev lit status" in c:
            return "dev lit status"
        if re.search(r"dev reuse", c):
            return "dev reuse"
        if re.search(
            r"gh (search repos|api[^|]*search/repositories)|pypi\.org|ecosyste\.ms|deps\.dev|libraries\.io", c
        ):
            return "bash library lookup"
        if re.search(r"\buv add\b", c):
            return "uv add"
        if re.search(r"curl[^|]*(openalex|semanticscholar|arxiv\.org|crossref)", c):
            return "bash raw lit API"
        return None
    if name == "WebFetch":
        u = inp.get("url", "")
        if re.search(r"openalex|semanticscholar|export\.arxiv|api\.crossref", u):
            return "WebFetch lit API (rule breach)"
        if re.search(r"pypi\.org|github\.com|libraries\.io|ecosyste|deps\.dev", u):
            return "WebFetch pypi/github"
        return "WebFetch other"
    if name == "WebSearch":
        return "WebSearch"
    if name.startswith("mcp__arxiv__"):
        return "arxiv MCP"
    if name.startswith("mcp__context7__"):
        return "context7 MCP"
    if name.startswith("mcp__wikipedia__"):
        return "wikipedia MCP"
    if name == "Skill":
        return "Skill:" + inp.get("skill", "?")
    if name == "ToolSearch":
        return "ToolSearch"
    return None


def result_len(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len(x.get("text", "")) if isinstance(x, dict) else len(str(x)) for x in c)
    return len(str(c))


runs = collections.defaultdict(list)  # agentType -> list of per-run Counter
sizes = collections.defaultdict(list)  # surface -> result chars
skill_calls = collections.Counter()  # (context, skill)
typed = collections.Counter()  # user-typed /commands in main sessions
for f in root.rglob("*.jsonl"):
    meta = f.with_suffix(".meta.json")
    atype = "main"
    if "subagents" in f.parts:
        try:
            atype = json.loads(meta.read_text()).get("agentType", "?")
        except Exception:
            atype = "?"
    recs = load(f)
    pending, cnt = {}, collections.Counter()
    for r in recs:
        msg = r.get("message") or {}
        content = msg.get("content")
        if r.get("type") == "user" and isinstance(content, str) and atype == "main":
            for m in re.finditer(r"<command-name>/?([\w:-]+)</command-name>", content):
                typed[m.group(1)] += 1
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                s = surface(b.get("name", ""), b.get("input") or {})
                if s:
                    cnt[s] += 1
                    pending[b.get("id")] = s
                    if s.startswith("Skill:"):
                        skill_calls[(atype if atype == "main" else "subagent", s[6:])] += 1
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                sizes[pending.pop(b["tool_use_id"])].append(result_len(b.get("content")))
    if recs:
        runs[atype].append(cnt)


def share(lst, pred):
    return f"{sum(1 for c in lst if pred(c))}/{len(lst)}"


for at in ["harness-ops:researcher", "harness-ops:builder", "harness-ops:research-auditor", "researcher", "builder"]:
    L = runs.get(at, [])
    if not L:
        continue
    print(f"\n## {at}: {len(L)} runs")
    print(
        " dev lit status:",
        share(L, lambda c: c["dev lit status"]),
        "| dev lit search/get:",
        share(L, lambda c: c["dev lit search/get"]),
        "| WebSearch:",
        share(L, lambda c: c["WebSearch"]),
        "| arxiv MCP:",
        share(L, lambda c: c["arxiv MCP"]),
        "| context7:",
        share(L, lambda c: c["context7 MCP"]),
        "| WebFetch lit API:",
        share(L, lambda c: c["WebFetch lit API (rule breach)"]),
        "| bash raw lit API:",
        share(L, lambda c: c["bash raw lit API"]),
    )
    print(
        " library discovery (bash lookup|WebFetch pypi/github|context7|dev reuse):",
        share(
            L, lambda c: c["bash library lookup"] or c["WebFetch pypi/github"] or c["context7 MCP"] or c["dev reuse"]
        ),
        "| uv add:",
        share(L, lambda c: c["uv add"]),
        "| uv add WITHOUT any discovery:",
        share(
            L,
            lambda c: (
                c["uv add"]
                and not (
                    c["bash library lookup"]
                    or c["WebFetch pypi/github"]
                    or c["context7 MCP"]
                    or c["WebSearch"]
                    or c["dev reuse"]
                )
            ),
        ),
    )
    tot = collections.Counter()
    for c in L:
        tot.update(c)
    print(" calls:", dict(tot.most_common(14)))
print("\n## result size per call (chars): n, median, p90")
for s, v in sorted(sizes.items(), key=lambda kv: -len(kv[1])):
    if len(v) >= 5 and not s.startswith("Skill:"):
        v = sorted(v)
        print(f" {s}: n={len(v)} median={statistics.median(v):.0f} p90={v[int(0.9 * (len(v) - 1))]}")
print("\n## Skill tool calls (model-invoked):", dict(skill_calls.most_common(20)))
print("## user-typed slash commands (main):", dict(typed.most_common(20)))
