"""Agent-harness-only view of the frozen dedup evaluation (#346 synthesis).

Reuses dedup_eval.py's cached rankings (.scratch/dedup/rank_<arm>.json), its frozen labels and its DEV-tuned
thresholds; nothing is re-tuned. Reports, for this repository only:
- Tier B TEST (held-out by query hash): recall@1/3/5 with a bootstrap CI, alert precision/recall at the frozen
  threshold, for every query and for the queries that are closed issues;
- Tier A (curated duplicates, never tuned on): recall@3 per query.
Run from the worktree root: python3 docs/research/_scratch/issue-pr-automation/dedup_own_repo.py
Needs only numpy (`uv run --no-project --with numpy python ...`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
from dedup_eval import ARMS, DESC, alerts, all_gold, boot, gold, per_query, rankings  # noqa: E402
from dedup_labels import is_test, load  # noqa: E402

REPO = "agent-harness"
th = json.loads((HERE / "dedup_thresholds.json").read_text())
iss = load(REPO)
closed = {n for n, i in iss.items() if i["state"] == "CLOSED"}
print(f"{REPO}: {len(iss)} issues, {len(closed)} closed")


def only(g: dict, keep) -> dict:
    return {k: v for k, v in g.items() if k[0] == REPO and keep(k[1])}


test_q_all = [(REPO, q) for q in iss if is_test(REPO, q)]
for label, keep in (("all queries", lambda q: True), ("closed queries", lambda q: q in closed)):
    g = only(gold("B", "TEST"), keep)
    qs = [k for k in test_q_all if keep(k[1])]
    print(
        f"\nTier B TEST, {label}: {len(g)} labelled queries, {sum(len(v) for v in g.values())} pairs, {len(qs)} filings"
    )
    for a in ("A2", "A3", "A4"):
        rk = rankings(a)
        r3 = boot(per_query(rk, g, 3))
        m = alerts(rk, all_gold(), th[a], qs, g)
        print(
            f"{a} {DESC[a]:32s} R@1 {np.mean(per_query(rk, g, 1)):.3f} R@3 {r3[0]:.3f} [{r3[1]:.3f},{r3[2]:.3f}] "
            f"R@5 {np.mean(per_query(rk, g, 5)):.3f} | alertP {m['precision']:.3f} alertR3 {m['recall3']:.3f} "
            f"rate {m['alert_rate']:.2f} shown {m['shown_per_filing']:.2f}"
        )

ga = only(gold("A", None), lambda q: True)
print(f"\nTier A ({REPO}): {len(ga)} queries")
for a in ARMS:
    rk = rankings(a)
    hits = per_query(rk, ga, 3)
    print(f"{a} {DESC[a]:38s} R@3 {np.mean(hits):.2f} ({int(sum(hits))}/{len(hits)})")
