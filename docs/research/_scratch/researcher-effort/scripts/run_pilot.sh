#!/usr/bin/env bash
# Stage 0 of #262 (PROTOCOL.md §6): Q06, one trial per arm (A, B, C), $3.80 cap per run, $13 ledger cap.
# Run from the repository root of a clean checkout of the pre-registration commit or later, by a human:
#   ANTHROPIC_API_KEY=... bash docs/research/_scratch/researcher-effort/scripts/run_pilot.sh [arm ...]
# The key is read from the environment only and never printed or written. Transcripts go to $OUT (default: a new
# temp dir, printed); the answer files and summaries go to docs/research/_scratch/researcher-effort/pilot/.
set -euo pipefail
set +x
[ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "set ANTHROPIC_API_KEY for this command only" >&2; exit 2; }

HERE=docs/research/_scratch/researcher-effort
Q=Q06
CAP=3.80
LEDGER_CAP=13.00
OUT=${OUT:-$(mktemp -d -t researcher-effort-XXXX)}
RES=$HERE/pilot
mkdir -p "$RES"
LEDGER=$RES/ledger.tsv
[ -f "$LEDGER" ] || printf 'arm\tquestion\tcost_usd\twhen\n' >"$LEDGER"
echo "transcripts: $OUT"

ARMS=("$@")
[ ${#ARMS[@]} -gt 0 ] || ARMS=(A B C)  # Q06: rotation 6 mod 3 = 0, so A, B, C
GOLD=$(uv run python -I -c "import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))['questions']['$Q']['gold'])" "$HERE/questions.yaml")
mapfile -t HIDE < <(uv run python -I -c "import yaml,sys; q=yaml.safe_load(open(sys.argv[1])); print('\n'.join(q['questions']['$Q']['hide']))" "$HERE/questions.yaml")
TOOLS=$(uv run python -I -c "from devkit.evals import DEFAULT_TOOLS; print(','.join(DEFAULT_TOOLS + ['WebSearch','WebFetch','mcp__arxiv__get_abstract','mcp__arxiv__get_paper_outline','mcp__arxiv__read_paper_section','mcp__arxiv__search_papers']))")

for ARM in "${ARMS[@]}"; do
  spent=$(awk -F'\t' 'NR>1 {s+=$3} END {printf "%.2f", s+0}' "$LEDGER")
  if awk -v s="$spent" -v c="$CAP" -v l="$LEDGER_CAP" 'BEGIN {exit !(s + c + 0.5 > l)}'; then
    echo "stop: ledger \$$spent + cap \$$CAP + 0.50 overshoot would pass \$$LEDGER_CAP" >&2; exit 3
  fi
  W=$OUT/$Q-$ARM
  git clone -q --local . "$W/repo"
  git -C "$W/repo" remote remove origin
  uv run python -I $HERE/scripts/prepare_trial.py $HERE/questions.yaml "$W/repo" $Q >"$W/prompt.txt"
  git -C "$W/repo" add -A
  git -C "$W/repo" commit -q -m "hide gold for $Q"
  case $ARM in
    A) EFFORT=high; AGENT=(--agent harness-ops:researcher); ALLOW=$TOOLS ;;
    B) EFFORT=medium; AGENT=(--agent harness-ops:researcher); ALLOW=$TOOLS ;;
    C) EFFORT=medium
       uv run python -I $HERE/scripts/arm_c_agents.py plugins/harness-ops/agents/researcher.md "$W/agents.json" >&2
       AGENT=(--agents "$W/agents.json" --agent researcher-helper); ALLOW="$TOOLS,Agent" ;;
    *) echo "unknown arm $ARM" >&2; exit 2 ;;
  esac
  echo "arm $ARM: effort $EFFORT, cap \$$CAP, ledger \$$spent" >&2
  (cd "$W/repo" && env -u ANTHROPIC_MODEL -u CLAUDE_CODE_SUBAGENT_MODEL CLAUDE_CODE_EFFORT_LEVEL=$EFFORT \
    timeout 2700 claude -p "$(cat "$W/prompt.txt")" --output-format stream-json --verbose --no-session-persistence \
      --max-turns 150 --permission-mode acceptEdits --plugin-dir "$W/repo/plugins/harness-ops" "${AGENT[@]}" \
      --allowedTools "$ALLOW" --model opus --effort $EFFORT --max-budget-usd $CAP >"$W/transcript.jsonl" 2>"$W/stderr.txt") || true
  ANS=$W/repo/docs/research/_scratch/eval-q/r1-answer.md
  mkdir -p "$RES/$ARM"
  [ -f "$ANS" ] && cp "$ANS" "$RES/$ARM/r1-answer.md"
  uv run python -I $HERE/scripts/summarize.py "$W/transcript.jsonl" "${ANS}" "$GOLD" "${HIDE[@]}" >"$RES/$ARM/summary.json"
  [ -f "$ANS" ] && (uv run dev cite-check "$RES/$ARM/r1-answer.md" >"$RES/$ARM/cite-check.txt" 2>&1 || true)
  cost=$(uv run python -I -c "import json,sys; print(json.load(open(sys.argv[1]))['cost_usd'])" "$RES/$ARM/summary.json")
  printf '%s\t%s\t%s\t%s\n' "$ARM" "$Q" "$cost" "$(date -u +%FT%TZ)" >>"$LEDGER"
  echo "arm $ARM: \$$cost" >&2
done
awk -F'\t' 'NR>1 {s+=$3} END {printf "pilot total: $%.2f of $%s\n", s, cap}' cap=$LEDGER_CAP "$LEDGER"
