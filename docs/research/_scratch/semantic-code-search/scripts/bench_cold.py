"""Cold, one-shot cost of each indexed arm at HEAD, as a `dev code` subcommand would pay it with no persisted index
(#495): parse every tracked non-docs .py file, build the index, answer one question. Run in a fresh process per arm.
Prints JSON: units, seconds per phase, index bytes (the vectors or the token lists held in memory).

usage: uv run --no-project --with numpy [--with model2vec | --with fastembed] python bench_cold.py ARM
ARM: bm25 | m2v-8M | bge-small
"""

import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

T0 = time.perf_counter()
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

QUERY = "gate: a queued dev check can report another worktree's test output as its own result"


def main() -> None:
    arm = sys.argv[1]
    from corpus import units_of

    t = {"start_s": time.perf_counter() - T0}
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True).stdout.split()
    s = time.perf_counter()
    units = units_of(REPO, only={f for f in files if not f.startswith("docs/")})
    t["parse_s"] = time.perf_counter() - s
    s = time.perf_counter()
    if arm == "bm25":
        from eval_nl import query_words, words

        from devkit.board import bm25

        docs = [words(u.full) for u in units]
        t["build_s"] = time.perf_counter() - s
        s = time.perf_counter()
        scores = bm25(docs, query_words(QUERY))
        size = len(pickle.dumps(docs))
    else:
        import numpy as np

        if arm == "m2v-8M":
            from model2vec import StaticModel

            m = StaticModel.from_pretrained("minishlab/potion-base-8M")
            enc = m.encode
        else:
            from fastembed import TextEmbedding

            m = TextEmbedding("BAAI/bge-small-en-v1.5", max_length=512)
            enc = lambda xs: np.asarray(list(m.embed(xs)))  # noqa: E731
        t["load_s"] = time.perf_counter() - s
        s = time.perf_counter()
        mat = np.asarray(enc([u.full for u in units]), dtype=np.float32)
        t["build_s"] = time.perf_counter() - s
        s = time.perf_counter()
        scores = mat @ np.asarray(enc([QUERY]), dtype=np.float32)[0]
        size = mat.nbytes
    t["query_s"] = time.perf_counter() - s
    t["total_s"] = time.perf_counter() - T0
    print(json.dumps({"arm": arm, "units": len(units), "index_bytes": size, "top": float(max(scores)), **t}))


if __name__ == "__main__":
    main()
