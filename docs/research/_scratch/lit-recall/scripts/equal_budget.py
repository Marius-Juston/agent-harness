"""Exploratory (not pre-registered, never used in a decision): L0+X with 10 results from each per query against
L0 with 20, i.e. the same 20 results per query. Paired bootstrap as in score.py.

Usage: uv run --no-project python equal_budget.py
"""

import statistics
from collections import Counter

from common import load
from score import Matcher, boot, boot_works, found, gold_all


def main() -> None:
    units = load("split.json")["test"]
    gold = gold_all()
    works = [(u, g["key"]) for u in units for g in gold[u]]
    for a, ka, b, kb in (("L0+X", 10, "L0", 20), ("L0+X", 10, "X", 20), ("L0+W", 10, "L0", 20)):
        fa = {u: found(a, u, ka, Matcher(gold[u]), Counter(), []) for u in units}
        fb = {u: found(b, u, kb, Matcher(gold[u]), Counter(), []) for u in units}
        pa = {u: len(fa[u]) / len(gold[u]) for u in units}
        pb = {u: len(fb[u]) / len(gold[u]) for u in units}
        ha = {(u, k) for u in units for k in fa[u]}
        hb = {(u, k) for u in units for k in fb[u]}
        d = statistics.fmean(pa[u] - pb[u] for u in units)
        lo, hi = boot(units, pa, pb)
        wlo, whi = boot_works(works, ha, hb)
        dw = (len(ha) - len(hb)) / len(works)
        print(f"{a}@{ka} - {b}@{kb}: macro {d:+.3f} [{lo:+.3f}, {hi:+.3f}]  micro {dw:+.3f} [{wlo:+.3f}, {whi:+.3f}]")


if __name__ == "__main__":
    main()
