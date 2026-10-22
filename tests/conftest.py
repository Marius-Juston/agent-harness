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


@pytest.fixture
def ros_like_site(tmp_path) -> Path:
    """A PYTHONPATH entry like a sourced ROS (#372): a pytest entry-point plugin whose import fails."""
    site = tmp_path / "ros-site"
    info = site / "broken_plugin-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: broken-plugin\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text("[pytest11]\nbroken = broken_plugin\n")
    (site / "broken_plugin.py").write_text("import lark_that_is_not_installed\n")
    return site
