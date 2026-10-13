"""Brute-force search latency over a PyPI-sized matrix and build-time arithmetic (#169 r2-pipeline)."""
import time
import numpy as np

N = 817_027  # distinct projects in ClickHouse pypi.projects, 2026-10-13
rng = np.random.default_rng(0)
for dim, dtype in ((1024, np.float32), (1024, np.float16), (256, np.float32)):
    M = rng.standard_normal((N, dim), dtype=np.float32).astype(dtype)
    q = rng.standard_normal(dim, dtype=np.float32).astype(dtype)
    M @ q
    t = time.perf_counter()
    for _ in range(10):
        s = M @ q
        np.argpartition(s, -100)[-100:]
    print(f"brute force N={N} dim={dim} {np.dtype(dtype).name}: {(time.perf_counter()-t)/10*1000:.1f} ms/query, matrix {M.nbytes/1e9:.2f} GB")
    del M

# build time from the measured RTX 4090 rates (bench_embed.py): 64,357 tok/s at 512, 47,905 tok/s at 2048
for seq, tok_per_doc, rate in ((512, 432, 64357), (2048, 1223, 47905)):
    tokens = N * tok_per_doc
    print(f"seq {seq}: {tokens/1e6:.0f}M tokens, one 4090 {tokens/rate/3600:.1f} h, 4 GPUs at 4090 speed {tokens/rate/3600/4:.2f} h, "
          f"daily 4,300 changed projects {4300*tok_per_doc/rate:.0f} s")
mteb_code = {"Qwen3-Embedding-0.6B": [75.3, 84.7, 64.4, 90.8, 86.4, 91.7, 91.0, 86.0, 31.4, 36.5, 90.0, 76.7],
             "embeddinggemma-300m": [84.4, 75.5, 62.1, 51.4, 80.3, 73.7, 90.1, 85.5, 33.5, 43.6, 86.5, 58.4],
             "pplx-embed-v1-0.6b": [78.7, 82.4, 76.4, 83.0, 84.7, 88.1, 89.9, 85.0, 35.2, 43.3, 90.7, 73.0]}
for m, v in mteb_code.items():
    print(f"MTEB(Code) 12-task mean {m}: {sum(v)/len(v):.2f} (n={len(v)})")
