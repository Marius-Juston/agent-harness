"""Find agent tool calls that used a stale claim's token while the claim was stale. Read-only.

usage: python3 -I r1_transcripts.py LABELS.csv EPISODES.jsonl REPO_LABEL TRANSCRIPT_DIR [TRANSCRIPT_DIR ...]
For every T or R episode (hand label) the window is [break merge time, fix merge time or now]. A hit is a tool_use
whose input contains one of the episode's tokens (word boundary) inside that window; its tool_result is classified
as error (is_error, or "does not exist" / "No such file" / "No matches" / "not found") or ok. Prints counts and, for
hits, the session id, time, tool and result class (no transcript text).
"""

import csv
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

labels_path, ep_path, repo_label, *dirs = sys.argv[1:]
lab = {}
with open(labels_path) as f:
    for row in csv.DictReader(ln for ln in f if not ln.startswith("#")):
        if row["repo"] == repo_label:
            lab[row["key"]] = row["label"]
eps = [json.loads(ln) for ln in open(ep_path)]  # noqa: SIM115 - research script
eps = [e for e in eps if lab.get(e["key"]) in ("T", "R")]
now = time.time()
watch = []
for e in eps:
    for t in e["toks"]:
        kind, tok = t.split(":", 1)
        if len(tok) < 6:
            continue
        watch.append((tok, e["ts"], e.get("fix_ts", now), e["key"], lab[e["key"]], kind, e["dtype"]))
toks = sorted({w[0] for w in watch}, key=len, reverse=True)
pre = re.compile("|".join(re.escape(t) for t in toks))
ERR = re.compile(r"does not exist|No such file|No matches found|not found|Found 0|no files found", re.I)


def ts_of(o):
    s = o.get("timestamp")
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


hits, results, n_calls, n_files = [], {}, 0, 0
for d in dirs:
    for path in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
        n_files += 1
        pend = {}
        for ln in open(path, errors="replace"):  # noqa: SIM115 - research script
            if '"tool_use"' not in ln and '"tool_result"' not in ln:
                continue
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            msg = o.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    n_calls += 1
                    inp = json.dumps(c.get("input", {}))
                    if not pre.search(inp):
                        continue
                    t = ts_of(o)
                    for tok, a, b, key, lb, kind, dt in watch:
                        if t and a <= t <= b and re.search(rf"(?<![\w/.-]){re.escape(tok)}(?![\w-])", inp):
                            pend[c["id"]] = dict(
                                session=os.path.basename(path)[:8],
                                side=o.get("isSidechain", False),
                                t=t,
                                tool=c.get("name"),
                                tok_kind=kind,
                                dtype=dt,
                                label=lb,
                                key=key,
                                tok_len=len(tok),
                            )
                            break
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in pend:
                    h = pend.pop(c["tool_use_id"])
                    body = c.get("content")
                    text = json.dumps(body)[:4000] if not isinstance(body, str) else body[:4000]
                    h["result"] = "error" if c.get("is_error") or ERR.search(text) else "ok"
                    hits.append(h)
print(
    f"{repo_label}: transcripts={n_files} tool_calls={n_calls} watched_tokens={len(toks)} episodes={len(eps)} hits={len(hits)}"
)
by = {}
for h in hits:
    k = (h["label"], h["dtype"], h["tool"], h["result"])
    by[k] = by.get(k, 0) + 1
for k, v in sorted(by.items()):
    print("  ", k, v)
for h in sorted(hits, key=lambda h: h["t"]):
    print(
        f"   {datetime.fromtimestamp(h['t']).isoformat()[:16]} {h['session']} side={h['side']} {h['tool']:6} {h['label']} {h['dtype']:9} {h['tok_kind']} ep={h['key']} {h['result']}"
    )
