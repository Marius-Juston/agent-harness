"""Hand-written codes for the failure clusters found by tx_docs.py and recurring.py (#497), applied to every failing
call, so the counts in the note reproduce.

Usage: python3 coded.py <transcript dir or name suffix> [...]
Prints per directory and code: failing calls, distinct runs, recovery calls (until the next call that does not fail)
and recovery minutes, plus all calls and runs for the denominator. The codes were written after reading a sample
of each cluster (one rater); `doc` marks the codes a doc, docstring, help text or setup note could have prevented.
"""

import pathlib
import re
import sys
from collections import defaultdict

import tx_docs as t

# (code, doc-preventable?, pattern on the failing call's result, optional pattern on its command)
CODES = [
    (
        "project class constructed with missing fields (undocumented constructor)",
        True,
        r"TypeError: \w+\.__init__\(\) missing \d+ required positional",
        None,
    ),
    (
        "CLI guess on `uv run dev` (unknown flag/subcommand, missing argument)",
        True,
        r"usage: dev[\s\S]*error: (unrecognized arguments|argument sub: invalid choice|the following arguments are required)",
        r"^(?![\s\S]*--bogus)",
    ),
    (
        "CLI guess on another tool (gh --json field, uv, pytest flag)",
        True,
        r"Unknown JSON field|error: unexpected argument|pytest: error: unrecognized arguments",
        None,
    ),
    (
        "module path guess in a harness repo (devkit/<command>.py that does not exist)",
        True,
        r"devkit/(integrate|doctor)\.py: No such file",
        None,
    ),
    (
        "third-party package not installed (scipy, sympy, mpmath, pandas ...)",
        True,
        r"No module named '(scipy|mpmath|sympy|pandas|numpy|tomli_w|pyinstrument|pytest_repeat|docx|rapidfuzz)'",
        None,
    ),
    (
        "project or unknown module not importable (mostly python3 run outside uv)",
        False,
        r"No module named '(?!(scipy|mpmath|sympy|pandas|numpy|tomli_w|pyinstrument|pytest_repeat|docx|rapidfuzz|lark_nope)')",
        None,
    ),
    ("tool not installed: ty", True, r"Failed to spawn: ty\b", None),
    ("node_modules absent in a worktree", True, r"node_modules'?: No such file", None),
    ("doc-link gate caught a stale path (check working)", False, r"FAIL doc links and paths", None),
    (
        "other path miss (layout guess, own scratch file, env): upper bound",
        None,
        r"No such file or directory|cannot access|File does not exist",
        None,
    ),
]


def code_of(e):
    cmd = e["input"].get("command", "") if e["name"] == "Bash" else ""
    for name, doc, rx, cmd_rx in CODES:
        if re.search(rx, e["result"]) and (cmd_rx is None or re.search(cmd_rx, cmd)):
            return name, doc
    return None, None


def main(argv):
    for d in argv:
        p = pathlib.Path(d).expanduser()
        if not p.exists():
            p = next(q for q in (pathlib.Path.home() / ".claude" / "projects").iterdir() if q.name.endswith(d))
        calls = defaultdict(int)
        runs = defaultdict(set)
        rcalls = defaultdict(int)
        rmin = defaultdict(float)
        all_calls = all_runs = 0
        for f in sorted(p.rglob("*.jsonl")):
            evs, _ = t.events(f)
            if not evs:
                continue
            all_runs += 1
            all_calls += len(evs)
            for k, e in enumerate(evs):
                if not t.classify(e):
                    continue
                name, _ = code_of(e)
                if not name:
                    continue
                calls[name] += 1
                runs[name].add(f)
                j = next((j for j in range(k + 1, len(evs)) if not t.classify(evs[j])), None)
                if j is not None:
                    rcalls[name] += j - k
                    rmin[name] += t.minutes(e.get("rts") or e["ts"], evs[j].get("rts") or evs[j]["ts"]) or 0
        print(f"== {p.name[-12:]}: runs={all_runs} calls={all_calls}")
        doc_calls = doc_rc = 0
        for name, doc, _, _ in CODES:
            if calls[name]:
                print(
                    f"  {'doc ' if doc else 'n/a ' if doc is None else 'no  '} calls={calls[name]:4d} "
                    f"runs={len(runs[name]):4d} recovery calls={rcalls[name]:4d} min={rmin[name]:6.1f}  {name}"
                )
                if doc:
                    doc_calls += calls[name]
                    doc_rc += rcalls[name]
        print(
            f"  doc-preventable: failing calls={doc_calls} ({100 * doc_calls / all_calls:.2f}% of calls), "
            f"recovery calls={doc_rc} ({100 * doc_rc / all_calls:.2f}%)"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
