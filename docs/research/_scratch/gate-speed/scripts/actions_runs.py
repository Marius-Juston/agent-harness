"""GitHub Actions timings of the `ci` workflow (#520): per run, the wait from the run's creation to its first job's
start (queue) and each job's and step's duration, through `gh run list` and `gh run view` (gh CLI, authenticated).

Usage: python actions_runs.py [N] > runs.json    (N = how many recent non-scheduled ci runs; default 40)
"""

import json
import statistics as st
import subprocess
import sys
from datetime import datetime


def gh(*args: str) -> object:
    return json.loads(subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout)


def t(s: str | None) -> float | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    runs = gh("run", "list", "-w", "ci", "-L", str(n + 20), "--json", "databaseId,event,createdAt,conclusion")
    runs = [r for r in runs if r["event"] != "schedule" and r["conclusion"] in ("success", "failure")][:n]
    out = []
    for r in runs:
        v = gh("run", "view", str(r["databaseId"]), "--json", "databaseId,event,createdAt,updatedAt,jobs")
        created = t(v["createdAt"])
        jobs = {}
        for j in v["jobs"]:
            s, c = t(j.get("startedAt")), t(j.get("completedAt"))
            steps = {
                x["name"]: round(t(x["completedAt"]) - t(x["startedAt"]))
                for x in j.get("steps", [])
                if x.get("startedAt") and x.get("completedAt")
            }
            jobs[j["name"]] = {
                "queue_s": round(s - created) if s else None,
                "run_s": round(c - s) if s and c else None,
                "steps": steps,
            }
        ends = [t(j.get("completedAt")) for j in v["jobs"] if j.get("completedAt")]
        out.append(
            {
                "id": v["databaseId"],
                "event": v["event"],
                "created": v["createdAt"],
                "jobs": jobs,
                "wall_s": round(max(ends) - created) if ends else None,
            }
        )
    json.dump(out, sys.stdout, indent=1)
    walls = sorted(x["wall_s"] for x in out if x["wall_s"])
    print(f"\n# {len(walls)} runs: wall median {st.median(walls)} s, max {walls[-1]} s", file=sys.stderr)


if __name__ == "__main__":
    main()
