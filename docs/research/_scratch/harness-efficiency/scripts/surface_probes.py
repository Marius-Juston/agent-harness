"""Probes behind r2-surface-audit.md F2, F3, F4, F8, F9. Usage: python3 -I surface_probes.py <transcript-dir>

Window: files whose first timestamp is before 2026-10-17. Prints:
- auto-mode (Bash-first) attachment presence per agent type;
- isolation refusals: runs with >=1 per type, by cause (cross-repo cd, scratchpad, other), and `dev pr --summary`
  heredoc calls by form (plain / piped, with or without cd) and outcome;
- builder `dev check` calls per run and passes;
- direct literature-API calls by researchers/auditors, split by whether the loaded agent body had the "never curl" rule;
- builder runs with `git branch -m`; `dev` output piped to tail/head/grep per type.
"""

import glob
import json
import re
import statistics as st
import sys
from collections import Counter, defaultdict

ROOT = sys.argv[1]
LIT = re.compile(
    r"(curl|wget|requests\.get|urllib)[^\n]*https?://(api\.)?(openalex\.org|api\.semanticscholar\.org|"
    r"api\.crossref\.org|www\.ebi\.ac\.uk/europepmc)"
)
LIT_URL = re.compile(r"(api\.openalex|api\.semanticscholar|api\.crossref|ebi\.ac\.uk/europepmc)")


def body_of(b):
    c = b.get("content")
    return c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))


automode, runs, iso_runs, iso_cause, prform = Counter(), Counter(), Counter(), Counter(), Counter()
checks, passes = defaultdict(list), defaultdict(list)
lit = Counter()
branch_m, piped = Counter(), Counter()
for f in glob.glob(ROOT + "/*.jsonl") + glob.glob(ROOT + "/*/subagents/*.jsonl"):
    sub = "/subagents/" in f
    try:
        with open(f[:-6] + ".meta.json") as fh:
            ag = json.load(fh).get("agentType", "?").replace("harness-ops:", "") if sub else "lead"
    except OSError:
        ag = "lead"
    with open(f, encoding="utf-8", errors="replace") as fh:
        R = [json.loads(x) for x in fh if x.strip()]
    ts = next((r["timestamp"] for r in R if r.get("timestamp")), None)
    if not ts or ts >= "2026-10-17":
        continue
    runs[ag] += 1
    agent_body = next(
        (
            r["attachment"]["systemPrompt"][0]
            for r in R
            if r.get("type") == "attachment"
            and r["attachment"].get("type") == "prompt_snapshot"
            and r["attachment"].get("systemPrompt")
        ),
        "",
    )
    uses, had_iso, n_check, n_pass, n_lit, did_m = {}, False, 0, 0, 0, False
    has_auto = False
    for r in R:
        t = r.get("type")
        if t == "attachment" and r["attachment"].get("type") == "auto_mode" and r["attachment"].get("bashFirst"):
            has_auto = True
        if t == "assistant":
            for b in r["message"].get("content") or []:
                if b.get("type") != "tool_use":
                    continue
                uses[b["id"]] = b
                inp = b.get("input") or {}
                cmd = inp.get("command", "") if b["name"] == "Bash" else ""
                if (b["name"] == "Bash" and LIT.search(cmd)) or (
                    b["name"] == "WebFetch" and LIT_URL.search(inp.get("url", ""))
                ):
                    n_lit += 1
                if "git branch -m" in cmd:
                    did_m = True
                if re.search(r"\bdev (check|test|pr|lit search|board brief)\b[^\n|]*\|\s*(tail|head|grep)", cmd):
                    piped[ag] += 1
        elif t == "user" and isinstance(r["message"].get("content"), list):
            for b in r["message"]["content"]:
                if b.get("type") != "tool_result":
                    continue
                u = uses.get(b.get("tool_use_id"), {})
                cmd = (u.get("input") or {}).get("command", "") or ""
                text = body_of(b)
                refused = "isolated in the worktree" in text
                if refused:
                    had_iso = True
                    cause = (
                        "cd other repo"
                        if re.match(r"\s*cd\s+\S*agent-harness", cmd)
                        else "scratchpad /tmp"
                        if "/tmp/claude" in cmd  # noqa: S108 (a match on command text, not a file)
                        else "other"
                    )
                    iso_cause[(ag, cause)] += 1
                if re.search(r"\bdev pr\b", cmd) and "--summary" in cmd and ag in ("builder", "tester"):
                    first = cmd.split("\n")[0]
                    form = ("cd+" if first.lstrip().startswith("cd ") else "") + (
                        "piped" if re.search(r"EOF'?\s*(2>&1)?\s*\|", first) else "plain"
                    )
                    prform[(form, "refused" if refused else "error" if b.get("is_error") else "ok")] += 1
                if re.search(r"\bdev check\b", cmd):
                    n_check += 1
                    n_pass += "RESULT: passed" in text
    automode[(ag, has_auto)] += 1
    iso_runs[ag] += had_iso
    if ag == "builder":
        checks[ag].append(n_check)
        passes[ag].append(n_pass)
        branch_m[ag] += did_m
    if ag in ("researcher", "research-auditor"):
        rule = "never curl" in agent_body
        lit[(ag, rule, "runs")] += 1
        lit[(ag, rule, "runs_with_calls")] += n_lit > 0
        lit[(ag, rule, "calls")] += n_lit

print("auto-mode Bash-first attachment (agent, present): runs", dict(automode))
print("runs with >=1 isolation refusal:", {a: f"{iso_runs[a]}/{runs[a]}" for a in runs if iso_runs[a]})
print("isolation refusals by cause:", dict(iso_cause))
print("dev pr --summary heredoc by form and outcome (builder+tester):", dict(prform))
c = checks["builder"]
print(
    f"builder dev check calls per run: median {st.median(c)}, runs >1: {sum(x > 1 for x in c)}/{sum(x >= 1 for x in c)}, "
    f"passes total {sum(passes['builder'])}"
)
print("direct literature-API calls (agent, rule in body, metric):", dict(sorted(lit.items())))
print(f"builder runs with git branch -m: {branch_m['builder']}/{runs['builder']}")
print("dev output piped to tail/head/grep:", dict(piped))
