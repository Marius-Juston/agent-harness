"""Install every package of the fit cases once, cold (fresh uv cache), then once warm, and record the cost (#196).

Usage: uv run --no-project python install_all.py <envroot> <out.json> <cases.json>...
Sequential, so timings do not compete. The warm pass reuses the default uv cache filled by an unmeasured priming
install, so it measures the case where the wheel is already cached.
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
pkgs = sorted({c["package"] for f in sys.argv[3:] for c in json.loads(Path(f).read_text())})


def call(*a: str) -> dict:
    r = subprocess.run(["uv", "run", "--no-project", "python", str(HERE / "sandbox.py"), *a], capture_output=True,
                       text=True, check=True)  # fmt: skip
    return json.loads(r.stdout)


res = {}
for p in pkgs:
    cold = call("install", p, str(ROOT), "--cold")
    call("install", p, str(ROOT))  # prime the shared cache
    warm = call("install", p, str(ROOT))
    res[p] = {"cold": cold, "warm": warm}
    print(p, cold["ok"], cold["seconds"], warm["seconds"], cold["venv_bytes"], cold["cache_bytes"], flush=True)
OUT.write_text(json.dumps(res, indent=1))
