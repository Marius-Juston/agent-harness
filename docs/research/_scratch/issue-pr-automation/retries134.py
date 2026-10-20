"""The integrate retries counted the command-sequences.md way, classed by what changed before each one (#346 r1).

Usage: python3 retries134.py CACHE.jsonl.gz GH.json
command-sequences.md (detector G) counts a retry as a `dev integrate` call whose PR key (the number after
`dev integrate`, or '?' if none) was already tried in the same lead transcript. This repeats that count with
bookkeeping.py's cache, then classes each numbered retry against the previous try of the same PR in that session,
using GitHub's commits, force pushes, comments and reviews (bookkeeping.between) and the flags on the two calls.
"""

import json
import re
import sys
from collections import Counter

import bookkeeping as B

ids, trs = B.load(sys.argv[1])
with open(sys.argv[2]) as fh:
    raw = json.load(fh)
gh = {p: {x["number"]: x for x in v["prs"]} for p, v in raw.items()}
n_calls = retries = 0
cls = Counter()
for tr in trs:
    if tr["role"] != "lead":
        continue
    last = {}
    for c in tr["calls"]:
        if c["tool"] != "Bash" or "dev integrate" not in B.labels(c):
            continue
        n_calls += 1
        m = re.search(r"dev integrate\s+(\d+)", c["cmd"])
        key = m.group(1) if m else "?"
        prev = last.get(key)
        last[key] = c
        if prev is None:
            continue
        retries += 1
        if key == "?":
            cls["no PR number (loop variable, --help): not classed"] += 1
            continue
        repo = B.repo_of(c["cmd"], tr["project"])
        w = B.between(gh, repo, int(key), prev["tsf"], c["tsf"])
        fa = {f for f in B.FLAGS if f in prev["cmd"]}
        fb = {f for f in B.FLAGS if f in c["cmd"]}
        if w is None:
            k = "PR not found on GitHub"
        elif w["new_commit"]:
            k = "new head commit"
        elif w["new_feedback"]:
            k = "same head, new comment/review"
        elif fb - fa:
            k = "same head, same feedback, lead added a flag"
        else:
            k = "same head, same feedback, same flags"
        cls[k] += 1
print(f"lead dev integrate calls {n_calls}, retries in the same session {retries}")
for k, v in cls.most_common():
    print(f"{v:4d}  {k}")
