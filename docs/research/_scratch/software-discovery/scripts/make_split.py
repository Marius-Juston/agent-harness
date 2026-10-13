"""Freeze the TUNE/TEST split for recall_bench.py (PROTOCOL.md step 1). Run once, before any tuning run.

Usage: uv run python make_split.py <cache_dir>      (writes data/split.json and prints its SHA-256)

Rules, fixed before any result of strata A, P or M was seen:
1. P (the lead's 16 pilot needs, used to design the method) -> TUNE only.
2. M (PyMigBench migration targets, 40 sampled) -> TEST only: the gold source that TUNE never sees.
3. C (10 code-snippet cases) -> its own TEST-only set, `code_test`.
4. A (awesome-python, 120): a need whose gold shares a repo with a P need is the same need as a pilot one -> TUNE.
   The rest is shuffled with random.Random("169-split") and filled into TUNE until A has 48 TUNE needs (40%); the
   remaining 72 are TEST.
5. Every C case and TEST need whose gold shares a repo with any TUNE need is listed in `overlaps_tune`, so the TEST
   report can be read with and without them.
"""

import hashlib
import json
import random
import sys
from pathlib import Path

sys.argv = [sys.argv[0], sys.argv[1]]
import recall_bench as rb  # noqa: E402 (needs the cache argument first)


def main() -> None:
    text = rb.load_text_needs()
    a = [n for n in text if n["stratum"] == "A"]
    p = [n for n in text if n["stratum"] == "P"]
    m = rb.load_migration_needs()
    pilot_gold = set().union(*(n["gold"] for n in p))
    forced = sorted(n["id"] for n in a if n["gold"] & pilot_gold)
    rest = sorted(n["id"] for n in a if n["id"] not in forced)
    random.Random("169-split").shuffle(rest)  # noqa: S311 (split, not security)
    a_tune = forced + rest[: 48 - len(forced)]
    a_test = rest[48 - len(forced) :]
    tune = sorted(a_tune) + sorted(n["id"] for n in p)
    test = sorted(a_test) + sorted(n["id"] for n in m)
    by_id = {n["id"]: n for n in [*text, *m]}
    tune_gold = set().union(*(by_id[i]["gold"] for i in tune))
    overlaps = sorted(
        [i for i in test if by_id[i]["gold"] & tune_gold]
        + [c["id"] for c in rb.CODE_CASES if rb.canon(c["gold"]) & tune_gold]
    )
    split = {
        "protocol": "PROTOCOL.md (lead, 2026-10-13)",
        "rules": __doc__.split("Rules")[1].strip(),
        "tune": tune,
        "test": test,
        "code_test": [c["id"] for c in rb.CODE_CASES],
        "forced_to_tune_by_pilot_overlap": forced,
        "overlaps_tune": overlaps,
        "counts": {
            "tune_A": len(a_tune),
            "tune_P": len(p),
            "test_A": len(a_test),
            "test_M": len(m),
            "code_test": len(rb.CODE_CASES),
        },
    }
    out = Path(__file__).resolve().parent / "data" / "split.json"
    if out.exists():
        sys.exit(f"{out} exists: the split is frozen. Delete it only if no tuning run has used it.")
    body = json.dumps(split, indent=1, ensure_ascii=False) + "\n"
    out.write_text(body)
    print(json.dumps(split["counts"]), "forced:", forced)
    print("overlaps_tune:", overlaps)
    print("sha256", hashlib.sha256(body.encode()).hexdigest(), out.name)


if __name__ == "__main__":
    main()
