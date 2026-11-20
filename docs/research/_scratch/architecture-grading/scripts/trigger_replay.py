"""Replay devkit's own consolidation trigger (`devkit.consolidation.signals`, the shipped code) over this
repository's first-parent history since review 1 (#496): when did its `churny` smell first name devkit/workflow.py,
and when did the composite trigger first become due against review 1's merge (3725af6)?

Run from the worktree root: ``uv run python docs/research/_scratch/architecture-grading/scripts/trigger_replay.py``.
Uses the configured thresholds of this checkout (`config.get().consolidation`).
"""

import subprocess
from pathlib import Path

from devkit import config
from devkit.consolidation import BlobReader, signals

REPO = Path.cwd()
BASE = "3725af6cf"  # merge of consolidation review 1 (#138)


def git(*a: str) -> str:
    return subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout


def main() -> None:
    cfg = config.get().consolidation
    revs = git("rev-list", "--first-parent", "--reverse", "main").split()
    reader = BlobReader(REPO)
    then = signals(REPO, BASE, cfg, reader)
    first_churny = first_due = None
    for i, rev in enumerate(revs):
        if i < 37:
            continue
        s = signals(REPO, rev, cfg, reader)
        why = []
        grown = s["code_loc"] - then["code_loc"]
        if grown >= cfg["growth_min_loc"] and s["code_loc"] >= cfg["growth"] * then["code_loc"]:
            why.append(f"growth {then['code_loc']}->{s['code_loc']}")
        if s["fp_commits"] - then["fp_commits"] >= cfg["fp_commits"]:
            why.append(f"{s['fp_commits'] - then['fp_commits']} fp commits")
        why += [f"new smell {x}" for x in s["smells"] if x not in then["smells"]]
        if first_due is None and why:
            first_due = (i, rev[:9], why)
        if first_churny is None and "devkit/workflow.py" in s["churny_files"]:
            first_churny = (i, rev[:9], s["loc"]["devkit/workflow.py"])
        if i % 20 == 0 or i in (80, 84, 112, 144):
            print(i, rev[:9], s["code_loc"], s["smells"], s["churny_files"], why)
    reader.close()
    print("first due after review 1:", first_due)
    print("first churny naming workflow.py:", first_churny)
    print(
        "thresholds:", {k: cfg[k] for k in ("growth", "growth_min_loc", "fp_commits", "churny_loc", "churny_commits")}
    )


if __name__ == "__main__":
    main()
