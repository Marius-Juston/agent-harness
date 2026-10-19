"""Tally the hand-coded drift encounters (one rater; codes BELIEVED, counts VERIFIED).

Usage: python3 against_tally.py <against_encounters_coded.csv>
Rows are indexed to the --dump output of against_drift_encounters.py (private text, not committed).
"""

import collections
import csv
import sys

rows = list(csv.DictReader(open(sys.argv[1])))  # noqa: SIM115
c = collections.Counter(r["kind"] for r in rows)
print("kinds:", dict(c))
d = [r for r in rows if r["kind"] == "drift"]
print("drift by mechanism:", dict(collections.Counter(r["mechanism"] for r in d)))
print("drift by found_by:", dict(collections.Counter(r["found_by"] for r in d)))
print("drift by cost:", dict(collections.Counter(r["cost"] for r in d)))
print("memory-involving:", sum("memory" in r["class"] for r in d))
