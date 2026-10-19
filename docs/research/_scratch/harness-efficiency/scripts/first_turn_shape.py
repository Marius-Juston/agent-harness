"""First-request composition per agent type from `api-request-shape` and `api-request-blob` records (round 2).

Usage: python3 -I first_turn_shape.py <transcript-dir>
Prints, per agent type: median chars of the system blocks, of all tool schemas, the largest tool schemas, the
first-request message blobs, the first assistant call's total input tokens, and a least-squares chars-per-token fit
(tokens = chars / r) over all runs. Window: files whose first timestamp is before 2026-10-17.
"""

import glob
import json
import statistics as st
import sys
from collections import Counter, defaultdict

ROOT = sys.argv[1]
rows = defaultdict(list)
toolsz = defaultdict(Counter)
toolruns = Counter()
fit = []
for f in glob.glob(ROOT + "/*.jsonl") + glob.glob(ROOT + "/*/subagents/*.jsonl"):
    sub = "/subagents/" in f
    try:
        with open(f[:-6] + ".meta.json") as fh:
            ag = json.load(fh).get("agentType", "?").replace("harness-ops:", "") if sub else "lead"
    except OSError:
        ag = "?" if sub else "lead"
    shape, blobs, tok, ts = None, 0, None, None
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = ts or d.get("timestamp")
            t = d.get("type")
            if t == "api-request-shape" and shape is None:
                shape = d["shape"]
            elif t == "api-request-blob" and tok is None:
                blobs += len(json.dumps(d.get("message", {}).get("content", "")))
            elif t == "assistant" and tok is None:
                u = d.get("message", {}).get("usage")
                if u:
                    tok = (
                        u.get("input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0)
                        + u.get("cache_read_input_tokens", 0)
                    )
    if not shape or tok is None or not ts or ts >= "2026-10-17":
        continue
    sysc = sum(len(b.get("text", "")) for b in shape.get("system", []))
    tools = shape.get("tools", [])
    toolc = sum(len(json.dumps(x)) for x in tools)
    for x in tools:
        toolsz[ag][x.get("name")] += len(json.dumps(x))
    toolruns[ag] += 1
    rows[ag].append((sysc, toolc, len(tools), blobs, tok))
    fit.append((sysc + toolc + blobs, tok))

# least squares through the origin: tok = chars / r
num = sum(c * t for c, t in fit)
den = sum(c * c for c, _ in fit)
r = 1 / (num / den)
res = [t - c / r for c, t in fit]
print(
    f"fit over {len(fit)} runs: {r:.2f} chars per token; residual median {st.median(res):.0f} tokens, "
    f"abs p90 {sorted(abs(x) for x in res)[int(len(res) * 0.9)]:.0f}"
)
for ag, v in sorted(rows.items(), key=lambda kv: -len(kv[1])):
    if len(v) < 5:
        continue
    m = [st.median(x[i] for x in v) for i in range(5)]
    print(
        f"\n{ag:18} runs={len(v)} system={m[0]:.0f}c tools={m[1]:.0f}c (n={m[2]:.0f}) first-msgs={m[3]:.0f}c "
        f"first-call tokens={m[4]:.0f}  (tools ~{m[1] / r:.0f} tok, msgs ~{m[3] / r:.0f} tok)"
    )
    top = sorted(toolsz[ag].items(), key=lambda kv: -kv[1])[:14]
    print("   largest tools (median-ish chars): " + ", ".join(f"{k} {v // toolruns[ag]}" for k, v in top))
