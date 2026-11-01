"""Quota: the weekly usage poller, the spawn guard's cap and its owner-only override (`dev usage-poll`, `dev quota-cap`).

The poller records one cheap `/usage` reading every 10 minutes under the git common dir; the spawn guard (a bash hook)
and `dev status` read it, with the effective cap written alongside.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from devkit import config
from devkit.paths import state_dir

POLLER = "setsid nohup uv run --quiet dev usage-poll >/dev/null 2>&1 &"
FRESH_S = 1800


CAP_FILE = "quota_cap"  # <git-common-dir>/devkit/quota_cap: the owner's machine-local override (`dev quota-cap`)
CAP_SOURCES = {
    "local": "local: `uv run dev quota-cap --clear` drops it",
    "pyproject": "pyproject: [tool.devkit] quota_cap",
}


def quota_cap() -> tuple[int, str]:
    """(cap percent, "local" | "pyproject"): the override from `dev quota-cap` when it is valid, else [tool.devkit].
    A corrupt or unreadable override falls back with a warning, never an error: the guard must not lose its cap."""
    f = state_dir() / CAP_FILE
    try:
        return config.parse_cap(f.read_text(errors="replace")), "local"
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"warning: ignoring {f} ({exc}); using [tool.devkit] quota_cap", file=sys.stderr)
    return config.get().quota_cap, "pyproject"


def set_quota_cap(pct: int | None) -> Path:
    """Owner only (the Bash and Edit guards deny agents): write the override, or drop it when pct is None. A usage
    reading is rewritten with the new cap so the spawn guard and `dev status` agree before the next poll."""
    f = state_dir() / CAP_FILE
    if pct is None:
        f.unlink(missing_ok=True)
    else:
        tmp = f.with_suffix(".tmp")
        tmp.write_text(f"{config.parse_cap(str(pct))}\n")
        tmp.replace(f)
    try:
        data = json.loads((state_dir() / "usage.json").read_text())
        write_usage(data["pct"], now=data["t"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no reading yet, or a corrupt one: the next poll writes it
    return f


def quota_reading() -> tuple[int | None, int]:
    """(weekly all-models percent or None when no reading under 30 min old, effective cap percent)."""
    f = state_dir() / "usage.json"
    cap = quota_cap()[0]
    if not f.exists():
        return None, cap
    data = json.loads(f.read_text())
    return (data["pct"] if time.time() - data["t"] < FRESH_S else None), cap


def quota() -> str:
    f = state_dir() / "usage.json"
    cap, source = quota_cap()
    if not f.exists():
        return f"quota: no reading (start the poller: `{POLLER}`), cap {cap}% ({source})"
    data = json.loads(f.read_text())
    age = (time.time() - data["t"]) / 60
    return f"quota: {data['pct']}% of weekly all-models used ({age:.0f} min ago), cap {cap}% ({source})"


USAGE_LINE = re.compile(r"all models.*?(\d+)%", re.I)


def usage_percent(text: str) -> int | None:
    """The weekly all-models percent from `/usage` output, or None."""
    for line in text.splitlines():
        if m := USAGE_LINE.search(line):
            return int(m.group(1))
    return None


def write_usage(pct: int, now: float | None = None) -> Path:
    """Atomically record a reading, with the cap, for the spawn guard (a bash hook that cannot read pyproject.toml)."""
    f = state_dir() / "usage.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pct": pct, "t": int(now or time.time()), "cap": quota_cap()[0]}) + "\n")
    tmp.replace(f)
    return f


def usage_poll(interval_s: int = 600, once: bool = False) -> int:
    """Every interval, one cheap `/usage` read (no model call), recorded for `dev status` and the spawn guard.
    One poller per clone: a second one exits at once."""
    import fcntl

    with open(state_dir() / "usage_poll.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("usage-poll already running")
            return 0
        while True:
            try:
                out = subprocess.run(
                    ["claude", "-p", "--no-session-persistence", "/usage"],
                    cwd=tempfile.gettempdir(),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                ).stdout
            except (OSError, subprocess.TimeoutExpired):
                out = ""
            if (pct := usage_percent(out)) is not None:
                write_usage(pct)
            if once:
                return 0 if pct is not None else 1
            time.sleep(interval_s)
