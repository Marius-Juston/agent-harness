"""Amendment 5 check: does `dev lit search -n 20` on main after PRs #190 and #202 merged return the same lists as L0
(run on f1cfce7)? Re-runs every TEST query with the merged lit.py (LITRECALL_ALSO_LIT points at a copy of it) from
the shared cache and compares the result keys in order.

Usage: LITRECALL_ALSO_LIT=<copy of merged devkit/lit.py> uv run python check_main_l0.py
"""

import json

from common import PRIV, load
from run_arms import also_lit, queries_for


def main() -> None:
    la = also_lit()
    c = la.client()
    same = diff = 0
    for u in load("split.json")["test"]:
        saved = json.loads((PRIV / "runs" / "L0" / f"{u}.json").read_text())
        for q in queries_for(u):
            papers, _ = c.search(q, *la.plan("auto")[:1], 20, "", fallback=la.plan("auto")[1])
            now = [p.title for p in papers]
            old = [r["title"] for r in saved[q]["results"]]
            same += now == old
            diff += now != old
    print({"same": same, "different": diff})


if __name__ == "__main__":
    main()
