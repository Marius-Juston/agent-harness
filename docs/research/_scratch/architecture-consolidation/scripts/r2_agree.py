"""Agreement of the fast regex trigger (trigger_signals) with the AST replay at sampled commits (#111 r2).
Usage: uv run --python 3.14 python .scratch/r2_agree.py REPO_DIR..."""

import importlib.util
import json
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("rm", "scripts/repo_metrics.py")
rm = importlib.util.module_from_spec(spec)
sys.modules["rm"] = rm
spec.loader.exec_module(rm)
worst = {"share": 0.0, "cycle": 0, "churny": 0, "smell_mismatch": 0, "n": 0, "max_s": 0.0}
for d in sys.argv[1:]:
    repo = Path(d)
    name = repo.name
    rows = json.loads(Path(f".scratch/replay_{name}.json").read_text())[name]["rows"]
    fp = rm.git(repo, "rev-list", "--first-parent", "--reverse", "HEAD").split()
    reader = rm.BlobReader(repo)
    for r in [*rows[:: max(1, len(rows) // 25)], rows[-1]]:
        t = time.perf_counter()
        s = rm.trigger_signals(repo, fp[r["i"]], reader)
        dt = time.perf_counter() - t
        ast_share = r["dormant30_orphan_loc"] / (r["code"] or 1)
        ast_dorm = r["code"] > 0 and ast_share >= 0.05
        worst["share"] = max(worst["share"], abs(ast_share - s["orphaned_dormant_share"]))
        worst["cycle"] = max(worst["cycle"], abs(r["scc_max"] - s["largest_import_cycle"]))
        worst["churny"] = max(worst["churny"], abs(r["churny_1000_30"] - len(s["churny_files"])))
        worst["smell_mismatch"] += (
            (ast_dorm != ("dormant" in s["smells"]))
            + ((r["churny_1000_30"] > 0) != ("churny" in s["smells"]))
            + ((r["scc_max"] >= 10) != ("import-cycle" in s["smells"]))
        )
        worst["n"] += 1
        worst["max_s"] = max(worst["max_s"], dt)
    reader.close()
print(json.dumps(worst))
