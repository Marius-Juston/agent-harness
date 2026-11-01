"""(b) Does each installed Claude Code version carry the same rule loader as 2.1.316?

usage: python3 -I b_versions.py CLAUDE_BINARY...
Counts five markers of the 2.1.316 pipeline in each binary's bundled JavaScript: the node-ignore `.ignores()` call
on the rule globs, the trailing `/**` cut, the pattern validator's node-ignore message, the node-ignore module
marker, and the 1,000-pattern / 4 MiB brace budget constants. A version with every count > 0 has the same loader
shape (minified names differ by build, so the patterns avoid them).
"""

import mmap
import re
import sys

MARKERS = {
    "ignores(rule globs)": rb'"claudemd_rule_globs"\)\)\.ignores\(',
    "/** cut": rb'endsWith\("/\*\*"\)\?[A-Za-z_$]+\.slice\(0,-3\)',
    "validator message": rb"skipped by the ignore library",
    "node-ignore module": rb'[A-Za-z_$]+="node-ignore";if\(typeof Symbol',
    "brace budget": rb"=1000,[A-Za-z_$]+=4194304",
}
for path in sys.argv[1:]:
    with open(path, "rb") as f:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        counts = {k: len(re.findall(p, m)) for k, p in MARKERS.items()}
    print(path.rsplit("/", 1)[-1], counts, "same loader" if all(counts.values()) else "DIFFERS")
