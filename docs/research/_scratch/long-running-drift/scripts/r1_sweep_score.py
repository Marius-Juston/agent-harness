"""Score the LLM claim-check sweeps: verdict counts, precision of STALE against hand labels, recall on known stale claims,
and cost (tokens, seconds, list-price USD). usage: python3 -I r1_sweep_score.py SWEEP_DIR LABELS.csv KNOWN.csv"""

import collections
import csv
import json
import sys
from pathlib import Path

d, lab_p, known_p = Path(sys.argv[1]), sys.argv[2], sys.argv[3]


def rows(p):
    with open(p) as f:
        return list(csv.DictReader(ln for ln in f if not ln.startswith("#")))


labels = collections.defaultdict(list)
for r in rows(lab_p):
    labels[r["run"]].append(r["label"])
tot = collections.Counter()
for f in sorted(d.glob("*.json")):
    j = json.load(open(f))  # noqa: SIM115 - research script
    out = [json.loads(ln) for ln in j["result"].splitlines() if ln.strip().startswith("{")]
    v = collections.Counter(o["verdict"] for o in out)
    u = j["usage"]
    tin = u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"]
    lab = collections.Counter(labels[f.stem])
    assert sum(lab.values()) == v["STALE"], (f.stem, lab, v)  # noqa: S101 - research script
    tot.update(lab)
    tot.update(
        {
            "claims": len(out),
            "in": tin,
            "out": u["output_tokens"],
            "s": j["duration_ms"] / 1000,
            "usd": j["total_cost_usd"],
        }
    )
    print(
        f"{f.stem}: claims={len(out)} TRUE={v['TRUE']} STALE={v['STALE']} UNVERIFIABLE={v['UNVERIFIABLE']} labels={dict(lab)} "
        f"precision={lab['T'] / v['STALE'] if v['STALE'] else float('nan'):.2f} in_tokens={tin} out_tokens={u['output_tokens']} "
        f"s={j['duration_ms'] / 1000:.0f} usd={j['total_cost_usd']:.2f} model={list(j['modelUsage'])}"
    )
flags = sum(tot[k] for k in "THESU")
print(
    f"ALL: flags={flags} T={tot['T']} precision={tot['T'] / flags:.2f}; FP kinds H={tot['H']} E={tot['E']} S={tot['S']} U={tot['U']}; "
    f"claims={tot['claims']} in_tokens={tot['in']} out_tokens={tot['out']} s={tot['s']:.0f} usd={tot['usd']:.2f}"
)
k = rows(known_p)
print(
    f"recall on known stale claims: {sum(r['caught'] == 'yes' for r in k)}/{len(k)}; by kind:",
    dict(collections.Counter((r["kind"], r["caught"]) for r in k)),
)
