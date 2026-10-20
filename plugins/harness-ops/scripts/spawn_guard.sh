#!/usr/bin/env bash
# Two resource gates before a subagent works, on two events (Claude Code 2.1.296: a workflow fires PreToolUse for
# `Workflow`, never for `Agent`, and SubagentStart with agent_type `workflow-subagent`).
# 1. PreToolUse(Agent|Workflow), quota: refuse when the weekly all-models usage recorded by `uv run dev usage-poll` is
#    at or above its cap (the owner's `dev quota-cap` override, else `[tool.devkit] quota_cap` as written into the
#    same reading). Fails open without a fresh
#    reading. On a workflow this refuses the whole run.
# 2. SubagentStart, memory: hold a heavy agent (builder, tester, plugin-scoped or not, and any workflow agent, whose
#    definition is unknown here) until MemAvailable is above DEVKIT_SPAWN_FLOOR_GB (default 6), up to
#    DEVKIT_SPAWN_WAIT_S (default 300) s. SubagentStart cannot refuse, so a timeout starts the agent with a warning.
# Any failure of this script (malformed input, a jq error, no jq) allows with a warning: it never blocks a spawn.
# 3. SubagentStart, every agent: name a loaded plugin that is not the pinned release's build (plugin_skew.sh, #296).
set -eEuo pipefail
skew=""
warn() {
  local m="spawn_guard: $1"
  [[ -z "$skew" ]] || m+=$'\n'"$skew"
  jq -n --arg m "$m" '{systemMessage: $m}' 2>/dev/null || printf '{"systemMessage": "spawn_guard: %s"}\n' "$1"
  exit 0
}
say_skew() { [[ -z "$skew" ]] || jq -n --arg m "$skew" '{systemMessage: $m}'; exit 0; }
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
  source="[tool.devkit] quota_cap"
  # the owner's machine-local override (`uv run dev quota-cap`) wins when valid: a poller may still record the old cap
  local_cap=$(tr -d '[:space:]' <"$common/devkit/quota_cap" 2>/dev/null) || local_cap=""
  if [[ "$local_cap" =~ ^[0-9]{1,3}$ ]] && (( 10#$local_cap <= 100 )); then
    cap=$(( 10#$local_cap )); source="local: uv run dev quota-cap"
  fi
  if [[ "$pct" =~ ^[0-9]+$ && "$t" =~ ^[0-9]+$ && "$cap" =~ ^[0-9]+$ ]] && (( $(date +%s) - t < 1800 && pct >= 10#$cap )); then
    jq -n --arg r "Quota gate: weekly usage ${pct}% >= cap ${cap}% (${source}). Stop spawning; commit and hand off." \
      '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  fi
  exit 0
fi

[[ "$event" == "SubagentStart" ]] || exit 0
skew=$(bash "$(dirname "${BASH_SOURCE[0]}")/plugin_skew.sh")
kind=$(jq -r '.agent_type // "" | tostring' <<<"$input")
kind=${kind##*:}  # harness-ops:builder -> builder
case "$kind" in builder|tester|workflow-subagent) ;; *) say_skew ;; esac
floor=${DEVKIT_SPAWN_FLOOR_GB:-6}; wait_s=${DEVKIT_SPAWN_WAIT_S:-300}
[[ "$floor" =~ ^[0-9]+$ && "$wait_s" =~ ^[0-9]+$ ]] || { floor=6; wait_s=300; }
free_gb() { awk '/^MemAvailable:/ {print int($2 / 1048576)}' /proc/meminfo 2>/dev/null || echo 99; }
deadline=$(( $(date +%s) + wait_s ))
while (( $(free_gb) < floor )); do
  (( $(date +%s) >= deadline )) && warn "held ${kind} ${wait_s} s; only $(free_gb) GB free (floor ${floor} GB). Started anyway: spawn no more until a gate finishes (uv run dev status)."
  sleep 1
done
say_skew
