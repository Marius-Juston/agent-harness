#!/usr/bin/env python3
"""PostToolUse(Edit|Write): ruff on the edited Python file; findings go back to the agent at once.

An error caught at the edit costs one line of context; the same error caught by the gate costs a red gate, a log read
and a re-run. Does not modify the file (a silent rewrite would desync the agent's view of it). Fast: one file.
"""

import json
import os
import subprocess
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict):
        return 0
    path = (data.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(".py") or not os.path.isfile(path):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or "."
    ruff = os.path.join(root, ".venv/bin/ruff")
    cmd = [ruff] if os.path.exists(ruff) else ["uvx", "ruff"]
    try:
        res = subprocess.run(
            [*cmd, "check", "--output-format", "concise", "--quiet", path],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    findings = [line for line in res.stdout.splitlines() if line.strip()][:15]
    if res.returncode == 1 and findings:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "ruff found (fix now; `ruff check --fix` handles [*] ones):\n"
                        + "\n".join(findings),
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
