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


def _surface_problems(monkeypatch, tmp_path, rel: str, text: str) -> list[str]:
    """check_surface's findings for one agent or skill file written under a throwaway surface root."""
    from devkit import config

    (tmp_path / "s" / rel).parent.mkdir(parents=True)
    (tmp_path / "s" / rel).write_text(text)
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(surface=["s"]))
    ran = checks.check_surface()
    assert ran.checked == 1
    return ran.problems


def _md(field: str, body: str = "body") -> str:
    return f"---\nname: a\ndescription: d\n{field}\n---\n{body}\n"


@pytest.mark.parametrize(
    ("rel", "field"),
    [
        ("agents/a.md", "maxturns: 5"),  # #153: a typo is silently ignored by Claude Code
        ("agents/a.md", "argument-hint: x"),  # a skill field on an agent
        ("skills/a/SKILL.md", "when-to-use: x"),
        ("skills/a/SKILL.md", "version: 1"),  # in neither documented table: Claude Code ignores it
        ("skills/a/SKILL.md", "maxTurns: 5"),  # an agent field on a skill
    ],
)
def test_the_gate_flags_an_undocumented_frontmatter_key(monkeypatch, tmp_path, rel, field):
    problems = _surface_problems(monkeypatch, tmp_path, rel, _md(field))
    assert any("unknown frontmatter keys" in p for p in problems), problems


@pytest.mark.parametrize(
    ("rel", "field"),
    [("skills/a/SKILL.md", f) for f in ("disallowed-tools: Agent", "background: false", "shell: bash")]
    + [("agents/a.md", f) for f in ("color: cyan", "maxTurns: 40", "effort: xhigh", "memory: project")],
)
def test_the_gate_accepts_every_documented_field(monkeypatch, tmp_path, rel, field):
    """code.claude.com/docs/en/skills#frontmatter-reference and /sub-agents#supported-frontmatter-fields."""
    assert _surface_problems(monkeypatch, tmp_path, rel, _md(field)) == []


@pytest.mark.parametrize(
    ("rel", "field"),
    [
        ("agents/a.md", "effort: extreme"),
        ("agents/a.md", "isolation: true"),
        ("agents/a.md", "color: teal"),
        ("agents/a.md", "memory: global"),
        ("agents/a.md", "maxTurns: 0"),
        ("agents/a.md", "maxTurns: many"),
        ("agents/a.md", "maxTurns: true"),
        ("skills/a/SKILL.md", "effort: hgih"),
        ("skills/a/SKILL.md", "context: subagent"),
        ("skills/a/SKILL.md", "shell: zsh"),
    ],
)
def test_the_gate_flags_a_value_outside_the_documented_options(monkeypatch, tmp_path, rel, field):
    problems = _surface_problems(monkeypatch, tmp_path, rel, _md(field))
    assert any(field.split(":")[0] in p and "must be" in p for p in problems), problems


@pytest.mark.parametrize("cmd", ["uv run dev status", "uv run dev status | head -20", "true || false"])
def test_the_gate_flags_an_injection_that_can_abort_the_skill(monkeypatch, tmp_path, cmd):
    """A failed !`cmd` aborts the whole skill invocation (code.claude.com/docs/en/skills#inject-dynamic-context)."""
    problems = _surface_problems(monkeypatch, tmp_path, "skills/a/SKILL.md", _md("", f"## Now\n!`{cmd}`"))
    assert any("|| true" in p for p in problems), problems


def test_the_gate_accepts_a_guarded_injection_and_ignores_a_mid_word_bang(monkeypatch, tmp_path):
    body = "## Now\n!`uv run dev status | head -20 || true`\nKEY=!`not run` and `!not` either"
    assert _surface_problems(monkeypatch, tmp_path, "skills/a/SKILL.md", _md("", body)) == []


def test_every_plugin_agent_bounds_its_turns_and_sets_its_effort():
    for p in (PLUGIN / "agents").glob("*.md"):
        meta, _ = frontmatter.read(p)
        assert isinstance(meta.get("maxTurns"), int) and meta["maxTurns"] > 0, p.name
        assert meta.get("effort") in checks.EFFORTS, p.name


@pytest.mark.parametrize("agent", ["architect", "builder", "suite-auditor", "tester"])
def test_agents_that_read_or_change_the_tree_run_in_a_worktree(agent):
    """#153: the read-only reviews read the default branch, not the lead's checkout mid-integration."""
    meta, _ = frontmatter.read(PLUGIN / f"agents/{agent}.md")
    assert meta.get("isolation") == "worktree"


def test_the_research_skill_takes_its_question_as_arguments():
    meta, body = frontmatter.read(PLUGIN / "skills/research/SKILL.md")
    assert meta.get("argument-hint") and "$ARGUMENTS" in body


def test_the_lead_skill_preloads_dev_status_without_aborting():
    _, body = frontmatter.read(PLUGIN / "skills/lead/SKILL.md")
    cmds = checks.INJECTION.findall(body)
    assert any("dev status" in c for c in cmds)
    assert all(c.rstrip().endswith("|| true") for c in cmds)
