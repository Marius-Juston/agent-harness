"""For each root context file: commit count, first/last commit date, line count at first commit and at HEAD."""

import base64
import json
import subprocess
import sys


def gh(path, paginate=False):
    cmd = ["gh", "api", path] + (["--paginate"] if paginate else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    if paginate:  # --paginate concatenates JSON arrays as ][
        return (
            json.loads("[" + r.stdout.replace("]\n[", ",").replace("][", ",").strip()[1:-1] + "]")
            if r.stdout.strip() not in ("", "[]")
            else []
        )
    return json.loads(r.stdout)


def lines_at(repo, f, ref):
    c = gh(f"repos/{repo}/contents/{f}?ref={ref}")
    if not isinstance(c, dict) or "content" not in c:
        return None, None
    txt = base64.b64decode(c["content"]).decode("utf-8", "replace")
    return txt.count("\n") + 1, txt


found = json.load(open(sys.argv[1]))  # noqa: SIM115
out = []
for row in found:
    commits = gh(f"repos/{row['repo']}/commits?path={row['file']}&per_page=100", paginate=True)
    if not commits:
        continue
    first, last = commits[-1], commits[0]
    n0, _ = lines_at(row["repo"], row["file"], first["sha"])
    n1, _ = lines_at(row["repo"], row["file"], last["sha"])
    out.append(
        {
            **row,
            "commits": len(commits),
            "first_sha": first["sha"],
            "first_date": first["commit"]["committer"]["date"],
            "last_date": last["commit"]["committer"]["date"],
            "lines_first": n0,
            "lines_head": n1,
            "authors": sorted({(c.get("author") or {}).get("login") or c["commit"]["author"]["name"] for c in commits}),
        }
    )
    sys.stderr.write(".")
json.dump(out, open(sys.argv[2], "w"), indent=1)  # noqa: SIM115
print(len(out))
