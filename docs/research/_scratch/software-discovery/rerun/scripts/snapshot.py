"""Snapshot the shipped `dev reuse` (devkit/reuse.py of the checkout on PYTHONPATH) on rerun needs, plus `dev reuse
check` on every agent-named package, so arms A1, A2, A3, B0, B1, B0W and B1W replay offline (#176 rerun PROTOCOL.md
rules R6, R9).

Usage (from an agent-harness checkout at the commit under test; model2vec must be importable):
  uv run --with model2vec python <this> <out_dir> --needs data/needs.json --split tune|test
      [--names b0=<answers.json> --names b0w=<answers.json> ...] [--ids data/ids.json] [--shard k/m]

Per need it writes <out_dir>/<sha1(id)>.json with:
- `search`: reuse.search(queries, "pypi", n=10) run unchanged, with two read-only taps: `pool` is every candidate
  reuse.recall() returned (name, repo, R), and `ordered` is the full list reuse.order() received and returned before
  the cut to n (the judged, non-FAIL shortlist in shipped order). `kept` is search()'s own top 10 (arm A1).
- `named[tag]`: reuse.check(names) for each tag's names, in the agent's order (NOT FOUND, UNVERIFIED or gated), with
  R set by reuse.rank_relevance against the same queries and S = R + LAMBDA * Q recomputed, as search() scores.
- `devkit`: the commit and the SHA-256 of devkit/reuse.py, LAMBDA and PASS_BEFORE_HOLD.
"""

import dataclasses
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from devkit import reuse  # the checkout under test (PYTHONPATH or the project itself)

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def arg(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def args(name: str) -> list[str]:
    return [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == name]


def row(c: reuse.Candidate) -> dict:
    return {k: v for k, v in dataclasses.asdict(c).items() if k != "record"}


def devkit_info() -> dict:
    src = Path(reuse.__file__)
    head = subprocess.run(
        ["git", "-C", str(src.parent), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(src.parent), "status", "--porcelain", "--", str(src)], capture_output=True, text=True
    ).stdout.strip()
    return {
        "commit": head,
        "reuse_py_dirty": bool(dirty),
        "reuse_py_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "LAMBDA": reuse.LAMBDA,
        "PASS_BEFORE_HOLD": reuse.PASS_BEFORE_HOLD,
        "DEFAULT_N": reuse.DEFAULT_N,
    }


def main() -> None:
    split = json.loads(Path(arg("--split-file", str(Path(arg("--needs")).parent / "split.json"))).read_text())
    part = arg("--split", "tune")
    needs = {n["id"]: n for n in json.loads(Path(arg("--needs")).read_text())}
    idmap = json.loads(Path(arg("--ids")).read_text()) if arg("--ids") else {}  # opaque id -> need id
    names: dict[str, dict] = {}
    for kv in args("--names"):
        tag, path = kv.split("=", 1)
        raw = json.loads(Path(path).read_text())
        names[tag] = {idmap.get(k, k): v for k, v in raw.items() if not k.startswith("_")}
    ids = list(split[part])
    if arg("--shard"):
        k, m = (int(x) for x in arg("--shard").split("/"))
        ids = ids[k::m]
    info = devkit_info()
    encoder, why = reuse.load_encoder()
    if encoder is None:
        sys.exit(f"no encoder ({why}): the shipped search would fall back to BM25; refusing to snapshot")
    client = reuse.client()
    real_recall, real_order = reuse.recall, reuse.order
    for k, i in enumerate(ids):
        n = needs[i]
        f = OUT / (hashlib.sha1(i.encode(), usedforsecurity=False).hexdigest() + ".json")
        if f.exists():
            continue
        queries = [n["need"], *n["also_described_as"]]
        tap: dict = {}

        def recall(*a, tap=tap, **kw):
            tap["pool"] = got = real_recall(*a, **kw)
            return got

        def order(kept, tap=tap):
            tap["ordered"] = out = real_order(kept)
            return out

        reuse.recall, reuse.order = recall, order
        t0 = time.time()
        try:
            rep = reuse.search(client, queries, "pypi", n=reuse.DEFAULT_N, encoder=encoder, why=why)
        finally:
            reuse.recall, reuse.order = real_recall, real_order
        named = {}
        for tag, by_id in names.items():
            want = [x for x in by_id.get(i, []) if isinstance(x, str) and x.strip()]
            got = reuse.check(client, want, "pypi")["candidates"] if want else []
            live = [c for c in got if c.status not in ("NOT FOUND", "UNVERIFIED")]
            if live:
                reuse.rank_relevance(live, queries, encoder, why)
                for c in live:
                    c.score = round(c.relevance + reuse.LAMBDA * c.quality, 4)
            named[tag] = {"asked": want, "candidates": [row(c) for c in got]}
        f.write_text(
            json.dumps(
                {
                    "id": i,
                    "queries": queries,
                    "date": time.strftime("%Y-%m-%d"),
                    "seconds": round(time.time() - t0, 1),
                    "devkit": info,
                    "search": {
                        "rerank": rep["rerank"],
                        "recalled": rep["recalled"],
                        "pool": [
                            {"name": c.name, "repo": c.repo, "relevance": c.relevance}
                            for c in tap.get("pool", {}).values()
                        ],
                        "ordered": [row(c) for c in tap.get("ordered", [])],
                        "kept": [row(c) for c in rep["candidates"]],
                        "notes": rep["notes"],
                    },
                    "named": named,
                },
                default=str,
            )
        )
        print(f"{k + 1}/{len(ids)} {i}: {rep['recalled']} recalled, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
