"""Window spend from the client cost records, segment by segment, cross-checked against transcript-priced cost.

Usage: python3 cost_segments.py <claude-project-dir> [--until 2026-10-17]

Why: `cost-state` records hold a running `totalCostUSD` per Claude Code process. A resumed session starts a new process
whose total restarts from 0, so keeping only the last record of a session (transcript_stats.py) loses every earlier
process. Here a drop in totalCostUSD starts a new segment and each segment counts at its maximum. Records are also
written rarely, so a process that never wrote one is missing entirely; the transcript-priced cost below shows how much.

Transcript-priced cost per session (lead file plus its subagents/*.jsonl): assistant messages deduplicated by message
id; cache reads, 5-minute and 1-hour cache writes and uncached input are exact; output is logged at stream start, so it
is estimated as max(logged, visible characters x 0.443 tokens/char, the r2_cost_phases.py fit). Hidden thinking is not
in the transcripts: it is the difference to the cost records in the sessions whose records cover the transcript.
Prices: list $/M tokens from the pricing page (2026-10-19). Stdlib only.
"""

import glob
import json
import os
import sys
from collections import Counter, defaultdict

PROJ = sys.argv[1]
UNTIL = sys.argv[sys.argv.index("--until") + 1] if "--until" in sys.argv else "2026-10-17"
VTPC = 0.443
# input, 5m write, 1h write, read, output
PRICE = {
    "opus": (4.0, 5.0, 8.0, 0.20, 20.0),
    "sonnet": (2.0, 2.5, 4.0, 0.10, 10.0),
    "haiku": (0.10, 0.125, 0.20, 0.01, 0.50),
    "fable": (10.0, 12.5, 20.0, 0.25, 50.0),
}


def family(model: str) -> str:
    return next((k for k in PRICE if k in (model or "")), "opus")


def records(path: str):
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def visible_chars(content) -> int:
    n = 0
    for b in content or []:
        if b.get("type") == "text":
            n += len(b.get("text", ""))
        elif b.get("type") == "thinking":
            n += len(b.get("thinking", ""))
        elif b.get("type") == "tool_use":
            n += len(json.dumps(b.get("input", {})))
    return n


def transcript_cost(path: str) -> Counter:
    """$ by kind for one transcript file, plus token counts."""
    msgs = {}
    for d in records(path):
        if d.get("type") != "assistant":
            continue
        m = d["message"]
        u, mid = m.get("usage"), m.get("id")
        if not u or not mid:
            continue
        e = msgs.setdefault(mid, {"u": u, "model": m.get("model", ""), "vis": 0, "out": 0})
        e["vis"] += visible_chars(m.get("content"))
        e["out"] = max(e["out"], u.get("output_tokens", 0))
    c = Counter()
    for e in msgs.values():
        u = e["u"]
        p = PRICE[family(e["model"])]
        cc = u.get("cache_creation") or {}
        w1 = cc.get("ephemeral_1h_input_tokens", 0)
        w5 = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - w1)
        out = max(e["out"], e["vis"] * VTPC)
        c["input"] += u.get("input_tokens", 0) * p[0] / 1e6
        c["write"] += (w5 * p[1] + w1 * p[2]) / 1e6
        c["read"] += u.get("cache_read_input_tokens", 0) * p[3] / 1e6
        c["output_visible"] += out * p[4] / 1e6
        c["turns"] += 1
        c["out_tok_visible"] += out
    return c


def cost_state(path: str):
    """Segments of the running total, one per process: [max totalCostUSD, Opus 5.5 output tokens, modelUsage]."""
    segs, cur = [], None
    for d in records(path):
        if d.get("type") != "cost-state":
            continue
        tot = d.get("totalCostUSD", 0) or 0
        mu = d.get("modelUsage", {})
        out = mu.get("claude-opus-5-5", {}).get("outputTokens", 0)
        if cur is None or tot < cur[0] - 1e-9:
            cur = [tot, out, mu]
            segs.append(cur)
        elif tot >= cur[0]:
            cur[0], cur[1], cur[2] = tot, out, mu
    return segs


def first_ts(path: str) -> str:
    return next((d["timestamp"] for d in records(path) if d.get("timestamp")), "")


rows = []
for f in sorted(glob.glob(os.path.join(PROJ, "*.jsonl"))):
    sid = os.path.basename(f)[:-6]
    ts = first_ts(f)
    if not ts or ts >= UNTIL:
        continue
    segs = cost_state(f)
    t = transcript_cost(f)
    for s in glob.glob(os.path.join(PROJ, sid, "subagents", "*.jsonl")):
        t += transcript_cost(s)
    rows.append((sid[:8], ts[:10], segs, t))

tot = Counter()
print("session  start       last-record  segments-sum  transcript input-side  + visible output   records/transcript")
for sid, ts, segs, t in rows:
    last = segs[-1][0] if segs else 0.0
    seg_sum = sum(s[0] for s in segs)
    inside = t["input"] + t["write"] + t["read"]
    vis = inside + t["output_visible"]
    tot.update({"last": last, "segsum": seg_sum, "inside": inside, "vis": vis, "turns": t["turns"]})
    for k in ("input", "write", "read", "output_visible", "out_tok_visible"):
        tot[k] += t[k]
    ratio = seg_sum / vis if vis else 0
    print(f"{sid}  {ts}  {last:11.1f}  {seg_sum:12.1f}  {inside:21.1f}  {vis:16.1f}   {ratio:5.2f}  ({len(segs)} seg)")

# hidden output: from sessions whose records cover at least their transcript-priced cost
cov = [
    (segs, t)
    for _, _, segs, t in rows
    if segs and sum(s[0] for s in segs) >= (sum(t[k] for k in ("input", "write", "read", "output_visible")))
]
hid_usd = sum(
    sum(s[0] for s in segs) - sum(t[k] for k in ("input", "write", "read", "output_visible")) for segs, t in cov
)
hid_turns = sum(t["turns"] for _, t in cov)
per_turn = hid_usd / hid_turns if hid_turns else 0.0
est = tot["vis"] + per_turn * tot["turns"]
print(
    f"\nwindow (sessions starting before {UNTIL}): last-record sum ${tot['last']:.0f} (transcript_stats.py), "
    f"segment sum ${tot['segsum']:.0f}, transcript input-side ${tot['inside']:.0f}, plus visible output "
    f"${tot['vis']:.0f}"
)
print(
    f"hidden output from {len(cov)} covered sessions: ${per_turn * 1000:.2f} per 1,000 turns "
    f"({per_turn / 20e-6:.0f} tokens per turn at $20/M); window estimate ${est:.0f} over {tot['turns']:.0f} turns"
)
kinds = {
    "cache read": tot["read"],
    "cache write": tot["write"],
    "input": tot["input"],
    "output, plus hidden output and uncached input": tot["output_visible"] + per_turn * tot["turns"],
}
print(
    "transcript-priced estimate by kind (uncached input is absent from transcripts):",
    {k: f"${v:.0f} ({v / est:.0%})" for k, v in kinds.items()},
)

# token-kind split of the per-process records, each model at its own list prices (cache writes at the 1-hour price,
# since 99.6 % of writes were 1-hour; Haiku at its <=100k-prompt prices, ASSUMED for its short side calls)
FIELDS = (
    ("input", "inputTokens", 0),
    ("cache write", "cacheCreationInputTokens", 2),
    ("cache read", "cacheReadInputTokens", 3),
    ("output", "outputTokens", 4),
)
split = defaultdict(float)
for _, _, segs, _ in rows:
    for seg in segs:
        for model, u in seg[2].items():
            fam = family(model)
            if "opus-5-5" in model:
                for kind, key, i in FIELDS:
                    split[kind] += u.get(key, 0) * PRICE["opus"][i] / 1e6
            elif fam == "haiku":
                split["Haiku 5.5 side calls"] += sum(u.get(key, 0) * PRICE["haiku"][i] for _, key, i in FIELDS) / 1e6
            else:
                split["other models (record costUSD)"] += u.get("costUSD", 0)
tot_split = sum(split.values())
print(
    "per-process records by kind, each model at its list prices:",
    {k: f"${v:.0f} ({v / tot_split:.1%})" for k, v in split.items()},
)
opus_out = sum(seg[1] for _, _, segs, _ in rows for seg in segs)
print(f"Opus 5.5 output tokens in the per-process records: {opus_out / 1e6:.1f} M")
