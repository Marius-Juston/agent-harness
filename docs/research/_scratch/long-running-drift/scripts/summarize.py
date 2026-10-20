"""Revision and growth stats for each repo's primary root context file (>5 lines, >=183 days old on 2026-10-19).

Usage: python summarize.py <hist.json from history.py>
"""

import collections
import json
import statistics as st
import sys
from datetime import UTC, datetime

now = datetime(2026, 10, 19, tzinfo=UTC)
d = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
h = json.load(open(sys.argv[1]))  # noqa: SIM115
by = collections.defaultdict(list)
for r in h:
    if (r["lines_head"] or 0) > 5:
        by[r["repo"]].append(r)
P = [max(v, key=lambda r: r["commits"]) for v in by.values()]
P = [r for r in P if (now - d(r["first_date"])).days >= 183]
q = lambda v: [round(x, 2) for x in st.quantiles(v, n=4)]  # noqa: E731
age = [(now - d(r["first_date"])).days for r in P]
c = [r["commits"] for r in P]
rate = [r["commits"] / (a / 30.44) for r, a in zip(P, age)]  # noqa: B905
g = [r["lines_head"] / r["lines_first"] for r in P if r["lines_first"]]
idle = [(now - d(r["last_date"])).days for r in P]
print(
    "n",
    len(P),
    "CLAUDE.md",
    sum(r["file"] == "CLAUDE.md" for r in P),
    "AGENTS.md",
    sum(r["file"] == "AGENTS.md" for r in P),
)
print("age days median/quartiles", st.median(age), q(age))
print("commits median/quartiles", st.median(c), q(c), "max", max(c), "; one commit only", sum(x == 1 for x in c))
print("commits per month", round(st.median(rate), 2), q(rate))
print(
    "lines first->head median",
    st.median(r["lines_first"] for r in P if r["lines_first"]),
    "->",
    st.median(r["lines_head"] for r in P),
)
print(
    "growth ratio",
    round(st.median(g), 2),
    q(g),
    "grew",
    sum(x > 1 for x in g),
    "shrank",
    sum(x < 1 for x in g),
    "same",
    sum(x == 1 for x in g),
)
print("days since last edit", st.median(idle), q(idle), "; idle >90d", sum(x > 90 for x in idle))
print("distinct authors median", st.median(len(r["authors"]) for r in P))
