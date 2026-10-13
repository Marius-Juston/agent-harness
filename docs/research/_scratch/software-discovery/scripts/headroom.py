"""PROTOCOL.md amendment 4 item 16, proxy: share of TUNE needs with a gold library in an index's top 100 that is absent
from the `dev reuse` pool (run_reuse_snapshot.py). The index here is the round-1 proxy (top 30k PyPI by downloads,
potion-base-8M, dense only), not P1 (full PyPI, BM25 + potion-retrieval-32M); A2's B0 names are not in the pool yet.

Usage: uv run --with model2vec --with numpy python headroom.py <snapshot_dir> <recall_bench cache_dir> <pypi index jsonl>
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP, CACHE, INDEX = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.argv = [sys.argv[0], CACHE]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)


def main() -> None:
    split = json.loads((HERE / "data" / "split.json").read_text())
    text = {n["id"]: n for n in rb.load_text_needs()}
    rk = rb.Ranker()
    px = rb.PypiIndex(INDEX, rk)
    rows = []
    for f in SNAP.glob("*.json"):
        d = json.loads(f.read_text())
        if d["id"] not in split["tune"]:
            sys.exit(f"{d['id']} is not a TUNE need")
        n = text[d["id"]]
        pool = {(c.get("repo") or "").removeprefix("github.com/").lower() for c in d["candidates"]}
        top = {i["name"] for i in px.search(rk.vec([n["q0"], *n["para"]]), k=100)}
        rows.append((d["id"], bool(n["gold"] & pool), bool(n["gold"] & top), sorted(n["gold"] & top - pool)))
    k = len(rows)
    extra = [r for r in rows if r[3]]
    only = [r for r in rows if r[2] and not r[1]]
    print(f"TUNE needs: {k}; gold in dev reuse pool {sum(r[1] for r in rows)}; gold in index top 100 {sum(r[2] for r in rows)}")
    print(f"needs with a gold library in index top 100 absent from the pool: {len(extra)}/{k} = {len(extra) / k:.2f}")
    print(f"needs whose only recalled gold is from the index: {len(only)}/{k} = {len(only) / k:.2f}")
    for r in extra:
        print(f"- {r[0]}: {', '.join(r[3])}")


if __name__ == "__main__":
    main()
