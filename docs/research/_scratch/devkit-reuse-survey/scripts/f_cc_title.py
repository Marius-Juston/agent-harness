"""(f) #100: does commitizen's Conventional Commits check decide any PR title differently from a ten-line regex?

usage: uv run python docs/research/_scratch/devkit-reuse-survey/scripts/f_cc_title.py [TITLES.txt]
CZ is commitizen's own check, copied from ConventionalCommitsCz.schema_pattern() (commitizen master, read
2026-11-01, commitizen/cz/conventional_commits/conventional_commits.py lines 195-218); `cz check` is re.match of it.
DEVKIT is the regex proposed for #100: the same grammar plus the '(#n)' suffix #100's title form ends with.
Probes: the spec's examples and the edge cases a reviewer would try. TITLES.txt (optional): one real title per line,
e.g. `git log --first-parent --format=%s main`.
"""

import re
import sys

TYPES = ("build", "bump", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test")
CZ = re.compile(r"(?s)(" + "|".join(TYPES) + r")(\(\S+\))?!?: ([^\n\r]+)((\n\n.*)|(\s*))?$")
DEVKIT = re.compile(r"(" + "|".join(TYPES) + r")(\([\w./-]+\))?!?: \S[^\n\r]*")  # title only, one line
ISSUE = re.compile(r".* \(#\d+\)$")

PROBES = [
    ("fix(parser): array parsing issue (#12)", True),
    ("feat!: drop python 3.12 (#3)", True),
    ("feat(api)!: send an email (#4)", True),
    ("docs: correct spelling of CHANGELOG (#5)", True),
    ("feat: x", True),  # valid CC, but no (#n): only DEVKIT+ISSUE refuses
    ("fix:missing space (#6)", False),
    ("fix : space before colon (#7)", False),
    ("Fix: capitalised type (#8)", False),  # the spec says types are case-insensitive; cz and DEVKIT both refuse
    ("feature: unknown type (#9)", False),
    ("fix(): empty scope (#10)", False),
    ("fix(a b): space in scope (#11)", False),
    ("fix:  (#12)", False),
    ("fix: (#13)", True),  # a description of only '(#13)': both accept; DEVKIT could require words before (#n)
    ("rules: path-scoped rules (#78) (#405)", False),  # this repo's current form
]


def decide(t):
    return bool(CZ.match(t)), bool(DEVKIT.fullmatch(t)), bool(DEVKIT.fullmatch(t) and ISSUE.match(t))


def main():
    disagree = 0
    print("title | cz | devkit | devkit+(#n) | expected")
    for t, want in PROBES:
        cz, dk, dki = decide(t)
        disagree += cz != dk
        print(f"{t!r} | {cz} | {dk} | {dki} | {want}")
    print(f"probes: {len(PROBES)}; cz vs devkit disagree on {disagree}")
    if len(sys.argv) > 1:
        titles = [x for x in open(sys.argv[1], encoding="utf-8").read().splitlines() if x]  # noqa: SIM115
        cz_ok = sum(decide(t)[0] for t in titles)
        dk_ok = sum(decide(t)[1] for t in titles)
        diff = [t for t in titles if decide(t)[0] != decide(t)[1]]
        print(f"real titles: {len(titles)}; cz accepts {cz_ok}; devkit accepts {dk_ok}; disagree {len(diff)}")
        for t in diff[:10]:
            print("   ", t)


main()
