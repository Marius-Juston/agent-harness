"""Independent re-check of the TEST hit@3 means and paired intervals (synthesis, 2026-10-13).

Input: the --json file written by `r3_score.py score --split test` (per_need: need -> arm -> 0/1). It uses its own
bootstrap code and seed, so it checks r3_score.py rather than repeating it. Run: python3 -I recheck_test.py <json>
"""

import json
import random
import sys
from pathlib import Path

ARMS = ["A1 R0", "A1 R4", "A2 R4", "B0", "B1", "A3", "P1 R4 (exploratory)"]
PAIRS = [("A2 R4", "B1"), ("A3", "B1"), ("A3", "A2 R4"), ("A1 R4", "A1 R0")]


def main(path: str) -> None:
    per_need = json.loads(Path(path).read_text())["per_need"]
    ids = sorted(per_need)
    for arm in ARMS:
        print(f"{arm:22s} hit@3 {sum(per_need[i][arm] for i in ids) / len(ids):.3f}")
    rng = random.Random(7)  # noqa: S311 - bootstrap resampling, not a secret
    for a, b in PAIRS:
        diffs = [per_need[i][a] - per_need[i][b] for i in ids]
        m = len(diffs)
        boot = sorted(sum(diffs[rng.randrange(m)] for _ in range(m)) / m for _ in range(10_000))
        print(f"{a} - {b}: {sum(diffs) / m:+.3f} [{boot[249]:+.3f}, {boot[9749]:+.3f}]")
    wins = sum(per_need[i]["A3"] > per_need[i]["B1"] for i in ids)
    losses = sum(per_need[i]["A3"] < per_need[i]["B1"] for i in ids)
    print(f"A3 vs B1: {wins} wins, {losses} losses")


if __name__ == "__main__":
    main(sys.argv[1])
