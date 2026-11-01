"""(b) Extract the node-ignore module Claude Code bundles, so its rule matcher can be run on probes.

usage: python3 -I b_extract_ignore.py CLAUDE_BINARY OUT.js
Finds the module whose body sets `ls="node-ignore"` and cuts it at the first `})` after which `node --check`
accepts the text. Claude Code 2.1.316's rule loader calls it as `X.default().add(globs).ignores(path)`; its pattern
validator says "skipped by the ignore library (blank, comment, or trailing backslash)".
"""

import mmap
import subprocess
import sys

binary, out = sys.argv[1:3]
with open(binary, "rb") as f:
    m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    tag = m.find(b'ls="node-ignore"')
    start = m.rfind(b"x(function(", 0, tag) + 2
    win = m[start : start + 40000].decode("utf8", "replace")
pos = 0
while (k := win.find("})", pos)) >= 0:
    with open(out, "w", encoding="utf8") as g:
        g.write("module.exports=(" + win[: k + 1] + ")")
    if subprocess.run(["node", "--check", out], capture_output=True).returncode == 0:
        print(f"node-ignore module at byte {start}, {k + 1} chars -> {out}")
        break
    pos = k + 1
else:
    sys.exit("no parseable cut found")
