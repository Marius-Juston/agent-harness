#!/usr/bin/env bash
# SessionStart (startup, resume, clear, compact): inject `uv run dev orient` as context, so every session and every
# post-compaction continuation starts from the current handoff, board and machine state rather than from memory.
# Fails silently (offline, no gh auth): orientation is a convenience, not a dependency.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
out=$(timeout 40 uv run --quiet dev orient 2>/dev/null) || exit 0
[[ -n "$out" ]] && jq -n --arg c "$out" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}}'
exit 0
