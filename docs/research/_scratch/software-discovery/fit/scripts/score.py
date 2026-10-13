"""Score fit-check arms against the gold labels (#196, PROTOCOL.md metrics and decision rule).

Usage: uv run --no-project python score.py <cases.json> <arm>=<out.json>... [--seed 196] [--draws 10000]
Metrics per arm: accuracy, catch rate (NOT_FIT gold judged NOT_FIT), false-alarm rate (FIT gold judged NOT_FIT),
UNPARSED count, agent calls, tokens and seconds per case. Paired bootstrap (resampling cases) of accuracy differences
between consecutive arms in cost order and of every pair. Exploratory: OR-combinations (NOT_FIT if either says so).
"""

import json
import random
import sys
from pathlib import Path

A = sys.argv[1:]
SEED = int(A[A.index("--seed") + 1]) if "--seed" in A else 196
DRAWS = int(A[A.index("--draws") + 1]) if "--draws" in A else 10000
cases = [c for c in json.loads(Path(A[0]).read_text()) if "exclude" not in c]
arms = {}
for a in A[1:]:
    if "=" in a:
        k, v = a.split("=", 1)
        arms[k] = json.loads(Path(v).read_text())
gold = {c["id"]: c["gold"] for c in cases}
ids = [c["id"] for c in cases]


def correct(verdicts: dict) -> list[int]:
    return [int(verdicts[i] == gold[i]) for i in ids]


V = {k: {i: r[i]["verdict"] for i in ids} for k, r in arms.items()}
keys = list(V)
for x in keys:
    for y in keys:
        if x < y:
            V[f"{x}|{y}"] = {i: "NOT_FIT" if "NOT_FIT" in (V[x][i], V[y][i]) else V[x][i] for i in ids}


def ci(diffs: list[int]) -> tuple[float, float, float]:
    rng = random.Random(SEED)  # noqa: S311  (seeded bootstrap, not cryptography)
    n = len(diffs)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(DRAWS))
    return sum(diffs) / n, boots[int(0.025 * DRAWS)], boots[int(0.975 * DRAWS) - 1]


nf = [i for i in ids if gold[i] == "NOT_FIT"]
fi = [i for i in ids if gold[i] == "FIT"]
print(f"cases {len(ids)} (FIT {len(fi)}, NOT_FIT {len(nf)})\n")
print(
    "| arm | accuracy [95% CI] | catch (NOT_FIT gold) | false alarm (FIT gold) | unparsed | calls/case | tokens in/out per case | s/case |"
)
print("|---|---|---|---|---|---|---|---|")
for k, v in V.items():
    acc = correct(v)
    m, lo, hi = ci(acc)
    catch = sum(v[i] == "NOT_FIT" for i in nf)
    fa = sum(v[i] == "NOT_FIT" for i in fi)
    unp = sum(v[i] == "UNPARSED" for i in ids)
    if k in arms:
        calls = [c for i in ids for c in arms[k][i].get("calls", [])]
        runs = [arms[k][i].get("run") for i in ids]
        secs = sum(c["seconds"] for c in calls) + sum((r or {}).get("seconds", 0) for r in runs)
        cost = (f"{len(calls) / len(ids):.2f} | {sum(c['tokens_in'] for c in calls) / len(ids):,.0f} / "
                f"{sum(c['tokens_out'] for c in calls) / len(ids):,.0f} | {secs / len(ids):.1f}")  # fmt: skip
    else:
        cost = "(exploratory) | | "
    print(f"| {k} | {m:.2f} [{lo:.2f}, {hi:.2f}] | {catch}/{len(nf)} | {fa}/{len(fi)} | {unp} | {cost} |")
print("\nPaired accuracy differences (row minus column arm):\n")
for x in keys:
    for y in keys:
        if x != y and keys.index(x) > keys.index(y):
            d = [a - b for a, b in zip(correct(V[x]), correct(V[y]), strict=True)]
            m, lo, hi = ci(d)
            w = sum(1 for e in d if e > 0)
            losses = sum(1 for e in d if e < 0)
            print(f"- {x} − {y}: {m:+.2f} [{lo:+.2f}, {hi:+.2f}], {w} wins, {losses} losses")
print("\nErrors per arm:")
for k in keys:
    print(f"- {k}: " + ", ".join(f"{i} {next(c['package'] for c in cases if c['id'] == i)} (gold {gold[i]})"
                                  for i in ids if V[k][i] != gold[i]))  # fmt: skip
