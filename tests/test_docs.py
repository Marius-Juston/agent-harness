"""The generated agent surface depends only on tracked files (#328)."""

import json
import subprocess

from devkit import config
from devkit.paths import root


def test_settings_json_is_tracked_and_holds_what_setup_installs():
    # `dev setup` runs `claude plugin install --scope project`, which writes enabledPlugins into .claude/settings.json
    # and the surface's Plugins table reads it. Tracked with exactly those entries, setup changes nothing and the
    # committed agent-surface.md does not depend on whether this machine ran setup.
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".claude/settings.json"], cwd=root(), capture_output=True, check=False
    )
    assert tracked.returncode == 0, ".claude/settings.json is not tracked"
    settings = json.loads((root() / ".claude/settings.json").read_text())
    assert settings.get("enabledPlugins") == dict.fromkeys(config.get().plugins, True)
