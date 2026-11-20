"""`[tool.devkit]`: defaults fit the template, an unknown key or a wrong type is an error, and the paths it moves are
the paths the checks read."""

import json
import re
import shlex
import sys
import tomllib

import pytest
import yaml

from devkit import checks, cli, config, paths, quota


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
        ({"commands": {"coverage": "python -m study.coverage"}}, "list of arguments"),
        ({"commands": {"coverage": []}}, "list of arguments"),
        ({"commands": {"Cover age": ["python"]}}, "command name"),
        ({"commands": {"-x": ["python"]}}, "command name"),
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
    assert "\nSearch coverage: " in cli.NOTE_TEMPLATE


def test_research_skill_researcher_and_template_require_discovery_check_and_coverage():
    """The discovery check moved from a separate `dev lit status` call into `dev lit search`'s first line (#266)."""
    from devkit.paths import root

    plugin = root() / "plugins/harness-ops"
    for rel in ("skills/research/SKILL.md", "agents/researcher.md"):
        text = (plugin / rel).read_text()
        assert "uv run dev lit search" in text and "DEGRADED" in text and "Search coverage" in text, rel
    assert "Search coverage" in (root() / "template/docs/research/README.md").read_text()


# #492: `[tool.devkit] commands` adds a project's own subcommands under `dev`.
ECHO = [sys.executable, "-c", "import os, sys; print(os.getcwd()); print(sys.argv[1:]); sys.exit(int(sys.argv[-1]))"]


def _commands(monkeypatch, commands):
    monkeypatch.setattr(config, "get", lambda: config.parse({"commands": commands}))


@pytest.mark.parametrize("code", [0, 3])
def test_a_project_command_runs_in_the_root_with_its_args_and_exit_code(monkeypatch, capfd, code):
    _commands(monkeypatch, {"coverage": ECHO})
    rest = ["--json", "-h", "--", "x y", str(code)]
    monkeypatch.setattr("sys.argv", ["dev", "coverage", *rest])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code
    cwd, args = capfd.readouterr().out.splitlines()
    assert cwd == str(paths.root()) and args == repr(rest)  # every argument as given, `--` and `-h` included


def test_a_project_command_named_like_a_built_in_is_a_config_error(monkeypatch, capsys):
    _commands(monkeypatch, {"check": ECHO})
    # The message says how to recover: which devkit owns the name, and where to rename it.
    recover = r"devkit \d+\.\d+\.\d+\S* has a built-in `dev check`; rename yours in \[tool\.devkit\.commands\]"
    with pytest.raises(config.ConfigError, match=recover):
        cli.build()
    monkeypatch.setattr("sys.argv", ["dev", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1 and re.search(recover, capsys.readouterr().err)


def test_a_percent_in_a_project_command_does_not_break_dev_help(monkeypatch):
    # argparse %-formats help strings: an unescaped `+%Y` raised ValueError in `dev --help` and `dev docs`.
    _commands(monkeypatch, {"stamp": ["date", "+%Y", "%(prog)s"]})
    assert "project: date +%Y %(prog)s" in cli.build().format_help()
    assert ("stamp", "project: date +%Y %(prog)s") in cli.command_help()


def test_a_missing_program_is_an_error_line_and_127(monkeypatch, capsys):
    _commands(monkeypatch, {"gone": ["devkit-no-such-program-492"]})
    monkeypatch.setattr("sys.argv", ["dev", "gone"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 127
    assert capsys.readouterr().err.startswith("error: dev gone: ")


def test_a_project_command_killed_by_a_signal_exits_128_plus_the_signal(monkeypatch):
    kill = [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"]
    _commands(monkeypatch, {"die": kill})
    monkeypatch.setattr("sys.argv", ["dev", "die"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 128 + 15


def test_dev_help_lists_project_commands_and_the_doc_checks_accept_them(monkeypatch):
    _commands(monkeypatch, {"coverage": ["python", "-m", "study.coverage"]})
    assert re.search(r"coverage +project: python -m study.coverage", cli.build().format_help())
    assert ("coverage", "project: python -m study.coverage") in cli.command_help()
    assert cli.command_map()["coverage"] == set()
    parser = checks.loosen(cli.build())
    assert checks.span_problem(parser, cli.command_map(), "coverage", " --json units") is None


# #544: `dev integrate --local-ci` posts the `check` context from `local_ci.check`, so that list runs what the ci
# `check` job runs. These `run:` steps the gate already covers; every other one needs its local command, in order.
GATE_COVERS = {
    "uv sync --locked": "the gate's `uv lock --check` stage",
    "uv run dev check --no-cache": "the gate itself",
    "uv run dev docs": "integrate runs it on the merged tree before the gate",
}
# A multi-line `run:` block stands in as itself, under bash -e as Actions runs it, with $RUNNER_TEMP a fresh directory.
AS_ACTIONS = ["env", "RUNNER_TEMP={tmpdir}", "bash", "-ec"]


def ci_check_commands(workflow: str) -> list[str]:
    """The `run:` text of each step of the workflow's `check` job that the gate does not cover, in order."""
    steps = yaml.safe_load(workflow)["jobs"]["check"]["steps"]
    return [s["run"].strip() for s in steps if "run" in s and s["run"].strip() not in GATE_COVERS]


def as_ci_run(argv: list[str]) -> str:
    """The `run:` text a `local_ci` command stands in for."""
    return argv[len(AS_ACTIONS)].strip() if argv[: len(AS_ACTIONS)] == AS_ACTIONS else shlex.join(argv)


def test_the_local_check_runs_what_the_ci_check_job_runs():
    local = tomllib.loads((paths.root() / "pyproject.toml").read_text())["tool"]["devkit"]["local_ci"]["check"]
    ci = ci_check_commands((paths.root() / ".github/workflows/ci.yml").read_text())
    assert [as_ci_run(argv) for argv in local] == ci
    assert any("gitleaks-history" in c for c in ci) and any(c.startswith("uv audit") for c in ci)
    assert any("-m live tests/test_security.py" in c for c in ci)


def test_a_ci_step_without_its_local_command_is_a_gap():
    workflow = "jobs:\n  check:\n    steps:\n      - run: uv sync --locked\n      - run: uv audit --locked\n"
    workflow += '      - run: |\n          a=1\n          echo "$a" \\\n            | cat\n'
    assert ci_check_commands(workflow) == ["uv audit --locked", 'a=1\necho "$a" \\\n  | cat']
    assert as_ci_run(["uv", "audit", "--locked"]) == "uv audit --locked"
    assert as_ci_run([*AS_ACTIONS, 'a=1\necho "$a" \\\n  | cat\n']) == 'a=1\necho "$a" \\\n  | cat'
