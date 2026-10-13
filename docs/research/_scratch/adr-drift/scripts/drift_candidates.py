"""Candidate drift findings: sentences in issue bodies/comments and PR bodies/comments that use drift wording.

usage: python3 drift_candidates.py DIR  (DIR holds ah_issues.json, pb_issues.json, ah_prs.json, pb_prs.json)
Prints one block per document with its matching sentences; the classification is done by hand in r1-taxonomy.md.
"""

import json
import re
import sys
from pathlib import Path

KW = re.compile(
    r"\b(drift\w*|stale|out of date|outdated|no longer|still says|still say|says .{0,40} but|"
    r"disagree\w*|mismatch\w*|contradict\w*|inconsisten\w*|does not exist|doesn't exist|not exist|"
    r"renamed|dangling|already (?:done|shipped|met|merged|fixed|exists)|re-?litigat\w*|"
    r"out of sync|diverg\w*|wrong (?:flag|option|number|count|path|name)|unknown (?:flag|option)|"
    r"never updated|left behind|forgot|copy of|copies|two places|duplicat\w*|"
    r"supersed\w*|obsolete|does not match|doesn't match|not match|no such)\b",
    re.I,
)
d = Path(sys.argv[1])
n_docs = n_hits = 0
for repo in ("ah", "pb"):
    for kind in ("issues", "prs"):
        for r in json.load(open(d / f"{repo}_{kind}.json")):  # noqa: SIM115 - research script, kept as run
            texts = [r["body"]] + [
                c if isinstance(c, str) else c["body"]
                for k in ("comments", "reviews", "review_comments")
                for c in r.get(k, [])
            ]
            sents = [s.strip() for t in texts for s in re.split(r"(?<=[.!?])\s+|\n", t or "") if KW.search(s)]
            sents = [s for s in sents if len(s) > 15]
            if sents:
                n_docs += 1
                n_hits += len(sents)
                print(f"### {repo} {kind[:-1]} #{r['number']} [{r.get('state', 'merged')}] {r['title'][:90]}")
                for s in sents[:12]:
                    print("  -", s[:260])
print(f"\n{n_docs} documents, {n_hits} sentences", file=sys.stderr)
