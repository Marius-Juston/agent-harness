"""PROTOCOL.md rule 11: TEST misses. Counts gold works no arm found at @20, then for a seeded sample of 20 checks
whether OpenAlex finds the work when searched by its own title (top 5, through `dev lit`'s shared client): if
it does, the index holds the work and the miss is the query's, not the index's.

Usage: uv run python misses.py [--arms L0,W,LA,X]
Writes data/misses_test.json (counts and per-sample flags; titles stay private) and PRIV/misses_test.txt.
"""

import random
import sys
from collections import Counter

from common import PRIV, dump, load, norm_title
from score import Matcher, found, gold_all

from devkit import lit


def main() -> None:
    arms = sys.argv[sys.argv.index("--arms") + 1].split(",") if "--arms" in sys.argv else ["L0", "W", "LA"]
    units = load("split.json")["test"]
    gold = gold_all()
    hit = set()
    for a in arms:
        for u in units:
            hit |= {(u, k) for k in found(a, u, 20, Matcher(gold[u]), Counter(), [])}
    missed = [(u, g) for u in units for g in gold[u] if (u, g["key"]) not in hit]
    rng = random.Random("192-misses")  # noqa: S311
    sample = rng.sample(missed, 20)
    c = lit.client()
    rows, own_title_found = [], 0
    for u, g in sample:
        papers, _notes = c.search(g["title"], ("openalex",), 5)
        ok = any(
            (p.ids.get("doi") and p.ids["doi"].lower() == g["ids"].get("doi"))
            or norm_title(p.title) == norm_title(g["title"])
            for p in papers
        )
        own_title_found += ok
        rows.append({"unit": u, "key": g["key"], "own_title_top5": ok, "year": g["year"], "domain": g["domain"]})
        (PRIV / "misses_test.txt").open("a").write(f"{u} {g['key']} {ok} {g['year']} {g['title']}\n")
    out = {
        "arms": arms,
        "gold": sum(len(gold[u]) for u in units),
        "missed_by_all": len(missed),
        "sample": rows,
        "sample_found_by_own_title_top5": own_title_found,
    }
    dump(out, "misses_test.json")
    print({k: v for k, v in out.items() if k != "sample"})


if __name__ == "__main__":
    main()
