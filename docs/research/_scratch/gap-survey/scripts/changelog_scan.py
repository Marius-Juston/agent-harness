"""Harness-relevant "Added" bullets in the Claude Code changelog since a version (#498).

Usage: uv run python docs/research/_scratch/gap-survey/scripts/changelog_scan.py <changelog.md> <since-version>
Prints each version's date and its Added bullets matching the keyword list, newest first, plus counts.
"""

import re
import sys

KEYWORDS = re.compile(
    r"hook|subagent|plugin|sandbox|telemetry|worktree|background|routine|runner|budget|memory|compact|cost|token|"
    r"permission|auto mode|skill|schedul|loop|teammate",
    re.I,
)


def main() -> None:
    text = open(sys.argv[1], encoding="utf-8").read().splitlines()  # noqa: SIM115 - one-shot script
    since = tuple(int(x) for x in sys.argv[2].split("."))
    version, date, section, n_versions, n_added, n_hits = None, "", "", 0, 0, 0
    for line in text:
        if m := re.fullmatch(r"\*\*(\d+)\.(\d+)\.(\d+)\*\*", line.strip()):
            v = tuple(map(int, m.groups()))
            if v < since:
                break
            version, date, n_versions = v, "", n_versions + 1
            continue
        if version and not date and re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", line.strip()):
            date = line.strip()
            continue
        if line.startswith("**") and line.endswith("**"):
            section = line.strip("*")
            continue
        if version and line.startswith("* ") and (section == "Added" or line.startswith("* Added")):
            n_added += 1
            if KEYWORDS.search(line):
                n_hits += 1
                print(f"{'.'.join(map(str, version))} ({date}): {line[2:240]}")
    print(f"versions: {n_versions}; Added bullets: {n_added}; matching: {n_hits}")


if __name__ == "__main__":
    main()
