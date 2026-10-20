"""Surface and maintenance cost of devkit's bookkeeping code, per release tag (#346, r1-against).

Run from the repository root: python3 docs/research/_scratch/issue-pr-automation/scripts/surface_growth.py
Reads only git objects (git show / git ls-tree / git log); writes nothing.
"""

import contextlib
import re
import subprocess
import sys


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def tags():
    ts = [t for t in git("tag").split() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    return sorted(ts, key=lambda t: tuple(int(x) for x in t[1:].split(".")))


def py_loc(ref, prefix):
    files = [f for f in git("ls-tree", "-r", "--name-only", ref, prefix).split() if f.endswith(".py")]
    total = 0
    per = {}
    for f in files:
        n = git("show", f"{ref}:{f}").count("\n")
        per[f] = n
        total += n
    return total, per


def subcommands(ref):
    """Top-level `dev` subcommands and `board` actions, from cli.py's add_parser calls."""
    try:
        src = git("show", f"{ref}:devkit/cli.py")
    except subprocess.CalledProcessError:
        return None, None
    top = set(re.findall(r'sub\.add_parser\(\s*"([a-z-]+)"', src))
    board_src = src
    with contextlib.suppress(subprocess.CalledProcessError):
        board_src += git("show", f"{ref}:devkit/board.py")
    acts = re.findall(
        r'"(init|list|next|wave|lint|new|link|claim|beat|block|release|sync|sweep|event|brief|feedback)"', board_src
    )
    return len(top), len(set(acts))


def fix_commits(path, since=None):
    """Non-merge commits touching path whose subject says fix/bug/refuse/regression."""
    args = ["log", "--no-merges", "--format=%s", "--", path]
    subj = git(*args).splitlines()
    fixes = [s for s in subj if re.search(r"\b(fix|bug|regress|broke|wrong|crash)", s, re.I)]
    return len(subj), len(fixes)


def main():
    rows = []
    for t in tags():
        dk, per = py_loc(t, "devkit")
        ts, _ = py_loc(t, "tests")
        top, board = subcommands(t)
        book = sum(v for k, v in per.items() if k in ("devkit/workflow.py", "devkit/board.py"))
        rows.append((t, dk, ts, book, top, board))
    print("tag      devkit_loc  tests_loc  workflow+board_loc  dev_subcmds  board_actions")
    for r in rows:
        print(f"{r[0]:8} {r[1]:10} {r[2]:10} {r[3]:18} {r[4]!s:>11} {r[5]!s:>13}")
    first, last = rows[0], rows[-1]
    print(
        f"\ngrowth {first[0]}->{last[0]}: devkit x{last[1] / first[1]:.2f}, tests x{last[2] / first[2]:.2f}, "
        f"workflow+board x{last[3] / max(first[3], 1):.2f}"
    )
    print(f"workflow+board share of devkit at {last[0]}: {last[3] / last[1]:.1%}")
    for t in (first[0], last[0]):
        files = [
            f
            for f in git("ls-tree", "-r", "--name-only", t).split()
            if not f.startswith("docs/research/_scratch") and f != "uv.lock"
        ]
        n = sum(git("show", f"{t}:{f}").count("\n") for f in files)
        print(f"all tracked lines at {t} (no _scratch, no uv.lock): {n}")
    print(f"tests per devkit line: {first[2] / first[1]:.2f} at {first[0]}, {last[2] / last[1]:.2f} at {last[0]}")
    for p in ("devkit/workflow.py", "devkit/board.py", "devkit/gate.py", "devkit/lit.py"):
        n, f = fix_commits(p)
        print(f"{p}: {n} non-merge commits, {f} with a fix/bug subject ({f / max(n, 1):.0%})")


if __name__ == "__main__":
    sys.exit(main())
