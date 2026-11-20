"""Median context tokens per builder turn in each transcript corpus (#497), to turn wasted calls into tokens.

Usage (repository root): uv run python docs/research/_scratch/docs-current/scripts/turn_cost.py <name suffix> [...]
Each extra tool call costs about one assistant turn, which re-reads the whole context (mostly cache reads).
"""

import pathlib
import statistics as st
import sys

from devkit import stats

base = pathlib.Path.home() / ".claude" / "projects"
for suf in sys.argv[1:]:
    d = next(q for q in base.iterdir() if q.name.endswith(suf))
    every = [r for r in stats.load_runs([d]) if r.messages]
    runs = [r for r in every if r.kind == "harness-ops:builder"]
    per = [(r.cached + r.fresh) / r.messages for r in runs]
    models = sorted({t["model"] for r in runs for t in r.turns})
    print(
        f"{suf[-12:]}: builder runs={len(runs)} median context tokens per turn={st.median(per):,.0f} "
        f"median turns per run={st.median(r.messages for r in runs)} models={models}; "
        f"all runs={len(every)} all turns={sum(r.messages for r in every):,}"
    )
