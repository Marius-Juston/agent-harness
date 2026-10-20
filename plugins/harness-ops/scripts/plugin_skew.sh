#!/usr/bin/env bash
# Prints one line when the loaded plugin (${CLAUDE_PLUGIN_ROOT}) is not the build of the release the project pins, else
# nothing (#296). session_brief.sh and spawn_guard.sh call it. The project's own devkit compares the two trees: it ships
# the pinned release's plugin, and the marketplace's version string does not identify a build. A project without
# devkit, or with a devkit older than this check, prints nothing; `uv run dev doctor` is the backstop.
dir="${CLAUDE_PROJECT_DIR:-$PWD}"
[[ -n "${CLAUDE_PLUGIN_ROOT:-}" ]] || exit 0
py="$dir/.venv/bin/python"
if [[ ! -x "$py" ]]; then  # a worktree without its own environment uses the main checkout's
  common=$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
  py="$(dirname "$common")/.venv/bin/python"
  [[ -x "$py" ]] || exit 0
fi
# -I: never import a `devkit` from the working directory instead of the installed one
cd "$dir" 2>/dev/null && timeout 3 "$py" -I -c 'import sys
from pathlib import Path
from devkit import config, setup
line = setup.skew_line(Path(sys.argv[1]), setup.pinned_plugin_root(), config.get().plugins)
if line:
    print(line)' "$CLAUDE_PLUGIN_ROOT" 2>/dev/null
exit 0
