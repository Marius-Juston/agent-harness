"""Replay the suite-audit trigger of test-suite-design.md §10 over first-parent history (no checkout).

Paths come from the environment, as in #111's codenames.py: REPO_L, REPO_R, REPO_E, REPO_H.
At each first-parent commit the script counts `def test...` functions in test files (paths under tests?/ or named
test_*.py, *_test.py), read incrementally through `git diff-tree` and `git cat-file --batch`. That count stands in
for collected tests (parametrize makes the collected count higher). The trigger fires when any of these holds since the
last fire: K commits (K by tier of the current count), the count grew by GROWTH and by at least MIN_ADD tests, or the
tier changed. Counting starts at the first commit with a test. Usage: python replay_audit_trigger.py [K_S K_M K_L]
"""

import itertools
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime

ENV = {"L": "REPO_L", "R": "REPO_R", "E": "REPO_E", "H": "REPO_H"}
TEST_PATH = re.compile(r"(^|/)tests?/.*\.py$|(^|/)test_[^/]*\.py$|_test\.py$")
TEST_DEF = re.compile(rb"^\s*(?:async\s+)?def test", re.M)
GROWTH, MIN_ADD = 0.25, 50


def tier(n: int) -> int:
    return 0 if n < 1000 else 1 if n <= 5000 else 2


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def counts(repo: str):
    """Yield (sha, date, test count) for every first-parent commit, oldest first."""
    log = git(repo, "log", "--first-parent", "--reverse", "--format=%H %cI", "HEAD").split("\n")
    cat = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    per_file: dict[str, int] = {}
    prev = None
    for line in filter(None, log):
        sha, date = line.split()
        if prev is None:
            changed = [("A", p) for p in git(repo, "ls-tree", "-r", "--name-only", sha).split("\n") if p]
        else:
            out = git(repo, "diff-tree", "-r", "--no-renames", "--name-status", prev, sha).split("\n")
            changed = [(x.split("\t")[0], x.split("\t")[1]) for x in out if "\t" in x]
        for status, p in changed:
            if not TEST_PATH.search(p):
                continue
            if status == "D":
                per_file.pop(p, None)
                continue
            cat.stdin.write(f"{sha}:{p}\n".encode())
            cat.stdin.flush()
            header = cat.stdout.readline().split()
            if header[-1] == b"missing":
                continue
            body = cat.stdout.read(int(header[2]) + 1)
            per_file[p] = len(TEST_DEF.findall(body))
        prev = sha
        yield sha, datetime.fromisoformat(date), sum(per_file.values())
    cat.stdin.close()
    cat.wait()


def replay(rows, k):
    fires, last_i, last_n = [], None, 0
    for i, (_, date, n) in enumerate(rows):
        if last_i is None:
            if n:
                last_i, last_n = i, n
            continue
        why = []
        if i - last_i >= k[tier(n)]:
            why.append("commits")
        if n - last_n >= MIN_ADD and n >= last_n * (1 + GROWTH):
            why.append("growth")
        if tier(n) != tier(last_n):
            why.append("tier")
        if why:
            fires.append((i, date, n, why))
            last_i, last_n = i, n
    return fires


def main():
    k = [int(x) for x in sys.argv[1:4]] if len(sys.argv) >= 4 else [25, 25, 50]
    print(f"K (S, M, L) = {k}; growth {GROWTH:.0%} and >= {MIN_ADD} tests; tier change")
    for code, var in ENV.items():
        repo = os.environ.get(var)
        if not repo:
            print(f"{code}: {var} not set, skipped")
            continue
        rows = list(counts(repo))
        fires = replay(rows, k)
        days = (rows[-1][1] - rows[0][1]).total_seconds() / 86400
        gaps = [(b[1] - a[1]).total_seconds() / 86400 for a, b in itertools.pairwise(fires)]
        reasons = {r: sum(r in f[3] for f in fires) for r in ("commits", "growth", "tier")}
        print(
            f"{code}: {len(rows)} first-parent commits over {days:.1f} days ({len(rows) / max(days, 1e-9):.1f}/day); "
            f"tests {rows[-1][2]}; fires {len(fires)} ({100 * len(fires) / len(rows):.1f} per 100 commits) {reasons}; "
            f"median days between fires {statistics.median(gaps) if gaps else float('nan'):.2f}"
        )


if __name__ == "__main__":
    main()
