"""Score saved arm answers against the gold (PROTOCOL.md rules 6-8). Reads only saved files: no network.

Usage: uv run --no-project python score.py <tune|test> [--arms L0,L1,...] [--lstar L0] [--audit]

Prints macro and micro recall@10/@20 per arm, paired bootstrap differences, per-domain tables, and writes
data/results_<split>.json (numbers only). --audit writes every non-identifier match (title and author-year rules)
and a sample of unmatched web results to PRIV/audit_<split>.txt for the hand check.
"""

import json
import random
import re
import statistics
import sys
import urllib.parse
from collections import Counter, defaultdict

from common import PRIV, dump, ids_in, load, norm_title

KS = (10, 20)
DRAWS = 10_000


def gold_all() -> dict[str, list[dict]]:
    """Gold per unit, each work with `alt`: the note's link texts for it (amendment 3)."""
    gold = {**load("gold_A.json"), **load("gold_P.json", private=True)}
    alt = {**load("alt_titles_A.json"), **load("alt_titles_P.json", private=True)}
    for u, works in gold.items():
        for g in works:
            g["alt"] = alt.get(u, {}).get(g["key"], [])
    return gold


def queries_for(unit: str) -> list[str]:
    qs = load("queries_P.json", private=True) if unit.startswith("P") else load("queries_A.json")
    return qs[unit]


def runs(arm: str, unit: str) -> dict:
    p = PRIV / "runs" / arm / f"{unit}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def result_ids(r: dict) -> dict[str, set[str]]:
    ids = {k: {v.lower() if k == "doi" else v} for k, v in (r.get("ids") or {}).items() if v}
    url = urllib.parse.unquote(urllib.parse.unquote(r.get("url") or ""))
    for k, vs in ids_in(url).items():
        ids.setdefault(k, set()).update(vs)
    if m := re.search(r"openalex\.org/(W\d+)", url, re.I):
        ids.setdefault("openalex", set()).add(m.group(1).upper())
    ids.setdefault("arxiv", set())
    ids["arxiv"] = {re.sub(r"v\d+$", "", a) for a in ids["arxiv"]}
    return {k: v for k, v in ids.items() if v}


def tokens(text: str) -> tuple[set[str], str]:
    low = urllib.parse.unquote(text).lower()
    return set(re.split(r"[^a-z0-9]+", low)), re.sub(r"[^a-z0-9]", "", low)


class Matcher:
    """Rule 6: identifiers, then titles, then (results without identifiers only) first author + year."""

    def __init__(self, gold: list[dict]):
        self.gold = gold
        self.by_id: dict[tuple[str, str], str] = {}
        for g in gold:
            for k, v in g["ids"].items():
                if v:
                    self.by_id[(k, v.lower() if k == "doi" else v)] = g["key"]
        self.titles = [
            (norm_title(t), g["key"]) for g in gold for t in [g["title"], *g.get("alt", [])] if norm_title(t)
        ]

    def match(self, r: dict) -> tuple[str, str] | None:
        ids = result_ids(r)
        for k, vs in ids.items():
            for v in vs:
                if (k, v) in self.by_id:
                    return self.by_id[(k, v)], "id"
        nr = norm_title(r.get("title", ""))
        if nr:
            for ng, key in self.titles:
                if nr == ng or (min(len(nr), len(ng)) >= 30 and (nr in ng or ng in nr)):
                    return key, "title"
        if ids:
            return None
        toks, squashed = tokens((r.get("title") or "") + " " + (r.get("url") or ""))
        hits = []
        for g in self.gold:
            fam, yr = g.get("first_author") or "", str(g.get("year") or "")
            if not fam or not yr or yr not in toks:
                continue
            if fam in toks or (len(fam) >= 4 and fam in squashed):
                hits.append(g["key"])
        return (hits[0], "author-year") if len(hits) == 1 else None


def lists(arm: str, unit: str) -> list[list[dict]]:
    """One ranked list per query (LA: one per unit; L0-1: L0's first query)."""
    if arm == "LA":
        return [v["results"] for v in runs("LA", unit).values()]
    if arm == "L0-1":
        return [runs("L0", unit).get(queries_for(unit)[0], {}).get("results", [])]
    data = runs(arm, unit)
    return [data.get(q, {}).get("results", []) for q in queries_for(unit)]


def found(arm: str, unit: str, k: int, m: Matcher, rules: Counter, audit: list) -> set[str]:
    if "+" in arm:
        return set().union(*(found(a, unit, k, m, rules, audit) for a in arm.split("+")))
    out = set()
    for lst in lists(arm, unit):
        for r in lst[:k]:
            hit = m.match(r)
            if hit:
                out.add(hit[0])
                rules[hit[1]] += 1
                if hit[1] != "id":
                    audit.append((arm, unit, hit[1], r.get("title", ""), r.get("url", ""), hit[0]))
    return out


def boot(units, per_unit_a, per_unit_b, seed="192-boot"):
    rng = random.Random(seed)  # noqa: S311
    diffs = []
    for _ in range(DRAWS):
        s = [rng.choice(units) for _ in units]
        diffs.append(statistics.fmean(per_unit_a[u] - per_unit_b[u] for u in s))
    diffs.sort()
    return diffs[int(0.025 * DRAWS)], diffs[int(0.975 * DRAWS) - 1]


def boot_works(works, hit_a, hit_b, seed="192-boot"):
    rng = random.Random(seed)  # noqa: S311
    n = len(works)
    diffs = []
    for _ in range(DRAWS):
        s = [works[rng.randrange(n)] for _ in range(n)]
        diffs.append((sum(w in hit_a for w in s) - sum(w in hit_b for w in s)) / n)
    diffs.sort()
    return diffs[int(0.025 * DRAWS)], diffs[int(0.975 * DRAWS) - 1]


def main() -> None:
    split = sys.argv[1]
    arms = sys.argv[sys.argv.index("--arms") + 1].split(",") if "--arms" in sys.argv else ["L0", "L1", "L2", "L3", "L4"]
    lstar = sys.argv[sys.argv.index("--lstar") + 1] if "--lstar" in sys.argv else "L0"
    units = load("split.json")[split]
    domains = {u["id"]: u["domain"] for u in load("units.json")}
    gold = gold_all()
    res: dict = {"split": split, "arms": {}, "pairs": {}, "domains": {}}
    hits: dict[tuple[str, int], set[tuple[str, str]]] = {}
    per_unit: dict[tuple[str, int], dict[str, float]] = {}
    rules_by_arm: dict[str, Counter] = {}
    audit: list = []
    all_works = [(u, g["key"]) for u in units for g in gold[u]]
    for arm in arms:
        rules: Counter = Counter()
        for k in KS:
            per_unit[(arm, k)] = {}
            hits[(arm, k)] = set()
            for u in units:
                f = found(arm, u, k, Matcher(gold[u]), rules if k == 20 else Counter(), audit if k == 20 else [])
                per_unit[(arm, k)][u] = len(f) / len(gold[u])
                hits[(arm, k)] |= {(u, x) for x in f}
        rules_by_arm[arm] = rules
        nres = [len(lst) for u in units for lst in lists(arm, u)] if "+" not in arm else []
        res["arms"][arm] = {
            **{f"macro@{k}": statistics.fmean(per_unit[(arm, k)].values()) for k in KS},
            **{f"micro@{k}": len(hits[(arm, k)]) / len(all_works) for k in KS},
            "per_unit@20": per_unit[(arm, 20)],
            "per_unit@10": per_unit[(arm, 10)],
            "match_rules@20": dict(rules),
            "results_per_list": statistics.fmean(nres) if nres else None,
        }
    print(f"{split}: {len(units)} units, {len(all_works)} gold works")
    print(f"{'arm':10} {'macro@10':>9} {'macro@20':>9} {'micro@10':>9} {'micro@20':>9}  rules@20")
    for arm, r in res["arms"].items():
        print(
            f"{arm:10} {r['macro@10']:9.3f} {r['macro@20']:9.3f} {r['micro@10']:9.3f} {r['micro@20']:9.3f}  "
            f"{r['match_rules@20']} per-list {r['results_per_list']}"
        )
    base = lstar if lstar in arms else arms[0]
    pairs = [(a, base) for a in arms if a != base and "+" not in a]
    if "LA" in arms and "L0-1" in arms:
        pairs.append(("LA", "L0-1"))
    pairs += [(a, base) for a in arms if "+" in a]
    for a, b in pairs:
        for k in KS:
            pa, pb = per_unit[(a, k)], per_unit[(b, k)]
            d = statistics.fmean(pa[u] - pb[u] for u in units)
            lo, hi = boot(units, pa, pb)
            dw = (len(hits[(a, k)]) - len(hits[(b, k)])) / len(all_works)
            wlo, whi = boot_works(all_works, hits[(a, k)], hits[(b, k)])
            res["pairs"][f"{a}-{b}@{k}"] = {"macro": [d, lo, hi], "micro": [dw, wlo, whi]}
            print(
                f"{a:>6} - {b:<5} @{k}: macro {d:+.3f} [{lo:+.3f}, {hi:+.3f}]  micro {dw:+.3f} [{wlo:+.3f}, {whi:+.3f}]"
            )
    # per-domain: unit domain (macro) and gold work's OpenAlex domain and arXiv status (micro)
    work_meta = {(u, g["key"]): g for u in units for g in gold[u]}
    for arm in arms:
        d: dict = {}
        for dom in sorted(set(domains[u] for u in units)):
            us = [u for u in units if domains[u] == dom]
            d[f"unit:{dom}"] = [statistics.fmean(per_unit[(arm, 20)][u] for u in us), len(us)]
        groups: dict[str, list] = defaultdict(list)
        for w, g in work_meta.items():
            groups["oa:" + (g.get("domain") or "none")].append(w)
            groups["arxiv:" + ("yes" if g["ids"].get("arxiv") else "no")].append(w)
        for name, ws in sorted(groups.items()):
            d[name] = [sum(w in hits[(arm, 20)] for w in ws) / len(ws), len(ws)]
        res["domains"][arm] = d
    print("\nper-domain recall@20 (unit macro / gold-work micro), n in brackets")
    keys = sorted({k for v in res["domains"].values() for k in v})
    print(f"{'group':28}" + "".join(f"{a:>10}" for a in arms))
    for key in keys:
        row = res["domains"][arms[0]].get(key, [0, 0])
        print(f"{key + ' (' + str(row[1]) + ')':28}" + "".join(f"{res['domains'][a][key][0]:10.3f}" for a in arms))
    # found by exactly one arm (main arms only)
    main_arms = [a for a in arms if "+" not in a and a not in ("L0-1", "LA")]
    only = Counter()
    for w in all_works:
        who = [a for a in main_arms if w in hits[(a, 20)]]
        if len(who) == 1:
            only[who[0]] += 1
        if not who:
            only["none"] += 1
    res["found_by_one_arm@20"] = dict(only)
    print("\nfound by exactly one arm @20:", dict(only), "of", len(all_works))
    dump(res, f"results_{split}.json")
    if "--audit" in sys.argv:
        lines = [" | ".join(map(str, a)) for a in audit]
        rng = random.Random("192-audit")  # noqa: S311
        unmatched = [
            (a, u, r.get("title", ""), r.get("url", ""))
            for a in arms
            if a == "W"
            for u in units
            for lst in lists(a, u)
            for r in lst
            if not Matcher(gold[u]).match(r)
        ]
        lines += ["", "UNMATCHED WEB SAMPLE"] + [" | ".join(x) for x in rng.sample(unmatched, min(40, len(unmatched)))]
        (PRIV / f"audit_{split}.txt").write_text("\n".join(lines) + "\n")
        print("audit lines:", len(audit))


if __name__ == "__main__":
    main()
