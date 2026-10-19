"""Score drift episodes against hand labels: precision of a break flag by doc type, latency to fix, who fixed it.

usage: python3 -I r1_score.py LABELS.csv DIR   (DIR holds <repo>.ep.jsonl and <repo>.json from the replay)
Prints no doc text: counts, days and merge counts only.
"""

import collections
import csv
import json
import statistics
import sys
from pathlib import Path

labels_path, d = sys.argv[1], Path(sys.argv[2])
lab = {}
with open(labels_path) as f:
    for row in csv.DictReader(ln for ln in f if not ln.startswith("#")):
        lab[(row["repo"], row["key"])] = row["label"]
repos = sorted({k[0] for k in lab})
eps = []
for r in repos:
    for ln in open(d / f"{r}.ep.jsonl"):  # noqa: SIM115 - research script
        e = json.loads(ln)
        e["label"] = lab.get((r, e["key"]), "?")
        eps.append(e)


def q(xs):
    if not xs:
        return "-"
    xs = sorted(xs)
    return f"n={len(xs)} median={statistics.median(xs):.1f} p25={xs[len(xs) // 4]:.1f} p75={xs[(3 * len(xs)) // 4]:.1f} max={xs[-1]:.1f}"


print("## episodes by repo, doc type and label")
c = collections.Counter((e["repo"], e["dtype"], e["label"]) for e in eps)
for r in repos:
    types = sorted({k[1] for k in c if k[0] == r})
    for t in types:
        row = {lab_: c[(r, t, lab_)] for lab_ in "TRHX"}
        n = sum(row.values())
        print(
            f"{r:3} {t:11} n={n:3} T={row['T']:3} R={row['R']:3} H={row['H']:3} X={row['X']:3} precision(T+R)={(row['T'] + row['R']) / n:.2f}"
        )
for r in repos:
    es = [e for e in eps if e["repo"] == r]
    n, tr = len(es), sum(e["label"] in "TR" for e in es)
    np_ = [e for e in es if e["dtype"] not in ("plan",)]
    print(f"{r} all: {tr}/{n} = {tr / n:.2f}; without plan docs: {sum(e['label'] in 'TR' for e in np_)}/{len(np_)}")

print("\n## true drift (T): days and merges from the breaking merge to the doc fix; still open at HEAD (censored)")
for r in repos:
    t = [e for e in eps if e["repo"] == r and e["label"] == "T"]
    fixed = [e for e in t if "fix_idx" in e]
    opened = [e for e in t if "open_d" in e]
    print(f"{r} T={len(t)} fixed={len(fixed)} open={len(opened)}")
    print("   fixed days   ", q([e["latency_d"] for e in fixed]))
    print("   fixed merges ", q([e["latency_merges"] for e in fixed]))
    print("   open days    ", q([e["open_d"] for e in opened]))
    print("   fixer agent/human:", collections.Counter("agent" if e["fix_agent"] else "human" for e in fixed))
    print(
        "   fix commits (distinct):",
        len({e["fix_commit"] for e in fixed}),
        collections.Counter(e["fix_commit"] for e in fixed).most_common(3),
    )
    print("   how:", collections.Counter("+".join(e["hows"]) for e in fixed))
    print("   by type:", dict(collections.Counter(e["dtype"] for e in t)))
print("\n## claim-level events (all, unlabelled): same-merge co-fix share = COFIX / (COFIX + BREAK)")
for r in repos:
    j = json.load(open(d / f"{r}.json"))  # noqa: SIM115 - research script
    ev = collections.Counter((e["ev"], e["dtype"]) for e in j["events"])
    for t in sorted({k[1] for k in ev}):
        co, br = ev[("COFIX", t)], ev[("FIX", t)] + ev[("OPEN", t)]
        if co + br:
            print(
                f"{r:3} {t:11} cofix={co:3} break={br:3} share={co / (co + br):.2f} born_stale={ev[('BORN_STALE', t)]}"
            )
    fin = collections.Counter()
    for k, v in j["final"].items():
        dt, kind, st = k.split("|")
        fin[(dt, st)] += v
    tot = collections.Counter()
    for (dt, st), v in fin.items():  # noqa: B007 - research script
        tot[st] += v
    print(
        f"{r} HEAD claims: valid={tot['valid']} stale={tot['stale']} never-valid={tot['never']} commits={j['n_commits']} days={(j['last_ts'] - j['first_ts']) / 86400:.0f}"
    )
