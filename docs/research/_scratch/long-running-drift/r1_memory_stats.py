"""R1 memory: tallies over the hand-labelled entries (r1_memory_gold.csv), plus frontmatter checks from the scan output.

usage: python3 -I r1_memory_stats.py GOLD.csv [SCAN.json] [--today 2026-10-19]
SCAN.json is r1_memory_scan.py output (private); with it the script also counts entries whose `modified` frontmatter lags
the file's mtime by more than an hour, and topic-file opens (Read tool) versus index loads.
"""

import collections
import csv
import datetime as dt
import json
import sys

args = sys.argv[1:]
today = dt.date.fromisoformat(args[args.index("--today") + 1]) if "--today" in args else dt.date(2026, 10, 19)
with open(args[0], newline="") as fh:
    rows = list(csv.DictReader(fh))
n = len(rows)
stale = lambda r: r["status"] in ("PARTLY-STALE", "STALE")  # noqa: E731 - research script


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "0/0"


print("entries", n, "by type", dict(collections.Counter(r["type"] for r in rows)))
print("status", dict(collections.Counter(r["status"] for r in rows)))
verifiable = [r for r in rows if r["status"] != "UNVERIFIABLE"]
print("stale (partly or fully) among verifiable", pct(sum(map(stale, verifiable)), len(verifiable)))
for lo, hi in ((0, 7), (7, 14), (14, 30)):
    b = [r for r in verifiable if lo <= (today - dt.date.fromisoformat(r["birth"])).days < hi]
    print(f"  age {lo:2}-{hi:2} d: stale {pct(sum(map(stale, b)), len(b))}")
for t in sorted({r["type"] for r in rows}):
    b = [r for r in verifiable if r["type"] == t]
    print(f"  type {t:9}: stale {pct(sum(map(stale, b)), len(b))}")
print(
    "stale entries corrected since",
    pct(sum(1 for r in rows if stale(r) and r["corrected"] == "Y"), sum(map(stale, rows))),
)
print("stale classes", dict(collections.Counter(r["stale_class"] for r in rows if stale(r))))
print("promoted", dict(collections.Counter(r["promoted"] for r in rows)))
print("conflicts a repo rule", pct(sum(r["conflicts_repo"] == "Y" for r in rows), n))
print("user-wide scope stored per project", pct(sum(r["scope"] == "user" for r in rows), n))
print(
    "dangling [[links]]",
    sum(int(r["dangling_links"]) for r in rows),
    "entries with **Why:**",
    pct(sum(r["has_why"] == "Y" for r in rows), n),
)
print("no `modified` frontmatter", pct(sum(r["has_modified_field"] == "N" for r in rows), n))
ages = sorted((today - dt.date.fromisoformat(r["birth"])).days for r in rows)
print("age at audit (days): min", ages[0], "median", ages[n // 2], "max", ages[-1])
if len(args) > 1 and args[1].endswith(".json"):
    with open(args[1]) as fh:
        scan = json.load(fh)
    lag = idx = reads = 0
    for proj in scan.values():
        for e in proj["inventory"]["entries"].values():
            if e["fm_modified"]:
                fm = dt.datetime.fromisoformat(e["fm_modified"].replace("Z", "+00:00"))
                mt = dt.datetime.fromisoformat(e["mtime"])
                lag += (mt - fm).total_seconds() > 3600
            reads += len(e["reads"])
        idx += proj["index_loads_main"] + proj["index_loads_sub"]
    print(
        "`modified` lags mtime by >1 h:",
        lag,
        "| Read-tool opens of topic files:",
        reads,
        "| transcripts loading an index:",
        idx,
    )
