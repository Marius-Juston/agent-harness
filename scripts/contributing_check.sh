#!/usr/bin/env bash
# Follow README -> CONTRIBUTING from a fresh clone, as a new contributor would, and require a green gate. The steps
# run are the ones CONTRIBUTING names, and the script fails first if the documents stop naming them.
#   scripts/contributing_check.sh <dest> [source; default: this checkout] [ref; default: HEAD]
set -euo pipefail
dest=${1:?usage: contributing_check.sh <dest> [source] [ref]}
here=$(cd "$(dirname "$0")/.." && pwd)
src=${2:-$here}
ref=${3:-HEAD}
[[ -e "$dest" ]] && { echo "contributing_check: $dest exists" >&2; exit 1; }
sha=$(git -C "$src" rev-parse "$ref")

git clone -q "$src" "$dest"
cd "$dest"
git checkout -q "$sha"
grep -q '](CONTRIBUTING.md)' README.md || { echo "contributing_check: README does not link CONTRIBUTING.md" >&2; exit 1; }
for step in '`uv sync`' '`uv run dev check`' '](docs/SETUP.md)' '`uv run dev --help`'; do
  grep -qF "$step" CONTRIBUTING.md || { echo "contributing_check: CONTRIBUTING.md no longer names $step" >&2; exit 1; }
done
unset VIRTUAL_ENV
uv sync
uv run dev check --no-cache
