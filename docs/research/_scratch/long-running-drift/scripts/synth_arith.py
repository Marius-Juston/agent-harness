"""Re-check the arithmetic the #282 synthesis quotes from the round files, and optionally count a commit trailer.

Usage:
    python synth_arith.py                                   # ratios only
    python synth_arith.py <repo> <since> <trailer-key>      # also count commits on origin/main carrying the trailer

The trailer key is an argument so that no forbidden trailer text is written into this public file.
"""

import subprocess
import sys


def pct(a: int, b: int) -> str:
    return f"{a}/{b} = {a / b:.3f}"


CHECKS = {
    # memory (r1-memory F2, F3)
    "subagents carrying the lead's MEMORY.md index": (1064, 1171),
    "stale memory entries among checkable ones": (8, 34),
    "stale within a day, of stale": (4, 8),
    "stale entries never corrected": (6, 8),
    "memory sweep precision after adjudication": (6, 13),
    "memory sweep recall after adjudication": (6, 8),
    "pointer check precision": (5, 17),
    "pointer check recall": (5, 8),
    # version skew (r1-platform-and-skew F2, F3)
    "harness-ops subagents running a body older than the pin": (305, 491),
    "builder spawns without the completion gate": (92, 96),
    "uv add calls without the reuse guard": (27, 29),
    # prose (r1-prose-vs-behaviour F1, F2, F5, F9)
    "L3 instruction-file claims co-fixed": (16, 17),
    "L3 spec claims co-fixed": (21, 105),
    "LLM sweep precision, all docs": (36, 48),
    "LLM sweep precision, drifted docs": (34, 39),
    "LLM sweep precision, maintained instruction files": (2, 9),
    "LLM sweep recall on known stale claims": (7, 9),
    "token check precision without plan/research (L3)": (52, 79),
    # outside (r1-outside F2)
    "genuine stale path tokens of flags": (9, 27),
    "repos with a genuine stale path": (7, 106),
    "intentional negative references among false positives": (9, 18),
    # warnings (r1-against A3)
    "board-lint count rise, repo-E (138/72)": (138, 72),
}

for name, (a, b) in CHECKS.items():
    print(f"{name}: {pct(a, b)}")

# skew window: the four pinned-ahead intervals sum to the reported 162.4 h
legs = [1.5, 9.8, 39.5, 111.6]
print(f"pinned-ahead hours: {' + '.join(map(str, legs))} = {sum(legs):.1f}")
# Claude Code release cadence (r1-outside F7): 155 versions in six months; r1-platform F8: 18 versions in 44 days
print(f"versions per month (155 / 6): {155 / 6:.1f}; versions per day (18 / 44): {18 / 44:.2f}")
# memory sweep cost per entry (r1-memory F6) and LLM prose sweep cost per doc (r1-prose F5)
print(f"memory sweep $/entry: {2.40 / 36:.3f}; prose sweep $/doc: {2.36 / 7:.3f}, s/doc: {527 / 7:.0f}")

if len(sys.argv) == 4:
    repo, since, key = sys.argv[1:]
    fmt = f"%H %(trailers:key={key},valueonly)"
    out = subprocess.run(
        ["git", "-C", repo, "log", "origin/main", f"--since={since}", f"--format={fmt}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    total = sum(1 for line in out if line.strip() and len(line.split()) >= 1 and len(line.split()[0]) == 40)
    carrying = sum(1 for line in out if len(line.split()) > 1)
    print(f"commits on origin/main since {since}: {total}; carrying the trailer: {carrying}")
