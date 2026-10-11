"""`[tool.devkit]`: defaults fit the template, an unknown key or a wrong type is an error, and the paths it moves are
the paths the checks read."""

import json

import pytest

from devkit import checks, config, workflow


def test_an_empty_table_gives_the_template_defaults():
    cfg = config.parse({})
    assert cfg.surface == [".claude"] and not cfg.surface_optional
    assert cfg.ratchets == ".devkit/ratchets.toml" and cfg.quota_cap == 95
    assert "harness-ops@agent-harness" in cfg.plugins


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"surfaces": [".claude"]}, "unknown key"),
        ({"surface": ".claude"}, "expected"),
        ({"quota_cap": "95"}, "expected"),
        ({"quota_cap": True}, "expected"),
        ({"surface_optional": "yes"}, "expected"),
        ({"plugins": ["x"]}, "expected"),
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
    assert workflow.usage_percent(text) == pct


def test_a_usage_reading_carries_the_configured_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow, "state_dir", lambda *a: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(quota_cap=50))
    data = json.loads(workflow.write_usage(60, now=1000).read_text())
    assert data == {"pct": 60, "t": 1000, "cap": 50}
