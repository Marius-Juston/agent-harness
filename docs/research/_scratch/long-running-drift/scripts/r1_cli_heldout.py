"""#282 r1: held-out replay of check 1a/1b (#229, the `uv run dev` span parse) on first-parent merges made AFTER the
#220 replay. Reuses the #220 scripts unchanged (same worker, same five syntax rules, each commit's own parser).

usage: PROJECT_B=<repo-E checkout> python3 r1_cli_heldout.py AH_FROM PB_FROM
AH_FROM / PB_FROM = index of the first held-out first-parent commit (agent-harness: 69, after 635a833; repo-E: 56).
Prints each unique finding (kind, gate-eligible, first..last index, live at head) without doc text.
"""

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "adr-drift" / "scripts"))
import r2_cli_replay as r
import r2_common as c

start = {"AH": int(sys.argv[1]), "PB": int(sys.argv[2])}
rows, checked = [], collections.Counter()
for name in c.REPOS:
    hist = c.history(name)
    for i in range(start[name], len(hist)):
        sha = hist[i][0]
        t = c.tree(name, sha)
        docs = [
            [f, (t / f).read_text(errors="replace")] for f in c.files(name, sha) if not r.skip(f) and (t / f).is_file()
        ]
        root = r.devroot(name, sha)
        if root is None:
            continue
        out = r.run(root, docs, f"ho_{name}")
        checked[name] += 1
        checked[name + "_spans"] += out["hits"]
        for x in out["findings"]:
            rows.append(dict(repo=name, idx=i, sha=sha[:9], **x))
heads = {n: len(c.history(n)) - 1 for n in c.REPOS}
u = {}
for x in rows:
    k = (x["repo"], x["src"], x["kind"], x["prefix"], x["span"], x["detail"])
    a = u.setdefault(k, dict(first=x["idx"], last=x["idx"]))
    a["last"] = max(a["last"], x["idx"])
print("commits checked and span-occurrences:", dict(checked))
for k, a in sorted(u.items(), key=lambda kv: (kv[0][0], kv[1]["first"])):
    repo, src, kind, prefix, span, detail = k
    gate = "G" if kind in ("K1", "K2", "K3", "K4", "K5") and prefix == "uv" and src.endswith(r.GATE_EXT) else "-"
    live = "LIVE" if a["last"] == heads[repo] else "gone"
    print(
        f"{repo} {kind} {prefix:4} {gate} {a['first']}..{a['last']} {live} {src.rsplit('.', 1)[-1]} | {span[:100]} | {detail}"
    )
print(
    "unique findings by repo/kind/gate-eligible:",
    dict(collections.Counter((k[0], k[2], "G" if k[3] == "uv" and k[1].endswith(r.GATE_EXT) else "-") for k in u)),
)
