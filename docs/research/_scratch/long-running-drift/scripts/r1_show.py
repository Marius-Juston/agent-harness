"""Print the events of r1_claims_replay.py output for hand labelling. usage: r1_show.py OUT.json [EV ...]"""

import collections
import json
import sys

d = json.load(open(sys.argv[1]))  # noqa: SIM115 - research script
want = set(sys.argv[2:]) or {"FIX", "OPEN"}
ev = [e for e in d["events"] if e["ev"] in want]
print(collections.Counter((e["ev"], e.get("how", ""), e["dtype"], e["kind"]) for e in ev))
for e in sorted(ev, key=lambda e: (e["idx"], e["doc"])):
    days = ((e.get("fix_ts") or d["last_ts"]) - e["ts"]) / 86400
    print(
        f"{e['ev']:5} {e.get('how', ''):7} {e['kind']:4} {e['tok'][:40]:40} | {e['doc'][-55:]:55} | {e['dtype']:9} | b{e['idx']} {e['subj'][:40]} | f{e.get('fix_idx', '-')} {days:5.1f}d {e.get('fix_subj', '')[:40]}"
    )
