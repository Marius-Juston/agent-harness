"""Universe for stratum L (#176 rerun PROTOCOL.md rule L1): public, non-fork, non-archived Python repositories created
2026-07-01..2026-10-12 with at least 100 stars, from GitHub repository search sliced by creation week (each slice
stays under search's 1,000-result cap), sorted by stars. Run 2026-10-13.

Usage: python fetch_new_repos.py <out.json>     (needs an authenticated `gh`)
"""

import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

FIELDS = ("full_name", "description", "topics", "stargazers_count", "created_at", "pushed_at", "homepage", "archived")


def main() -> None:
    start, end = dt.date(2026, 7, 1), dt.date(2026, 10, 12)
    rows: dict[str, dict] = {}
    d = start
    while d <= end:
        e = min(d + dt.timedelta(days=6), end)
        for page in range(1, 11):
            q = f"language:Python created:{d}..{e} stars:>=100 fork:false archived:false is:public"
            cmd = ["gh", "api", "-X", "GET", "search/repositories", "-f", f"q={q}", "-f", "per_page=100"]
            r = subprocess.run(
                [*cmd, "-f", f"page={page}", "-f", "sort=stars"],
                capture_output=True,
                text=True,
                check=False,
            )
            if r.returncode:
                print("ERR", d, page, r.stderr[:200])
                time.sleep(30)
                continue
            j = json.loads(r.stdout)
            for it in j["items"]:
                rows[it["full_name"].lower()] = {k: it.get(k) for k in (*FIELDS, "fork")}
            print(d, e, page, j["total_count"], len(j["items"]), flush=True)
            time.sleep(2.2)  # search allows 30 requests a minute
            if len(j["items"]) < 100:
                break
        d = e + dt.timedelta(days=1)
    Path(sys.argv[1]).write_text(json.dumps(rows, indent=0))
    print("repos", len(rows))


if __name__ == "__main__":
    main()
