"""Billable-minute budget for a private repo on GitHub Free (2,000 min/month, each job rounded up to a whole minute).
Usage: python ci_minutes_budget.py  (prints runs/month and runs/day for job-duration scenarios)"""

import math

FREE = 2000
scenarios = {
    "repo-E today: check 0.2 + citations 0.5 (PR)": [0.2, 0.5],
    "one 3-min job": [3.0],
    "two jobs 5 + 1 min": [5.0, 1.0],
    "one 10-min job (DORA upper bound)": [10.0],
    "10-min job + 3 small jobs": [10.0, 0.3, 0.3, 0.3],
}
for name, jobs in scenarios.items():
    billed = sum(math.ceil(j) for j in jobs)
    raw = sum(jobs)
    print(
        f"{name}: raw {raw:.1f} min, billed {billed} min, {FREE // billed} runs/month, {FREE / billed / 30:.1f} runs/day"
    )
