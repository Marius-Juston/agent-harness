"""A4 with the leaking Context sentences removed (r2 B0, leak-removed) against TF-IDF title + DONE WHEN (A2), which
indexes no Context, on Tier B TEST and DEV (#346 audit N1). Reads cached rankings only; run from the worktree root:
    uv run --no-project --with numpy python docs/research/_scratch/issue-pr-automation/leak_vs_tfidf.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dedup_eval as ev

raw = json.loads((Path.cwd() / ".scratch/dedup/bm25v/B0_leak-removed.json").read_text())
a4x = {r: {int(q): v for q, v in d.items()} for r, d in raw.items()}
a2 = ev.rankings("A2")
for split in ("DEV", "TEST"):
    g = ev.gold("B", split)
    x, y = ev.per_query(a4x, g, 3), ev.per_query(a2, g, 3)
    d = ev.boot([p - q for p, q in zip(x, y, strict=True)])
    print(
        f"{split}: A4x R@3 {sum(x) / len(x):.3f}, A2 {sum(y) / len(y):.3f}, A4x - A2 {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]"
    )
