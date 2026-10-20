"""Score the pre-registered dedup arms (r1-dedup.md) on dedup_labels.json.

Run from the worktree root (deps are ephemeral, not added to the project):
    uv run --no-project --with scikit-learn --with bm25s --with model2vec --with fastembed \
        python docs/research/_scratch/issue-pr-automation/dedup_eval.py [dev|test]

dev: Tier B DEV metrics and the per-arm alert threshold (written to dedup_thresholds.json).
test: everything (Tier B TEST, Tier A, alert rates) with the frozen thresholds; run once.
Rankings are cached in .scratch/dedup/rank_<arm>.json. Raw issue text never leaves .scratch/.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from dedup_labels import REPOS, load, sections

HERE = Path(__file__).parent
RAW = Path.cwd() / ".scratch" / "dedup"
LABELS = json.loads((HERE / "dedup_labels.json").read_text())
THRESH = HERE / "dedup_thresholds.json"
ARMS = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]
DESC = {
    "A1": "TF-IDF title",
    "A2": "TF-IDF title+DONE WHEN",
    "A3": "BM25 title+DONE WHEN",
    "A4": "BM25 title+Context+DONE WHEN",
    "A5": "potion-base-8M title+DONE WHEN",
    "A6": "potion-retrieval-32M title+DONE WHEN",
    "A7": "bge-small-en-v1.5 title+DONE WHEN",
    "A8": "RRF(A3, A6)",
}
TOPN = 10
NUMREF = re.compile(r"[\w.-]*#\d+")
CHECK = re.compile(r"^\s*-\s*\[[ xX]\]\s*", re.M)


def clean(s: str) -> str:
    return CHECK.sub("", NUMREF.sub(" ", s or "")).replace("`", " ")


def texts(iss: dict) -> dict[str, dict[int, str]]:
    out: dict[str, dict[int, str]] = {"t": {}, "td": {}, "tcd": {}}
    for n, i in iss.items():
        sec = sections(i["body"])
        title, done, ctx = clean(i["title"]), clean(sec.get("DONE WHEN", "")), clean(sec.get("Context", ""))
        out["t"][n] = title
        out["td"][n] = f"{title}\n{done}"
        out["tcd"][n] = f"{title}\n{ctx}\n{done}"
    return out


def order(iss: dict) -> list[int]:
    return sorted(iss, key=lambda n: iss[n]["createdAt"])


def rank_lexical(kind: str, field: str, iss: dict, tx: dict) -> dict[int, list]:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

    tok = re.compile(r"(?u)\b\w\w+\b")

    def toks(s: str) -> list[str]:
        return [w for w in tok.findall(s.lower()) if w not in ENGLISH_STOP_WORDS]

    seq = order(iss)
    res: dict[int, list] = {}
    for k, q in enumerate(seq):
        cands = seq[:k]
        if not cands:
            continue
        docs = [tx[field][c] for c in cands]
        if kind == "tfidf":
            vec = TfidfVectorizer(sublinear_tf=True, stop_words="english")
            try:
                m = vec.fit_transform(docs)
            except ValueError:
                continue
            s = (m @ vec.transform([tx[field][q]]).T).toarray().ravel()
        else:
            import bm25s

            r = bm25s.BM25(k1=1.5, b=0.75)
            corpus = [toks(d) or ["_empty_"] for d in docs]
            r.index(corpus, show_progress=False)
            vocab = r.vocab_dict
            qt = [w for w in toks(tx[field][q]) if w in vocab]
            s = r.get_scores(qt) if qt else np.zeros(len(cands))
        top = np.argsort(-s, kind="stable")[:TOPN]
        res[q] = [[cands[j], float(s[j])] for j in top]
    return res


def rank_dense(model: str, iss: dict, tx: dict) -> dict[int, list]:
    seq = order(iss)
    docs = [tx["td"][n] for n in seq]
    if model.startswith("minishlab/"):
        from model2vec import StaticModel

        e = np.asarray(StaticModel.from_pretrained(model).encode(docs))
    else:
        from fastembed import TextEmbedding

        e = np.asarray(list(TextEmbedding(model_name=model).embed(docs)))
    e = e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
    sim = e @ e.T
    res: dict[int, list] = {}
    for k, q in enumerate(seq):
        if k == 0:
            continue
        s = sim[k, :k]
        top = np.argsort(-s, kind="stable")[:TOPN]
        res[q] = [[seq[j], float(s[j])] for j in top]
    return res


def rrf(a: dict, b: dict, k: int = 60) -> dict[int, list]:
    out = {}
    for q in a:
        sc: dict[int, float] = {}
        for lst in (a[q], b.get(q, [])):
            for r, (d, _) in enumerate(lst):
                sc[d] = sc.get(d, 0.0) + 1.0 / (k + r + 1)
        out[q] = sorted(([d, s] for d, s in sc.items()), key=lambda x: -x[1])[:TOPN]
    return out


def rankings(arm: str) -> dict[str, dict[int, list]]:
    path = RAW / f"rank_{arm}.json"
    if path.exists():
        raw = json.loads(path.read_text())
        return {r: {int(q): v for q, v in d.items()} for r, d in raw.items()}
    out = {}
    for repo in REPOS:
        iss = load(repo)
        tx = texts(iss)
        if arm == "A1":
            out[repo] = rank_lexical("tfidf", "t", iss, tx)
        elif arm == "A2":
            out[repo] = rank_lexical("tfidf", "td", iss, tx)
        elif arm == "A3":
            out[repo] = rank_lexical("bm25", "td", iss, tx)
        elif arm == "A4":
            out[repo] = rank_lexical("bm25", "tcd", iss, tx)
        elif arm == "A5":
            out[repo] = rank_dense("minishlab/potion-base-8M", iss, tx)
        elif arm == "A6":
            out[repo] = rank_dense("minishlab/potion-retrieval-32M", iss, tx)
        elif arm == "A7":
            out[repo] = rank_dense("BAAI/bge-small-en-v1.5", iss, tx)
        elif arm == "A8":
            out[repo] = rrf(rankings("A3")[repo], rankings("A6")[repo])
    path.write_text(json.dumps(out))
    return out


def gold(tier: str, split: str | None) -> dict[tuple[str, int], set[int]]:
    g: dict[tuple[str, int], set[int]] = {}
    for repo, d in LABELS["repos"].items():
        for p in d["tier_a" if tier == "A" else "tier_b"]:
            if split and p.get("split") != split:
                continue
            g.setdefault((repo, p["query"]), set()).add(p["target"])
    return g


def all_gold() -> dict[tuple[str, int], set[int]]:
    g: dict[tuple[str, int], set[int]] = {}
    for t in ("A", "B"):
        for k, v in gold(t, None).items():
            g.setdefault(k, set()).update(v)
    return g


def per_query(rk: dict, g: dict, k: int) -> list[float]:
    return [float(any(d in tg for d, _ in rk[repo].get(q, [])[:k])) for (repo, q), tg in g.items()]


def p_at3(rk: dict, g: dict) -> float:
    return float(np.mean([sum(d in tg for d, _ in rk[repo].get(q, [])[:3]) / 3 for (repo, q), tg in g.items()]))


def boot(x: list[float], n: int = 2000, seed: int = 346) -> tuple[float, float, float]:
    a = np.asarray(x)
    idx = np.random.default_rng(seed).integers(0, len(a), size=(n, len(a)))
    ms = np.sort(a[idx].mean(axis=1))
    return float(a.mean()), float(ms[int(0.025 * n)]), float(ms[int(0.975 * n) - 1])


def alerts(rk: dict, g_all: dict, tau: float, queries: list[tuple[str, int]], g_eval: dict) -> dict:
    """Show up to 3 suggestions with score >= tau. Precision counts any Tier A/B target as correct (lower bound)."""
    shown = hit = alerted = 0
    rec = []
    for repo, q in queries:
        top = [d for d, s in rk[repo].get(q, [])[:3] if s >= tau]
        shown += len(top)
        alerted += bool(top)
        hit += sum(d in g_all.get((repo, q), set()) for d in top)
        if (repo, q) in g_eval:
            rec.append(float(any(d in g_eval[(repo, q)] for d in top)))
    return {
        "precision": hit / shown if shown else float("nan"),
        "recall3": float(np.mean(rec)) if rec else float("nan"),
        "alert_rate": alerted / len(queries),
        "shown_per_filing": shown / len(queries),
    }


def split_queries(split: str) -> list[tuple[str, int]]:
    """Every filing whose query hash falls in split (labelled or not), for alert rate and precision."""
    from dedup_labels import is_test

    out = []
    for repo in REPOS:
        for q in load(repo):
            if (split == "TEST") == is_test(repo, q):
                out.append((repo, q))
    return out


def tune(rk: dict) -> float:
    g_dev, g_all, qs = gold("B", "DEV"), all_gold(), split_queries("DEV")
    scores = sorted({round(s, 6) for repo in rk for lst in rk[repo].values() for _, s in lst[:3]})
    cand = scores[:: max(1, len(scores) // 400)]
    best, best_f = cand[0], -1.0
    for t in cand:
        m = alerts(rk, g_all, t, qs, g_dev)
        p, r = m["precision"], m["recall3"]
        f = 2 * p * r / (p + r) if p == p and p + r > 0 else 0.0
        if f > best_f:
            best, best_f = t, f
    return best


def main(mode: str) -> None:
    rks = {a: rankings(a) for a in ARMS}
    if mode == "dev":
        g = gold("B", "DEV")
        th = {}
        print(f"Tier B DEV: {len(g)} queries")
        for a in ARMS:
            th[a] = tune(rks[a])
            m = alerts(rks[a], all_gold(), th[a], split_queries("DEV"), g)
            r3 = boot(per_query(rks[a], g, 3))
            print(
                f"{a} {DESC[a]:38s} R@1 {np.mean(per_query(rks[a], g, 1)):.3f} R@3 {r3[0]:.3f} "
                f"[{r3[1]:.3f},{r3[2]:.3f}] R@5 {np.mean(per_query(rks[a], g, 5)):.3f} P@3 {p_at3(rks[a], g):.3f} "
                f"| tau {th[a]:.4f} alertP {m['precision']:.3f} alertR3 {m['recall3']:.3f} "
                f"rate {m['alert_rate']:.2f} shown {m['shown_per_filing']:.2f}"
            )
        THRESH.write_text(json.dumps(th, indent=1) + "\n")
        return
    th = json.loads(THRESH.read_text())
    for tier, split in (("B", "TEST"), ("A", None)):
        g = gold(tier, split)
        print(f"\nTier {tier} {split or 'all'}: {len(g)} queries, {sum(len(v) for v in g.values())} pairs")
        for a in ARMS:
            r3 = boot(per_query(rks[a], g, 3))
            m = alerts(rks[a], all_gold(), th[a], split_queries("TEST") if tier == "B" else list(g), g)
            print(
                f"{a} {DESC[a]:38s} R@1 {np.mean(per_query(rks[a], g, 1)):.3f} R@3 {r3[0]:.3f} "
                f"[{r3[1]:.3f},{r3[2]:.3f}] R@5 {np.mean(per_query(rks[a], g, 5)):.3f} P@3 {p_at3(rks[a], g):.3f} "
                f"| alertP {m['precision']:.3f} alertR3 {m['recall3']:.3f} rate {m['alert_rate']:.2f} "
                f"shown {m['shown_per_filing']:.2f}"
            )
        if tier == "B":
            best = max(ARMS, key=lambda a: np.mean(per_query(rks[a], g, 3)))
            print(f"best on TEST R@3: {best}; paired bootstrap of (arm - best):")
            xb = per_query(rks[best], g, 3)
            for a in ARMS:
                d = boot([x - y for x, y in zip(per_query(rks[a], g, 3), xb, strict=True)])
                print(f"  {a} {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]")
    for repo in REPOS:
        for (r, q), tg in gold("A", None).items():
            if r == repo:
                print(
                    repo,
                    q,
                    "->",
                    sorted(tg),
                    {a: [d for d, _ in rks[a][repo].get(q, [])[:5]] for a in ("A2", "A3", "A6")},
                )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
