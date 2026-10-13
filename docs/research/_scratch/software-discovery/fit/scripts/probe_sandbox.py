"""Check the sandbox's isolation claims (#196).

Usage: uv run --no-project python probe_sandbox.py <envroot>/<package>
Runs one probe script through sandbox.py `run` and one sdist-only install through sandbox.py `install`, and prints
PASS or FAIL per property: no network, read-only venv and /usr, host home not visible, a cleared environment (only
PATH, HOME and LANG, plus what bwrap itself sets), and `--no-build` refusing a package that has only an sdist
(docopt 0.6.2). Not checked because not enforced: memory, process count and syscall (seccomp) limits; the only
resource bound is the wall-clock timeout.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENV = Path(sys.argv[1]).resolve()
HOME = Path.home()
SBX = str(Path(__file__).parent / "sandbox.py")
SDIST_ONLY = "docopt==0.6.2"  # PyPI lists a single sdist and no wheel for this release (checked 2026-10-13)
PROBE = f"""
import os, socket
def w(p):
    try:
        open(p, "w").write("x"); return "wrote"
    except OSError as e:
        return f"blocked {{e.errno}}"
try:
    socket.create_connection(("1.1.1.1", 80), timeout=3); print("net=open")
except OSError as e:
    print(f"net=blocked {{e.errno}}")
print("venv=" + w({str(ENV / ".venv" / "probe")!r}))
print("usr=" + w("/usr/probe"))
print("home_ssh=" + str(os.path.exists({str(HOME / ".ssh")!r})))
print("env=" + ",".join(sorted(os.environ)))
print("FIT_OK")
"""
os.environ["PROBE_MARKER"] = "parent-only"  # set in the parent; the sandbox must not see it
with tempfile.TemporaryDirectory() as d:
    script = Path(d) / "probe.py"
    script.write_text(PROBE)
    r = subprocess.run(["uv", "run", "--no-project", "python", SBX, "run", str(ENV), str(script)],
                       capture_output=True, text=True, check=True)  # fmt: skip
    inst = subprocess.run(["uv", "run", "--no-project", "python", SBX, "install", SDIST_ONLY, d],
                          capture_output=True, text=True, check=True)  # fmt: skip
out = dict(ln.split("=", 1) for ln in json.loads(r.stdout)["stdout"].splitlines() if "=" in ln)
sdist = json.loads(inst.stdout)
env_keys = set(out.get("env", "").split(","))
checks = {
    "network unreachable": out.get("net", "").startswith("blocked"),
    "venv read-only": out.get("venv", "").startswith("blocked"),
    "/usr read-only": out.get("usr", "").startswith("blocked"),
    "host ~/.ssh not visible": out.get("home_ssh") == "False",
    "probe file absent on host venv": not (ENV / ".venv" / "probe").exists(),
    "environment cleared (no parent variable)": "PROBE_MARKER" not in env_keys
    and env_keys <= {"PATH", "HOME", "LANG", "PWD"},
    "--no-build refuses an sdist-only package": not sdist["ok"] and "no-build" in sdist["error"],
}
for k, v in checks.items():
    print("PASS" if v else "FAIL", k)
print("sandbox environment keys:", sorted(env_keys))
sys.exit(0 if all(checks.values()) else 1)
