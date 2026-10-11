"""The release version is declared in three files; they must agree, so a release can never ship mixed versions."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_plugin_and_template_versions_agree():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((ROOT / "plugins/harness-ops/.claude-plugin/plugin.json").read_text())["version"]
    template = re.search(r"^\s*default:\s*v(\S+)\s*$", (ROOT / "copier.yml").read_text(), re.M)
    assert template, "copier.yml has no harness_ref default"
    assert package == plugin == template.group(1), f"pyproject {package}, plugin {plugin}, copier v{template.group(1)}"
