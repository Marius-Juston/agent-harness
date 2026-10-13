"""Embedding throughput of potion-base-8M on the PyPI index (pypi_index.py output), extrapolated to all of PyPI.

Usage: uv run --with model2vec python embed_time.py <pypi index jsonl>
"""

import json
import sys
import time
from pathlib import Path

from model2vec import StaticModel

ALL_PYPI = 941_619  # packages.ecosyste.ms registry count, 2026-10-13

rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
m = StaticModel.from_pretrained("minishlab/potion-base-8M")
texts = [f"{r['name']} {r['summary']} {' '.join(r['keywords'][:10])}" for r in rows]
t = time.time()
v = m.encode(texts)
dt = time.time() - t
print(
    f"rows {len(rows)} with summary {sum(bool(r['summary']) for r in rows)} with repo {sum(bool(r['repo']) for r in rows)}"
)
print(f"min downloads in top {len(rows)}: {min(r['downloads'] for r in rows)}")
print(f"embed {len(texts)} texts: {dt:.1f}s = {len(texts) / dt:.0f}/s; dim {v.shape[1]}; float32 bytes {v.nbytes}")
print(
    f"extrapolated {ALL_PYPI:,} packages: {ALL_PYPI / (len(texts) / dt) / 60:.1f} min, "
    f"{ALL_PYPI * v.shape[1] * 4 / 1e6:.0f} MB float32"
)
