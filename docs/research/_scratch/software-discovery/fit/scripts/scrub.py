"""Split each fit-check result file into a committed summary and a raw copy kept outside git (#196).

Usage: uv run --no-project python scrub.py <raw_dir> <file.json>...

This repository is public, so third-party text (tracebacks that quote package source, package output, README text a
model repeated) stays out of git. For each file: the original moves to <raw_dir>, its SHA-256 goes into
data/raw_manifest.json, and the committed file keeps only verdicts, the model's one-sentence reason, timings, token
counts, exit codes and the exception class name. Agent-written check scripts (`*_scripts/`) are model output and stay.
"""

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from hedge import HEDGE  # same directory; the flag is computed before the reason is cut

RAW = Path(sys.argv[1])
MANIFEST = Path(__file__).parent.parent / "data" / "raw_manifest.json"
EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Expired)):?", re.M)


def exc_class(stderr: str) -> str | None:
    found = EXC.findall(stderr or "")
    return found[-1].rsplit(".", 1)[-1] if found else None


def run_summary(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"exit": r.get("exit"), "seconds": r.get("seconds"), "fit_ok": r.get("fit_ok"),
            "error_type": exc_class(r.get("stderr", ""))}  # fmt: skip


def scrub_case(v: dict) -> dict:
    if "verdict" in v and "calls" not in v:  # a gold result
        return {"exit": v["exit"], "seconds": v["seconds"], "verdict": v["verdict"], "agrees": v["agrees"],
                "error_type": exc_class(v.get("stderr", ""))}  # fmt: skip
    out = {"verdict": v["verdict"]}
    if "reason" in v:
        out["hedged"] = bool(HEDGE.search(str(v["reason"])))
        out["reason"] = str(v["reason"])[:300]
    if "repaired" in v:
        out["repaired"] = v["repaired"]
    out["calls"] = [{k: c.get(k) for k in ("models", "seconds", "tokens_in", "tokens_out", "cost_usd", "error")}
                    for c in v.get("calls", [])]  # fmt: skip
    for k in ("run", "run0"):
        if k in v:
            out[k] = run_summary(v[k])
    return out


def is_raw(v: dict) -> bool:
    runs = [v, v.get("run") or {}, v.get("run0") or {}]
    return any("stdout" in r for r in runs) or any("text" in c for c in v.get("calls", []))


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    for f in map(Path, sys.argv[2:]):
        data = json.loads(f.read_text())
        if not any(isinstance(v, dict) and is_raw(v) for v in data.values()):
            print("already scrubbed:", f.name)
            continue
        shutil.copy2(f, RAW / f.name)
        manifest[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
        f.write_text(
            json.dumps({k: (scrub_case(v) if not k.startswith("_") else v) for k, v in data.items()}, indent=1)
        )
        print("scrubbed:", f.name, manifest[f.name][:12])
    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
