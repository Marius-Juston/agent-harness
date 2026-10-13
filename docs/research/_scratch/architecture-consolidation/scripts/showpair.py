"""Print both fragments of each flagged pair at its merge commit. Usage: showpair.py <repo> <bt.jsonl> <filter: t12|t4hi|t4lo>"""

import json
import subprocess
import sys
from pathlib import Path

repo, bt, mode = sys.argv[1:4]


def frag(m, loc):
    f, rng = loc.rsplit(":", 1)
    a, b = map(int, rng.split("-"))
    txt = subprocess.run(["git", "-C", repo, "show", f"{m}:{f}"], capture_output=True, text=True).stdout.splitlines()
    return "\n".join(f"   {ln}" for ln in txt[a - 1 : b])


i = 0
for line in Path(bt).read_text().splitlines():
    r = json.loads(line)
    for p in r["flagged"]:
        t = p["type"]
        s = p["sim"]
        if mode == "t12" and t not in (1, 2):
            continue
        if mode == "t4hi" and not (t == 4 and s >= 0.8):
            continue
        if mode == "t4lo" and not (t == 4 and s < 0.8):
            continue
        i += 1
        print(
            f"#### {i} {r['merge'][:8]} type{t} sim{s}\n-- NEW {p['new']}\n{frag(r['merge'], p['new'])}\n-- OLD {p['old']}\n{frag(r['merge'], p['old'])}"
        )
