"""Fetch merged-PR and issue history for the conflict replay (#346, round 1, sub-question d).

Usage:
    python3 conflicts_fetch.py OWNER/REPO OUT.json

Reads, through `gh api graphql` (read-only), every pull request (merged, closed, open) with its timestamps, head and
merge commits, changed files, commits (oid, committed date, parents, headline) and comments (author, date, first 400
characters), and every issue with its body and the dates of its `devkit:claim` comments. The output holds titles and
comment text, so keep it outside the repository (repo-E is private); conflicts_replay.py prints only counts.
"""

import json
import subprocess
import sys

PR_QUERY = """query($owner: String!, $name: String!, $cursor: String) { repository(owner: $owner, name: $name) {
  pullRequests(first: 25, after: $cursor, orderBy: {field: CREATED_AT, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { number state createdAt mergedAt closedAt headRefName headRefOid baseRefName body
      mergeCommit { oid }
      closingIssuesReferences(first: 10) { nodes { number } }
      files(first: 100) { totalCount nodes { path } }
      commits(first: 100) { totalCount nodes { commit { oid committedDate messageHeadline parents(first: 2) { nodes { oid } } } } }
      comments(first: 100) { nodes { author { login } createdAt body } }
    } } } }"""

ISSUE_QUERY = """query($owner: String!, $name: String!, $cursor: String) { repository(owner: $owner, name: $name) {
  issues(first: 50, after: $cursor, orderBy: {field: CREATED_AT, direction: ASC}) {
    pageInfo { hasNextPage endCursor }
    nodes { number state createdAt closedAt title body
      comments(first: 100) { nodes { createdAt body } }
    } } } }"""


def pages(query: str, owner: str, name: str, key: str) -> list[dict]:
    out, cursor = [], None
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}", "-F", f"name={name}"]
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        data = json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)
        conn = data["data"]["repository"][key]
        out += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]


def main() -> None:
    repo, path = sys.argv[1], sys.argv[2]
    owner, name = repo.split("/")
    prs = pages(PR_QUERY, owner, name, "pullRequests")
    for p in prs:
        for c in p["comments"]["nodes"]:
            c["body"] = (c["body"] or "")[:400]
    issues = pages(ISSUE_QUERY, owner, name, "issues")
    for i in issues:
        i["claims"] = [c["createdAt"] for c in i.pop("comments")["nodes"] if "devkit:claim" in (c["body"] or "")]
    with open(path, "w") as f:
        json.dump({"repo": repo, "prs": prs, "issues": issues}, f)
    print(f"{repo}: {len(prs)} PRs, {len(issues)} issues -> {path}")


if __name__ == "__main__":
    main()
