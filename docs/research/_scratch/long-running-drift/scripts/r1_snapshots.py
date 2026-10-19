"""Mechanical stale rate over time from r1_claims_replay.py snapshots: stale / (valid + stale) per doc type, and by claim
age (older or younger than 30 days). `never` claims (never valid while present) are left out of the rate.

usage: python3 -I r1_snapshots.py OUT.json [OUT.json ...]
"""

import collections
import datetime
import json
import sys

TYPES = ["instruction", "agent", "skill", "spec", "research", "docstring", "readme", "otherdoc", "plan", "vendored"]
for path in sys.argv[1:]:
    d = json.load(open(path))  # noqa: SIM115 - research script
    print(f"# {d['label']}  ({d['n_commits']} first-parent commits)")
    print("date        idx  claims  stale%  " + " ".join(f"{t[:6]:>9}" for t in TYPES) + "   old%  new%")
    for s in d["snapshots"]:
        c = collections.Counter()
        for k, v in s["counts"].items():
            dt, st, age = k.split("|")
            c[(dt, st)] += v
            c[("*", st)] += v
            c[("age-" + age, st)] += v

        def rate(t, c=c):
            n = c[(t, "valid")] + c[(t, "stale")]
            return f"{100 * c[(t, 'stale')] / n:5.1f}/{n:<3}" if n else "    -    "

        day = datetime.date.fromtimestamp(s["ts"]).isoformat()
        tot = c[("*", "valid")] + c[("*", "stale")]
        print(
            f"{day} {s['idx']:4} {tot:7} {100 * c[('*', 'stale')] / max(tot, 1):6.2f}  "
            + " ".join(f"{rate(t):>9}" for t in TYPES)
            + f"  {rate('age-old')}  {rate('age-new')}"
        )
