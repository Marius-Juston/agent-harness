#!/usr/bin/env bash
# Install footprint of each candidate on its own (#495): a bare uv project per package in OUT_DIR, then the number
# of locked packages (the candidate and its dependencies) and the .venv size; then the size of each model in the
# Hugging Face cache. usage: footprint.sh OUT_DIR
set -u
out=$1
probe() {
  local d="$out/proj_$1"
  rm -rf "$d" && mkdir -p "$d" && cd "$d" || return
  uv init -q --bare --python 3.13 --name probe
  uv add -q "$1" > /dev/null 2>&1
  local n s
  n=$(grep -c '^name = ' uv.lock)
  s=$(du -sm .venv | cut -f1)
  echo "$1 packages=$((n - 1)) venv_MB=$s"
}
probe model2vec
probe fastembed
probe ty
probe ast-grep-cli
# model files (the hub cache holds symlinks into blobs, so follow them)
du -shL ~/.cache/huggingface/hub/models--minishlab--potion-*/snapshots/*/
du -shL "${TMPDIR:-/tmp}"/fastembed_cache/models--*/snapshots/*/
