"""Index arms P1/P2 (PROTOCOL.md amendment 4; r2-pipeline.md "Candidate PROTOCOL arms"): BM25 (bm25s, k1=1.5, b=0.75,
name repeated twice) and one dense embedder over the p_corpus.py corpus, fused with RRF k=60; top 100 per need.

Usage:
  uv run --no-project --with bm25s --with model2vec --with numpy python p_index.py build <corpus.jsonl> <dir> --model potion
  uv run --no-project --with bm25s --with sentence-transformers --with numpy python p_index.py build <corpus.jsonl> <dir> --model qwen
  ... p_index.py query <dir> --model potion|qwen --split tune --out <top100.json>

Models: potion = minishlab/potion-retrieval-32M (P1, CPU); qwen = Qwen/Qwen3-Embedding-0.6B (P2, GPU, 512 tokens,
query instruction fixed in r2-pipeline.md). A need's queries are its label and its two paraphrases: BM25 gets them
joined into one query; the dense side gets the mean of the three query vectors (as `dev reuse` computes R).
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RRF_K, TOP, DEPTH = 60, 100, 1000
MODELS = {"potion": "minishlab/potion-retrieval-32M", "qwen": "Qwen/Qwen3-Embedding-0.6B"}
INSTRUCT = ("Instruct: Given a description of a programming need or a code snippet, retrieve Python packages that "
            "provide it\nQuery: ")


def arg(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def encoder(kind: str):
    if kind == "potion":
        from model2vec import StaticModel
        m = StaticModel.from_pretrained(MODELS[kind])
        return lambda texts, query=False: m.encode(texts, batch_size=4096)
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(MODELS[kind], device="cuda", model_kwargs={"torch_dtype": "bfloat16"})
    m.max_seq_length = 512

    def enc(texts, query=False):
        if query:
            return m.encode([INSTRUCT + t for t in texts], normalize_embeddings=True)
        order = np.argsort([len(t) for t in texts])[::-1]  # length-sorted batches: far less padding
        out = m.encode([texts[i] for i in order], batch_size=128, normalize_embeddings=True, show_progress_bar=True,
                       convert_to_numpy=True)
        res = np.empty_like(out)
        res[order] = out
        return res
    return enc


def build(corpus: Path, out: Path, kind: str) -> None:
    import bm25s
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in corpus.open()]
    (out / "names.json").write_text(json.dumps([[r["name"], r["repo"]] for r in rows]))
    if not (out / "bm25").exists():
        t0 = time.time()
        toks = bm25s.tokenize([f"{r['name']} {r['name'].replace('-', ' ').replace('_', ' ')} {r['text']}" for r in rows],
                              stopwords="en")
        bm = bm25s.BM25(k1=1.5, b=0.75)
        bm.index(toks)
        bm.save(str(out / "bm25"))
        print(f"BM25 over {len(rows)} docs: {time.time() - t0:.0f}s", flush=True)
    t0 = time.time()
    v = np.asarray(encoder(kind)([r["text"] for r in rows]), dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    np.save(out / f"dense-{kind}.npy", v)
    print(f"{MODELS[kind]}: {len(rows)} docs in {time.time() - t0:.0f}s, dim {v.shape[1]}", flush=True)


def needs(split_name: str) -> list[dict]:
    split = json.loads((HERE / "data" / "split.json").read_text())
    if split_name != "tune":
        sys.exit("round 3 queries TUNE only")
    inp = {n["id"]: n for n in json.loads((HERE / "data" / "b0_tune_input.json").read_text())}
    return [{"id": i, "queries": [inp[i]["need"], *inp[i]["also_described_as"]]} for i in split["tune"]]


def query(idx: Path, kind: str, out: Path) -> None:
    import bm25s
    names = json.loads((idx / "names.json").read_text())
    bm = bm25s.BM25.load(str(idx / "bm25"))
    v = np.load(idx / f"dense-{kind}.npy", mmap_mode="r")
    enc = encoder(kind)
    res = {}
    for n in needs(arg("--split", "tune")):
        qtok = bm25s.tokenize([" ".join(n["queries"])], stopwords="en", show_progress=False)
        docs, _ = bm.retrieve(qtok, k=DEPTH, show_progress=False)
        qv = np.asarray(enc(n["queries"], query=True), dtype=np.float32).mean(axis=0)
        qv /= np.linalg.norm(qv) + 1e-9
        sims = np.asarray(v @ qv)
        dense = np.argpartition(-sims, DEPTH)[:DEPTH]
        dense = dense[np.argsort(-sims[dense])]
        fused: dict[int, float] = {}
        for lst in (docs[0].tolist(), dense.tolist()):
            for r, j in enumerate(lst):
                fused[j] = fused.get(j, 0.0) + 1.0 / (RRF_K + r + 1)
        top = sorted(fused, key=lambda j: -fused[j])[:TOP]
        res[n["id"]] = [{"name": names[j][0], "repo": names[j][1], "rrf": round(fused[j], 5)} for j in top]
    out.write_text(json.dumps(res))
    print(f"top {TOP} for {len(res)} needs -> {out}")


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(Path(sys.argv[2]), Path(sys.argv[3]), arg("--model", "potion"))
    else:
        query(Path(sys.argv[2]), arg("--model", "potion"), Path(arg("--out")))
