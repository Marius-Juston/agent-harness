"""Composite trigger variants over replayed history (#111 r2). Usage, from the worktree root with the env vars of codenames.py:
python3 docs/research/_scratch/architecture-consolidation/scripts/r2_composite.py"""

from r2_simulate import STATE, extra, rows_of

SMELLS = ("dormant30_orphan>=5%", "churny_1000_30>=1", "lazy_scc>=10", "lazy_regex>=10%")


def composite(rows, growth=(1.0, 5000), fp=100, merges=None, smells=SMELLS):
    fires, base = [], rows[0]
    for r in rows[1:]:
        why = []
        d = r["code"] - base["code"]
        if growth and d >= growth[1] and r["code"] >= base["code"] * (1 + growth[0]):
            why.append("growth")
        if fp and r["i"] - base["i"] >= fp:
            why.append("fp")
        if merges and r["merges"] - base["merges"] >= merges:
            why.append("merges")
        for s in smells:  # a smell that was absent at the last review and is present now
            if STATE[s](r) and not STATE[s](base):
                why.append(s.split(">=")[0])
        if "churny_1000_30>=1" in smells and base["churny_1000_30"] and r["churny_1000_30"] > base["churny_1000_30"]:
            why.append("churny+")  # one more large churny file than at the last review
        if why:
            fires.append((r, why))
            base = r
    return fires


VARIANTS = {
    "A growth x2 & >=5k": dict(fp=None, smells=()),
    "B A or fp>=100": dict(smells=()),
    "C B or new smell": dict(),
    "D C with merges>=40 instead of fp": dict(fp=None, merges=40),
    "E C, smells dormant+churny only": dict(smells=("dormant30_orphan>=5%", "churny_1000_30>=1")),
}
if __name__ == "__main__":
    for v, kw in VARIANTS.items():
        print(f"\n## {v}")
        for n, rows in rows_of.items():
            f = composite(rows, **kw)
            kloc = (rows[-1]["code"] - rows[0]["code"]) / 1000
            print(f"  {n}: {len(f)} fires ({len(f) / max(kloc, 1) * 10:.2f} per 10k LOC added)")
            for r, why in f:
                print(f"     {r['i']}@{r['date'][5:]} {r['code'] // 1000}k {'+'.join(why)}{extra(n, r)}")
