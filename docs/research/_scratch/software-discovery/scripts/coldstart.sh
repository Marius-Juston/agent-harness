#!/usr/bin/env bash
# Cold-start cost a persistent MCP server would save per `dev reuse` call: model2vec import+load, and dev CLI startup.
# Usage: run from a checkout whose pyproject has the `semantic` extra (the 29-dev-reuse worktree).
for i in 1 2 3; do uv run --extra semantic python -c "
import time;t=time.time()
from model2vec import StaticModel
StaticModel.from_pretrained('minishlab/potion-base-8M'); print(f'model2vec import+load {time.time()-t:.2f}s')" 2>/dev/null; done
for i in 1 2 3; do /usr/bin/time -f "%e s uv run dev --help" uv run dev --help >/dev/null; done
