#!/usr/bin/env bash
# Two resource gates before a subagent works, on two events (Claude Code 2.1.296: a workflow fires PreToolUse for
# `Workflow`, never for `Agent`, and SubagentStart with agent_type `workflow-subagent`).
# 1. PreToolUse(Agent|Workflow), quota: refuse when the weekly all-models usage recorded by `uv run dev usage-poll` is
#    at or above its cap (`[tool.devkit] quota_cap`, written into the same reading). Fails open without a fresh
#    reading. On a workflow this refuses the whole run.
# 2. SubagentStart, memory: hold a heavy agent (builder, tester, plugin-scoped or not, and any workflow agent, whose
#    definition is unknown here) until MemAvailable is above DEVKIT_SPAWN_FLOOR_GB (default 6), up to
#    DEVKIT_SPAWN_WAIT_S (default 300) s. SubagentStart cannot refuse, so a timeout starts the agent with a warning.
# Any failure of this script (malformed input, a jq error, no jq) allows with a warning: it never blocks a spawn.
set -eEuo pipefail
warn() { printf '{"systemMessage": "spawn_guard: %s"}\n' "$1"; exit 0; }
trap 'warn "failed at line $LINENO; allowed without the quota and memory gates"' ERR

input=$(cat)
jq -e 'type == "object"' <<<"$input" >/dev/null 2>&1 || warn "unreadable hook input; allowed without the gates"
event=$(jq -r '.hook_event_name // "PreToolUse"' <<<"$input")
root="${CLAUDE_PROJECT_DIR:-$PWD}"

if [[ "$event" == "PreToolUse" ]]; then
  common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
  usage="$common/devkit/usage.json"
  [[ -f "$usage" ]] || exit 0
  read -r pct t cap < <(jq -r '"\(.pct) \(.t) \(.cap // 95)"' "$usage" 2>/dev/null) \
    || warn "unreadable usage reading; allowed without the quota gate"
  if [[ "$pct" =~ ^[0-9]+$ && "$t" =~ ^[0-9]+$ && "$cap" =~ ^[0-9]+$ ]] && (( $(date +%s) - t < 1800 && pct >= cap )); then
    jq -n --arg r "Quota gate: weekly usage ${pct}% >= cap ${cap}% ([tool.devkit] quota_cap). Stop spawning; commit and hand off." \
      '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  fi
  exit 0
fi

[[ "$event" == "SubagentStart" ]] || exit 0
kind=$(jq -r '.agent_type // "" | tostring' <<<"$input")
kind=${kind##*:}  # harness-ops:builder -> builder
case "$kind" in builder|tester|workflow-subagent) ;; *) exit 0 ;; esac
floor=${DEVKIT_SPAWN_FLOOR_GB:-6}; wait_s=${DEVKIT_SPAWN_WAIT_S:-300}
[[ "$floor" =~ ^[0-9]+$ && "$wait_s" =~ ^[0-9]+$ ]] || { floor=6; wait_s=300; }
free_gb() { awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo 2>/dev/null || echo 99; }
deadline=$(( $(date +%s) + wait_s ))
while (( $(free_gb) < floor )); do
  (( $(date +%s) >= deadline )) && warn "held ${kind} ${wait_s} s; only $(free_gb) GB free (floor ${floor} GB). Started anyway: spawn no more until a gate finishes (uv run dev status)."
  sleep 1
done
exit 0
