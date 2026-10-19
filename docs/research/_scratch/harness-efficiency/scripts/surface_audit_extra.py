"""Follow-up probes for the surface audit (round 2). Usage: python3 -I surface_audit_extra.py <transcript-dir>

1. batching and `dev code` use with vs without HARNESS.md in the agent's loaded instructions (same agent type);
2. auto-mode "Bash-first" steer text present in subagent context;
3. curl/WebFetch targets that hit the shared literature APIs;
4. builder `dev check` sequences: result of each run, and what came between two runs;
5. hand-back size in characters;
6. skill listing composition (which skills, chars each);
7. read-before-edit errors: was the file seen through Bash (cat/sed/grep/head) before the failed edit?
8. commands refused by worktree isolation (first word after any cd);
9. unknown agent type names the lead asked for.
Window: files whose first timestamp is before 2026-10-17.
"""

import glob
import json
import re
import statistics as st
import sys
from collections import Counter, defaultdict

ROOT = sys.argv[1]
BEFORE = "2026-10-17"
CD = re.compile(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*(&&|;)\s*")


def recs(f):
    out = []
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def atype(f):
    try:
        with open(f[:-6] + ".meta.json") as fh:
            return json.load(fh).get("agentType", "?").replace("harness-ops:", "")
    except OSError:
        return "?"


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


def strip_cd(cmd):
    while m := CD.match(cmd):
        cmd = cmd[m.end() :]
    return cmd


batch = defaultdict(lambda: defaultdict(Counter))  # agent -> harness? -> counter
bashfirst = Counter()
lit_hits = Counter()
check_seq = Counter()
check_res = Counter()
hb_chars = defaultdict(list)
skills_chars = Counter()
skills_seen = Counter()
rbe = Counter()
iso = Counter()
iso_samples = []
unknown = Counter()
lit_samples = []

for f in glob.glob(ROOT + "/*.jsonl") + glob.glob(ROOT + "/*/subagents/*.jsonl"):
    sub = "/subagents/" in f
    ag = atype(f) if sub else "lead"
    R = recs(f)
    ts = next((r["timestamp"] for r in R if r.get("timestamp")), None)
    if not ts or ts >= BEFORE:
        continue
    has_harness, listing_done = None, False
    uses, msgs = {}, defaultdict(list)
    seen_paths = set()  # paths printed through Bash
    events = []  # builder check sequence: ('check', id) / ('commit',) / ('edit',)
    handback = None
    blob = ""
    for r in R:
        t = r.get("type")
        if t == "attachment":
            a = r["attachment"]
            if a.get("type") == "instructions" and has_harness is None:
                has_harness = any(fl.get("path", "").endswith("HARNESS.md") for fl in a.get("files", []))
            if a.get("type") == "skill_listing" and not listing_done and sub:
                listing_done = True
                for line in a.get("content", "").split("\n- "):
                    name = line.lstrip("- ").split(":")[0] if ": " in line else line[:30]
                    if line.lstrip("- ").startswith(("anthropic-skills:", "harness-ops:", "astral:")):
                        name = ":".join(line.lstrip("- ").split(":")[:2])
                    skills_chars[name] += len(line)
                    skills_seen[name] += 1
        if t == "user" and sub and not blob:
            blob = text_of(r.get("message", {}).get("content"))
        if t == "user":
            c = r.get("message", {}).get("content")
            s = text_of(c) if isinstance(c, list) else (c or "")
            if "read files with cat, head, or sed -n" in s:
                bashfirst[ag] += 1
            if not isinstance(c, list):
                continue
            for b in c:
                if b.get("type") != "tool_result":
                    continue
                name, inp = uses.get(b.get("tool_use_id"), ("?", {}))
                body = text_of(b.get("content"))
                if name == "Bash" and inp.get("_check"):
                    ok = "RESULT: passed" in body
                    check_res[(ag, ok)] += 1
                    events.append(("check", ok))
                if not b.get("is_error"):
                    continue
                low = body.lower()
                if "has not been read yet" in low:
                    p = inp.get("file_path", "")
                    rbe[(ag, "seen via bash" if p and p in seen_paths else "not seen via bash")] += 1
                if "isolated in the worktree" in low and name == "Bash":
                    cmd = strip_cd(inp.get("command", ""))
                    w = cmd.split()[:2]
                    iso[(ag, " ".join(w[:2]) if w and w[0] in ("git", "uv", "gh") else (w[0] if w else "?"))] += 1
                    if len(iso_samples) < 6:
                        iso_samples.append((ag, inp.get("command", "")[:160].replace("\n", " ")))
                if name == "Agent" and "agent type" in low:
                    unknown[str(inp.get("subagent_type"))] += 1
        if t == "assistant":
            m = r.get("message", {})
            mid = m.get("id")
            for b in m.get("content") or []:
                if b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name"), dict(b.get("input") or {})
                if name == "SubagentHandback":
                    handback = inp.get("message", "")
                if name == "Bash":
                    cmd = inp.get("command", "")
                    head = strip_cd(cmd)
                    if re.search(r"\bdev check\b", cmd):
                        inp["_check"] = True
                    if re.search(r"\bgit commit\b", cmd):
                        events.append(("commit",))
                    for p in re.findall(r"(/[\w./-]+\.\w+)", cmd):
                        if re.match(r"^(cat|sed|grep|rg|head|tail|awk|nl)\b", head):
                            seen_paths.add(p)
                    if re.search(r"(openalex\.org|semanticscholar\.org|crossref\.org|europepmc)", cmd):
                        lit_hits[(ag, "Bash:" + ("curl" if "curl" in cmd else "other"))] += 1
                        if len(lit_samples) < 4:
                            lit_samples.append(cmd[:160].replace("\n", " "))
                if name == "WebFetch" and re.search(
                    r"(openalex|semanticscholar|crossref|europepmc)", inp.get("url", "")
                ):
                    lit_hits[(ag, "WebFetch")] += 1
                if name in ("Edit", "Write"):
                    events.append(("edit",))
                if name == "Read":
                    seen_paths.add(inp.get("file_path", ""))
                uses[b.get("id")] = (name, inp)
                kind = (
                    "ro"
                    if name in ("Read", "Grep", "Glob")
                    or (
                        name == "Bash"
                        and re.match(
                            r"^(cat|sed -n|grep|rg|head|tail|ls|find|git (log|diff|show|status))\b|^uv run (--quiet )?dev code\b",
                            strip_cd(inp.get("command", "")),
                        )
                    )
                    else name
                )
                msgs[mid].append(kind)
                if name == "Bash" and re.match(r"^uv run (--quiet )?dev code\b", strip_cd(inp.get("command", ""))):
                    batch[ag][has_harness]["dev_code"] += 1
                if name == "Bash" and re.match(r"^(sed -n|cat|grep|rg)\b", strip_cd(inp.get("command", ""))):
                    batch[ag][has_harness]["sed_cat_grep"] += 1
    for v in msgs.values():
        batch[ag][has_harness]["responses"] += 1
        batch[ag][has_harness]["parallel"] += len(v) > 1
    batch[ag][has_harness]["runs"] += 1
    if sub and handback:
        hb_chars[ag].append(len(handback))
    if ag == "builder":
        # what happened between consecutive checks
        prev = None
        between = set()
        for e in events:
            if e[0] == "check":
                if prev is not None:
                    key = (
                        "after "
                        + ("pass" if prev else "fail")
                        + ": "
                        + (
                            "edit+commit"
                            if {"edit", "commit"} <= between
                            else "commit only"
                            if "commit" in between
                            else "edit only"
                            if "edit" in between
                            else "nothing"
                        )
                    )
                    check_seq[key] += 1
                prev, between = e[1], set()
            else:
                between.add(e[0])

print("## 1. HARNESS.md loaded vs not (same agent type)")
for ag, d in batch.items():
    for h, c in d.items():
        if c["runs"] >= 5:
            print(
                f"{ag:18} harness={h!s:5} runs={c['runs']:4} parallel={100 * c['parallel'] / max(c['responses'], 1):5.1f}% "
                f"dev_code={c['dev_code']:4} sed/cat/grep={c['sed_cat_grep']:5} dev_code_share="
                f"{100 * c['dev_code'] / max(c['dev_code'] + c['sed_cat_grep'], 1):4.1f}%"
            )
print("\n## 2. Bash-first steer text seen (records)", dict(bashfirst))
print("\n## 3. literature API hits outside dev lit", dict(lit_hits))
for s in lit_samples:
    print("   ", s)
print("\n## 4. builder dev check results", dict(check_res))
for k, v in check_seq.most_common():
    print("  ", v, k)
print("\n## 5. hand-back chars")
for ag, v in hb_chars.items():
    if len(v) >= 5:
        v.sort()
        print(f"  {ag:18} n={len(v)} median={st.median(v):.0f} p90={v[int(len(v) * 0.9)]}")
print("\n## 6. skill listing (subagents): name, times listed, total chars -> chars per listing")
for k, v in skills_chars.most_common(40):
    print(f"  {k[:45]:45} {skills_seen[k]:4} {v // max(skills_seen[k], 1):5}")
print("\n## 7. read-before-edit errors by whether the file was printed through Bash first", dict(rbe))
print("\n## 8. isolation refusals by command", iso.most_common(12))
for s in iso_samples:
    print("   ", s)
print("\n## 9. unknown agent types asked for", dict(unknown))
