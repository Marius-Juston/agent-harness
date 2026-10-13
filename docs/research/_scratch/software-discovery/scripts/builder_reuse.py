"""Builder runs that wrote a new non-test .py module: did any library discovery happen first? Also raw lit-API curls.

Usage: python3 builder_reuse.py [projects_dir]
"""
import json, re, sys
from pathlib import Path
root = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".claude/projects")
DISC = re.compile(r"gh (search repos|api[^|]*search/repositories)|pypi\.org|ecosyste\.ms|deps\.dev|libraries\.io|dev reuse")
RAW = re.compile(r"curl[^|]*https?://api\.(openalex\.org|crossref\.org|semanticscholar\.org)")
stats = {}
for f in root.rglob("subagents/*.jsonl"):
    try: at = json.loads(f.with_suffix(".meta.json").read_text()).get("agentType")
    except Exception: continue
    if not at or not at.startswith("harness-ops:"): continue
    disc_before = False; new_mod = None; disc_any = False; raw = 0
    for line in f.open(errors="ignore"):
        try: r = json.loads(line)
        except Exception: continue
        c = (r.get("message") or {}).get("content")
        if not isinstance(c, list): continue
        for b in c:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"): continue
            n, i = b.get("name"), b.get("input") or {}
            hit = (n == "Bash" and DISC.search(i.get("command", ""))) or (n == "WebFetch" and re.search(r"pypi\.org|github\.com", i.get("url", ""))) \
                  or (n or "").startswith("mcp__context7") or n == "WebSearch"
            if n == "Bash" and RAW.search(i.get("command", "")): raw += 1
            if hit:
                disc_any = True
                if new_mod is None: disc_before = True
            if n == "Write" and new_mod is None:
                p = i.get("file_path", "")
                if p.endswith(".py") and "/tests/" not in p and "/.scratch/" not in p and "/_scratch/" not in p and "/scratchpad/" not in p and not Path(p).name.startswith("test_"):
                    new_mod = p
    s = stats.setdefault(at, {"runs": 0, "new_mod": 0, "new_mod_disc_before": 0, "new_mod_disc_any": 0, "raw_runs": 0, "raw_calls": 0})
    s["runs"] += 1; s["raw_runs"] += raw > 0; s["raw_calls"] += raw
    if new_mod:
        s["new_mod"] += 1; s["new_mod_disc_before"] += disc_before; s["new_mod_disc_any"] += disc_any
for k, v in sorted(stats.items()): print(k, v)
