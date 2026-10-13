"""Snapshot `dev reuse --from` (PR #171) on the 10 code cases (set C, TEST only, PROTOCOL.md step 7).

Usage (PR #171's devkit first on the path, as for run_reuse_snapshot.py):
  PYTHONPATH=<dir> uv run --no-project --with model2vec python run_code_snapshot.py <out_dir> <recall_bench cache>

Each case's need is what `dev reuse --from <file>:<symbol>` writes (reuse.from_spec): reuse.need_from over the
symbols' source plus the file's imports, named after the first symbol; it is the only query (no --also). The rest is
run_reuse_snapshot.py's pipeline unchanged (same enrich set), so r3_score.py --split code_test replays R0 and R4.
"""

import ast
import json
import sys
from pathlib import Path

OUT, CACHE = sys.argv[1], sys.argv[2]
sys.argv = [sys.argv[0], CACHE]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)

from devkit import reuse  # noqa: E402 (PR #171's module, PYTHONPATH)


def need(case: dict) -> str:
    code, _, _ = rb.snippet(case)
    src = rb.cached("raw", f"{case['repo']}@{case['sha']}:{case['path']}", lambda: None)
    try:
        imports = [ast.unparse(n) for n in ast.parse(src).body if isinstance(n, ast.Import | ast.ImportFrom)]
    except SyntaxError:
        imports = []
    return reuse.need_from("\n".join([code, *imports]), case["symbols"][0])


def main() -> None:
    split = json.loads((Path(__file__).resolve().parent / "data" / "split.json").read_text())
    cases = {c["id"]: c for c in rb.CODE_CASES}
    needs = [{"id": i, "q0": need(cases[i]), "para": []} for i in split["code_test"]]
    for n in needs:
        print(f"{n['id']}: {n['q0']}", flush=True)
    sys.argv = [sys.argv[0], OUT, "--split", "code_test"]
    import run_reuse_snapshot as rrs

    rrs.needs_for = lambda _split: needs
    rrs.main()


if __name__ == "__main__":
    main()
