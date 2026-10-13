"""Throughput and latency of candidate embedders on real PyPI READMEs (agent-harness #169, r2-pipeline).

Usage: uv run python bench_embed.py <sample.jsonl>
GPU: Qwen3-Embedding-0.6B doc throughput (bf16) at max_seq_length 512 and 2048, text = name + summary + README.
CPU: single-query latency (batch 1) for Qwen3-Embedding-0.6B, bge-small-en-v1.5, potion-base-8M at 32 and 4 threads,
and a 0.6B cross-encoder proxy (50 docs x 512 tokens through the same Qwen3 backbone).
"""

import json
import statistics
import sys
import time

import torch
from model2vec import StaticModel
from sentence_transformers import SentenceTransformer

with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
    rows = [json.loads(line) for line in fh]
docs = [f"{r['name']}: {r['s']}\n{r['d']}" for r in rows]
queries = [
    "retry a function call with exponential backoff",
    "parse yaml config files",
    "fast fuzzy string matching",
    "draw graphs of networks",
    "read and write parquet files",
    "validate json against a schema",
    "async http client with connection pooling",
    "progress bar for loops",
] * 4


def gpu_throughput(seq: int) -> None:
    m = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device="cuda", model_kwargs={"torch_dtype": torch.bfloat16})
    m.max_seq_length = seq
    toks = sum(min(len(m.tokenizer(d, add_special_tokens=True)["input_ids"]), seq) for d in docs)
    m.encode(docs[:64], batch_size=32)
    torch.cuda.synchronize()
    t = time.perf_counter()
    m.encode(docs, batch_size=32 if seq <= 512 else 8)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t
    print(
        f"GPU {torch.cuda.get_device_name()} seq={seq}: {len(docs)} docs, {toks} tokens, {dt:.1f}s, "
        f"{len(docs) / dt:.0f} docs/s, {toks / dt:.0f} tok/s, mean {toks / len(docs):.0f} tok/doc"
    )
    del m
    torch.cuda.empty_cache()


def cpu_latency(name: str, threads: int) -> None:
    torch.set_num_threads(threads)
    if name.startswith("minishlab"):
        m = StaticModel.from_pretrained(name)
        enc = lambda q: m.encode([q])  # noqa: E731
    else:
        st = SentenceTransformer(name, device="cpu")
        enc = lambda q: st.encode([q], prompt_name="query" if "Qwen3" in name else None)  # noqa: E731
    enc(queries[0])
    lat = []
    for q in queries:
        t = time.perf_counter()
        enc(q)
        lat.append((time.perf_counter() - t) * 1000)
    print(
        f"CPU {threads} threads {name}: median {statistics.median(lat):.1f} ms, p90 {sorted(lat)[int(0.9 * len(lat))]:.1f} ms"
    )


def cpu_rerank_proxy(threads: int) -> None:
    torch.set_num_threads(threads)
    st = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device="cpu")
    st.max_seq_length = 512
    batch = docs[:50]
    t = time.perf_counter()
    st.encode(batch, batch_size=50)
    print(
        f"CPU {threads} threads 0.6B forward over 50 docs x <=512 tok (cross-encoder proxy): {time.perf_counter() - t:.1f}s"
    )


if __name__ == "__main__":
    gpu_throughput(512)
    gpu_throughput(2048)
    for th in (32, 4):
        for n in ("Qwen/Qwen3-Embedding-0.6B", "BAAI/bge-small-en-v1.5", "minishlab/potion-base-8M"):
            cpu_latency(n, th)
        cpu_rerank_proxy(th)
