"""Dump every issue's body edit history (GitHub GraphQL userContentEdits; read-only) for the DONE WHEN replay.
usage: python3 r2_issue_edits.py  -> .scratch/r2/data/{ah,pb}_edits.json  {number: [[editedAt, body-after-edit], ...]}
"""

import json
import subprocess

import r2_common as c

Q = """query($repo:String!,$after:String){repository(owner:"Marius-Juston",name:$repo){issues(first:50,after:$after){
pageInfo{hasNextPage endCursor} nodes{number createdAt closedAt stateReason
userContentEdits(first:100){totalCount nodes{editedAt diff}}}}}}"""
for pre, repo in (("ah", "agent-harness"), ("pb", c.PB_NAME)):
    out, after = {}, None
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={Q}", "-f", f"repo={repo}"] + (
            ["-f", f"after={after}"] if after else []
        )
        d = json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)["data"]["repository"][
            "issues"
        ]
        for n in d["nodes"]:
            e = n["userContentEdits"]
            assert e["totalCount"] <= 100, n["number"]  # noqa: S101 - research script, kept as run
            out[n["number"]] = dict(
                stateReason=n["stateReason"], edits=sorted([x["editedAt"], x["diff"] or ""] for x in e["nodes"])
            )
        if not d["pageInfo"]["hasNextPage"]:
            break
        after = d["pageInfo"]["endCursor"]
    json.dump(out, open(c.DATA / f"{pre}_edits.json", "w"))  # noqa: SIM115 - research script, kept as run
    print(
        pre,
        len(out),
        "issues;",
        sum(len(v["edits"]) for v in out.values()),
        "edits;",
        sum(1 for v in out.values() if v["edits"]),
        "issues edited",
    )
