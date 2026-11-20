#!/usr/bin/env bash
# Time the --local-ci stand-in commands of this repository's [tool.devkit.local_ci] one by one (#520).
# Usage: time_standins.sh <empty scratch dir>; run from the repository root.
set -u
out="$1"
s=$(date +%s.%N)
scripts/render_check.sh "$out/rendered-project" > "$out/render.log" 2>&1
echo "render_check exit $? wall $(echo "$(date +%s.%N) - $s" | bc) s; rendered gate: $(grep 'RESULT' "$out/render.log" | tail -1)"
for target in . ./plugins/harness-ops; do
  s=$(date +%s.%N)
  npx --yes @anthropic-ai/claude-code@2.1.296 plugin validate --strict "$target" > "$out/npx.log" 2>&1
  echo "plugin validate $target exit $? wall $(echo "$(date +%s.%N) - $s" | bc) s"
done
