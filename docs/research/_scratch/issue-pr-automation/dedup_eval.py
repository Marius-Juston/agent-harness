"""Score the pre-registered dedup arms (r1-dedup.md) on dedup_labels.json.

Run from the worktree root (deps are ephemeral, not added to the project):
    uv run --no-project --with scikit-learn --with bm25s --with model2vec --with fastembed \
        python docs/research/_scratch/issue-pr-automation/dedup_eval.py [dev|test]

dev: Tier B DEV metrics and the per-arm alert threshold (written to dedup_thresholds.json).
test: everything (Tier B TEST, Tier A, alert rates) with the frozen thresholds; run once.
Rankings are cached in .scratch/dedup/rank_<arm>.json. Raw issue text never leaves .scratch/.

Ship mode (#350): the ranker `board new` ships, scored by devkit.board's own code. Needs only numpy and the project:
    REPO_E=<name> uv run --with numpy python docs/research/_scratch/issue-pr-automation/dedup_eval.py ship-dev
    REPO_E=<name> uv run --with numpy python docs/research/_scratch/issue-pr-automation/dedup_eval.py ship-test
Pre-registered on 2026-10-20, committed before any ship-mode number was computed:
- Data: the issues of dedup_labels.json's freeze, i.e. the first n_issues of each repository by creation (later
  filings are dropped), refetched by `dedup_labels.py fetch`; text is the current text, as in round 1. Every filing is
  a query, ranked against earlier filings by devkit.board's terms() and bm25() over title + Context + DONE WHEN
  (full text, as `board new` indexes it).
- Cells: window W-all (every earlier issue, any state) or W-30 (earlier issues still open when the query was filed,
  or closed at most 30 days before) x threshold form raw (BM25 score) or ratio (score / the query's self-score).
- Threshold per cell, on DEV only: the candidate value (tune()'s grid) with the highest F1 of alert precision
  (labels of both tiers, as tune()) and alert recall@3 (Tier B DEV), among values whose DEV alert rate is at most
  0.45: DONE WHEN's 0.50 cap less about 1.5 standard errors of a rate over the ~240 TEST filings.
- Pick: the cell with the highest DEV alert recall@3. Any cell whose paired bootstrap 95% CI of the difference to it
  includes 0 ties with it; ties go to W-all over W-30 (the setting of every earlier number, and no closing dates to
  fetch), then ratio over raw (r1-dedup.md F5: a raw score drifts with corpus size). Written to ship_pick.json.
- Exploratory, never used to pick: the same rule for windows of 1, 3 and 7 days, since the boards were about 10
  days old at the freeze.
- TEST, once (refuses when ship_test_output.txt exists): for the pick, Tier B TEST recall@1/3 with CI (no
  threshold), alert precision (labels), alert recall@3 and alert rate, for both repositories and agent-harness
  alone, on full text and with the Context sentences that name an issue removed (round 2's leak-removed); Tier A
  hits; and the unlabelled agent-harness TEST alerts (numbers only) in ship_unlabelled.json for the second rater.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
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
        for q in frozen(repo):
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


# --- ship mode (#350): the shipped ranker, its candidate window and its threshold form ----------------------------

SHIP_PICK = HERE / "ship_pick.json"
SHIP_DEV = HERE / "ship_dev_output.txt"
SHIP_TEST = HERE / "ship_test_output.txt"
SHIP_UNLABELLED = HERE / "ship_unlabelled.json"
WINDOWS = {"W-all": None, "W-30": 30}
EXPLORE = {"W-7": 7, "W-3": 3, "W-1": 1}
FORMS = ("raw", "ratio")
RATE_CAP = 0.45
SENT = re.compile(r"(?<=[.;:!?])\s+|\n+")  # as dedup_explore.py leak
HASREF = re.compile(r"#\d+")


def frozen(repo: str) -> dict[int, dict]:
    """The issues of the freeze: the first n_issues by creation (filings since then are not in the labels)."""
    iss = load(repo)
    keep = sorted(iss, key=lambda n: iss[n]["createdAt"])[: LABELS["repos"][repo]["n_issues"]]
    return {n: iss[n] for n in keep}


def when(s: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def ship_rankings(window: int | None, leak_removed: bool = False) -> dict[str, dict[int, list]]:
    """repo -> query -> top TOPN [candidate, raw, ratio], by devkit.board's terms() and bm25()."""
    from devkit import board

    path = RAW / f"ship_{window or 'all'}{'_leak' if leak_removed else ''}.json"
    if path.exists():
        raw = json.loads(path.read_text())
        return {r: {int(q): v for q, v in d.items()} for r, d in raw.items()}
    out = {}
    for repo in REPOS:
        iss = frozen(repo)
        seq = order(iss)
        toks = {}
        for n in seq:
            sec = board.sections(iss[n]["body"])
            ctx = sec.get("Context", "")
            if leak_removed:
                ctx = " ".join(s for s in SENT.split(ctx) if not HASREF.search(s))
            toks[n] = board.issue_terms(iss[n]["title"], ctx, sec.get("DONE WHEN", ""))
        res: dict[int, list] = {}
        for k, q in enumerate(seq):
            t = when(iss[q]["createdAt"])
            cands = [
                c
                for c in seq[:k]
                if window is None or not iss[c]["closedAt"] or when(iss[c]["closedAt"]) >= t - window * 86400
            ]
            if not cands:
                continue
            docs = [toks[c] for c in cands]
            scores, own = board.bm25(docs, toks[q]), self_score(docs, toks[q])
            top = sorted(range(len(cands)), key=lambda j: (-scores[j], j))[:TOPN]
            res[q] = [[cands[j], scores[j], scores[j] / own if own else 0.0] for j in top]
        out[repo] = res
    path.write_text(json.dumps(out))
    return out


def self_score(docs: list[list[str]], query: list[str]) -> float:
    """The query's BM25 score as a document under the candidates' statistics (the same terms its candidates can
    match), so score / self-score does not grow with the corpus the way a raw score does. board.bm25's formula."""
    from devkit import board

    n, k1, b = len(docs), board.BM25_K1, board.BM25_B
    avg = sum(map(len, docs)) / n or 1.0
    df = Counter(w for d in docs for w in set(d))
    tf, norm = Counter(query), k1 * (1 - b + b * len(query) / avg)
    return sum(math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * tf[w] / (tf[w] + norm) for w in query if df[w])


def form(rk: dict, f: str) -> dict:
    """Rankings with the score of threshold form f: raw BM25 or score / self-score (ranking order is the same)."""
    col = 1 if f == "raw" else 2
    return {r: {q: [[x[0], x[col]] for x in lst] for q, lst in d.items()} for r, d in rk.items()}


def tune_capped(rk: dict) -> float:
    """tune() with the pre-registered alert-rate cap on DEV."""
    g_dev, g_all, qs = gold("B", "DEV"), all_gold(), split_queries("DEV")
    scores = sorted({round(s, 6) for repo in rk for lst in rk[repo].values() for _, s in lst[:3]})
    cand = scores[:: max(1, len(scores) // 400)]
    best, best_f = math.inf, -1.0
    for t in cand:
        m = alerts(rk, g_all, t, qs, g_dev)
        if m["alert_rate"] > RATE_CAP:
            continue
        p, r = m["precision"], m["recall3"]
        f = 2 * p * r / (p + r) if p == p and p + r > 0 else 0.0
        if f > best_f:
            best, best_f = t, f
    return best


def alert_hits(rk: dict, tau: float, g: dict) -> list[float]:
    return [float(any(d in tg for d, s in rk[repo].get(q, [])[:3] if s >= tau)) for (repo, q), tg in g.items()]


def ship_dev() -> None:
    g, qs, out = gold("B", "DEV"), split_queries("DEV"), []
    cells, hits = {}, {}
    for wname, days in {**WINDOWS, **EXPLORE}.items():
        rk = ship_rankings(days)
        r3 = boot(per_query(form(rk, "raw"), g, 3))
        out.append(
            f"{wname}: Tier B DEV {len(g)} queries, R@1 {np.mean(per_query(form(rk, 'raw'), g, 1)):.3f} "
            f"R@3 {r3[0]:.3f} [{r3[1]:.3f},{r3[2]:.3f}]"
        )
        for f in FORMS:
            rf = form(rk, f)
            tau = tune_capped(rf)
            m = alerts(rf, all_gold(), tau, qs, g)
            cells[(wname, f)] = {"tau": tau, **m}
            hits[(wname, f)] = alert_hits(rf, tau, g)
            out.append(
                f"  {f:5s} tau {tau:.4f} alertP {m['precision']:.3f} alertR3 {m['recall3']:.3f} "
                f"rate {m['alert_rate']:.3f} shown {m['shown_per_filing']:.2f}"
            )
    pre = [(w, f) for w in WINDOWS for f in FORMS]
    best = max(pre, key=lambda c: cells[c]["recall3"])
    out.append(f"best DEV alert recall@3: {best}; paired bootstrap (cell - best):")
    tied = []
    for c in pre:
        d = boot([x - y for x, y in zip(hits[c], hits[best], strict=True)])
        out.append(f"  {c} {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]")
        if c == best or d[1] <= 0 <= d[2]:
            tied.append(c)
    pick = min(tied, key=lambda c: (list(WINDOWS).index(c[0]), -FORMS.index(c[1])))
    out.append(f"tied with best: {tied}; pick {pick} at tau {cells[pick]['tau']:.6f}")
    for c in [(w, f) for w in EXPLORE for f in FORMS]:
        d = boot([x - y for x, y in zip(hits[c], hits[pick], strict=True)])
        out.append(f"  exploratory {c} minus pick {d[0]:+.3f} [{d[1]:+.3f},{d[2]:+.3f}]")
    for repo in REPOS:
        iss = frozen(repo)
        seq = order(iss)
        kept = {
            w: sum(
                1
                for k, q in enumerate(seq)
                for c in seq[:k]
                if d is None
                or not iss[c]["closedAt"]
                or when(iss[c]["closedAt"]) >= when(iss[q]["createdAt"]) - d * 86400
            )
            for w, d in {**WINDOWS, **EXPLORE}.items()
        }
        out.append(f"{repo}: {len(iss)} issues; (query, candidate) pairs per window {kept}")
    text = "\n".join(out)
    print(text)
    SHIP_DEV.write_text(text + "\n")
    SHIP_PICK.write_text(
        json.dumps(
            {
                "window": pick[0],
                "form": pick[1],
                "tau": cells[pick]["tau"],
                "cells": {f"{w} {f}": cells[(w, f)] for w, f in cells},
            },
            indent=1,
        )
        + "\n"
    )


def ship_test() -> None:
    from devkit import board

    if SHIP_TEST.exists():
        sys.exit(f"{SHIP_TEST.name} exists: TEST is scored once")
    pick = json.loads(SHIP_PICK.read_text())
    # the shipped code ranks every fetched issue (W-all) by raw score, at RELATED_MIN: score exactly that line
    if (pick["window"], pick["form"]) != ("W-all", "raw") or abs(board.RELATED_MIN - pick["tau"]) >= 0.01:
        sys.exit(f"devkit.board ships W-all raw at {board.RELATED_MIN}, not the DEV pick {pick}")
    tau, out = board.RELATED_MIN, [f"pick {pick['window']} {pick['form']}, shipped RELATED_MIN {board.RELATED_MIN}"]
    g_all, qs_all = all_gold(), split_queries("TEST")
    for text, leak in (("full text", False), ("leak-removed", True)):
        rk = form(ship_rankings(WINDOWS[pick["window"]], leak), pick["form"])
        for scope in ("both repositories", "agent-harness"):
            keep = (lambda r: True) if scope == "both repositories" else (lambda r: r == "agent-harness")
            g = {k: v for k, v in gold("B", "TEST").items() if keep(k[0])}
            qs = [k for k in qs_all if keep(k[0])]
            r3 = boot(per_query(rk, g, 3))
            m = alerts(rk, g_all, tau, qs, g)
            out.append(
                f"[{text}] {scope}: Tier B TEST {len(g)} queries, {len(qs)} filings | R@1 "
                f"{np.mean(per_query(rk, g, 1)):.3f} R@3 {r3[0]:.3f} [{r3[1]:.3f},{r3[2]:.3f}] "
                f"R@5 {np.mean(per_query(rk, g, 5)):.3f} | alertP {m['precision']:.3f} alertR3 {m['recall3']:.3f} "
                f"rate {m['alert_rate']:.3f} shown {m['shown_per_filing']:.2f} ({round(m['shown_per_filing'] * len(qs))})"
            )
        ga = gold("A", None)
        out.append(
            f"[{text}] Tier A: {int(sum(per_query(rk, ga, 3)))}/{len(ga)} in top 3, "
            f"{int(sum(alert_hits(rk, tau, ga)))}/{len(ga)} shown"
        )
    rk = form(ship_rankings(WINDOWS[pick["window"]]), pick["form"])
    repo = "agent-harness"
    rows = [
        {"query": q, "suggested": d}
        for r, q in qs_all
        if r == repo
        for d, s in rk[repo].get(q, [])[:3]
        if s >= tau and d not in g_all.get((repo, q), set())
    ]
    labelled = sum(
        d in g_all.get((repo, q), set()) for r, q in qs_all if r == repo for d, s in rk[repo].get(q, [])[:3] if s >= tau
    )
    first = {(p["query"], p["suggested"]) for p in json.loads((HERE / "dedup_judged.json").read_text())["pairs"]}
    out.append(
        f"agent-harness TEST alerts: {labelled} labelled, {len(rows)} unlabelled, "
        f"{sum((p['query'], p['suggested']) in first for p in rows)} of them judged by rater 1"
    )
    SHIP_UNLABELLED.write_text(
        json.dumps(
            {
                "what": "Unlabelled shipped-line alerts on agent-harness TEST (dedup_eval.py ship-test)",
                "labelled_alerts": labelled,
                "pairs": rows,
            },
            indent=1,
        )
        + "\n"
    )
    text = "\n".join(out)
    print(text)
    SHIP_TEST.write_text(text + "\n")


def kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's kappa of two raters' binary calls on the same items."""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def ship_agree() -> None:
    """Both raters' precision of the shipped line on agent-harness TEST, and their agreement (run after rater 2
    committed dedup_judged_r2.json without reading dedup_judged.json's judgements)."""
    shipped = json.loads(SHIP_UNLABELLED.read_text())
    keys = [(p["query"], p["suggested"]) for p in shipped["pairs"]]
    raters = {
        name: {(p["query"], p["suggested"]): p["judgement"] for p in json.loads((HERE / f).read_text())["pairs"]}
        for name, f in (("rater 1", "dedup_judged.json"), ("rater 2", "dedup_judged_r2.json"))
    }
    shown, out = shipped["labelled_alerts"] + len(keys), []
    calls = {}
    for name, j in raters.items():
        missing = [k for k in keys if k not in j]
        if missing:
            sys.exit(f"{name} has not judged {missing}")
        calls[name] = [j[k] != "unrelated" for k in keys]
        dup = sum(j[k] == "duplicate" for k in keys)
        p = (shipped["labelled_alerts"] + sum(calls[name])) / shown
        out.append(
            f"{name}: {sum(calls[name])} of {len(keys)} unlabelled related ({dup} duplicates); precision "
            f"({shipped['labelled_alerts']} labelled + {sum(calls[name])}) / {shown} = {p:.3f}"
        )
    a, b = calls["rater 1"], calls["rater 2"]
    out.append(
        f"agreement on {len(keys)} unlabelled: {sum(x == y for x, y in zip(a, b, strict=True))} same call, "
        f"Cohen's kappa {kappa(a, b):.3f}; both related {sum(x and y for x, y in zip(a, b, strict=True))}, "
        f"only rater 1 {sum(x and not y for x, y in zip(a, b, strict=True))}, "
        f"only rater 2 {sum(y and not x for x, y in zip(a, b, strict=True))}"
    )
    text = "\n".join(out)
    print(text)
    (HERE / "ship_agree_output.txt").write_text(text + "\n")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if mode == "ship-dev":
        ship_dev()
    elif mode == "ship-test":
        ship_test()
    elif mode == "ship-agree":
        ship_agree()
    else:
        main(mode)
