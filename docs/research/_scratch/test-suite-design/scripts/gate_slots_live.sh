#!/usr/bin/env bash
# #461: four real `uv run dev check` runs in four worktrees of one rendered project with `gate_slots = 3`, each gate
# held about 25 s by a sleeping test. Expect three to run at once, the fourth to say it waits and to start when one ends,
# and `dev status` to list three holders.
#   bash gate_slots_live.sh <new dir>   (run from an agent-harness checkout with the change committed)
set -euo pipefail
r=${1:?usage: gate_slots_live.sh <new dir>}
scripts/render_check.sh "$r" &>/dev/null
r=$(cd "$r" && pwd)
cd "$r"
sed -i 's/^quota_cap = 95$/quota_cap = 95\ngate_slots = 3/' pyproject.toml
printf 'import time\n\n\ndef test_takes_a_while():\n    time.sleep(25)\n' > tests/test_slow_gate.py
git add -A
git -c user.name=r -c user.email=r@example.invalid commit -q -m "gate_slots = 3 and a slow test"
for i in 2 3 4; do
  git worktree add -q "$r-wt$i" -b "wt$i"
  (cd "$r-wt$i" && uv sync -q)
done
start=$(date +%s.%N)
for d in "$r" "$r-wt2" "$r-wt3" "$r-wt4"; do
  (cd "$d" && uv run dev check --no-cache 2>&1 | grep --line-buffered -E "RESULT|waiting" | while IFS= read -r l; do
     printf '%6.1f %s %s\n' "$(echo "$(date +%s.%N) - $start" | bc)" "$(basename "$d")" "$l"; done) &
  sleep 0.5
done
sleep 8
echo "--- dev status at 8 s:"
uv run dev status | head -1
wait
