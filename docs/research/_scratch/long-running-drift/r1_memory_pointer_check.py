"""R1 memory: deterministic "evidence pointer" sweep — flag an entry when work it names is finished or a path it names is gone.

usage: python3 -I r1_memory_pointer_check.py CONFIG.json GOLD.csv
CONFIG.json: {"<code>": {"entry": "<memory .md>", "repo": "<checkout>"}} (private). Pointers: board tickets `T-NNNN`
(finished when the repo has board/done/T-NNNN-*.md), GitHub `#N` (finished when `gh` reports CLOSED or MERGED), and
backticked repo-relative paths with a slash (gone when absent from the checkout and not gitignored). Prints per-code flags
and precision/recall against GOLD.csv's stale labels (PARTLY-STALE or STALE). Read-only; `gh` needs network.
"""

import csv
import functools
import json
import re
import subprocess
import sys
from pathlib import Path


@functools.cache
def gh_state(repo: str, n: str) -> str:
    for kind in ("issue", "pr"):
        p = subprocess.run(
            ["gh", kind, "view", n, "--json", "state", "-q", ".state"], cwd=repo, capture_output=True, text=True
        )
        if p.returncode == 0:
            return p.stdout.strip()
    return "UNKNOWN"


def check(entry: str, repo: str) -> list[str]:
    body = Path(entry).read_text(errors="replace").split("---", 2)[-1]
    flags = []
    for t in sorted(set(re.findall(r"\bT-\d{4}\b", body))):
        if list(Path(repo, "board", "done").glob(f"{t}-*.md")):
            flags.append(f"{t} done")
    for n in sorted(set(re.findall(r"(?<![\w/&])#(\d+)\b", body))):
        st = gh_state(repo, n)
        if st in ("CLOSED", "MERGED"):
            flags.append(f"#{n} {st.lower()}")
    for path in sorted(set(re.findall(r"`([\w.-]+/[\w./<>-]+)`", body))):
        if "<" in path or path.startswith(("~", "/")):
            continue
        if not Path(repo, path).exists():
            ign = subprocess.run(["git", "check-ignore", "-q", path], cwd=repo).returncode == 0
            if not ign:
                flags.append(f"path gone: {path}")
    return flags


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text())
    with open(sys.argv[2], newline="") as fh:
        gold = {r["code"]: r for r in csv.DictReader(fh)}
    tp = fp = fn = 0
    for code, c in cfg.items():
        fl = check(c["entry"], c["repo"])
        stale = gold[code]["status"] in ("PARTLY-STALE", "STALE")
        tp += bool(fl) and stale
        fp += bool(fl) and not stale
        fn += (not fl) and stale
        # flags name tickets/issues/paths of private repos: print the count only
        print(f"{code:6} flags={len(fl)} gold_stale={stale}")
    print(
        f"pointer sweep: TP={tp} FP={fp} FN={fn} precision={tp / max(1, tp + fp):.2f} recall={tp / max(1, tp + fn):.2f}"
    )


if __name__ == "__main__":
    main()
