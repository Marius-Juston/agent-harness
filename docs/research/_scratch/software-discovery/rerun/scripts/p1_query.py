"""P1 top 100 for rerun needs (rule R10 headroom): #169's index (../../scripts/p_index.py, BM25 + potion-retrieval-32M,
RRF k=60) queried with exactly the queries `dev reuse` gets (need + also_described_as).

Usage: uv run --no-project --with bm25s --with model2vec --with numpy python p1_query.py <index dir> --split tune
           --out <top100.json> [--corpus <corpus.jsonl>]
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
import p_index  # noqa: E402


def needs(split_name: str) -> list[dict]:
    split = json.loads((HERE.parent / "data" / "split.json").read_text())
    by_id = {n["id"]: n for n in json.loads((HERE.parent / "data" / "needs.json").read_text())}
    return [{"id": i, "queries": [by_id[i]["need"], *by_id[i]["also_described_as"]]} for i in split[split_name]]


if __name__ == "__main__":
    p_index.needs = needs
    p_index.query(Path(sys.argv[1]), "potion", Path(p_index.arg("--out")))
