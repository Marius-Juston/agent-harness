"""The harness-ops plugin and its marketplace: what Claude Code loads must exist, and the texts must use the names
Claude Code gives plugin components (code.claude.com/docs/en/plugins/components)."""

import json
import re
import shutil
import subprocess

import pytest

from devkit import checks, frontmatter
from devkit.paths import root

PLUGIN = root() / "plugins/harness-ops"
AGENTS = sorted(p.stem for p in (PLUGIN / "agents").glob("*.md"))


def test_the_marketplace_lists_the_plugin_under_its_manifest_name():
    market = json.loads((root() / ".claude-plugin/marketplace.json").read_text())
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == manifest["name"])
    assert (root() / entry["source"]).resolve() == PLUGIN.resolve()
    assert market["name"] == "agent-harness" and manifest["name"] == "harness-ops"


def test_the_plugin_has_the_seven_agents_and_three_skills():
    assert AGENTS == ["architect", "builder", "research-auditor", "researcher", "reviewer", "suite-auditor", "tester"]
    assert sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md")) == ["harness", "lead", "research"]


@pytest.mark.parametrize("agent", ["architect", "suite-auditor"])
def test_the_read_only_agents_get_read_tools_only(agent):
    """#113, #114: the consolidation review and the suite audit propose and never edit; Bash is held read-only by
    guard_bash.py (READ_ONLY_AGENTS)."""
    meta, _ = frontmatter.read(PLUGIN / f"agents/{agent}.md")
    assert meta is not None
    assert [t.strip() for t in str(meta["tools"]).split(",")] == ["Read", "Grep", "Glob", "Bash"]
    assert not {"disallowedTools", "permissionMode", "hooks"} & set(meta)


def test_every_hook_script_exists_and_is_executable():
    data = json.loads((PLUGIN / "hooks/hooks.json").read_text())
    commands = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
    assert len(commands) >= 7
    for cmd in commands:
        rel = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}\"?/(\S+)", cmd).group(1)
        target = PLUGIN / rel
        assert target.is_file() and target.stat().st_mode & 0o111, rel
    assert all((PLUGIN / "hooks" / m).is_file() for m in data["modules"])


@pytest.mark.parametrize(
    "path", sorted((PLUGIN / "skills").glob("*/SKILL.md")) + sorted((PLUGIN / "agents").glob("*.md"))
)
def test_texts_name_plugin_agents_with_their_namespace(path):
    """A plugin agent is `harness-ops:<name>`; a bare `builder` in a spawn instruction would not resolve."""
    _, body = frontmatter.read(path)
    bare = [m for m in re.findall(r"`([a-z-]+)`", body) if m in AGENTS]
    bare += re.findall(r"subagent_type: (?!harness-ops:)([a-z-]+)", body)
    assert not bare, f"{path.name}: write harness-ops:{bare[0]}"


def test_plugin_agents_use_no_field_claude_code_ignores_in_a_plugin(monkeypatch):
    for p in (PLUGIN / "agents").glob("*.md"):
        meta, _ = frontmatter.read(p)
        assert not set(meta) & checks.PLUGIN_AGENT_IGNORED, p.name


def test_the_gate_flags_an_ignored_plugin_agent_field(monkeypatch, tmp_path):
    from devkit import config

    (tmp_path / "plug/.claude-plugin").mkdir(parents=True)
    (tmp_path / "plug/.claude-plugin/plugin.json").write_text('{"name": "plug"}')
    (tmp_path / "plug/agents").mkdir()
    (tmp_path / "plug/agents/a.md").write_text("---\nname: a\ndescription: d\npermissionMode: plan\n---\nbody\n")
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(surface=["plug"]))
    ran = checks.check_surface()
    assert ran.checked == 1 and any("ignored in a plugin agent" in p for p in ran.problems)


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not installed")
@pytest.mark.parametrize("target", [".", "plugins/harness-ops"])
def test_claude_plugin_validate_passes(target):
    out = subprocess.run(
        ["claude", "plugin", "validate", "--strict", target], cwd=root(), capture_output=True, text=True, check=False
    )
    assert out.returncode == 0 and "Validation passed" in out.stdout, out.stdout + out.stderr
