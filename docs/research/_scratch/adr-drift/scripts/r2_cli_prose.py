"""Variant of check 1: full-parse (K4 leftover positionals) on UNbackticked `uv run dev` spans in issue bodies,
against the parser at the issue's creation. Shows whether a prose-wide positional check is usable (#143's DONE WHEN
wrote `uv run dev board block <n> "<why>"` without backticks).  usage: python3 r2_cli_prose.py"""

import bisect
import datetime
import json
import subprocess

import r2_common as c
from r2_cli_replay import PY, WORKER, devroot

for name, pre in (("AH", "ah"), ("PB", "pb")):
    hist = c.history(name)
    times = [h[1] for h in hist]
    n_iss = 0
    hits = []
    for iss in json.load(open(c.DATA / f"{pre}_issues.json")):  # noqa: SIM115 - research script, kept as run
        t0 = int(datetime.datetime.fromisoformat(iss["created_at"].replace("Z", "+00:00")).timestamp())
        k = max(0, bisect.bisect_right(times, t0) - 1)
        root = devroot(name, hist[k][0]) or devroot(name, hist[k + 1][0])
        inp, out = c.SCR / "in_p.json", c.SCR / "out_p.json"
        json.dump([[f"#{iss['number']}", iss["body"] or ""]], open(inp, "w"))  # noqa: SIM115 - research script, kept as run
        subprocess.run([PY, "-I", WORKER, str(root), str(inp), str(out), "--prose"], check=True)
        f = [x for x in json.load(open(out))["findings"] if x["kind"] == "K4"]  # noqa: SIM115 - research script, kept as run
        n_iss += bool(f)
        hits += [(iss["number"], x["span"][:80], x["detail"][:40]) for x in f]
    print(f"{name}: {n_iss} issues with a prose K4 finding, {len(hits)} spans")
    for h in hits:
        print("  ", *h)
