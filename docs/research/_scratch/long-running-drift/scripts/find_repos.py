"""Find top-starred, recently pushed GitHub repos with a root CLAUDE.md or AGENTS.md (via gh api)."""

import json
import subprocess
import sys


def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


repos = []
for page in range(1, 4):
    d = gh(f"search/repositories?q=stars:%3E15000+pushed:%3E2026-10-01&sort=stars&order=desc&per_page=100&page={page}")
    repos += [x["full_name"] for x in d["items"]]
out = []
for full in repos:
    for f in ("CLAUDE.md", "AGENTS.md"):
        c = gh(f"repos/{full}/contents/{f}")
        if isinstance(c, dict) and c.get("type") == "file":
            out.append({"repo": full, "file": f, "size": c["size"]})
    sys.stderr.write(".")
json.dump(out, open(sys.argv[1], "w"), indent=1)  # noqa: SIM115
print(len(repos), "repos scanned;", len(out), "files found")
