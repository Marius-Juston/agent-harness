"""CLAUDE.md / AGENTS.md growth and churn per revision (all commits that touched the file, oldest first). Read-only.

usage: python3 -I r1_growth.py REPO REF LABEL FILE [FILE ...]
Per revision: words (whitespace split), rule lines (bullets or numbered items outside fences), rule lines added and
removed against the previous revision (exact-text set difference), and whether the commit is agent-made (author Claude
or a Claude Co-Authored-By trailer). Prints one row per revision and a summary.
"""

import re
import subprocess
import sys

repo, ref, label, *paths = sys.argv[1:]


def git(*a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout


RULE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")


def rules(text):
    out, fence = set(), False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence and RULE.match(ln):
            out.add(re.sub(r"\s+", " ", ln.strip()))
    return out


log = git("log", "--reverse", "--format=%H%x09%ct%x09%an", ref, "--", *paths).splitlines()
prev_rules, prev_words, rows = set(), 0, []
for ln in log:
    sha, ts, an = ln.split("\t")
    text = ""
    for p in paths:
        text += git("show", f"{sha}:{p}") + "\n"
    body = git("log", "-1", "--format=%B", sha).lower()
    agent = an.lower().startswith("claude") or "co-authored-by: claude" in body
    r = rules(text)
    w = len(text.split())
    rows.append((sha[:8], int(ts), w, len(r), len(r - prev_rules), len(prev_rules - r), agent, w - prev_words))
    prev_rules, prev_words = r, w
t0 = rows[0][1]
print(f"# {label}: {len(rows)} revisions")
print("sha      day   words rules +rules -rules agent dwords")
for sha, ts, w, nr, a, d, ag, dw in rows:
    print(f"{sha} {(ts - t0) / 86400:5.1f} {w:6} {nr:5} {a:6} {d:6} {'A' if ag else 'H'} {dw:+6}")
adds = sum(r[4] for r in rows[1:])
dels = sum(r[5] for r in rows[1:])
shrink = sum(1 for r in rows[1:] if r[7] < 0)
print(
    f"SUMMARY {label} revisions={len(rows)} days={(rows[-1][1] - t0) / 86400:.0f} words {rows[0][2]}->{rows[-1][2]} "
    f"max={max(r[2] for r in rows)} rules {rows[0][3]}->{rows[-1][3]} added={adds} removed={dels} "
    f"shrinking_revisions={shrink} agent_revisions={sum(r[6] for r in rows)}"
)
