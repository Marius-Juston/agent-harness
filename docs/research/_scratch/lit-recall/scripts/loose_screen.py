"""Post-audit sensitivity (exploratory, changes no TEST number): a loose word-overlap screen over every TEST result
the rule-6 matcher left unmatched. A result is a candidate for a still-unfound gold work of its unit when it holds
at least 60% of the work's title words (4+ letters, a few generic words dropped; at least 3 such words). The
candidates are then checked by hand (the outcome is stated in lit-search-quality.md section 2).

Usage: uv run --no-project python loose_screen.py      (prints candidate counts; details to PRIV/loose_screen.txt)
"""

import re
from collections import Counter

from common import PRIV, load
from score import Matcher, found, gold_all, lists, tokens

STOP = set(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "under",
        "between",
        "their",
        "about",
        "what",
        "when",
        "does",
        "using",
        "study",
        "review",
        "analysis",
        "effects",
        "effect",
        "evidence",
    ]
)


def main() -> None:
    gold, units = gold_all(), load("split.json")["test"]
    lines = []
    for arm in ("L0", "X", "W"):
        works = set()
        for u in units:
            m = Matcher(gold[u])
            hit = found(arm, u, 20, m, Counter(), [])
            for lst in lists(arm, u):
                for r in lst[:20]:
                    if m.match(r):
                        continue
                    toks, _ = tokens((r.get("title") or "") + " " + (r.get("url") or ""))
                    for g in gold[u]:
                        words = [
                            w for w in re.split(r"[^a-z0-9]+", g["title"].lower()) if len(w) >= 4 and w not in STOP
                        ]
                        if g["key"] in hit or len(words) < 3:
                            continue
                        if sum(w in toks for w in words) / len(words) >= 0.6:
                            works.add((u, g["key"]))
                            lines.append(
                                f"{arm} | {u} | {g['key']} | {g['title']} | {r.get('title', '')} | {r.get('url', '')}"
                            )
        print(arm, "candidate works:", len(works))
    (PRIV / "loose_screen.txt").write_text("\n".join(sorted(set(lines))) + "\n")


if __name__ == "__main__":
    main()
