"""Freeze the TUNE/TEST split (PROTOCOL.md rule 3). Run once, before any query is written or any arm is run.

Usage: uv run --no-project python make_split.py      (writes data/split.json and prints its SHA-256)

Rule: units are stratified by domain into EDU and the rest (CS + SEC). Each stratum is sorted by id, shuffled with
random.Random("192-split"), and its first round(0.4 * n) units go to TUNE, the others to TEST.
"""

import hashlib
import random

from common import dump, load


def main() -> None:
    units = load("units.json")
    split = {"tune": [], "test": []}
    for stratum in ("EDU", "OTHER"):
        ids = sorted(u["id"] for u in units if (u["domain"] == "EDU") == (stratum == "EDU"))
        random.Random("192-split").shuffle(ids)  # noqa: S311 (split, not security)
        k = round(0.4 * len(ids))
        split["tune"] += ids[:k]
        split["test"] += ids[k:]
    split = {"rule": __doc__.split("Rule: ")[1].strip(), "tune": sorted(split["tune"]), "test": sorted(split["test"])}
    p = dump(split, "split.json")
    print(split["tune"], split["test"])
    print("sha256", hashlib.sha256(p.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
