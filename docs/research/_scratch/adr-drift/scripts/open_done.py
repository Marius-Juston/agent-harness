"""Open issues that may already be done: every DONE WHEN box ticked, a merged PR that names the issue,
or a comment saying it is done. Candidates only; each is confirmed by hand in r1-taxonomy.md.

usage: python3 open_done.py DIR PREFIX   (reads PREFIX_issues.json and PREFIX_prs.json from DIR)
"""

import json
import re
import sys
from pathlib import Path

d, p = Path(sys.argv[1]), sys.argv[2]
issues = json.load(open(d / f"{p}_issues.json"))  # noqa: SIM115 - research script, kept as run
prs = json.load(open(d / f"{p}_prs.json"))  # noqa: SIM115 - research script, kept as run
DONE_SAY = re.compile(
    r"\b(already (done|met|shipped|exists)|done in (PR )?#\d+|met by|DONE WHEN .{0,20}met|is done|fixed in #\d+|merged in PR)\b",
    re.I,
)
n_open = 0
rows = []
for i in issues:
    if i["state"] != "open" or i["title"].startswith("Epic"):
        continue
    n_open += 1
    n = i["number"]
    box = re.findall(r"^- \[( |x)\]", i["body"], re.M)
    ticked = bool(box) and all(b == "x" for b in box)
    refs = [
        pr["number"]
        for pr in prs
        if re.search(rf"(Refs|Closes|Fixes|closes|refs)\s+#{n}\b|\(#{n}\)", pr["title"] + "\n" + pr["body"])
    ]
    says = [c[:140].replace("\n", " ") for c in i["comments"] if DONE_SAY.search(c) and "devkit:claim" not in c]
    if ticked or refs or says:
        rows.append((n, i["title"][:80], f"boxes {box.count('x')}/{len(box)}", f"merged PRs {refs}", says[:2]))
print(f"{p}: {n_open} open non-epic issues, {len(rows)} candidates")
for r in rows:
    print(" ", *r)
