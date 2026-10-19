"""Rule compliance and fixed context cost per agent type, from Claude Code transcripts (round 2, surface audit).

Usage: python3 -I surface_audit.py <project-transcript-dir> [--before 2026-10-17] > out.txt
Reads <dir>/*.jsonl (lead sessions) and <dir>/*/subagents/*.jsonl (+ .meta.json agentType). Only files whose first
timestamp is before --before count (the repo-E window 2026-10-11..16). Stdlib only.
"""

import contextlib
import glob
import itertools
import json
import os
import re
import shlex
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_E = os.environ.get("REPO_E", "repo-E")  # the private consuming repository, by code name

ARGS = sys.argv[1:]
ROOT = ARGS[0]
BEFORE = ARGS[ARGS.index("--before") + 1] if "--before" in ARGS else "2026-10-17"

CD = re.compile(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*(&&|;)\s*")
READONLY_BASH = re.compile(
    r"^(cat|sed -n|grep|rg|head|tail|ls|find|wc|git (log|diff|show|status|branch)|gh (pr|issue) (view|diff|list)|"
    r"uv run (--quiet )?dev (code|board (brief|list|show)|lit (search|get)|status|orient))\b"
)
LIMITS = {
    "builder": 25,
    "researcher": 20,
    "tester": 20,
    "reviewer": 25,
    "research-auditor": 30,
    "architect": 25,
    "suite-auditor": 25,
}


def recs(f):
    out = []
    for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            out.append(json.loads(line))
    return out


def atype(f):
    try:
        t = json.loads(Path(f[:-6] + ".meta.json").read_text()).get("agentType", "?")
    except OSError:
        t = "?"
    t = t.replace("harness-ops:", "")
    return "researcher" if t == "researcher" else t


def strip_cd(cmd):
    n = 0
    while m := CD.match(cmd):
        cmd, n = cmd[m.end() :], n + 1
    return cmd, n


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            b.get("text", "")
            if b.get("type") == "text"
            else (text_of(b.get("content")) if b.get("type") == "tool_result" else "")
            for b in c
        )
    return ""


def whole_read(cmd):
    """read_guard.whole_read (v0.1.9): (path, lines asked; None = all) for a lone cat/head, else None."""
    if any(ch in cmd for ch in "|;&<>()$`\n#*?"):
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    if len(argv) == 2 and argv[0] == "cat":
        return argv[1], None
    return None


def cap_for(path):
    if path.endswith((".py", ".ts", ".tsx", ".js", ".rs", ".go")):
        return 300
    if path.endswith(".md") and not path.endswith(("SKILL.md", "CLAUDE.md")) and "/.claude/agents/" not in path:
        return 400
    return None


files = [(f, "lead") for f in glob.glob(ROOT + "/*.jsonl")]
files += [(f, atype(f)) for f in glob.glob(ROOT + "/*/subagents/*.jsonl")]

A = defaultdict(lambda: defaultdict(list))  # agent -> metric -> list
C = defaultdict(Counter)  # agent -> counter
G = Counter()  # global
samples = defaultdict(list)

for f, ag in files:
    R = recs(f)
    ts = next((r["timestamp"] for r in R if r.get("timestamp")), None)
    if not ts or ts >= BEFORE:
        continue
    C[ag]["runs"] += 1
    first_usage, comp, seen_comp = None, Counter(), set()
    uses = {}  # tool_use id -> (name, input)
    msg_tools = defaultdict(list)  # message id -> tool names/kinds, in order
    msg_order = []
    last_text, handback = "", None
    n_check = n_affected = n_reuse = n_litsearch = n_litstatus = 0
    litstatus_before_search = None
    for r in R:
        t = r.get("type")
        if t == "attachment":
            a = r["attachment"]
            at = a.get("type")
            if first_usage is None and at not in seen_comp:
                seen_comp.add(at)
                if at == "prompt_snapshot":
                    sp = a.get("systemPrompt") or []
                    comp["system_prompt"] += sum(len(s) for s in sp)
                    if ag != "lead" and sp:
                        comp["agent_body"] += len(sp[0])
                        A[ag]["agent_body_words"].append(len(sp[0].split()))
                elif at == "instructions":
                    for fl in a.get("files", []):
                        p = fl.get("path", "")
                        key = "instr:" + (
                            "HARNESS.md"
                            if p.endswith("HARNESS.md")
                            else "CLAUDE.md(project)"
                            if p.endswith(f"{REPO_E}/CLAUDE.md")
                            or (f"/{REPO_E}-worktrees/" in p and p.endswith("CLAUDE.md"))
                            else "MEMORY.md"
                            if p.endswith("MEMORY.md")
                            else "user-rules"
                            if "/.claude/rules/" in p
                            else p.split("/")[-1]
                        )
                        comp[key] += len(fl.get("content", ""))
                elif at in (
                    "skill_listing",
                    "deferred_tools_delta",
                    "mcp_instructions_delta",
                    "agent_listing_delta",
                    "hook_additional_context",
                    "environment",
                    "session_context",
                    "deferred_tools_record",
                ):
                    comp[at] += len(json.dumps(a))
            if at == "hook_additional_context":
                hn = a.get("hookName", "")
                body = "".join(a.get("content") or [])
                C[ag]["hookctx:" + hn] += 1
                C[ag]["hookctx_chars:" + hn] += len(body)
                if body.startswith("read-guard"):
                    C[ag]["read_guard_hit:" + hn.split(":")[-1]] += 1
        elif t == "assistant":
            m = r.get("message", {})
            u = m.get("usage")
            if u and first_usage is None:
                first_usage = (
                    u.get("input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)
                    + u.get("cache_read_input_tokens", 0)
                )
            mid = m.get("id") or r.get("uuid")
            if mid not in msg_tools:
                msg_order.append(mid)
                msg_tools[mid]
            for b in m.get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    last_text = b["text"]
                if b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name"), b.get("input") or {}
                uses[b.get("id")] = (name, inp)
                C[ag]["tool:" + name] += 1
                kind = name
                if name == "SubagentHandback":
                    handback = inp.get("message", "")
                if name in ("Edit", "Write", "NotebookEdit"):
                    C[ag]["edits"] += 1
                if name == "Skill":
                    C[ag]["skill:" + str(inp.get("skill"))] += 1
                if name == "Agent":
                    C[ag]["spawn:" + str(inp.get("subagent_type"))] += 1
                    if inp.get("isolation"):
                        C[ag]["spawn_isolation"] += 1
                if name == "Read":
                    C[ag]["read_with_range" if (inp.get("limit") or inp.get("offset")) else "read_whole"] += 1
                    if inp.get("allow_large"):
                        C[ag]["read_allow_large"] += 1
                if name == "Bash":
                    cmd = inp.get("command", "") or ""
                    C[ag]["bash"] += 1
                    rest, ncd = strip_cd(cmd)
                    if ncd:
                        C[ag]["bash_cd"] += 1
                    if re.search(r"&&|\|\||;|\|", cmd):
                        C[ag]["bash_compound"] += 1
                    head = rest.strip()
                    for lab, rx in [
                        ("nav:dev code", r"^uv run (--quiet )?dev code\b"),
                        ("nav:sed -n", r"^sed -n"),
                        ("nav:cat", r"^cat\b"),
                        ("nav:grep/rg", r"^(grep|rg)\b"),
                        ("nav:head/tail", r"^(head|tail)\b"),
                        ("nav:find/ls", r"^(find|ls)\b"),
                    ]:
                        if re.match(rx, head):
                            C[ag][lab] += 1
                            break
                    if READONLY_BASH.match(head):
                        kind = "ro"
                    if re.search(r"\buv run (--quiet )?dev check\b", cmd):
                        n_check += 1
                    if re.search(r"\bdev test\b.*--affected", cmd):
                        n_affected += 1
                    if re.search(r"\bdev reuse\b", cmd):
                        n_reuse += 1
                    if re.search(r"\bdev lit status\b", cmd):
                        n_litstatus += 1
                    if re.search(r"\bdev lit search\b", cmd):
                        if litstatus_before_search is None:
                            litstatus_before_search = n_litstatus > 0
                        n_litsearch += 1
                    if re.search(r"\bsleep\s+\d", cmd) or re.search(r"\buntil\b", cmd):
                        C[ag]["poll_sleep_until"] += 1
                    if re.search(r"\bdev --help\b|\bdev \w+ --help\b", cmd):
                        C[ag]["dev_help"] += 1
                    if re.search(r"multi-agent\.md|agent-surface\.md|HARNESS\.md", cmd):
                        C[ag]["harness_doc_read_bash"] += 1
                    if re.search(r"curl[^|]*(openalex|semanticscholar|crossref|europepmc)", cmd):
                        C[ag]["lit_api_curl"] += 1
                    if re.search(r"\bdev board claim\b", cmd):
                        C[ag]["board_claim"] += 1
                    wr = whole_read(head)
                    if wr and ncd:
                        C[ag]["cd_cat_lone"] += 1
                        uses[b.get("id")] = (name, dict(inp, _cdcat=wr[0]))
                    elif wr:
                        uses[b.get("id")] = (name, dict(inp, _cat=wr[0]))
                if name in ("Read", "Grep", "Glob") or (name == "WebFetch") or name.startswith("mcp__arxiv__get"):
                    kind = "ro"
                msg_tools[mid].append(kind)
        elif t == "user":
            m = r.get("message", {})
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get("type") != "tool_result":
                    continue
                name, inp = uses.get(b.get("tool_use_id"), ("?", {}))
                body = text_of(b.get("content"))
                if "_cdcat" in inp or "_cat" in inp:
                    path = inp.get("_cdcat") or inp.get("_cat")
                    cap = cap_for(path)
                    if cap and body.count("\n") > cap:
                        C[ag]["long_cat_bypass" if "_cdcat" in inp else "long_cat_plain_unrewritten"] += 1
                        C[ag]["long_cat_chars"] += len(body)
                if not b.get("is_error"):
                    continue
                C[ag]["err:" + name] += 1
                low = body.lower()
                for lab, needle in [
                    ("read_before_edit", "has not been read yet"),
                    ("isolation_refusal", "isolated in the worktree"),
                    ("report_file", "should return findings as text"),
                    ("unknown_agent", "agent type"),
                    ("project_rule_block", "blocked by project rule"),
                    ("file_modified_since_read", "modified since read"),
                    ("string_not_found", "string to replace not found"),
                    ("concurrent_limit", "concurrent"),
                    ("quota_gate", "quota gate"),
                ]:
                    if needle in low:
                        if lab == "unknown_agent" and name != "Agent":
                            continue
                        C[ag]["e:" + lab] += 1
                        if len(samples[lab]) < 3:
                            samples[lab].append(body[:200].replace("\n", " "))
                        if lab == "project_rule_block":
                            k = body.split("Blocked by project rule:")[-1].strip()[:70]
                            G["block:" + k] += 1
    # per-run summaries
    if first_usage:
        A[ag]["first_ctx"].append(first_usage)
    for k, v in comp.items():
        A[ag]["c:" + k].append(v)
    multi = [len(v) for v in msg_tools.values() if v]
    A[ag]["responses"].append(len(multi))
    A[ag]["tool_calls"].append(sum(multi))
    A[ag]["parallel_resp"].append(sum(1 for x in multi if x > 1))
    # chained single read-only calls: a response with one read-only call right after another such response
    seq = [msg_tools[mid] for mid in msg_order if msg_tools[mid]]
    chained = sum(1 for p, q in itertools.pairwise(seq) if p == ["ro"] and q == ["ro"])
    A[ag]["chained_ro"].append(chained)
    if ag != "lead":
        hb = handback if handback is not None else last_text
        if hb:
            A[ag]["handback_lines"].append(len([x for x in hb.splitlines() if x.strip()]))
            C[ag]["handback_tool" if handback is not None else "handback_text"] += 1
        C[ag]["checks_runs_ge1"] += n_check >= 1
        C[ag]["checks_runs_gt1"] += n_check > 1
        A[ag]["checks_per_run"].append(n_check)
        C[ag]["affected_runs"] += n_affected >= 1
        C[ag]["reuse_runs"] += n_reuse >= 1
        if litstatus_before_search is not None:
            C[ag]["lit_runs"] += 1
            C[ag]["lit_status_first"] += litstatus_before_search


def med(x):
    return st.median(x) if x else 0


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


order = sorted(C, key=lambda a: -C[a]["runs"])
print(f"# window: first timestamp < {BEFORE}; runs per agent type")
for ag in order:
    c, a = C[ag], A[ag]
    if c["runs"] < 3:
        continue
    print(f"\n## {ag}  runs={c['runs']}")
    print(
        f"first-turn context tokens: median {med(a['first_ctx']):.0f}, p10 "
        f"{sorted(a['first_ctx'])[len(a['first_ctx']) // 10] if a['first_ctx'] else 0}, max {max(a['first_ctx'] or [0])}"
    )
    comps = sorted((k for k in a if k.startswith("c:")), key=lambda k: -med(a[k]))
    print(
        "first-turn components (median chars, runs present): "
        + "; ".join(f"{k[2:]} {med(a[k]):.0f} ({len(a[k])})" for k in comps)
    )
    if a["agent_body_words"]:
        print(f"agent body words (as loaded): median {med(a['agent_body_words']):.0f}")
    resp, calls = sum(a["responses"]), sum(a["tool_calls"])
    print(
        f"responses with tools {resp}, calls {calls}, calls/response {calls / max(resp, 1):.2f}, "
        f"parallel responses {pct(sum(a['parallel_resp']), resp)}, chained single read-only {pct(sum(a['chained_ro']), resp)}"
    )
    print(
        f"bash {c['bash']}: cd-prefixed {pct(c['bash_cd'], c['bash'])}, compound {pct(c['bash_compound'], c['bash'])}; "
        f"poll sleep/until {c['poll_sleep_until']}; dev --help {c['dev_help']}; harness-doc reads via bash {c['harness_doc_read_bash']}"
    )
    nav = {k[4:]: v for k, v in c.items() if k.startswith("nav:")}
    nav["Read tool"] = c["tool:Read"]
    print(
        "navigation: "
        + ", ".join(f"{k} {v}" for k, v in sorted(nav.items(), key=lambda kv: -kv[1]))
        + f"; Read ranged {c['read_with_range']} / whole {c['read_whole']} / allow_large {c['read_allow_large']}"
    )
    print(
        f"read_guard rewrites: Read {c['read_guard_hit:Read']}, Bash {c['read_guard_hit:Bash']}; long-file `cd X && cat` "
        f"passed {c['long_cat_bypass']}; plain cat of long file not rewritten {c['long_cat_plain_unrewritten']}; chars {c['long_cat_chars']}"
    )
    print(
        f"edits {c['edits']}: read-before-edit errors {c['e:read_before_edit']} ({pct(c['e:read_before_edit'], c['edits'])}), "
        f"string-not-found {c['e:string_not_found']}, modified-since-read {c['e:file_modified_since_read']}"
    )
    print(
        f"isolation refusals {c['e:isolation_refusal']}; report-file refusals {c['e:report_file']}; project-rule blocks "
        f"{c['e:project_rule_block']}; unknown agent {c['e:unknown_agent']}; concurrent {c['e:concurrent_limit']}; quota {c['e:quota_gate']}"
    )
    if ag != "lead":
        hb = a["handback_lines"]
        lim = LIMITS.get(ag)
        over = sum(1 for x in hb if lim and x > lim)
        print(
            f"hand-back non-empty lines: median {med(hb):.0f}, p90 {sorted(hb)[int(len(hb) * 0.9)] if hb else 0}, "
            f"limit {lim}, over {over}/{len(hb)} ({pct(over, len(hb))}); via SubagentHandback {c['handback_tool']}"
        )
        print(
            f"dev check: runs with >=1 {c['checks_runs_ge1']}, with >1 {c['checks_runs_gt1']} (median per run "
            f"{med(a['checks_per_run'])}); runs using test --affected {c['affected_runs']}; runs using dev reuse {c['reuse_runs']}; "
            f"board claim calls {c['board_claim']}"
        )
        if c["lit_runs"]:
            print(
                f"lit: runs searching {c['lit_runs']}, lit status before first search {c['lit_status_first']} "
                f"({pct(c['lit_status_first'], c['lit_runs'])}); curl to lit APIs {c['lit_api_curl']}"
            )
    hk = {k: v for k, v in c.items() if k.startswith("hookctx:")}
    if hk:
        print(
            "hook context injections: "
            + ", ".join(
                f"{k[8:]} {v} (median-ish chars {c['hookctx_chars:' + k[8:]] // max(v, 1)})" for k, v in hk.items()
            )
        )
    sk = {k: v for k, v in c.items() if k.startswith("skill:")}
    if sk:
        print("skills: " + ", ".join(f"{k[6:]} {v}" for k, v in sk.items()))

print("\n## project-rule blocks by message (all agents)")
for k, v in G.most_common(15):
    print(v, k)
print("\n## error samples")
for k, v in samples.items():
    print(k, "|", v[0])
