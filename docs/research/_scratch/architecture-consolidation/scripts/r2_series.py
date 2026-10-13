"""Print a compact time series of replayed trigger signals (#111 r2). Usage: python r2_series.py CODE [every], CODE one of repo-L repo-R repo-E H"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from codenames import APP, replay_rows

name = sys.argv[1]
every = int(sys.argv[2]) if len(sys.argv) > 2 else 1
rows = replay_rows(name)
V1 = ("systems", "core", "agents", "models", "utils", "tests", "(root)")
print("i date merges code nontest dev v1 d30 d50 d100 d50u lazyE% scc lazyRx% leafmain% ch1000 ch800 ch500")
for r in rows:
    if r["i"] % every and r is not rows[-1]:
        continue
    c = r["code"] or 1
    d2 = r["by_dir2"]
    dev = d2.get(f"{APP}/dev", 0)
    v1 = sum(v for k, v in d2.items() if k.split("/")[0] in V1)
    print(
        r["i"],
        r["date"],
        r["merges"],
        c,
        r["nontest"],
        dev,
        v1,
        *(f"{r[f'dormant{k}_loc'] / c:.2f}" for k in (30, 50, 100)),
        f"{r['dormant50_unreached_loc'] / c:.2f}",
        f"{100 * r['lazy_edges'] / max(r['edges'], 1):.1f}",
        r["scc_max"],
        f"{100 * r['lazy_rx'] / max(r['imports_rx'], 1):.1f}",
        f"{100 * r['leaf_main_loc'] / max(r['nontest'], 1):.1f}",
        r["churny_1000_30"],
        r["churny_800_20"],
        r["churny_500_15"],
    )
