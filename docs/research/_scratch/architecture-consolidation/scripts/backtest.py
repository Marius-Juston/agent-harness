"""Backtest a diff-only near-duplicate gate: at each first-parent merge, run pyscn clone detection on the
merged tree and keep only pairs where one fragment is mostly lines added by the merge and the other is untouched
(pre-existing). Usage: backtest.py <repo-clone> <n_merges> <out.jsonl>"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

repo, n, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def git(*a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout


def append(rec):
    with open(out, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def fl(c):
    return f"{c['location']['file_path']}:{c['location']['start_line']}-{c['location']['end_line']}"


merges = git("log", "--merges", "--first-parent", "--format=%H", f"-n{n}").split()
done = set()
if os.path.exists(out):
    done = {json.loads(row)["merge"] for row in Path(out).read_text().splitlines()}
for m in merges:
    if m in done:
        continue
    added = {}
    cur = None
    for line in git("diff", "-U0", "--no-renames", f"{m}^1", m, "--", "*.py").splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and cur:
            mm = re.search(r"\+(\d+)(?:,(\d+))?", line)
            s, c = int(mm.group(1)), int(mm.group(2) or 1)
            added.setdefault(cur, set()).update(range(s, s + c))
    n_added = sum(len(v) for v in added.values())
    rec = {"merge": m, "subject": git("log", "-1", "--format=%s", m).strip()[:100], "py_lines_added": n_added}
    if n_added == 0:
        rec.update(flagged=[], pairs=0, secs=0)
        append(rec)
        continue
    git("checkout", "-q", "-f", m)
    t = time.time()
    js = out + ".tmp.json"
    subprocess.run(
        ["uvx", "pyscn@1.32.4", "analyze", "--select", "clones", "--json", "--no-open", "-o", js, "."],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    rec["secs"] = round(time.time() - t, 2)
    try:
        pairs = json.loads(Path(js).read_text())["clone"]["clone_pairs"] or []
    except Exception as e:
        rec.update(error=str(e))
        append(rec)
        continue

    def frac(c, added=added):
        loc = c["location"]
        f = loc["file_path"]
        rng = range(loc["start_line"], loc["end_line"] + 1)
        a = added.get(f, set())
        return sum(1 for i in rng if i in a) / len(rng)

    flagged = []
    for p in pairs:
        f1, f2 = frac(p["clone1"]), frac(p["clone2"])
        if (f1 >= 0.5 and f2 == 0) or (f2 >= 0.5 and f1 == 0):
            new, old = (p["clone1"], p["clone2"]) if f1 >= 0.5 else (p["clone2"], p["clone1"])
            flagged.append(
                {
                    "new": fl(new),
                    "old": fl(old),
                    "type": p["type"],
                    "sim": round(p["similarity"], 3),
                    "lines": new["line_count"],
                }
            )
    rec.update(pairs=len(pairs), flagged=flagged)
    append(rec)
