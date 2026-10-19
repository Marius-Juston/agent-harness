"""Size and churn of an always-loaded instruction file over a repository's history.

Usage: python3 against_prose_churn.py <repo_path> <file> [<file> ...]
For each file: revisions, first and last size in words, peak size, total lines added and deleted,
and the share of revisions that deleted more lines than they added (pruning revisions).
Prints numbers only (no content), so it is safe to run on private repositories.
"""

import subprocess
import sys


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout


def main(repo, *files):
    total_commits = int(git(repo, "rev-list", "--count", "HEAD").strip() or 0)
    for f in files:
        revs = git(repo, "log", "--follow", "--format=%h %ad", "--date=short", "--numstat", "--", f).split("\n")
        rows, cur = [], None
        for line in revs:
            parts = line.split()
            if len(parts) == 2 and len(parts[0]) >= 7 and "-" in parts[1]:
                cur = parts
            elif len(parts) >= 3 and cur and parts[0].isdigit():
                rows.append((cur[0], cur[1], int(parts[0]), int(parts[1])))
                cur = None
        if not rows:
            print(f"{f}: no history")
            continue
        rows.reverse()
        sizes = []
        for h, d, a, r in rows:  # noqa: B007
            txt = git(repo, "show", f"{h}:{f}") if h else ""
            sizes.append(len(txt.split()))
        add = sum(r[2] for r in rows)
        dele = sum(r[3] for r in rows)
        prune = sum(1 for r in rows if r[3] > r[2])
        print(
            f"{f}: revisions={len(rows)} of {total_commits} commits ({len(rows) / total_commits:.1%}); "
            f"{rows[0][1]}..{rows[-1][1]}; words first={sizes[0]} peak={max(sizes)} last={sizes[-1]}; "
            f"lines +{add} -{dele}; pruning revisions={prune} ({prune / len(rows):.0%})"
        )


if __name__ == "__main__":
    main(*sys.argv[1:])
