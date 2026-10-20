"""Exploratory (not pre-registered) checks after the one-shot TEST of dedup_eval.py.

    uv run --no-project --with scikit-learn --with bm25s --with model2vec \
        python docs/research/_scratch/issue-pr-automation/dedup_explore.py volume|leak|judge|latent

volume: duplicate / not-planned counts and time to closure.
leak:   A4 with every Context sentence that names an issue (#N) removed, the text a Tier B label rests on.
judge:  agent-harness (public) alerts of A2 and A4 that no label marks, printed for a manual relatedness judgement.
latent: the most similar pairs of currently open issues (A2 cosine), agent-harness titles printed, repo-E numbers only.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import dedup_eval as ev
from dedup_labels import REPOS, load, sections

SENT = re.compile(r"(?<=[.;:!?])\s+|\n+")
HASREF = re.compile(r"#\d+")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def volume() -> None:
    total = 0
    for repo in REPOS:
        iss = load(repo)
        total += len(iss)
        fmt = sum("## DONE WHEN" in (i["body"] or "") and "## Context" in (i["body"] or "") for i in iss.values())
        np_ = [n for n, i in iss.items() if i["stateReason"] == "NOT_PLANNED"]
        ctx_dup = sum(
            bool(re.search(r"duplicat|not to dup", sections(i["body"]).get("Context", ""), re.I)) for i in iss.values()
        )
        print(
            f"{repo}: {len(iss)} issues; board-new format (Context+DONE WHEN) {fmt}; NOT_PLANNED {len(np_)}; "
            f"Context mentions 'duplicat' {ctx_dup}"
        )
        for p in ev.LABELS["repos"][repo]["tier_a"]:
            dup = p["query"] if iss[p["query"]]["state"] == "CLOSED" and iss[p["query"]]["stateReason"] else p["target"]
            i = iss[dup]
            age = (ts(i["closedAt"]) - ts(i["createdAt"])).total_seconds() / 3600 if i["closedAt"] else None
            other = p["target"] if dup == p["query"] else p["query"]
            print(
                f"  pair q{p['query']} t{p['target']}: closed one #{dup} ({i['stateReason'] or 'open'}), "
                f"open for {age if age is None else round(age, 1)} h, filed "
                f"{(ts(iss[p['query']]['createdAt']) - ts(iss[p['target']]['createdAt'])).total_seconds() / 3600:.1f} h"
                f" after its target; other #{other}"
            )
    print("total issues", total)


def leak() -> None:
    out = {}
    for repo in REPOS:
        iss = load(repo)
        tx = ev.texts(iss)
        for n, i in iss.items():
            sec = sections(i["body"])
            ctx = " ".join(s for s in SENT.split(sec.get("Context", "")) if not HASREF.search(s))
            tx["tcd"][n] = f"{ev.clean(i['title'])}\n{ev.clean(ctx)}\n{ev.clean(sec.get('DONE WHEN', ''))}"
        out[repo] = ev.rank_lexical("bm25", "tcd", iss, tx)
    rks = {a: ev.rankings(a) for a in ("A2", "A3", "A4")}
    rks["A4x"] = out
    for split in ("DEV", "TEST"):
        g = ev.gold("B", split)
        base = ev.per_query(rks["A3"], g, 3)
        for a in ("A2", "A3", "A4", "A4x"):
            x = ev.per_query(rks[a], g, 3)
            d = ev.boot([p - q for p, q in zip(x, base, strict=True)])
            print(f"{split} {a:4s} R@3 {np.mean(x):.3f}  minus A3 {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]")
    g = ev.gold("A", None)
    print("Tier A R@3 A4x", np.mean(ev.per_query(out, g, 3)))


def judge() -> None:
    repo = "agent-harness"
    iss = load(repo)
    th = json.loads(ev.THRESH.read_text())
    gall = ev.all_gold()
    qs = [q for r, q in ev.split_queries("TEST") if r == repo]
    for a in ("A2", "A4"):
        rk = ev.rankings(a)[repo]
        rows = []
        for q in qs:
            for d, s in rk.get(q, [])[:3]:
                if s >= th[a] and d not in gall.get((repo, q), set()):
                    rows.append((q, d, s))
        print(f"== {a}: {len(rows)} unlabelled alerts on agent-harness TEST")
        for q, d, s in rows:
            print(f"{q}|{d}|{s:.3f}|{iss[q]['title'][:90]}|{iss[d]['title'][:90]}")


def latent() -> None:
    from sklearn.feature_extraction.text import TfidfVectorizer

    for repo in REPOS:
        iss = load(repo)
        tx = ev.texts(iss)["td"]
        open_ = sorted(n for n, i in iss.items() if i["state"] == "OPEN")
        m = TfidfVectorizer(sublinear_tf=True, stop_words="english").fit_transform([tx[n] for n in open_])
        sim = (m @ m.T).toarray()
        np.fill_diagonal(sim, 0)
        pairs = sorted(
            ((sim[i, j], open_[i], open_[j]) for i in range(len(open_)) for j in range(i + 1, len(open_))), reverse=True
        )
        print(
            f"== {repo}: {len(open_)} open; pairs with cosine >= 0.5: {sum(p[0] >= 0.5 for p in pairs)}, "
            f">= 0.4: {sum(p[0] >= 0.4 for p in pairs)}"
        )
        for s, a, b in pairs[:15]:
            if repo == "agent-harness":  # public; repo-E is private
                print(f"{s:.3f}|{a}|{b}|{iss[a]['title'][:80]}|{iss[b]['title'][:80]}")
            else:
                print(f"{s:.3f}|{a}|{b}")


def bm25_pure(docs: list[list[str]], query: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Stdlib BM25 (Lucene idf), the variant bm25s uses by default: what devkit would ship without numpy."""
    import math
    from collections import Counter

    n = len(docs)
    avg = sum(map(len, docs)) / n
    df = Counter(w for d in docs for w in set(d))
    out = []
    for d in docs:
        tf = Counter(d)
        norm = k1 * (1 - b + b * len(d) / avg)
        out.append(
            sum(math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * tf[w] / (tf[w] + norm) for w in query if w in tf)
        )
    return out


def purepy() -> None:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    tok = re.compile(r"(?u)\b\w\w+\b")
    a4 = ev.rankings("A4")
    same = total = 0
    worst = 0.0
    for repo in REPOS:
        iss = load(repo)
        tx = ev.texts(iss)["tcd"]
        seq = ev.order(iss)
        for k, q in enumerate(seq[1:], start=1):
            cands = seq[:k]
            docs = [
                [w for w in tok.findall(tx[c].lower()) if w not in ENGLISH_STOP_WORDS] or ["_empty_"] for c in cands
            ]
            qt = [w for w in tok.findall(tx[q].lower()) if w not in ENGLISH_STOP_WORDS]
            s = bm25_pure(docs, qt)
            top = sorted(range(len(cands)), key=lambda j: -s[j])[:3]
            ref = a4[repo].get(q, [])[:3]
            total += 1
            same += [cands[j] for j in top] == [d for d, _ in ref]
            worst = max([worst] + [abs(s[cands.index(d)] - sc) for d, sc in ref])
    print(f"pure-python BM25 vs bm25s A4: identical top-3 on {same}/{total} queries; max score diff {worst:.2e}")


if __name__ == "__main__":
    {"volume": volume, "leak": leak, "judge": judge, "latent": latent, "purepy": purepy}[sys.argv[1]]()
