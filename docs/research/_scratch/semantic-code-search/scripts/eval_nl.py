"""Score retrieval arms on the history questions (#495): each question is an issue title, asked of the tree before
the commit that closed it; the answer is the set of pre-existing functions that commit modified (mine.py).

Arms (each returns a ranked list of units, `path::Qualname`):
  grep      `dev code find` as an agent would run it: the 3 rarest content words of the title as one -i alternation
            (grep-all: every content word of three letters or more),
            output grouped by file (alphabetical, tests last), 6 lines per file, 60 lines in all; rank = the order of
            the output lines, each mapped to the function that contains it
  bm25-head / bm25-full   devkit.board.bm25 (Lucene idf, k1 1.5, b 0.75) over a unit's head or full text
  m2v-8M / m2v-32M        model2vec potion-base-8M / potion-retrieval-32M, cosine over the full text
  bge-small               fastembed BAAI/bge-small-en-v1.5 (ONNX), cosine over the full text (truncated at 512)
  jina-code               fastembed jinaai/jina-embeddings-v2-base-code, same
  rrf(a+b)                reciprocal-rank fusion, k = 60
  repomap   aider's get_ranked_tags reimplemented on stdlib ast: file graph referencer -> definer weighted by
            sqrt(refs) and aider's multipliers, personalised PageRank on files whose path matches a title word,
            rank spread over (file, identifier) definitions

usage: uv run --no-project --with model2vec --with fastembed --with numpy python eval_nl.py QUESTIONS.json OUT.json
       [--arms a,b,...]
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from corpus import Unit, extract, split_ident, tokens, units_of  # noqa: E402

from devkit.board import STOP_WORDS, bm25, terms  # noqa: E402

TREES = Path(os.environ.get("TREES", Path.home() / ".cache" / "semantic-code-search-trees"))
TOPK = 5


def is_test(key: str) -> bool:
    """Test and eval-fixture code: shown last by `dev code refs`, dropped from the `src` view of every ranking."""
    path = key.split("::")[0]
    return path.startswith(("tests/", "evals/")) or "/tests/" in path or Path(path).name.startswith("test_")


def words(text: str) -> list[str]:
    return [w for w in split_ident(text) if len(w) > 1 and w not in STOP_WORDS]


def query_words(q: str) -> list[str]:
    return words(" ".join(terms(q)))


def card_block(units: list[Unit], ranked: list[str], k: int = TOPK) -> str:
    by = {u.key: u for u in units}
    return "\n".join(by[r].card for r in ranked[:k])


# ---------- grep (dev code find) ----------
def locate(units: list[Unit]) -> dict[str, list[Unit]]:
    by = defaultdict(list)
    for u in units:
        by[u.path].append(u)
    return by


def unit_at(by_path: dict[str, list[Unit]], path: str, line: int) -> str | None:
    best = None
    for u in by_path.get(path, []):
        if u.line <= line <= u.end and (best is None or u.line >= best.line):
            best = u
    return best.key if best else None


def grep_arm(root: Path, units: list[Unit], q: str, n_words: int = 3) -> tuple[list[str], str]:
    qw = list(dict.fromkeys(w for w in query_words(q) if len(w) > 2))
    if not qw:
        return [], "no matches"
    # rarity of each word = number of matching lines in the tree (case-insensitive substring, as grep -i would)
    counts = {}
    for w in qw:
        r = subprocess.run(["grep", "-rIci", "--", w, "."], cwd=root, capture_output=True, text=True)
        counts[w] = sum(int(x.rsplit(":", 1)[1]) for x in r.stdout.splitlines() if x.rsplit(":", 1)[1] != "0")
    picked = [w for w in sorted(qw, key=lambda w: counts[w]) if counts[w]][:n_words]
    if not picked:
        return [], "no matches"
    r = subprocess.run(["grep", "-rnIiE", "--", "|".join(picked), "."], cwd=root, capture_output=True, text=True)
    hits = []
    for line in r.stdout.splitlines():
        path, _, rest = line.partition(":")
        num, _, text = rest.partition(":")
        if num.isdigit():
            hits.append((path.removeprefix("./"), int(num), text))
    by_file = defaultdict(list)
    for f, n, t in hits:
        by_file[f].append((n, t))
    order = sorted(by_file, key=lambda f: (f.startswith("tests/"), f))
    out, shown, ranked = [f"(grep -iE '{'|'.join(picked)}')"], 0, []
    bp = locate(units)
    for f in order:
        rows = sorted(by_file[f])
        out.append(f"== {f} ({len(rows)})")
        for n, t in rows[:6]:
            out.append(f"{n:>5}  {t.strip()[:160]}")
            shown += 1
            if (k := unit_at(bp, f, n)) and k not in ranked:
                ranked.append(k)
        if len(rows) > 6:
            out.append(f"      ... {len(rows) - 6} more in this file")
        if shown >= 60:
            break
    return ranked, "\n".join(out)


# ---------- BM25 ----------
class BM25Index:
    def __init__(self, units: list[Unit], field: str) -> None:
        self.keys = [u.key for u in units]
        self.docs = [words(getattr(u, field)) for u in units]

    def query(self, q: str) -> list[str]:
        s = bm25(self.docs, query_words(q))
        return [self.keys[i] for i in sorted(range(len(s)), key=lambda i: -s[i]) if s[i] > 0]


# ---------- embeddings ----------
class Embedder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cache: dict[str, np.ndarray] = {}
        t = time.time()
        if name.startswith("minishlab/"):
            from model2vec import StaticModel

            m = StaticModel.from_pretrained(name)
            self.enc = lambda xs: np.asarray(m.encode(xs))
        else:
            from fastembed import TextEmbedding

            m = TextEmbedding(name, max_length=512)
            self.enc = lambda xs: np.asarray(list(m.embed(xs, batch_size=int(os.environ.get("BATCH", "32")))))
        self.load_s = time.time() - t

    def vecs(self, texts: list[str]) -> np.ndarray:
        todo = [t for t in dict.fromkeys(texts) if t not in self.cache]
        if todo:
            for t, v in zip(todo, self.enc(todo), strict=True):
                self.cache[t] = v / (np.linalg.norm(v) or 1.0)
        return np.stack([self.cache[t] for t in texts])


class EmbIndex:
    def __init__(self, emb: Embedder, units: list[Unit]) -> None:
        self.emb, self.keys = emb, [u.key for u in units]
        self.m = emb.vecs([u.full for u in units])

    def query(self, q: str) -> list[str]:
        v = self.emb.enc([q])[0]
        s = self.m @ (v / (np.linalg.norm(v) or 1.0))
        return [self.keys[i] for i in np.argsort(-s)]


def rrf(*lists: list[str], k: int = 60) -> list[str]:
    score: dict[str, float] = defaultdict(float)
    for lst in lists:
        for r, key in enumerate(lst):
            score[key] += 1 / (k + r + 1)
    return sorted(score, key=lambda x: -score[x])


# ---------- aider-style repo map ----------
class RepoMap:
    def __init__(self, root: Path, units: list[Unit]) -> None:
        import ast

        self.defines: dict[str, set[str]] = defaultdict(set)
        self.refs: dict[str, list[str]] = defaultdict(list)
        self.files = sorted({u.path for u in units})
        self.units = units
        for f in self.files:
            try:
                tree = ast.parse((root / f).read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    self.defines[n.name].add(f)
                elif isinstance(n, ast.Name):
                    self.refs[n.id].append(f)
                elif isinstance(n, ast.Attribute):
                    self.refs[n.attr].append(f)

    def query(self, q: str) -> list[str]:
        mentioned = set(re.split(r"\W+", q)) - {""}
        # aider: personalise files whose path components (or stem) match a mentioned identifier
        pers = {}
        for f in self.files:
            parts = set(Path(f).parts) | {Path(f).name, Path(f).stem}
            if parts & mentioned:
                pers[f] = 100 / len(self.files)
        edges: dict[tuple[str, str], float] = defaultdict(float)
        eid: list[tuple[str, str, str, float]] = []
        for ident, definers in self.defines.items():
            if ident not in self.refs:
                for d in definers:
                    eid.append((d, d, ident, 0.1))
                continue
            mul = 1.0
            snake = "_" in ident and any(c.isalpha() for c in ident)
            camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)
            if ident in mentioned:
                mul *= 10
            if (snake or camel) and len(ident) >= 8:
                mul *= 10
            if ident.startswith("_"):
                mul *= 0.1
            if len(definers) > 5:
                mul *= 0.1
            for referencer, n in Counter(self.refs[ident]).items():
                for d in definers:
                    eid.append((referencer, d, ident, mul * math.sqrt(n)))
        for s, d, _, w in eid:
            edges[(s, d)] += w
        nodes = sorted({s for s, _ in edges} | {d for _, d in edges})
        rank = pagerank(nodes, edges, pers)
        out_w: dict[str, float] = defaultdict(float)
        for s, _, _, w in eid:
            out_w[s] += w
        rdef: dict[tuple[str, str], float] = defaultdict(float)
        for s, d, ident, w in eid:
            rdef[(d, ident)] += rank[s] * w / out_w[s]
        by = defaultdict(list)
        for u in self.units:
            by[(u.path, u.key.split("::")[1].split(".")[-1])].append(u.key)
            if "." in u.key.split("::")[1]:  # a class name ranks its methods too
                by[(u.path, u.key.split("::")[1].split(".")[0])].append(u.key)
        ranked = []
        for key, _ in sorted(rdef.items(), key=lambda x: (-x[1], x[0])):
            for k in by.get(key, []):
                if k not in ranked:
                    ranked.append(k)
        return ranked


def pagerank(nodes, edges, pers, alpha=0.85, iters=100, tol=1e-10):
    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    out = np.zeros(n)
    for (s, _), w in edges.items():
        out[idx[s]] += w
    p = np.array([pers.get(x, 0.0) for x in nodes]) if pers else np.ones(n)
    p = p / p.sum()
    r = np.ones(n) / n
    src = np.array([idx[s] for s, _ in edges])
    dst = np.array([idx[d] for _, d in edges])
    w = np.array(list(edges.values()))
    for _ in range(iters):
        nr = np.zeros(n)
        np.add.at(nr, dst, alpha * r[src] * w / out[src])
        dangling = alpha * r[out == 0].sum()
        nr += dangling * p + (1 - alpha) * p
        if np.abs(nr - r).sum() < tol:
            r = nr
            break
        r = nr
    return dict(zip(nodes, r, strict=True))


MODELS = {
    "m2v-8M": "minishlab/potion-base-8M",
    "m2v-32M": "minishlab/potion-retrieval-32M",
    "bge-small": "BAAI/bge-small-en-v1.5",
    "jina-code": "jinaai/jina-embeddings-v2-base-code",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("out")
    ap.add_argument("--arms", default="grep,bm25-head,bm25-full,m2v-8M,m2v-32M,bge-small,jina-code,repomap")
    a = ap.parse_args()
    arms = a.arms.split(",")
    qs = json.loads(Path(a.questions).read_text())
    embs = {m: Embedder(MODELS[m]) for m in arms if m in MODELS}
    rows = []
    for q in qs:
        root = extract(q["tree"], TREES)
        units = units_of(root)
        keys = {u.key for u in units}
        gold = [g for g in q["gold"] if g in keys]
        if len(gold) != len(q["gold"]):
            print("gold missing", q["id"], set(q["gold"]) - keys, file=sys.stderr)
        res = {}
        for arm in arms:
            t0 = time.perf_counter()
            if arm in ("grep", "grep-all"):
                ranked, out = grep_arm(root, units, q["query"], 3 if arm == "grep" else 99)
                build = 0.0
            else:
                if arm.startswith("bm25"):
                    idx = BM25Index(units, arm.split("-")[1])
                elif arm in embs:
                    idx = EmbIndex(embs[arm], units)
                elif arm == "repomap":
                    idx = RepoMap(root, units)
                build = time.perf_counter() - t0
                t0 = time.perf_counter()
                ranked = idx.query(q["query"])
                out = card_block(units, ranked)
            ms = (time.perf_counter() - t0) * 1000
            src = [r for r in ranked if not is_test(r)]
            res[arm] = {
                "ranked": ranked[:50],
                "ranked_src": src[:50],
                "tokens": tokens(out),
                "tokens_src": tokens(card_block(units, src)) if not arm.startswith("grep") else tokens(out),
                "ms": ms,
                "build_s": build,
            }
        rows.append({**q, "gold": gold, "results": res, "n_units": len(units)})
        print(q["id"], {k: next((i + 1 for i, r in enumerate(v["ranked"]) if r in gold), None) for k, v in res.items()})
    Path(a.out).write_text(json.dumps(rows, indent=0))


if __name__ == "__main__":
    main()
