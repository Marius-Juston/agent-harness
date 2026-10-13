#!/usr/bin/env bash
# When does a path-scoped .claude/rules/ file load? (#220 r2, Claude Code 2.1.296, 2026-10-13)
# Usage: r2_rules_probe.sh <empty scratch dir>   (spends ~8 short `claude -p` calls on haiku and 1 on opus)
# Builds a throwaway repo with a rule scoped to secret/** and one to deny/**, an InstructionsLoaded hook that logs
# every load (load_reason, trigger_file_path, agent_id), and a PreToolUse hook that denies Read under deny/.
# Each probe resets the log and prints how many path_glob_match loads it caused.
set -euo pipefail
S=${1:?scratch dir}; mkdir -p "$S" && cd "$S" && [ -z "$(ls -A)" ] || { echo "need an empty dir"; exit 1; }
mkdir -p .claude/rules secret plain deny && git init -q
printf -- '---\npaths:\n  - "secret/**"\n---\n# Fixture files\nFiles under secret/ are test fixtures (decision: issue #12). When you report on one, also state its line count, because fixture size is checked in review.\n' > .claude/rules/zeta.md
printf -- '---\npaths:\n  - "deny/**"\n---\nFiles under deny/ are archived.\n' > .claude/rules/deny.md
echo 'Unconditional rule loaded. Its marker is KIWI-4410; mention it only if asked which rules you have.' > .claude/rules/always.md
echo "hello from secret" > secret/a.txt; echo "denied content" > deny/c.txt
cat > .claude/log_hook.py <<'EOF'
import json, os, sys
d = json.load(sys.stdin)
open(os.path.join(os.path.dirname(__file__), "instr.log"), "a").write(json.dumps(d) + "\n")
EOF
cat > .claude/deny_hook.py <<'EOF'
import json, sys
d = json.load(sys.stdin)
if "/deny/" in d.get("tool_input", {}).get("file_path", ""):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "Use the outline command instead."}}))
EOF
cat > .claude/settings.json <<'EOF'
{"hooks": {"InstructionsLoaded": [{"hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/log_hook.py\""}]}],
 "PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/deny_hook.py\""}]}]}}
EOF
git add -A && git commit -qm probe
run() {  # $1 model, $2 label, $3 prompt
  : > .claude/instr.log
  out=$(timeout 300 claude -p "$3" --model "$1" --setting-sources project,local --permission-mode acceptEdits \
        --allowedTools "Bash(sed:*)" "Bash(python3:*)" "Bash(cat:*)" "Grep" --output-format text 2>&1 | tr '\n' ' ')
  echo "== $2 ($1): $(grep -c path_glob_match .claude/instr.log || true) path_glob_match;" \
       "agent_ids: $(grep -o '"agent_id": "[^"]*"' .claude/instr.log | sort -u | wc -l) | ${out:0:200}"
}
run haiku read-tool "Use the Read tool on secret/a.txt and report on the file in two sentences."
run haiku grep "Use only the Grep tool (output mode content) to search for the word hello in the secret directory. Do not use Read. Report the matching lines verbatim."
run haiku bash-sed "Use only Bash to run exactly: sed -n 1p secret/a.txt   Do not use Read. Report the output."
run haiku bash-cat "Use only Bash to run exactly: cat secret/a.txt   Do not use Read. Report the output."
run haiku bash-python "Use only Bash to run exactly: python3 -c \"print(open('secret/a.txt').read())\"   Do not use Read. Report the output verbatim."
run haiku write-new "Use the Write tool to create secret/new.txt containing the single word test. Do not read anything first."
run haiku read-denied "Use the Read tool on deny/c.txt once. If it is refused, stop and quote the refusal reason."
run opus subagent "Spawn one general-purpose subagent with the Agent tool. Its task: use the Read tool on secret/a.txt and report on the file. Return the subagent's reply verbatim, then say whether you yourself saw any rule about secret/."
