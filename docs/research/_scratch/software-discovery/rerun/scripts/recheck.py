"""Re-compute the #176 TEST hit@3 means and paired hit@3 intervals (hit@3 only: harm and the R9 picks come from
score.py) from score.py's --json rows with separate bootstrap code (its own RNG seed), as #169's recheck_test.py did,
and count the L needs that mention AI tooling (section 12 bounds).
Usage: python recheck.py <test_scores.json> <data/needs.json>"""

import json
import random
import re
import sys

AI = re.compile(r"\b(agents?|agentic|llms?|ai|mcp|claude|codex|gpt|rag|prompts?|language models?)\b", re.I)


def boot(vals: list[float], reps: int = 10000, seed: int = 176) -> tuple[float, float, float]:
    rng = random.Random(seed)  # noqa: S311 (bootstrap)
    n = len(vals)
    means = sorted(sum(rng.choice(vals) for _ in range(n)) / n for _ in range(reps))
    return sum(vals) / n, means[int(0.025 * reps)], means[int(0.975 * reps)]


def main() -> None:
    sc = json.load(open(sys.argv[1]))  # noqa: SIM115
    strat = {n["id"]: n["stratum"] for n in json.load(open(sys.argv[2]))}  # noqa: SIM115
    ids, rows = sc["ids"], sc["rows"]
    for name, keep in (("main", {"A2", "M2"}), ("L", {"L"})):
        sel = [k for k, i in enumerate(ids) if strat[i] in keep]
        print(name, len(sel), {a: round(sum(rows[a][k]["hit3"] for k in sel) / len(sel), 3) for a in rows})
        for a, b in (("B1W", "B1"), ("A2", "B1"), ("A3", "B1"), ("A2", "B1W"), ("A3", "B1W"), ("P1", "A2")):
            d = boot([rows[a][k]["hit3"] - rows[b][k]["hit3"] for k in sel])
            print(f"  {a} - {b}: {d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]")
    lneeds = [n for n in json.load(open(sys.argv[2])) if n["stratum"] == "L"]  # noqa: SIM115
    ai = sum(bool(AI.search(" ".join([n["need"], *n["also_described_as"]]))) for n in lneeds)
    print(f"L needs mentioning AI tooling: {ai}/{len(lneeds)}")


if __name__ == "__main__":
    main()
