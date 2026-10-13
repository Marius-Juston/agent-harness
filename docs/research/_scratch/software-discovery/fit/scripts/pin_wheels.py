"""Record the exact wheel each fit-check environment installed (#196): name, version, wheel URL and SHA-256.

Usage: uv run --no-project python pin_wheels.py <envroot> <out.json>
Reads each environment's uv.lock and keeps, per installed distribution, the locked wheel whose file name carries
one of the tags in that distribution's installed WHEEL file. Locked packages that were not installed (platform
markers) are left out. A re-run can download these URLs and check the hashes.
"""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])


def norm(s: str) -> str:
    return re.sub(r"[-_.]+", "-", s).lower()


def installed_tags(site: Path) -> dict[str, set[str]]:
    tags = {}
    for d in site.glob("*.dist-info"):
        name = norm(d.name.removesuffix(".dist-info").rsplit("-", 1)[0])
        wheel = d / "WHEEL"
        if wheel.exists():
            tags[name] = {ln.split(":", 1)[1].strip() for ln in wheel.read_text().splitlines() if ln.startswith("Tag:")}
    return tags


def pick(wheels: list[dict], tags: set[str]) -> dict | None:
    for w in wheels:
        fname = w["url"].rsplit("/", 1)[-1].removesuffix(".whl")
        parts = fname.split("-")
        py, abi, plats = parts[-3], parts[-2], parts[-1]
        combos = {f"{a}-{b}-{c}" for a in py.split(".") for b in abi.split(".") for c in plats.split(".")}
        if combos & tags:
            return w
    return None


out = {}
for lock in sorted(ROOT.glob("*/uv.lock")):
    site = next((lock.parent / ".venv" / "lib").glob("python3*/site-packages"))
    tags = installed_tags(site)
    pkgs = []
    for p in tomllib.loads(lock.read_text())["package"]:
        if p["name"] == "fitcheck" or norm(p["name"]) not in tags:
            continue
        w = pick(p.get("wheels", []), tags[norm(p["name"])])
        pkgs.append([p["name"], p["version"], w["url"] if w else None, w["hash"] if w else None])
    out[lock.parent.name] = pkgs
OUT.write_text(json.dumps(out, indent=1))
missing = [(k, n) for k, v in out.items() for n, _, u, _ in v if u is None]
print(len(out), "environments;", sum(len(v) for v in out.values()), "wheels; unmatched:", missing)
