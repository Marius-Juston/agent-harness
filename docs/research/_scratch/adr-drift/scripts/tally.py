"""Tally findings.csv by level, by whether an existing check caught the finding, and by cheapest enforcement kind."""

import collections
import csv
from pathlib import Path

rows = list(csv.DictReader(open(Path(__file__).with_name("findings.csv"))))  # noqa: SIM115 - research script, kept as run
lv = collections.Counter(r["level"] for r in rows)
caught = collections.Counter((r["level"], r["existing_check_caught"]) for r in rows)
kind = collections.Counter(r["cheapest_enforcement"].split(" ")[0] for r in rows)
print(f"{len(rows)} findings")
print("level | n | caught Y | partial | N")
for l, n in lv.most_common():  # noqa: E741 - research script, kept as run
    print(f"{l} | {n} | {caught[(l, 'Y')]} | {caught[(l, 'partial')]} | {caught[(l, 'N')]}")
tot = collections.Counter(r["existing_check_caught"] for r in rows)
print("all |", len(rows), "|", tot["Y"], "|", tot["partial"], "|", tot["N"])
print("cheapest enforcement:", dict(kind.most_common()))
now = collections.Counter(r["caught_by_todays_checks"] for r in rows)
print("caught by today's checks:", dict(now))
print(
    "not caught today, by level:",
    dict(collections.Counter(r["level"] for r in rows if r["caught_by_todays_checks"] == "N").most_common()),
)
