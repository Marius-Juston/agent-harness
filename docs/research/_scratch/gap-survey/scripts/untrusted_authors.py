"""Who writes the text `dev board brief` hands to agents? (#498, prompt-injection exposure)

Counts issue and comment authors over every issue of a repository (open and closed), split into the
authenticated owner and everyone else, and lists which `brief` sections would carry a non-owner's text.
Usage: uv run python docs/research/_scratch/gap-survey/scripts/untrusted_authors.py [owner/repo]
"""

import collections
import json
import subprocess
import sys


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else ""
    me = gh("api", "user", "--jq", ".login").strip()
    extra = ["--repo", repo] if repo else []
    issues = json.loads(
        gh("issue", "list", "--state", "all", "--limit", "2000", "--json", "number,author,comments,state", *extra)
    )
    issue_authors = collections.Counter((i.get("author") or {}).get("login", "ghost") for i in issues)
    comment_authors = collections.Counter(
        (c.get("author") or {}).get("login", "ghost") for i in issues for c in i.get("comments") or []
    )
    n_comments = sum(comment_authors.values())
    print(f"issues: {len(issues)} ({sum(i['state'] == 'OPEN' for i in issues)} open); comments: {n_comments}")
    print(
        f"issue authors: owner {issue_authors[me]}, others {len(issues) - issue_authors[me]}: "
        f"{dict((k, v) for k, v in issue_authors.items() if k != me)}"
    )
    print(
        f"comment authors: owner {comment_authors[me]}, others {n_comments - comment_authors[me]}: "
        f"{dict((k, v) for k, v in comment_authors.items() if k != me)}"
    )


if __name__ == "__main__":
    main()
