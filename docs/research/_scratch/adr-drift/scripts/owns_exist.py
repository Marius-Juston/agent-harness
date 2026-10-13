"""Open issues whose `## Owns` paths match nothing in the repository: the issue targets a file that is gone.
usage: python3 owns_exist.py ISSUES_JSON REPO_DIR
"""

import json
import re
import sys
from pathlib import Path

issues, repo = json.load(open(sys.argv[1])), Path(sys.argv[2])  # noqa: SIM115 - research script, kept as run
n_open = n_paths = 0
for i in issues:
    if i["state"] != "open":
        continue
    m = re.search(r"## Owns\n(.*?)(\n## |\Z)", i["body"], re.S)
    if not m:
        continue
    n_open += 1
    for p in re.findall(r"`([^`]+)`", m.group(1)):
        n_paths += 1
        pat = p.rstrip("/")
        hit = list(repo.glob(pat)) if any(c in pat for c in "*?[") else [repo / pat] if (repo / pat).exists() else []
        if not hit:
            print(f"#{i['number']}: Owns `{p}` matches nothing | {i['title'][:70]}")
print(f"RESULT: {n_open} open issues with Owns, {n_paths} paths checked")
