"""Pre-registered comparison of BM25 variants for `board new`'s related-issue line (r2-bm25-variants.md).

Run from the worktree root (deps ephemeral):
    uv run --no-project --with scikit-learn --with bm25s==0.3.14 \
        python docs/research/_scratch/issue-pr-automation/bm25_variants.py dev|test

dev:  rank every arm (cached in .scratch/dedup/bm25v/), score Tier B DEV, apply the selection rule, write
      bm25_variants_pick.json.
test: score Tier B TEST once for the pick and B0 (refuses if bm25_variants_test.txt exists), plus Tier A hits.
Issue text stays in .scratch/dedup/ (gitignored); only numbers are printed.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import dedup_eval as ev  # noqa: E402
from dedup_labels import REPOS, load, sections  # noqa: E402

CACHE = Path.cwd() / ".scratch" / "dedup" / "bm25v"
PICK = HERE / "bm25_variants_pick.json"
TEST_OUT = HERE / "bm25_variants_test.txt"
SENT = re.compile(r"(?<=[.;:!?])\s+|\n+")  # as dedup_explore.py leak
HASREF = re.compile(r"#\d+")
TOK = re.compile(r"(?u)\b\w\w+\b")
TOPN = ev.TOPN
CONDS = ("leak-removed", "full")

ARMS: dict[str, dict] = {"B0": {"kind": "bm25s", "method": "lucene", "k1": 1.5, "b": 0.75}}
for m, name in (("M1", "robertson"), ("M2", "atire"), ("M3", "bm25l"), ("M4", "bm25+")):
    ARMS[m] = {"kind": "bm25s", "method": name, "k1": 1.5, "b": 0.75}
for k1 in (0.9, 1.2, 1.5, 2.0):
    for b in (0.5, 0.75, 1.0):
        if (k1, b) != (1.5, 0.75):
            ARMS[f"G-{k1}-{b}"] = {"kind": "bm25s", "method": "lucene", "k1": k1, "b": b}
for f, w in (("F1", (1, 1, 1)), ("F2", (2, 1, 1)), ("F3", (3, 1, 1)), ("F4", (2, 0.5, 1))):
    ARMS[f] = {"kind": "bm25f", "w": w, "k1": 1.5, "b": 0.75}
ORDER = list(ARMS)  # tie-break order of the pre-registration


def stop() -> frozenset:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return frozenset(ENGLISH_STOP_WORDS)


def fields(cond: str) -> dict[str, dict[int, tuple[list[str], list[str], list[str]]]]:
    """repo -> issue -> (title, Context, DONE WHEN) tokens under a text condition."""
    sw = stop()

    def toks(s: str) -> list[str]:
        return [w for w in TOK.findall(s.lower()) if w not in sw]

    out = {}
    for repo in REPOS:
        iss = load(repo)
        d = {}
        for n, i in iss.items():
            sec = sections(i["body"])
            ctx = sec.get("Context", "")
            if cond == "leak-removed":
                ctx = " ".join(s for s in SENT.split(ctx) if not HASREF.search(s))
            d[n] = (toks(ev.clean(i["title"])), toks(ev.clean(ctx)), toks(ev.clean(sec.get("DONE WHEN", ""))))
        out[repo] = d
    return out


def needed() -> dict[str, set[int]]:
    """Only labelled queries are ranked (Tier A and Tier B, both splits)."""
    q: dict[str, set[int]] = defaultdict(set)
    for repo, n in ev.all_gold():
        q[repo].add(n)
    return q


def rank_bm25s(spec: dict, fl: dict, seq: list[int], want: set[int]) -> dict[int, list]:
    import bm25s

    res = {}
    for k, q in enumerate(seq):
        if k == 0 or q not in want:
            continue
        cands = seq[:k]
        corpus = [(fl[c][0] + fl[c][1] + fl[c][2]) or ["_empty_"] for c in cands]
        r = bm25s.BM25(k1=spec["k1"], b=spec["b"], method=spec["method"])
        r.index(corpus, show_progress=False)
        vocab = r.vocab_dict
        qt = [w for w in fl[q][0] + fl[q][1] + fl[q][2] if w in vocab]
        s = r.get_scores(qt) if qt else np.zeros(len(cands))
        top = np.argsort(-s, kind="stable")[:TOPN]
        res[q] = [[cands[j], float(s[j])] for j in top]
    return res


def rank_bm25f(spec: dict, fl: dict, seq: list[int], want: set[int]) -> dict[int, list]:
    """Stdlib BM25F with incremental df, lengths and postings over the issues filed so far."""
    k1, b, w = spec["k1"], spec["b"], spec["w"]
    tfs = {n: [Counter(f) for f in fl[n]] for n in seq}
    lens = {n: [len(f) for f in fl[n]] for n in seq}
    df: Counter = Counter()
    post: dict[str, list[int]] = defaultdict(list)
    tot = [0, 0, 0]
    res = {}
    for k, q in enumerate(seq):
        if k > 0 and q in want:
            avg = [t / k if t else 1.0 for t in tot]
            score = [0.0] * k
            for term, c in Counter(fl[q][0] + fl[q][1] + fl[q][2]).items():
                if term not in df:
                    continue
                idf = math.log(1 + (k - df[term] + 0.5) / (df[term] + 0.5))
                for j in post[term]:
                    n = seq[j]
                    tt = sum(
                        w[f] * tfs[n][f][term] / (1 - b + b * lens[n][f] / avg[f]) for f in range(3) if tfs[n][f][term]
                    )
                    score[j] += c * idf * tt / (k1 + tt)
            top = sorted(range(k), key=lambda j: (-score[j], j))[:TOPN]
            res[q] = [[seq[j], score[j]] for j in top]
        for term in set(fl[q][0] + fl[q][1] + fl[q][2]):
            df[term] += 1
            post[term].append(k)
        tot = [t + x for t, x in zip(tot, lens[q], strict=True)]
    return res


def rank(job: tuple[str, str]) -> tuple[str, str, dict]:
    arm, cond = job
    path = CACHE / f"{arm}_{cond}.json"
    if path.exists():
        raw = json.loads(path.read_text())
        return arm, cond, {r: {int(q): v for q, v in d.items()} for r, d in raw.items()}
    fl_all, want = fields(cond), needed()
    out = {}
    for repo in REPOS:
        iss = load(repo)
        seq = ev.order(iss)
        fn = rank_bm25s if ARMS[arm]["kind"] == "bm25s" else rank_bm25f
        out[repo] = fn(ARMS[arm], fl_all[repo], seq, want[repo])
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return arm, cond, out


def all_rankings(arms: list[str], conds=CONDS) -> dict[tuple[str, str], dict]:
    with Pool(8) as p:
        return {(a, c): rk for a, c, rk in p.map(rank, [(a, c) for a in arms for c in conds])}


def line(name: str, rk: dict, g: dict) -> str:
    r3 = ev.boot(ev.per_query(rk, g, 3))
    return (
        f"{name:10s} R@1 {np.mean(ev.per_query(rk, g, 1)):.3f} R@3 {r3[0]:.3f} [{r3[1]:.3f},{r3[2]:.3f}] "
        f"R@5 {np.mean(ev.per_query(rk, g, 5)):.3f}"
    )


def diff(rk: dict, base: dict, g: dict) -> tuple[float, float, float]:
    x, y = ev.per_query(rk, g, 3), ev.per_query(base, g, 3)
    return ev.boot([p - q for p, q in zip(x, y, strict=True)])


def dev() -> None:
    rks = all_rankings(ORDER)
    g = ev.gold("B", "DEV")
    print(f"Tier B DEV: {len(g)} queries")
    for cond in CONDS:
        print(f"\n[{cond}]")
        for a in ORDER:
            d = diff(rks[(a, cond)], rks[("B0", cond)], g)
            print(f"{line(a, rks[(a, cond)], g)} | minus B0 {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]")
    prim = {a: float(np.mean(ev.per_query(rks[(a, "leak-removed")], g, 3))) for a in ORDER}
    best = max(prim.values())
    cand = next(a for a in ORDER if prim[a] == best)
    d = diff(rks[(cand, "leak-removed")], rks[("B0", "leak-removed")], g)
    pick = cand if d[1] > 0 else "B0"
    rule = f"candidate {cand} DEV R@3 {best:.3f}, minus B0 {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}] -> pick {pick}"
    print("\nselection (leak-removed DEV):", rule)
    PICK.write_text(json.dumps({"candidate": cand, "pick": pick, "dev_diff": d, "rule": rule}, indent=1) + "\n")


def test() -> None:
    if TEST_OUT.exists() and "--force" not in sys.argv:
        sys.exit(f"{TEST_OUT.name} exists: TEST is scored once")
    pick = json.loads(PICK.read_text())["pick"]
    arms = ["B0"] if pick == "B0" else ["B0", pick]
    rks = all_rankings(arms)
    out = []
    g = ev.gold("B", "TEST")
    out.append(f"pick {pick}; Tier B TEST: {len(g)} queries")
    conds = ["leak-removed"] if pick == "B0" else list(CONDS)
    for cond in conds:
        out.append(f"[{cond}]")
        for a in arms:
            out.append(line(a, rks[(a, cond)], g))
        if pick != "B0":
            d = diff(rks[(pick, cond)], rks[("B0", cond)], g)
            out.append(f"{pick} minus B0 R@3 {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]")
    ga = ev.gold("A", None)
    for cond in CONDS:
        for a in arms:
            hits = ev.per_query(rks[(a, cond)], ga, 3)
            out.append(f"Tier A [{cond}] {a}: {int(sum(hits))}/{len(hits)} in top 3")
    text = "\n".join(out)
    print(text)
    TEST_OUT.write_text(text + "\n")


if __name__ == "__main__":
    {"dev": dev, "test": test}[sys.argv[1]]()
