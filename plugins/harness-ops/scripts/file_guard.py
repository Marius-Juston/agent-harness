#!/usr/bin/env python3
"""PreToolUse(Grep, and Read through `if` filters): refuse the file tools the secrets guard_bash refuses to Bash.

Grep cannot be filtered by path in hooks.json (an `if` filter never fires for it: VERIFIED, Claude Code 2.1.329), so
this runs on every Grep. It stays cheap: this file is small, and a call that names no secret-shaped path exits after
reading stdin, before guard_bash (large, so compiled on each run when started as a script) is imported. guard_bash
imported as a module is compiled once into __pycache__. Defense in depth, not a security boundary (see guard_bash).
"""

import os
import sys

# Hooks run as `#!/usr/bin/env python3`, the host's interpreter, not the project's: every script must run on this
# (Ubuntu 22.04 LTS ships 3.10). tests/test_plugin.py runs each one under it; devkit.setup.HOOK_PYTHON must agree.
MIN_PYTHON = (3, 10)
# Substrings of every path guard_bash.cred_path, is_env or is_secrets can refuse; the input holds the path and cwd.
HINTS = (".config", ".ssh", ".env", "hosts", "api-keys", "GH_CONFIG_DIR", "XDG_CONFIG_HOME")


def hints() -> list[str]:
    custom = (os.environ.get(k, "") for k in ("GH_CONFIG_DIR", "XDG_CONFIG_HOME", "DEVKIT_SECRETS_FILE"))
    return [*HINTS, *(os.path.basename(v.rstrip("/")) for v in custom if v.strip("/"))]


def stores() -> list[str]:
    """The credential stores guard_bash.cred_dirs protects (kept in step by a test)."""
    home = os.path.expanduser("~")
    dirs = [os.environ.get("GH_CONFIG_DIR", ""), os.path.join(os.environ.get("XDG_CONFIG_HOME") or "", "gh")]
    dirs += [os.path.join(home, ".config", "gh"), os.path.join(home, ".ssh")]
    return [os.path.normpath(d) for d in dirs if os.path.isabs(d)]


def above_a_store(raw: str) -> bool:
    """A Grep whose path (or cwd, without one) is a folder at or above a credential store: ~, ~/.config, /home, /."""
    import json  # only for a Grep: 3 ms

    try:
        data = json.loads(raw)
        inp = data.get("tool_input") or {}
        path = inp.get("path") or data.get("cwd") or os.getcwd()
        full = os.path.normpath(os.path.join(data.get("cwd") or os.getcwd(), os.path.expanduser(str(path))))
    except (ValueError, AttributeError, TypeError):
        return True  # unreadable input: let guard_bash decide
    return any(d == full or d.startswith(full.rstrip(os.sep) + os.sep) for d in stores())


def main() -> int:
    raw = sys.stdin.read()
    if not any(h in raw for h in hints()) and not ('"Grep"' in raw and above_a_store(raw)):
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import guard_bash

    return guard_bash.main(raw)


if __name__ == "__main__":
    sys.exit(main())
