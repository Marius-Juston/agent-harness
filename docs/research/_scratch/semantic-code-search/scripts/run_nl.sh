#!/usr/bin/env bash
# Run eval_nl.py in four batches (the embedding models together exceeded this machine's memory once): lexical,
# repo map and model2vec; then bge-small; then jina-code (batches of 4); then grep-all. usage: run_nl.sh OUT_DIR  (TREES may be set; see eval_nl.py)
set -u
out=$1
here=$(cd "$(dirname "$0")" && pwd)
export HF_HUB_DISABLE_PROGRESS_BARS=1
run() {
  uv run --no-project --with numpy --with pyyaml --with model2vec --with fastembed \
    python "$here/eval_nl.py" "$here/../questions_hist.json" "$out/res_$1.json" --arms "$2" > "$out/run_$1.log" 2>&1
}
[ -e "$out/res_a.json" ] || run a grep,bm25-head,bm25-full,repomap,m2v-8M,m2v-32M
[ -e "$out/res_b.json" ] || run b bge-small
BATCH=4 run c jina-code
run d grep-all
echo done
