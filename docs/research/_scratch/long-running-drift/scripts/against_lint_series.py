"""Time series of standing warnings that `uv run dev orient` prints, read back from transcripts.

Usage: python3 against_lint_series.py <transcript_dir> [...]
For each line 'board lint: N problem(s) on M item(s)' and each 'due' nag found anywhere in a transcript
(SessionStart hook output, tool results), prints the per-day min/max N and the count of distinct nag kinds,
so a reader can see whether a standing warning ever goes down.
"""

import collections
import json
import pathlib
import re
import sys

LINT = re.compile(r"board lint: (\d+) problem\(s\) on (\d+) item")
DUE = re.compile(r"## ([A-Z][a-z]+ (?:audit|review)) due")


def walk(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def main(dirs):
    for d in dirs:
        day_lint = collections.defaultdict(list)
        day_due = collections.defaultdict(collections.Counter)
        for f in pathlib.Path(d).expanduser().rglob("*.jsonl"):
            for line in f.open(errors="replace"):
                if "board lint:" not in line and " due" not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:  # noqa: S112
                    continue
                day = (o.get("timestamp") or "")[:10]
                for s in walk(o):
                    for m in LINT.finditer(s):
                        day_lint[day].append(int(m.group(1)))
                    for m in DUE.finditer(s):
                        day_due[day][m.group(1)] += 1
        print("==", pathlib.Path(d).name)
        for day in sorted(set(day_lint) | set(day_due)):
            v = day_lint.get(day, [])
            rng = f"{min(v)}-{max(v)} (n={len(v)})" if v else "-"
            print(f"{day}\tboard_lint={rng}\tdue={dict(day_due.get(day, {}))}")


if __name__ == "__main__":
    main(sys.argv[1:])
