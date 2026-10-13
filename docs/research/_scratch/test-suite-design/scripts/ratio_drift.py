"""test/prod SLOC ratio and asserts per test function at sampled commits, read through git (no checkout).
Usage: python ratio_drift.py REPO PKG_DIR TEST_DIR [every_n_commits]"""

import re
import subprocess
import sys
import time

repo, pkg, tdir = sys.argv[1:4]
step = int(sys.argv[4]) if len(sys.argv) > 4 else 10


def git(*a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout


shas = git("rev-list", "--first-parent", "--reverse", "HEAD").split()
t0 = time.monotonic()


def sloc(t):
    return sum(1 for line in t.splitlines() if line.strip() and not line.strip().startswith("#"))


for i, sha in enumerate(shas):
    if i % step and i != len(shas) - 1:
        continue
    files = [f for f in git("ls-tree", "-r", "--name-only", sha).split() if f.endswith(".py")]
    p = t = tests = asserts = 0
    for f in files:
        if not (f.startswith(pkg + "/") or f.startswith(tdir + "/")):
            continue
        text = git("show", f"{sha}:{f}")
        if f.startswith(tdir + "/"):
            t += sloc(text)
            tests += len(re.findall(r"^\s*(?:async\s+)?def test", text, re.M))
            asserts += len(re.findall(r"^\s*assert\b|pytest\.raises\(", text, re.M))
        else:
            p += sloc(text)
    date = git("show", "-s", "--format=%cs", sha).strip()
    print(
        f"{i:4d} {date} {sha[:8]} prod={p:6d} test={t:6d} ratio={t / p if p else 0:.2f} "
        f"test_fns={tests:5d} asserts/test={asserts / tests if tests else 0:.2f}"
    )
print(f"{len(shas)} first-parent commits, {time.monotonic() - t0:.1f}s")
