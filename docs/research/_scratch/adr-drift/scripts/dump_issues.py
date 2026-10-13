"""Dump every issue (not PR) of a repo with body, labels, state and comments (read-only, via gh api).

usage: python3 dump_issues.py OWNER/REPO OUT.json
"""

import json
import subprocess
import sys


def gh(path):
    out = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True, check=True
    ).stdout
    return [x for p in json.loads(out) for x in (p if isinstance(p, list) else [p])]


repo, out = sys.argv[1], sys.argv[2]
issues = [i for i in gh(f"repos/{repo}/issues?state=all&per_page=100") if "pull_request" not in i]
comments = gh(f"repos/{repo}/issues/comments?per_page=100")
by = {}
for c in comments:
    by.setdefault(int(c["issue_url"].rsplit("/", 1)[1]), []).append(c["body"] or "")
rows = [
    {
        "number": i["number"],
        "title": i["title"],
        "state": i["state"],
        "body": i["body"] or "",
        "labels": [l["name"] for l in i["labels"]],  # noqa: E741 - research script, kept as run
        "created_at": i["created_at"],
        "closed_at": i["closed_at"],
        "comments": by.get(i["number"], []),
    }
    for i in issues
]
json.dump(rows, open(out, "w"), indent=1)  # noqa: SIM115 - research script, kept as run
print(repo, len(rows), "issues,", sum(r["state"] == "open" for r in rows), "open")
