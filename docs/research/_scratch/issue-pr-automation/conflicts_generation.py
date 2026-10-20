"""What of the PR bodies and HANDOFF.md is data devkit already has (#346, sub-question d iii).

Usage:
    python3 conflicts_generation.py DATA.json [DATA.json ...]     # PR bodies (conflicts_fetch.py output)
    python3 conflicts_generation.py --handoff REPO_CHECKOUT         # every committed version of docs/HANDOFF.md

PR bodies: each line of each merged PR body is put in one class by a regular expression, first match wins:
  ref        the `Closes #N` / `Refs #N` line `dev pr` already writes
  gate       a line reporting the gate or test counts (`dev check`, `N passed`, PASS/HOLD, ruff, pytest)
  paths      a line that is only file paths or a diffstat row (what `git diff --stat` prints)
  done-when  a checkbox line (`- [ ]`, `- [x]`): DONE WHEN restated, which #232 generates
  issues     a line naming filed issues ("filed #N", "Follow-up", "Issues filed")
  prose      everything else: the worker's account (what changed, why, unverified)
Prints counts and character shares only (no body text). HANDOFF: section headings are classed by keyword as
in-flight, merged, next, owner (decisions, waiting) or state, and their line counts summed over all versions.
"""

import json
import re
import subprocess
import sys
from collections import Counter

CLASSES = [
    ("ref", re.compile(r"^\s*(Closes|Refs|Fixes)\s+#\d+", re.I)),
    ("gate", re.compile(r"dev check|\b\d+ (passed|tests?)\b|\bPASS\b|\bHOLD\b|pytest|ruff|gate (passed|green)", re.I)),
    ("paths", re.compile(r"^\s*[-*]?\s*`?[\w./-]+\.(py|md|toml|json|ya?ml|sh|ts|tsx|js|lock)`?\s*(\|.*|\(.*\))?\s*$")),
    ("done-when", re.compile(r"^\s*[-*]\s*\[[ xX]\]")),
    ("issues", re.compile(r"(filed|follow-?ups?|issues? filed|new issues?)\b.*#\d+", re.I)),
]

HANDOFF_KINDS = [
    ("in-flight", re.compile(r"in flight|open prs|paused", re.I)),
    ("merged", re.compile(r"merged", re.I)),
    ("next", re.compile(r"^##\s*next", re.I)),
    ("owner", re.compile(r"owner|counsel|decision", re.I)),
    ("state", re.compile(r"state", re.I)),
]


def classify(line: str) -> str:
    for name, rx in CLASSES:
        if rx.search(line):
            return name
    return "prose"


def bodies(paths: list[str]) -> None:
    for path in paths:
        with open(path) as f:
            d = json.load(f)
        merged = [p for p in d["prs"] if p["mergedAt"]]
        chars, lines, has = Counter(), Counter(), Counter()
        for p in merged:
            seen = set()
            for line in (p.get("body") or "").splitlines():
                if not line.strip():
                    continue
                c = classify(line)
                chars[c] += len(line)
                lines[c] += 1
                seen.add(c)
            has.update(seen)
        total = sum(chars.values())
        print(
            json.dumps(
                {
                    "repo": d["repo"],
                    "merged_prs": len(merged),
                    "median_body_chars": sorted(len(p.get("body") or "") for p in merged)[len(merged) // 2],
                    "char_share": {k: round(v / total, 3) for k, v in chars.most_common()},
                    "lines": dict(lines.most_common()),
                    "prs_with_class": dict(has.most_common()),
                }
            )
        )


def handoff(checkout: str) -> None:
    git = ["git", "-C", checkout]
    shas = subprocess.run([*git, "log", "--format=%h", "--", "docs/HANDOFF.md"], capture_output=True, text=True)
    totals, versions = Counter(), 0
    for sha in shas.stdout.split():
        show = subprocess.run([*git, "show", f"{sha}:docs/HANDOFF.md"], capture_output=True, text=True)
        if show.returncode:
            continue
        versions += 1
        kind = "preamble"
        for line in show.stdout.splitlines():
            if line.startswith("## "):
                kind = next((k for k, rx in HANDOFF_KINDS if rx.search(line)), "other")
                continue
            if line.strip():
                totals[kind] += 1
    total = sum(totals.values())
    print(
        json.dumps(
            {"versions": versions, "lines": dict(totals), "share": {k: round(v / total, 3) for k, v in totals.items()}}
        )
    )


if __name__ == "__main__":
    if sys.argv[1] == "--handoff":
        handoff(sys.argv[2])
    else:
        bodies(sys.argv[1:])
