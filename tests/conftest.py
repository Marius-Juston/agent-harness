import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins/harness-ops/scripts"


@pytest.fixture
def hook():
    """Run a hook script with a JSON payload: (exit code, stdout, stderr)."""

    def run(name: str, payload: dict | str, env: dict | None = None):
        data = payload if isinstance(payload, str) else json.dumps(payload)
        cmd = [sys.executable, str(HOOKS / name)] if name.endswith(".py") else ["bash", str(HOOKS / name)]
        out = subprocess.run(cmd, input=data, capture_output=True, text=True, env=env, timeout=30, check=False)
        return out.returncode, out.stdout, out.stderr

    return run
