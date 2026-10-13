"""Snapshot `dev reuse` (PR #171, devkit/reuse.py @ 4d61af0) on benchmark needs, with every recalled candidate enriched,
so ranking configurations (data/configs.json) can be replayed offline without new API calls (PROTOCOL.md step 6).

Usage (from an agent-harness checkout, with PR #171's devkit first on the path):
  git archive 4d61af0 devkit | tar -x -C <dir>
  PYTHONPATH=<dir> uv run --with model2vec python run_reuse_snapshot.py <out_dir> --split tune|test [--limit k]
      [--bench-cache <recall_bench cache_dir>]   (needed for TEST: it adds the PyMigBench needs)

Per need it writes <out_dir>/<sha1(id)>.json: the queries, the re-rank that ran, and every
candidate with name, found_by, R, prior, stars, dependents, downloads, quality Q, gates and status. It replays
search() step by step, but enriches, verifies and gates every candidate any config in configs.json could show at n=10
(the 20 best by prior, the 10 best by R, and all within 0.20 of the best R); the rest are kept as NOT_ENRICHED for
recall analysis. R0 replayed on this snapshot is arm A1.
"""

import dataclasses
import hashlib
import json
import sys
import time
from pathlib import Path

from devkit import reuse  # PR #171's module (PYTHONPATH)

HERE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
N = 10  # results shown per need in the benchmark (hit@10 is the widest metric)
FLOOR = 0.20  # configs.json: the union rule's R floor, and the widest tier
OUT.mkdir(parents=True, exist_ok=True)


def needs_for(split_name: str) -> list[dict]:
    split = json.loads((HERE / "data" / "split.json").read_text())
    text = {n["id"]: n for n in json.loads((HERE / "data" / "needs_raw.json").read_text())}
    para = json.loads((HERE / "data" / "paraphrases.json").read_text())
    for n in text.values():
        n["para"] = para[n["id"]]
    for n in json.loads((HERE / "data" / "pilot_needs.json").read_text()):
        text[n["id"]] = n
    out = [text[i] for i in split[split_name] if i in text]
    if "--bench-cache" in sys.argv and any(i.startswith("migration / ") for i in split[split_name]):
        sys.argv[1:1] = [sys.argv[sys.argv.index("--bench-cache") + 1]]  # recall_bench reads its cache from argv[1]
        import recall_bench as rb
        mig = {n["id"]: n for n in rb.load_migration_needs(sample=False)}
        del sys.argv[1]
        for i in split[split_name]:
            if i in mig:  # a migration need's only query is the source package's PyPI summary (+ its keywords)
                n = mig[i]
                kw = n["keywords"].replace(",", " ").strip()
                out.append({"id": i, "q0": n["summary"], "para": [kw] if kw else []})
    return out


def main() -> None:
    split_name = sys.argv[sys.argv.index("--split") + 1] if "--split" in sys.argv else "tune"
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    needs = needs_for(split_name)[:limit]
    encoder, why = reuse.load_encoder()
    client = reuse.client()
    for k, n in enumerate(needs):
        f = OUT / (hashlib.sha1(n["id"].encode(), usedforsecurity=False).hexdigest() + ".json")
        if f.exists():
            continue
        queries = [n["q0"], *n["para"]]
        t0 = time.time()
        notes: list[str] = []
        # search() of PR #171, step by step, except that every recalled candidate is enriched, verified and gated
        found = list(reuse.recall(client, queries, "pypi", notes).values())
        rerank = reuse.rank_relevance(found, queries, encoder, why)
        tiers, osi = reuse.licence_lists(client, notes) if found else (None, None)
        # Enough for every config in configs.json at n = 10: the 2n best by prior (prior2n), the n best by R, and every
        # candidate within 0.20 of the best R (the union rule's adoption pool and both tier rules).
        best = max((c.relevance for c in found), default=0.0)
        enrich_set = ({c.key() for c in sorted(found, key=lambda c: -c.prior)[:2 * N]}
                      | {c.key() for c in sorted(found, key=lambda c: -c.relevance)[:N]}
                      | {c.key() for c in found if c.relevance >= best - FLOOR})
        rows = []
        for c in found:
            if c.key() not in enrich_set:
                c.status = "NOT_ENRICHED"
                rows.append({key: v for key, v in dataclasses.asdict(c).items() if key != "record"})
                continue
            reuse.enrich(client, c, "pypi", notes)
            ok = reuse.verify(client, c, notes)
            now = client.clock()
            c.gates = reuse.gates(c, now, tiers, False)
            c.status = reuse.status(c.gates) if ok else "UNVERIFIED"
            c.quality = reuse.quality(c, now)
            c.score = round(c.relevance + reuse.LAMBDA * c.quality, 4)
            if flag := reuse.osi_flag(c.license, osi):
                c.flags.append(flag)
            rows.append({key: v for key, v in dataclasses.asdict(c).items() if key != "record"})
        f.write_text(json.dumps({"id": n["id"], "queries": queries, "rerank": rerank, "seconds": round(time.time() - t0, 1),
                                 "date": time.strftime("%Y-%m-%d"), "candidates": rows,
                                 "notes": list(dict.fromkeys(notes))}, default=str))
        print(f"{k + 1}/{len(needs)} {n['id']}: {len(rows)} candidates, {time.time() - t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
