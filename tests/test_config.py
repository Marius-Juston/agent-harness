"""`[tool.devkit]`: defaults fit the template, an unknown key or a wrong type is an error, and the paths it moves are
the paths the checks read."""

import json

import pytest

from devkit import checks, config, quota


def test_an_empty_table_gives_the_template_defaults():
    cfg = config.parse({})
    assert cfg.surface == [".claude"] and not cfg.surface_optional
    assert cfg.ratchets == ".devkit/ratchets.toml" and cfg.quota_cap == 95
    assert "harness-ops@agent-harness" in cfg.plugins
    assert cfg.mcp_prereqs == {}  # doctor checks no MCP prerequisites unless the project names them
    assert config.parse({"mcp_prereqs": {"lean-lsp": ["rg"]}}).mcp_prereqs == {"lean-lsp": ["rg"]}
    assert cfg.required_reviews == {}  # #391: integrate requires no review unless the project maps paths to one
    rules = {"src/math/": "harness-ops:reviewer"}
    assert config.parse({"required_reviews": rules}).required_reviews == rules
    assert cfg.regenerate == []  # integrate runs `dev docs` alone unless the project declares more (#386)
    assert config.parse({"regenerate": [["uv", "run", "rehash"]]}).regenerate == [["uv", "run", "rehash"]]


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"surfaces": [".claude"]}, "unknown key"),
        ({"surface": ".claude"}, "expected"),
        ({"quota_cap": "95"}, "expected"),
        ({"quota_cap": True}, "expected"),
        ({"surface_optional": "yes"}, "expected"),
        ({"plugins": ["x"]}, "expected"),
        ({"mcp_prereqs": {"lean-lsp": "rg"}}, "list of binary names"),
        ({"mcp_prereqs": {"lean-lsp": [""]}}, "list of binary names"),
        ({"required_reviews": {"src/math/": ["harness-ops:reviewer"]}}, "reviewer name"),
        ({"required_reviews": {"": "harness-ops:reviewer"}}, "reviewer name"),
        ({"regenerate": ["uv", "run", "rehash"]}, "list of arguments"),
        ({"regenerate": [[]]}, "list of arguments"),
        ({"regenerate": [["uv", ""]]}, "list of arguments"),
    ],
)
def test_a_typo_or_a_wrong_type_is_an_error_not_a_default(data, match):
    with pytest.raises(config.ConfigError, match=match):
        config.parse(data)


def test_this_repository_reads_its_own_table():
    cfg = config.get()
    assert cfg.surface == ["plugins/harness-ops"] and "template/" in cfg.path_check_skip


def test_an_empty_surface_fails_unless_declared_optional(monkeypatch, tmp_path):
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config())
    assert not checks.check_surface().ok
    monkeypatch.setattr(config, "get", lambda: config.Config(surface_optional=True))
    assert checks.check_surface().ok


def test_moved_research_dir_is_what_the_note_check_reads(monkeypatch, tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/a.md").write_text("no house style\n")
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(research_dir="notes"))
    ran = checks.check_research_notes()
    assert ran.checked == 1 and ran.problems


def test_budget_kinds_follow_the_surface_roots(monkeypatch):
    monkeypatch.setattr(config, "get", lambda: config.Config(surface=[".claude", "plugins/p"]))
    assert checks._kind(".claude/agents/a.md") == "agent"
    assert checks._kind("plugins/p/skills/s/SKILL.md") == "skill"
    assert checks._kind("plugins/p/skills/s/*") == "skill"
    assert checks._kind("plugins/q/agents/a.md") == "context"
    assert checks._kind("CLAUDE.md") == "context"


@pytest.mark.parametrize(
    ("text", "pct"),
    [
        ("Current week (all models): 42% used\n", 42),
        ("Session: 10%\nCurrent week (all models) ... 7%\n", 7),
        ("nothing here", None),
        ("", None),
    ],
)
def test_usage_percent_reads_the_all_models_line(text, pct):
    assert quota.usage_percent(text) == pct


def test_a_usage_reading_carries_the_configured_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(quota, "state_dir", lambda *a: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(quota_cap=50))
    data = json.loads(quota.write_usage(60, now=1000).read_text())
    assert data == {"pct": 60, "t": 1000, "cap": 50}


NOTE = "# Research: x\n\nStatus: v0\nLabels: SOURCED = cited\n{coverage}\nA claim ([src](https://example.org)).\n"


def test_a_note_without_a_search_coverage_line_fails_the_note_check(monkeypatch, tmp_path):
    (tmp_path / "notes").mkdir()
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(research_dir="notes"))
    (tmp_path / "notes/a.md").write_text(NOTE.format(coverage=""))
    assert any("Search coverage" in p for p in checks.check_research_notes().problems)
    (tmp_path / "notes/a.md").write_text(
        NOTE.format(coverage="Search coverage: openalex, s2, crossref ok (dev lit status, 2026-10-11)")
    )
    assert checks.check_research_notes().problems == []


def test_the_new_note_template_has_a_search_coverage_line():
    from devkit import cli

    assert "\nSearch coverage: " in cli.NOTE_TEMPLATE


def test_research_skill_researcher_and_template_require_discovery_check_and_coverage():
    """The discovery check moved from a separate `dev lit status` call into `dev lit search`'s first line (#266)."""
    from devkit.paths import root

    plugin = root() / "plugins/harness-ops"
    for rel in ("skills/research/SKILL.md", "agents/researcher.md"):
        text = (plugin / rel).read_text()
        assert "uv run dev lit search" in text and "DEGRADED" in text and "Search coverage" in text, rel
    assert "Search coverage" in (root() / "template/docs/research/README.md").read_text()
