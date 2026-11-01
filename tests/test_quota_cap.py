"""`dev quota-cap` (#247): a machine-local cap override under the git common dir that the spawn guard, `usage-poll`
and orient honour, without a file in the repository tree changing."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import cli, config, quota

GUARD = Path(__file__).resolve().parents[1] / "plugins/harness-ops/scripts/spawn_guard.sh"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository whose `.git/devkit` is the state dir and whose [tool.devkit] quota_cap is 95."""
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "pyproject.toml").write_text("[tool.devkit]\nquota_cap = 95\n")
    git = ["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    state = repo / ".git/devkit"
    state.mkdir()
    monkeypatch.setattr(quota, "state_dir", lambda *a: state)
    monkeypatch.setattr(config, "get", lambda: config.Config(quota_cap=95))
    return repo


def _spawn_denied(repo: Path) -> bool:
    payload = json.dumps({"tool_input": {"subagent_type": "researcher"}})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    out = subprocess.run(["bash", str(GUARD)], input=payload, capture_output=True, text=True, env=env, check=True)
    return bool(out.stdout) and json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_dev_quota_cap_changes_the_spawn_guard_cap_and_no_repository_file(repo, capsys):
    quota.write_usage(85)
    assert not _spawn_denied(repo)  # 85% < the pyproject cap 95%

    assert cli.run(cli.parse(["quota-cap", "80"])) == 0
    assert quota.quota_cap() == (80, "local")
    assert _spawn_denied(repo)
    assert "cap 80% (local)" in quota.quota()
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""  # the override lives under .git, never in the tree

    assert cli.run(cli.parse(["quota-cap", "--clear"])) == 0
    assert quota.quota_cap() == (95, "pyproject")
    assert not _spawn_denied(repo)


def test_the_spawn_guard_reads_the_override_even_when_a_poller_wrote_the_old_cap(repo):
    """A poller started before the override (old code, or before `dev quota-cap`) still records the pyproject cap."""
    (repo / ".git/devkit/usage.json").write_text(json.dumps({"pct": 85, "t": int(time.time()), "cap": 95}))
    quota.set_quota_cap(80)
    (repo / ".git/devkit/usage.json").write_text(json.dumps({"pct": 85, "t": int(time.time()), "cap": 95}))
    assert _spawn_denied(repo)


def test_usage_poll_records_the_effective_cap(repo):
    quota.set_quota_cap(90)
    assert json.loads(quota.write_usage(60, now=1000).read_text()) == {"pct": 60, "t": 1000, "cap": 90}
    assert quota.quota_reading() == (None, 90)


def test_printing_the_cap_names_its_source(repo, capsys):
    assert cli.run(cli.parse(["quota-cap"])) == 0
    assert capsys.readouterr().out.strip() == "quota cap 95% (pyproject: [tool.devkit] quota_cap)"
    quota.set_quota_cap(90)
    assert cli.run(cli.parse(["quota-cap"])) == 0
    assert "quota cap 90% (local:" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["abc", "-1", "101", "9.5", "", " ", "1e2", "0x10", "١٠"])
def test_invalid_values_are_refused(repo, capsys, value):
    assert cli.run(cli.parse(["quota-cap", "--", value])) == 1
    assert "0-100" in capsys.readouterr().err
    assert not (repo / ".git/devkit/quota_cap").exists()


@given(st.integers(min_value=0, max_value=100))
def test_every_percent_round_trips(n):
    assert config.parse_cap(str(n)) == n


@given(st.text())
def test_parse_cap_accepts_exactly_the_decimal_integers_0_to_100(text):
    ok = text.strip() in {str(n) for n in range(101)}
    try:
        assert config.parse_cap(text) == int(text.strip()) and ok
    except ValueError:
        assert not ok


@pytest.mark.parametrize("junk", ["", "abc\n", "150\n", "-3", "\x00\xff"])
def test_a_corrupt_file_falls_back_to_pyproject_with_a_warning(repo, capsys, junk):
    (repo / ".git/devkit/quota_cap").write_text(junk, errors="surrogateescape")
    assert quota.quota_cap() == (95, "pyproject")
    assert "warning" in capsys.readouterr().err
    quota.write_usage(96)
    assert _spawn_denied(repo)  # the guard falls back to the recorded pyproject cap too
    quota.write_usage(90)
    assert not _spawn_denied(repo)


def test_an_unreadable_override_file_falls_back(repo, capsys):
    (repo / ".git/devkit/quota_cap").mkdir()  # a directory: read_text raises
    assert quota.quota_cap() == (95, "pyproject")
    assert "warning" in capsys.readouterr().err
