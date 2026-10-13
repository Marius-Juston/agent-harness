"""Run each gold script (gold/<id>.txt, Python run as a sandbox input, so not linted as project code) in the sandbox against its package and record the outcome (#196).

Usage: uv run --no-project python gold_run.py <envroot> <cases.json> <out.json>
A gold script prints GOLD_FIT or GOLD_NOT as its verdict line (GOLD_CHECK means: read the output by hand).
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT, CASES, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
res = {}
for c in json.loads(CASES.read_text()):
    script = HERE.parent / ("gold_n" if c["id"].startswith("n") else "gold") / f"{c['id']}.txt"
    r = subprocess.run(["uv", "run", "--no-project", "python", str(HERE / "sandbox.py"), "run", str(ROOT / c["package"]),
                        str(script)], capture_output=True, text=True, check=True)  # fmt: skip
    o = json.loads(r.stdout)
    lines = o["stdout"].strip().splitlines()
    got = next((ln for ln in reversed(lines) if ln.startswith("GOLD_")), "GOLD_ERROR")
    o["verdict"] = got
    o["agrees"] = {"GOLD_FIT": "FIT", "GOLD_NOT": "NOT_FIT"}.get(got) == c["gold"]
    res[c["id"]] = o
    print(c["id"], c["package"], c["gold"], got, "" if o["agrees"] else "<-- CHECK", flush=True)
OUT.write_text(json.dumps(res, indent=1))
