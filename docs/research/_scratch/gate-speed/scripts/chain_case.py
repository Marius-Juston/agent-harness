"""One chain under load (#520): the lead's loop over PRs 493, 500 and 504 on 2026-11-19 (`gh pr checks --watch`, then
`dev integrate` for each), split by the integrate logs' birth and modify times and the outcome records.

Usage: python chain_case.py <integrate dir> <loop start epoch> <loop end epoch> <stand-in allowance s>
The loop's start is the lead's tool call; its end is the background task output's last write.
"""

import re
import subprocess
import sys
from pathlib import Path

GATE = re.compile(r"RESULT: (?:passed|failed)[^\n]*?\((?:\d+ stage\(s\), )?(\d+) s\)")


def bm(p: Path) -> tuple[int, int]:
    b, m = subprocess.run(["stat", "-c", "%W %Y", str(p)], capture_output=True, text=True, check=True).stdout.split()
    return int(b), int(m)


def main() -> None:
    idir, start, end, allow = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    steps = [
        ("493", "pr-493-9e8ccbe858e3.log", "pr-493.outcome.json"),
        ("500", "pr-500-09912d0397b5.log", None),  # stopped: its outcome was rewritten later by a rerun
        ("504", "pr-504-e70b2903c4cb.log", "pr-504.outcome.json"),
    ]
    prev = start
    totals = {"pre": 0.0, "lock": 0.0, "gate": 0.0, "standin": 0.0, "post": 0.0}
    stop_500 = 1795129606  # pr-500.outcome.json mtime of the stopped run ("when" 18:06 local), read before the rerun
    for pr, log, outcome in steps:
        born, done = bm(idir / log)
        secs = [int(s) for s in GATE.findall((idir / log).read_text(errors="replace"))]
        gate_s, standin = secs[0], secs[1] + allow
        lock = max(0.0, done - born - gate_s - standin)
        out = bm(idir / outcome)[1] if outcome else stop_500
        row = {"pre": born - prev, "lock": lock, "gate": gate_s, "standin": standin, "post": out - done}
        for k, v in row.items():
            totals[k] += v
        print(pr, {k: round(v) for k, v in row.items()})
        prev = out
    wall = end - start
    print("loop wall", wall, "s;", {k: f"{round(v)} s ({100 * v / wall:.0f}%)" for k, v in totals.items()})


if __name__ == "__main__":
    main()
