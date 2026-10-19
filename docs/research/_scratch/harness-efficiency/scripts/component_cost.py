"""Window cost of a fixed context component, per agent type (round 2 surface audit).

Usage: python3 -I component_cost.py <transcript-dir>
A component of T tokens in an agent's prefix is re-read on every API call of that agent: cost = calls x T x read price.
Prices: r1 fit on repo-E cost records (cache read $0.20/M, cache write $7.95/M). chars-per-token 2.88 from
first_turn_shape.py (JSON-escaped chars). Also prints the first call's cache read vs write, to show whether the tool
prefix is shared between agents (read) or written per run.
"""

import glob
import json
import statistics as st
import sys
from collections import defaultdict

ROOT, READ, WRITE, CPT = sys.argv[1], 0.20, 7.95, 2.88
COMP = {  # name: (chars, agent types that carry it), chars from first_turn_shape.py / surface_audit.py medians
    "Artifact tool schema": (37145, {"builder", "researcher", "tester"}),
    "Skill tool + skill listing": (1877 + 12500, {"builder", "researcher", "tester"}),
    "Agent tool schema": (3572, {"builder", "tester"}),
    "HARNESS.md + project CLAUDE.md (prose)": (3487 + 552, None),
}
calls, runs, first = defaultdict(int), defaultdict(int), defaultdict(list)
for f in glob.glob(ROOT + "/*.jsonl") + glob.glob(ROOT + "/*/subagents/*.jsonl"):
    sub = "/subagents/" in f
    try:
        with open(f[:-6] + ".meta.json") as fh:
            ag = json.load(fh).get("agentType", "?").replace("harness-ops:", "") if sub else "lead"
    except OSError:
        ag = "lead"
    ids, ts, fu = set(), None, None
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = ts or d.get("timestamp")
            if d.get("type") == "assistant":
                m = d.get("message", {})
                ids.add(m.get("id"))
                if fu is None and m.get("usage"):
                    fu = m["usage"]
    if not ts or ts >= "2026-10-17":
        continue
    calls[ag] += len(ids)
    runs[ag] += 1
    if fu:
        first[ag].append((fu.get("cache_read_input_tokens", 0), fu.get("cache_creation_input_tokens", 0)))
print("first call: median cache read / cache write tokens")
for ag, v in first.items():
    if len(v) >= 5:
        print(
            f"  {ag:18} read {st.median(x[0] for x in v):.0f}  write {st.median(x[1] for x in v):.0f}  runs {runs[ag]} calls {calls[ag]}"
        )
total = 0
for name, (chars, types) in COMP.items():
    t = chars / CPT
    sel = types or {a for a in calls if a not in ("?",)}
    c = sum(calls[a] for a in sel)
    r = sum(runs[a] for a in sel)
    lo, hi = c * t * READ / 1e6, c * t * READ / 1e6 + r * t * WRITE / 1e6
    print(f"{name:42} ~{t:6.0f} tok x {c} calls: ${lo:6.1f} (reads only) .. ${hi:6.1f} (+ one write per run)")
