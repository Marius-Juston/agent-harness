import json
import subprocess
import sys
from pathlib import Path

import pytest

from devkit import config, paths

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins/harness-ops/scripts"


@pytest.fixture(autouse=True)
def _fresh_location_caches():
    """root(), common_dir() and config.get() are cached per process: a test that chdirs into a temporary repository
    (or runs integrate in one) must not leave its answers to the next test on the same worker."""
    caches = (paths.root, paths.common_dir, config.get)
    for cached in caches:
        cached.cache_clear()
    yield
    for cached in caches:
        cached.cache_clear()


@pytest.fixture
def hook():
    """Run a hook script with a JSON payload: (exit code, stdout, stderr)."""

    def run(name: str, payload: dict | str, env: dict | None = None):
        data = payload if isinstance(payload, str) else json.dumps(payload)
        cmd = [sys.executable, str(HOOKS / name)] if name.endswith(".py") else ["bash", str(HOOKS / name)]
        out = subprocess.run(cmd, input=data, capture_output=True, text=True, env=env, timeout=30, check=False)
        return out.returncode, out.stdout, out.stderr

    return run
