"""Mine Claude Code transcripts for harness usage stats. Usage: python3 analyze.py <project_dir>... > out.json"""

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict


def blen(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        n = 0
        for b in c:
            if b.get("type") == "text":
                n += len(b.get("text", ""))
            elif b.get("type") == "image":
                n += 6000
            else:
                n += len(json.dumps(b))
        return n
    return 0


def ctext(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
    return ""


S = dict(
    tok=Counter(),
    tok_by_model=defaultdict(Counter),
    tok_by_agent=defaultdict(Counter),
    tools=Counter(),
    tools_by_agent=defaultdict(Counter),
    bash_first=Counter(),
    dev_cmds=Counter(),
    git_cmds=Counter(),
    gh_cmds=Counter(),
    result_chars_by_tool=Counter(),
    result_chars_dev=Counter(),
    errors_by_tool=Counter(),
    error_samples=defaultdict(Counter),
    hook_blocks=Counter(),
    read_repeat=Counter(),
    reads=0,
    reads_with_range=0,
    agents=Counter(),
    agent_models=Counter(),
    ctx_hist=Counter(),
    sessions=0,
    subagent_files=0,
    compactions=0,
    cost=0.0,
    cost_by_model=Counter(),
    skills=Counter(),
    per_agent_turns=defaultdict(list),
    per_agent_tokens=defaultdict(list),
    dev_errors=Counter(),
    cache_create_by_agent=Counter(),
    bash_chain=0,
    bash_total=0,
    cd_prefix=0,
    big_results=[],
    user_prompts=0,
    interrupts=0,
    permission_denied=0,
    slash=Counter(),
    agent_descs=Counter(),
    days=Counter(),
    webfetch_domains=Counter(),
    mcp=Counter(),
    turns_main=0,
    file_ext_read=Counter(),
    sleep_poll=0,
    grep_tool=0,
)


def classify_bash(cmd):
    c = cmd.strip()
    S["bash_total"] += 1
    if "&&" in c or ";" in c or "|" in c:
        S["bash_chain"] += 1
    if c.startswith("cd "):
        S["cd_prefix"] += 1
    for seg in re.split(r"&&|\|\||;|\|", c):
        seg = seg.strip()
        seg = re.sub(r"^(\w+=\S+\s+)+", "", seg)
        if not seg:
            continue
        w = seg.split()
        S["bash_first"][w[0]] += 1
        if w[0] in ("sleep",):
            S["sleep_poll"] += 1
        m = re.match(r"uv run (?:--\S+ )*dev\s+([\w-]+)(?:\s+([\w-]+))?", seg)
        if m:
            sub = m.group(1)
            if (
                sub
                in (
                    "board",
                    "code",
                    "reuse",
                    "lit",
                    "research",
                    "worktree",
                    "suite-audit",
                    "consolidation",
                    "quota",
                    "docs",
                    "test",
                )
                and m.group(2)
                and not m.group(2).startswith("-")
            ):
                sub += " " + m.group(2)
            S["dev_cmds"][sub] += 1
        elif w[0] == "git" and len(w) > 1:
            k = w[1] if not w[1].startswith("-") else (w[3] if len(w) > 3 else w[1])
            S["git_cmds"][k] += 1
        elif w[0] == "gh" and len(w) > 2:
            S["gh_cmds"][w[1] + " " + w[2]] += 1
        elif w[0] == "uv" and len(w) > 2:
            S["bash_first"]["uv " + w[1] + " " + w[2]] += 1
    return S["dev_cmds"]


def process(f, is_sub, agent_type):
    seen = set()
    out_seen = {}
    pending = {}
    reads = Counter()
    turns = 0
    toks = Counter()
    with open(f, errors="ignore") as fh:
        lines = fh.readlines()
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:  # a partly written last line
            continue
        t = d.get("type")
        if t == "cost-state" and not is_sub:
            S["cost"] = S["cost"]  # summed per session below
            S.setdefault("_cost", {})[d["sessionId"]] = d
        if t == "system" and d.get("subtype") == "compact_boundary":
            S["compactions"] += 1
        if t == "assistant":
            m = d["message"]
            mid = m.get("id")
            ts = d.get("timestamp", "")[:10]
            for b in m.get("content", []):
                if b.get("type") == "tool_use":
                    name = b["name"]
                    inp = b.get("input", {})
                    S["tools"][name] += 1
                    S["tools_by_agent"][agent_type][name] += 1
                    pending[b["id"]] = (name, inp)
                    if name == "Bash":
                        classify_bash(inp.get("command", ""))
                    elif name == "Read":
                        S["reads"] += 1
                        p = inp.get("file_path", "")
                        reads[p] += 1
                        S["file_ext_read"][os.path.splitext(p)[1] or os.path.basename(p)] += 1
                        if inp.get("offset") or inp.get("limit"):
                            S["reads_with_range"] += 1
                    elif name in ("Agent", "Task"):
                        S["agents"][inp.get("subagent_type", "general-purpose")] += 1
                        S["agent_models"][inp.get("model", "inherit")] += 1
                    elif name == "Skill":
                        S["skills"][inp.get("skill")] += 1
                    elif name == "WebFetch":
                        mm = re.match(r"https?://([^/]+)", inp.get("url", ""))
                        S["webfetch_domains"][mm.group(1) if mm else "?"] += 1
                    elif name.startswith("mcp__"):
                        S["mcp"][name.split("__")[1]] += 1
                    elif name == "Grep":
                        S["grep_tool"] += 1
            u = m.get("usage") or {}
            if mid in seen:
                # streamed blocks of one message repeat its usage; only output_tokens grows, so add the increase
                extra = u.get("output_tokens", 0) - out_seen.get(mid, 0)
                if extra > 0:
                    out_seen[mid] += extra
                    for bucket in (S["tok"], S["tok_by_model"][m.get("model")], S["tok_by_agent"][agent_type], toks):
                        bucket["out"] += extra
                continue
            seen.add(mid)
            if not u:
                continue
            out_seen[mid] = u.get("output_tokens", 0)
            turns += 1
            if ts:
                S["days"][ts] += 1
            k = dict(
                inp=u.get("input_tokens", 0),
                cw=u.get("cache_creation_input_tokens", 0),
                cr=u.get("cache_read_input_tokens", 0),
                out=u.get("output_tokens", 0),
            )
            for a, v in k.items():
                S["tok"][a] += v
                S["tok_by_model"][m.get("model")][a] += v
                S["tok_by_agent"][agent_type][a] += v
                toks[a] += v
            ctx = k["inp"] + k["cw"] + k["cr"]
            S["ctx_hist"][min(ctx // 25000 * 25, 1000)] += 1
        if t == "user":
            c = d["message"].get("content")
            if isinstance(c, str) and not is_sub:
                S["user_prompts"] += 1
                mm = re.match(r"<command-name>(/\S+)</command-name>", c)
                if mm:
                    S["slash"][mm.group(1)] += 1
                if "Request interrupted" in c:
                    S["interrupts"] += 1
            if isinstance(c, list):
                for b in c:
                    if b.get("type") != "tool_result":
                        continue
                    name, inp = pending.get(b.get("tool_use_id"), ("?", {}))
                    n = blen(b.get("content"))
                    S["result_chars_by_tool"][name] += n
                    txt = ctext(b.get("content")) if not isinstance(b.get("content"), str) else b["content"]
                    if name == "Bash":
                        mm = re.search(r"uv run (?:--\S+ )*dev\s+([\w-]+)", inp.get("command", ""))
                        if mm:
                            S["result_chars_dev"][mm.group(1)] += n
                    if n > 40000:
                        S["big_results"].append((n, name, json.dumps(inp)[:150], os.path.basename(f)))
                    if b.get("is_error"):
                        S["errors_by_tool"][name] += 1
                        first = re.sub(r"\d+", "N", txt.strip().split("\n")[0][:110])
                        S["error_samples"][name][first] += 1
                        if "hook" in txt.lower() and ("block" in txt.lower() or "denied" in txt.lower()):
                            S["hook_blocks"][first] += 1
                        if "doesn't want to proceed" in txt or "permission" in txt.lower():
                            S["permission_denied"] += 1
                        if name == "Bash":
                            mm = re.search(r"uv run (?:--\S+ )*dev\s+([\w-]+)", inp.get("command", ""))
                            if mm:
                                S["dev_errors"][mm.group(1)] += 1
    S["read_repeat"][max(reads.values()) if reads else 0] += 1
    S["_dupreads"] = S.get("_dupreads", 0) + sum(v - 1 for v in reads.values() if v > 1)
    S["per_agent_turns"][agent_type].append(turns)
    S["per_agent_tokens"][agent_type].append(sum(toks.values()))
    S["cache_create_by_agent"][agent_type] += toks["cw"]


for proj in sys.argv[1:]:
    for f in glob.glob(os.path.join(proj, "*.jsonl")):
        S["sessions"] += 1
        process(f, False, "main")
    for f in glob.glob(os.path.join(proj, "*", "subagents", "*.jsonl")):
        S["subagent_files"] += 1
        meta = f[:-6] + ".meta.json"
        at = "unknown"
        if os.path.exists(meta):
            with open(meta) as fh:
                at = json.load(fh).get("agentType", "unknown")
        process(f, True, at)

cost = S.pop("_cost", {})
S["cost"] = sum(c["totalCostUSD"] for c in cost.values())
for c in cost.values():
    for mname, mu in c.get("modelUsage", {}).items():
        S["cost_by_model"][mname] += mu.get("costUSD", 0)
S["lines_added"] = sum(c.get("totalLinesAdded", 0) for c in cost.values())
S["big_results"] = sorted(S["big_results"], reverse=True)[:25]


def summ(v):
    v = sorted(v)
    return dict(n=len(v), med=v[len(v) // 2] if v else 0, p90=v[int(len(v) * 0.9)] if v else 0, sum=sum(v))


S["per_agent_turns"] = {k: summ(v) for k, v in S["per_agent_turns"].items()}
S["per_agent_tokens"] = {k: summ(v) for k, v in S["per_agent_tokens"].items()}
S["error_samples"] = {k: v.most_common(12) for k, v in S["error_samples"].items()}
out = {
    k: (
        v.most_common(60)
        if isinstance(v, Counter)
        else (
            {kk: (vv.most_common(25) if isinstance(vv, Counter) else vv) for kk, vv in v.items()}
            if isinstance(v, dict)
            else v
        )
    )
    for k, v in S.items()
}
json.dump(out, sys.stdout, indent=1, default=str)
