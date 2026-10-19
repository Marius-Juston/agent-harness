#!/usr/bin/env bash
# SessionStart (startup, resume, clear, compact): inject `uv run dev orient` as context, so every session and every
# post-compaction continuation starts from the current handoff, board and machine state rather than from memory.
# First, a loaded plugin that is not the pinned release's build is named to the owner and the agent (plugin_skew.sh).
# Fails silently (offline, no gh auth): orientation is a convenience, not a dependency.
skew=$(bash "$(dirname "${BASH_SOURCE[0]}")/plugin_skew.sh")
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
out=$(timeout 40 uv run --quiet dev orient 2>/dev/null) || out=""
ctx=$(printf '%s\n%s' "$skew" "$out" | sed '/./,$!d')
[[ -n "$ctx" ]] && jq -n --arg c "$ctx" --arg s "$skew" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}} + if $s == "" then {} else {systemMessage: $s} end'
exit 0
