"""Tables for the #495 note from eval_nl.py and eval_callers.py output.

NL questions, per arm and view (`all` = every unit; `src` = tests and eval fixtures dropped from the ranking):
  top1/top5   share of questions with a gold function at rank 1 / in the first 5 (Wilson 95 % interval for top5)
  file5       share with a gold file among the first 5 distinct files of the ranking
  tok         mean tokens the arm prints (grep: its whole capped output; others: 5 one-line cards)
  tok/ans     the owner's efficiency figure: all tokens printed over all questions / questions answered (top5)
  ms          median query latency (warm index), build_s median index build per tree
  d5 vs X     paired difference in top5 against arm X, bootstrap 95 % interval (10,000 resamples, seed 0)
RRF fusions (k = 60) of BM25 with each embedding arm are computed here from the stored rankings (first 50 of each).

usage: uv run --no-project --with numpy python score.py RES.json [RES2.json ...] [--callers CALLERS.json]
"""

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def rrf(*lists: list[str], k: int = 60) -> list[str]:
    s: dict[str, float] = defaultdict(float)
    for lst in lists:
        for r, key in enumerate(lst):
            s[key] += 1 / (k + r + 1)
    return sorted(s, key=lambda x: -s[x])


def files_of(ranked: list[str]) -> list[str]:
    return list(dict.fromkeys(r.split("::")[0] for r in ranked))


CARD_TOKENS = None  # fused arms print 5 cards like the others; their size is taken as the mean of the two inputs


def nl(paths: list[str]) -> None:
    rows: dict[str, dict] = {}
    for p in paths:
        for r in json.loads(Path(p).read_text()):
            rows.setdefault(r["id"], {**r, "results": {}})["results"].update(r["results"])
    qs = list(rows.values())
    arms = list(qs[0]["results"])
    emb = [a for a in arms if a.startswith(("m2v", "bge", "jina"))]
    for q in qs:
        for e in emb:
            for view in ("", "_src"):
                a, b = q["results"]["bm25-full"], q["results"][e]
                f = q["results"].setdefault(
                    f"rrf(bm25+{e})", {"ms": a["ms"] + b["ms"], "build_s": a["build_s"] + b["build_s"]}
                )
                f[f"ranked{view}"] = rrf(a[f"ranked{view}"], b[f"ranked{view}"])
                f[f"tokens{view}"] = (a[f"tokens{view}"] + b[f"tokens{view}"]) / 2
    arms = list(qs[0]["results"])
    n = len(qs)
    print(f"n = {n} questions, mean units per tree {statistics.mean(q['n_units'] for q in qs):.0f}")
    hits5: dict[tuple[str, str], np.ndarray] = {}
    for view, key in (("all", ""), ("src", "_src")):
        print(f"\n### view: {view}\n")
        print("| arm | top-1 | top-5 [95 % CI] | file top-5 | tokens | tokens per answer | median ms | build s |")
        print("|---|---|---|---|---|---|---|---|")
        for arm in arms:
            t1 = t5 = f5 = 0
            toks, ms, bs, h = [], [], [], []
            for q in qs:
                r = q["results"][arm]
                ranked = r[f"ranked{key}"] if not arm.startswith("grep") else r["ranked"]
                gold = set(q["gold"])
                gfiles = {g.split("::")[0] for g in gold}
                t1 += bool(ranked[:1] and ranked[0] in gold)
                hit = any(x in gold for x in ranked[:5])
                t5 += hit
                h.append(hit)
                f5 += any(f in gfiles for f in files_of(ranked)[:5])
                toks.append(r[f"tokens{key}"] if not arm.startswith("grep") else r["tokens"])
                ms.append(r["ms"])
                bs.append(r.get("build_s", 0))
            hits5[(view, arm)] = np.array(h)
            lo, hi = wilson(t5, n)
            per = sum(toks) / t5 if t5 else float("inf")
            print(
                f"| {arm} | {t1 / n:.2f} | {t5 / n:.2f} [{lo:.2f}, {hi:.2f}] | {f5 / n:.2f} | {statistics.mean(toks):.0f} | "
                f"{per:.0f} | {statistics.median(ms):.1f} | {statistics.median(bs):.2f} |"
            )
        # grep: answered anywhere in its output (the agent reads all of it)
        if view == "all":
            g_any = sum(any(x in set(q["gold"]) for x in q["results"]["grep"]["ranked"]) for q in qs)
            g_tok = sum(q["results"]["grep"]["tokens"] for q in qs)
            print(
                f"\ngrep, gold anywhere in the capped output: {g_any}/{n}; tokens per such answer {g_tok / max(g_any, 1):.0f}"
            )
    rng = np.random.default_rng(0)
    print("\n### paired top-5 differences (src view; grep uses its own output)\n")
    for base in ("grep", "bm25-full"):
        for arm in arms:
            if arm == base:
                continue
            a, b = hits5[("src", arm)].astype(float), hits5[("src", base)].astype(float)
            idx = rng.integers(0, n, (10000, n))
            d = (a[idx] - b[idx]).mean(axis=1)
            print(
                f"- {arm} - {base}: {a.mean() - b.mean():+.2f} [{np.percentile(d, 2.5):+.2f}, {np.percentile(d, 97.5):+.2f}]"
            )


def callers(path: str) -> None:
    d = json.loads(Path(path).read_text())
    rows = d["rows"]
    print(
        f"\n### callers: {len(rows)} targets, {sum(len(r['gold']) for r in rows)} gold caller functions; ty server start {d['ty_start_ms']:.0f} ms\n"
    )
    print("| arm | recall | precision | all callers found | tokens (mean) | tokens per caller found | median ms |")
    print("|---|---|---|---|---|---|---|")
    for arm in rows[0]["results"]:
        tp = fp = fn = complete = 0
        toks, ms = [], []
        for r in rows:
            found, gold = set(r["results"][arm]["found"]), set(r["gold"])
            tp += len(found & gold)
            fp += len(found - gold)
            fn += len(gold - found)
            complete += gold <= found
            toks.append(r["results"][arm]["tokens"])
            ms.append(r["results"][arm]["ms"])
        print(
            f"| {arm} | {tp / (tp + fn):.2f} | {tp / max(tp + fp, 1):.2f} | {complete}/{len(rows)} | {statistics.mean(toks):.0f} | "
            f"{sum(toks) / max(tp, 1):.0f} | {statistics.median(ms):.0f} |"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("res", nargs="*")
    ap.add_argument("--callers")
    a = ap.parse_args()
    if a.res:
        nl(a.res)
    if a.callers:
        callers(a.callers)


if __name__ == "__main__":
    main()
