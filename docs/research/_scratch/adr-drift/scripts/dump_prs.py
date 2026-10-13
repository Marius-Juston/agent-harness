"""Dump every merged PR of a repo with body, issue comments, reviews and review comments (read-only, via gh api).

usage: python3 dump_prs.py OWNER/REPO OUT.json
"""

import json
import subprocess
import sys


def gh(path):
    out = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True, check=True
    ).stdout
    pages = json.loads(out)
    return [x for p in pages for x in (p if isinstance(p, list) else [p])]


repo, out = sys.argv[1], sys.argv[2]
prs = [p for p in gh(f"repos/{repo}/pulls?state=closed&per_page=100") if p.get("merged_at")]
rows = []
for p in prs:
    n = p["number"]
    rows.append(
        {
            "number": n,
            "title": p["title"],
            "body": p["body"] or "",
            "merged_at": p["merged_at"],
            "head": p["head"]["ref"],
            "comments": [
                {"user": c["user"]["login"], "body": c["body"]} for c in gh(f"repos/{repo}/issues/{n}/comments")
            ],
            "reviews": [
                {"user": r["user"]["login"], "state": r["state"], "body": r["body"] or ""}
                for r in gh(f"repos/{repo}/pulls/{n}/reviews")
            ],
            "review_comments": [
                {"user": c["user"]["login"], "path": c["path"], "body": c["body"]}
                for c in gh(f"repos/{repo}/pulls/{n}/comments")
            ],
        }
    )
json.dump(rows, open(out, "w"), indent=1)  # noqa: SIM115 - research script, kept as run
print(
    repo,
    len(rows),
    "merged PRs;",
    sum(len(r["comments"]) + len(r["reviews"]) + len(r["review_comments"]) for r in rows),
    "comments",
)
