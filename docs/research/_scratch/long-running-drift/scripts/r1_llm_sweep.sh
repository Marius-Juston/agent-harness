#!/usr/bin/env bash
# #282 r1: one LLM claim-check sweep of one doc inside an extracted snapshot tree (git archive, no .git). Read-only tools.
# usage: r1_llm_sweep.sh TREE DOC OUT.json [MODEL]
# Project settings and hooks are not loaded (--setting-sources user), no MCP servers, no session saved.
set -euo pipefail
tree=$1 doc=$2 out=$3 model=${4:-sonnet}
prompt="You audit documentation drift in this repository snapshot (the working directory; there is no git history).
Read the file ${doc}. List every claim it makes about THIS repository that the code here can confirm or refute:
file and directory paths, commands and their subcommands and flags, default values, constants and numbers, names of
types, functions, tests and database tables, and workflow steps that scripts or code implement. Skip opinions, goals,
plans, history (text that says something was removed or used to be), and claims about external services or other machines.
Verify each claim against the code with Read, Grep and Glob. Then output ONLY JSON lines, one per claim, no prose:
{\"line\": <line number in ${doc}>, \"claim\": \"<at most 20 words>\", \"verdict\": \"TRUE\"|\"STALE\"|\"UNVERIFIABLE\", \"evidence\": \"<path:line or the reason>\"}
STALE means the code here contradicts the claim or the named thing does not exist."
cd "$tree"
claude -p "$prompt" --model "$model" --output-format json --setting-sources user --strict-mcp-config \
  --allowedTools Read Grep Glob --disallowedTools Bash Write Edit WebFetch WebSearch Agent \
  --no-session-persistence --max-turns 120 > "$out"
