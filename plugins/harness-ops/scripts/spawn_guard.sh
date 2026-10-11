#!/usr/bin/env bash
# PreToolUse(Agent): two resource gates before a subagent starts.
# 1. Quota: refuse when the weekly all-models usage recorded by `uv run dev usage-poll` is at or above its cap
#    (`[tool.devkit] quota_cap`, written into the same reading). Fails open (allows) without a fresh reading.
# 2. Memory: hold a heavy spawn (builder, tester; plugin-scoped or not) until MemAvailable is above
#    DEVKIT_SPAWN_FLOOR_GB (default 6), up to DEVKIT_SPAWN_WAIT_S (default 300) s, instead of refusing: a refusal
#    throws away the written spawn call. Researchers, reviewers and Explore run no tests and pass at once.
set -euo pipefail
input=$(cat)
kind=$(jq -r '.tool_input.subagent_type // ""' <<<"$input" 2>/dev/null) || exit 0
kind=${kind##*:}  # harness-ops:builder -> builder
root="${CLAUDE_PROJECT_DIR:-$PWD}"
common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
state="$common/devkit"
deny() { jq -n --arg r "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'; exit 0; }

if [[ -f "$state/usage.json" ]]; then
  pct=$(jq -r '.pct' "$state/usage.json"); t=$(jq -r '.t' "$state/usage.json"); cap=$(jq -r '.cap // 95' "$state/usage.json")
  if [[ "$pct" =~ ^[0-9]+$ && "$t" =~ ^[0-9]+$ && "$cap" =~ ^[0-9]+$ ]] && (( $(date +%s) - t < 1800 )) && (( pct >= cap )); then
    deny "Quota gate: weekly usage ${pct}% >= cap ${cap}% ([tool.devkit] quota_cap). Stop spawning; commit and hand off."
  fi
fi

case "$kind" in builder|tester) ;; *) exit 0 ;; esac
floor=${DEVKIT_SPAWN_FLOOR_GB:-6}; wait_s=${DEVKIT_SPAWN_WAIT_S:-300}
[[ "$floor" =~ ^[0-9]+$ && "$wait_s" =~ ^[0-9]+$ ]] || { floor=6; wait_s=300; }
free_gb() { awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo 2>/dev/null || echo 99; }
deadline=$(( $(date +%s) + wait_s ))
while (( $(free_gb) < floor )); do
  (( $(date +%s) >= deadline )) && deny "Waited ${wait_s}s; only $(free_gb) GB free (floor ${floor} GB). Spawn again after a gate finishes (uv run dev status)."
  sleep 10
done
exit 0
