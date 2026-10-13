"""Build the B0 input for TEST (PROTOCOL.md amendment 5 item 20) in the TUNE format (id, need, also_described_as).

Usage: uv run --no-project python make_test_inputs.py <recall_bench cache>

A needs: id = the awesome-python label (as on TUNE), need = q0, also_described_as = the two frozen paraphrases.
M needs: the agent sees exactly what `dev reuse` sees (run_reuse_snapshot.py): need = the source package's PyPI summary,
also_described_as = its keywords if any. The id is opaque ("M-01" ...), because "migration / <name>" would tell the
agent which package is being replaced, which no other arm is told. data/b0_test_ids.json maps the opaque ids back;
data/m_sources.json lists each M need's source package and repository (excluded from every arm, amendment 5 item 22).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0], sys.argv[1]]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)


def main() -> None:
    split = json.loads((HERE / "data" / "split.json").read_text())
    para = json.loads((HERE / "data" / "paraphrases.json").read_text())
    raw = {n["id"]: n for n in json.loads((HERE / "data" / "needs_raw.json").read_text())}
    mig = {n["id"]: n for n in rb.load_migration_needs(sample=False)}
    out, ids, k = [], {}, 0
    for i in split["test"]:
        if i in raw:
            out.append({"id": i, "need": raw[i]["q0"], "also_described_as": para[i]})
        else:
            k += 1
            kw = mig[i]["keywords"].replace(",", " ").strip()
            out.append({"id": f"M-{k:02d}", "need": mig[i]["summary"], "also_described_as": [kw] if kw else []})
            ids[f"M-{k:02d}"] = i
    (HERE / "data" / "b0_test_input.json").write_text(json.dumps(out, indent=1))
    (HERE / "data" / "b0_test_ids.json").write_text(json.dumps(ids, indent=1))
    src = {i: [mig[i]["pkg"], mig[i]["src_repo"] or ""] for i in split["test"] if i in mig}
    (HERE / "data" / "m_sources.json").write_text(json.dumps(src, indent=1))  # amendment 5 item 22
    print(f"B0 TEST input: {len(out)} needs ({k} M, opaque ids)")


if __name__ == "__main__":
    main()
