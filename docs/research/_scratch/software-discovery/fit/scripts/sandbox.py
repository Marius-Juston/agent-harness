"""Install one PyPI package into a throwaway environment and run a script against it in a sandbox (#196, arm F3).

Usage:
  uv run --no-project python sandbox.py install <package> <envroot> [--cold]
  uv run --no-project python sandbox.py run <envroot>/<package> <script.py> [--timeout 60]

install: writes a one-dependency pyproject.toml under <envroot>/<package> and runs
  `uv sync --no-build --no-install-project --python 3.13` there. `--no-build` refuses source distributions, so no
  package code (setup.py, PEP 517 backends) runs at install time; only wheels are unpacked. `--cold` uses a fresh,
  empty UV_CACHE_DIR, so the time and the cache size measure a first install with downloads. Prints one JSON line:
  ok, seconds, venv_bytes, cache_bytes (cold only: approximately the bytes downloaded, unpacked), error tail.

run: executes `<venv>/bin/python -I <script>` under bubblewrap with every namespace unshared (no network), a read-only
  bind of /usr, the uv-managed interpreter and the venv, a tmpfs /tmp as the only writable place, a cleared
  environment and a wall-clock timeout. Prints one JSON line: exit, seconds, fit_ok (last stdout line == FIT_OK),
  stdout and stderr tails.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TAIL = 2000
SBX_TMP = "/tmp"  # noqa: S108  (a fresh tmpfs inside the bwrap sandbox, not the host /tmp)


def du(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def install(pkg: str, root: Path, cold: bool) -> dict:
    d = root / pkg
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "fitcheck"\nversion = "0"\nrequires-python = ">=3.13"\ndependencies = ["{pkg}"]\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VIRTUAL_ENV", "UV_PROJECT"))}
    cache = None
    if cold:
        cache = Path(tempfile.mkdtemp(prefix="uvcache-"))
        env["UV_CACHE_DIR"] = str(cache)
    t = time.monotonic()
    r = subprocess.run(
        ["uv", "sync", "--no-build", "--no-install-project", "--python", "3.13", "--quiet"],
        cwd=d,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    out = {
        "package": pkg,
        "ok": r.returncode == 0,
        "seconds": round(time.monotonic() - t, 2),
        "venv_bytes": du(d / ".venv") if (d / ".venv").exists() else 0,
        "cache_bytes": du(cache) if cache else None,
        "error": r.stderr[-TAIL:] if r.returncode else "",
    }
    if cache:
        shutil.rmtree(cache, ignore_errors=True)
    return out


def run(envdir: Path, script: Path, timeout: int) -> dict:
    venv = (envdir / ".venv").resolve()
    py = venv / "bin" / "python"
    interp = Path(os.path.realpath(py)).parent.parent  # the uv-managed CPython install
    cmd = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", SBX_TMP,
        "--setenv", "LANG", "C.UTF-8",
        "--tmpfs", SBX_TMP,  # first, so the binds below may sit under the host /tmp
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
        # amendment 2: CA certificates and the time zone, read-only. Without them pygit2 fails to import (it loads
        # the system CA store at import), which is a sandbox defect, not a fact about the package.
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates",
        "--ro-bind-try", "/etc/pki", "/etc/pki",
        "--ro-bind-try", "/etc/localtime", "/etc/localtime",
        "--ro-bind", str(interp), str(interp),
        "--ro-bind", str(venv), str(venv),
        "--ro-bind", str(script.resolve()), "/check/script.py",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", SBX_TMP,
        str(py), "-I", "/check/script.py",
    ]  # fmt: skip
    t = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        code, so, se = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        code, so, se = "timeout", (e.stdout or b"").decode(errors="replace"), (e.stderr or b"").decode(errors="replace")
    lines = [ln.strip() for ln in so.strip().splitlines()]
    return {
        "exit": code,
        "seconds": round(time.monotonic() - t, 2),
        "fit_ok": code == 0 and bool(lines) and lines[-1] == "FIT_OK",
        "stdout": so[-TAIL:],
        "stderr": se[-TAIL:],
    }


def main() -> None:
    a = sys.argv[1:]
    if a[0] == "install":
        print(json.dumps(install(a[1], Path(a[2]), "--cold" in a)))
    elif a[0] == "run":
        timeout = int(a[a.index("--timeout") + 1]) if "--timeout" in a else 60
        print(json.dumps(run(Path(a[1]), Path(a[2]), timeout)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
