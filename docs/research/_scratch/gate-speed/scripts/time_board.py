"""Time the parts of the gate's board stage (#520): the issue fetch, merged_closes and path_history, each once.

Usage (from the repository root): uv run python docs/research/_scratch/gate-speed/scripts/time_board.py
"""

import time

from devkit import board

t = time.perf_counter()
issues = board.fetch(with_comments=True, links=board.ALL_LINK_FIELDS)
print(f"fetch (with comments, links): {time.perf_counter() - t:.2f} s, {len(issues)} issues")
for name in ("merged_closes", "path_history"):
    t = time.perf_counter()
    getattr(board, name)()
    print(f"{name}: {time.perf_counter() - t:.2f} s")
