#!/usr/bin/env bash
# Render the Copier template into a new directory, then gate the result the way a new project would be:
# git init, uv sync, generated references, commit, `uv run dev check`. CI runs this on every PR, so a template, devkit
# or plugin change that breaks a fresh project fails here.
#   scripts/render_check.sh <dest> [harness path; default: this checkout] [vcs ref; default: HEAD]
set -euo pipefail
dest=${1:?usage: render_check.sh <dest> [harness-path] [vcs-ref]}
here=$(cd "$(dirname "$0")/.." && pwd)
harness=${2:-$here}
ref=${3:-HEAD}
[[ -e "$dest" ]] && { echo "render_check: $dest exists" >&2; exit 1; }

uvx --from "copier==9.18.2" copier copy --defaults --vcs-ref "$ref" \
  --data project_name=rendered-project --data harness_path="$harness" "$here" "$dest"
cd "$dest"
for f in CONTRIBUTING.md SECURITY.md .editorconfig CHANGELOG.md; do
  [[ -s "$f" ]] || { echo "render_check: the rendered project lacks $f" >&2; exit 1; }
done
git init -q -b main
git -c user.name=render -c user.email=render@example.invalid commit -q --allow-empty -m "empty root"
uv sync
uv run --quiet dev docs
git add -A
git -c user.name=render -c user.email=render@example.invalid commit -q -m "rendered from agent-harness"
uv run dev check --no-cache
