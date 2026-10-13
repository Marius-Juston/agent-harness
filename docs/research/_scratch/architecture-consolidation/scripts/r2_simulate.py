"""Simulate consolidation triggers over replayed history (#111 r2). Reads .scratch/replay_<dir>.json via codenames.py.

Counter triggers reset when they fire (a review happened, code not shrunk: pessimistic). State triggers report the
first crossing and the share of first-parent commits where they hold. Usage: python r2_simulate.py
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from codenames import APP, replay_rows

NAMES = ("repo-L", "repo-R", "H", "repo-E")
V1 = {"systems", "core", "agents", "models", "utils", "tests", "(root)"}
rows_of = {n: replay_rows(n) for n in NAMES}


def day(r):
    return dt.date.fromisoformat(r["date"])


def extra(n, r):
    if n != "repo-L":
        return ""
    d2 = r["by_dir2"]
    v1 = sum(v for k, v in d2.items() if k.split("/")[0] in V1)
    return f" dev={d2.get(f'{APP}/dev', 0)} v1={v1 / r['code']:.0%}"


def counter(rows, kind, th):
    fires, base = [], rows[0]
    for r in rows[1:]:
        if kind == "merges":
            hit = r["merges"] - base["merges"] >= th
        elif kind == "fp":
            hit = r["i"] - base["i"] >= th
        elif kind == "growth":
            g, a = th
            hit = r["code"] - base["code"] >= a and r["code"] >= base["code"] * (1 + g)
        elif kind == "days":
            hit = (day(r) - day(base)).days >= th
        if hit:
            fires.append(r)
            base = r
    return fires


STATE = {
    "dormant30_orphan>=5%": lambda r: r["code"] > 0 and r["dormant30_orphan_loc"] >= 0.05 * r["code"],
    "dormant50_orphan>=5%": lambda r: r["code"] > 0 and r["dormant50_orphan_loc"] >= 0.05 * r["code"],
    "dormant50_any>=5%": lambda r: r["code"] > 0 and r["dormant50_loc"] >= 0.05 * r["code"],
    "lazy_edges>=10%": lambda r: r["lazy_edges"] >= 0.10 * max(r["edges"], 1) and r["edges"] >= 50,
    "lazy_regex>=10%": lambda r: r["lazy_rx"] >= 0.10 * max(r["imports_rx"], 1) and r["imports_rx"] >= 50,
    "lazy_scc>=10": lambda r: r["scc_max"] >= 10,
    "leaf_main>=20%": lambda r: r["leaf_main_loc"] >= 0.20 * max(r["nontest"], 1),
    "churny_1000_30>=1": lambda r: r["churny_1000_30"] >= 1,
    "churny_800_20>=2": lambda r: r["churny_800_20"] >= 2,
    "churny_500_15>=3": lambda r: r["churny_500_15"] >= 3,
}

if __name__ == "__main__":
    for n, rows in rows_of.items():
        span = (day(rows[-1]) - day(rows[0])).days
        print(
            f"\n=== {n}: {len(rows)} first-parent commits, {rows[-1]['merges']} merged PRs, {span} days, "
            f"code {rows[0]['code']} -> {rows[-1]['code']}"
        )
        for kind, ths in (
            ("merges", (20, 40, 80)),
            ("fp", (50, 100, 200)),
            ("growth", ((0.5, 2000), (1.0, 5000), (1.0, 10000))),
            ("days", (7, 14, 30)),
        ):
            for th in ths:
                f = counter(rows, kind, th)
                pts = "; ".join(f"{r['i']}@{r['date'][5:]} {r['code'] // 1000}k{extra(n, r)}" for r in f[:8])
                print(f"  {kind}>={th}: {len(f)} fires  {pts}{' ...' if len(f) > 8 else ''}")
        for name, fn in STATE.items():
            hits = [r for r in rows if fn(r)]
            first = hits[0] if hits else None
            print(
                f"  {name}: true at {len(hits) / len(rows):.0%} of commits; first "
                + (f"{first['i']}@{first['date'][5:]} {first['code'] // 1000}k{extra(n, first)}" if first else "never")
            )
