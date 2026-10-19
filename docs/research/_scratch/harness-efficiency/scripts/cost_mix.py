"""r2-against: per-agent-type cost mix, TTL split, effort and >5 min gaps from Claude Code transcripts.
Usage: python3 -I cost_mix.py <project-dir> [end-date YYYY-MM-DD]"""

import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

P = sys.argv[1]
END = sys.argv[2] if len(sys.argv) > 2 else "2026-10-17"
OPUS = dict(inp=4, w5=5, w1=8, rd=0.20, out=20)
SON = dict(inp=2, w5=2.5, w1=4, rd=0.20, out=10)


def atype(f):
    try:
        return json.load(open(f[:-6] + ".meta.json")).get("agentType", "?").replace("harness-ops:", "")
    except OSError:
        return "?"


tok = defaultdict(lambda: Counter())
eff = defaultdict(Counter)
gaps = defaultdict(Counter)
runs = Counter()
files = [(f, "main") for f in glob.glob(os.path.join(P, "*.jsonl"))] + [
    (f, None) for f in glob.glob(os.path.join(P, "*", "subagents", "*.jsonl"))
]
for f, kind in files:
    at = kind or atype(f)
    seen = {}
    prev = None
    used = False
    for line in Path(f).read_text(errors="ignore").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        ts = d.get("timestamp", "")
        if ts[:10] < "2026-10-11" or ts[:10] >= END:
            continue
        m = d.get("message", {})
        u = m.get("usage")
        mid = m.get("id")
        if not u:
            continue
        if mid in seen:
            # later chunk of the same message: add only the output growth
            extra = u.get("output_tokens", 0) - seen[mid]
            if extra > 0:
                tok[at]["out"] += extra
                seen[mid] = u.get("output_tokens", 0)
            continue
        seen[mid] = u.get("output_tokens", 0)
        used = True
        cc = u.get("cache_creation") or {}
        t = tok[at]
        t["inp"] += u.get("input_tokens", 0)
        t["rd"] += u.get("cache_read_input_tokens", 0)
        t["out"] += u.get("output_tokens", 0)
        t["w1"] += cc.get("ephemeral_1h_input_tokens", 0)
        t["w5"] += cc.get("ephemeral_5m_input_tokens", 0)
        t["calls"] += 1
        eff[at][str(d.get("effort"))] += 1
        now = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if prev is not None:
            g = (now - prev).total_seconds()
            gaps[at]["n"] += 1
            gaps[at][">5m"] += g > 300
            gaps[at][">60m"] += g > 3600
        prev = now
    if used:
        runs[at] += 1


def cost(t, p):
    return sum(t[k] * p[k] for k in ("inp", "w5", "w1", "rd", "out")) / 1e6


print("transcript totals:", {k: f"{sum(t[k] for t in tok.values()):,}" for k in ("inp", "w1", "w5", "rd", "out")})
tot = sum(cost(t, OPUS) for t in tok.values())
print(f"total opus-priced ${tot:,.0f}")
print(
    "agent | runs | $opus | share | mix rd/w/out/in % | w1h share of writes | $ if sonnet same tokens | saving % | effort | gaps>5m %"
)
for at, t in sorted(tok.items(), key=lambda kv: -cost(kv[1], OPUS)):
    c = cost(t, OPUS)
    s = cost(t, SON)
    if c < 5:
        continue
    parts = {k: t[k] * OPUS[k] / 1e6 / c * 100 for k in ("rd", "out", "inp")}
    parts["w"] = (t["w1"] * 8 + t["w5"] * 5) / 1e6 / c * 100
    w = t["w1"] + t["w5"]
    print(
        f"{at} | {runs[at]} | {c:,.0f} | {c / tot * 100:.1f} | {parts['rd']:.0f}/{parts['w']:.0f}/{parts['out']:.0f}/{parts['inp']:.0f} | {t['w1'] / max(w, 1) * 100:.0f} | {s:,.0f} | {(1 - s / c) * 100:.0f} | {dict(eff[at].most_common(3))} | {gaps[at]['>5m'] / max(gaps[at]['n'], 1) * 100:.1f} ({gaps[at]['>5m']}/{gaps[at]['n']})"
    )
