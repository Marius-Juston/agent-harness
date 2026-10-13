"""Per-job wall time of recent successful CI runs (gh api). Usage: python ci_job_durations.py <owner/repo> <workflow> <n>"""

import collections
import datetime as dt
import json
import statistics as st
import subprocess
import sys

repo, wf, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
runs = json.loads(
    subprocess.check_output(
        ["gh", "run", "list", "-R", repo, "-w", wf, "-L", "60", "--json", "databaseId,event,conclusion"]
    )
)
ids = [r["databaseId"] for r in runs if r["conclusion"] == "success" and r["event"] in ("pull_request", "push")][:n]


def p(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


by = collections.defaultdict(list)
for i in ids:
    for j in json.loads(subprocess.check_output(["gh", "api", f"repos/{repo}/actions/runs/{i}/jobs"]))["jobs"]:
        if j["conclusion"] == "success":
            by[j["name"]].append((p(j["completed_at"]) - p(j["started_at"])).total_seconds() / 60)
        else:
            by[f"{j['name']} [{j['conclusion']}]"].append(0.0)
print(repo, wf, "runs:", len(ids))
for k, v in sorted(by.items()):
    print(f"  {k}: n={len(v)} median={st.median(v):.1f} min max={max(v):.1f} min")
