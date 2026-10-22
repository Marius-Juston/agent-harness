"""devkit.paths: git output devkit parses does not depend on the owner's git config (#374)."""

import os
import subprocess
import sys

import pytest

from devkit import gate, paths


def _sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with a module, a test importing it and a change to the module on top."""
    _sh(tmp_path, "git", "init", "-q", "-b", "main")
    _sh(tmp_path, "git", "config", "user.email", "t@example.com")
    _sh(tmp_path, "git", "config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg.py").write_text("X = 1\n")
    (tmp_path / "tests/test_pkg.py").write_text("import pkg\n")
    _sh(tmp_path, "git", "add", "-A")
    _sh(tmp_path, "git", "commit", "-q", "-m", "one")
    (tmp_path / "pkg.py").write_text("X = 2\n")
    _sh(tmp_path, "git", "commit", "-q", "-am", "two")
    monkeypatch.chdir(tmp_path)
    for key in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:  # the gate's own pins (dev runs pinned)
        monkeypatch.delenv(key)
    paths.root.cache_clear()
    yield tmp_path
    paths.root.cache_clear()


HOSTILE = {
    "color.ui": "always",
    "diff.noprefix": "true",
    "diff.relative": "true",
    "diff.external": "/bin/false",
    "log.showSignature": "true",
}


def test_pinned_git_output_does_not_depend_on_the_repo_config(repo, monkeypatch):
    plain = gate.affected_tests("HEAD~1"), paths.git("diff", "-U0", "HEAD~1", "HEAD")
    for key, value in HOSTILE.items():
        _sh(repo, "git", "config", key, value)
    hostile = paths.git("diff", "-U0", "HEAD~1", "HEAD", check=False)
    assert hostile != plain[1]  # the trap works: the raw output changes
    for key, value in paths.pin_git_output({}).items():
        monkeypatch.setenv(key, value)
    assert (gate.affected_tests("HEAD~1"), paths.git("diff", "-U0", "HEAD~1", "HEAD")) == plain
    assert plain[0] == ["tests/test_pkg.py"] and "--- a/pkg.py" in plain[1]


def test_pin_git_output_keeps_entries_already_set():
    env = paths.pin_git_output({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "a.b", "GIT_CONFIG_VALUE_0": "c"})
    assert env["GIT_CONFIG_KEY_0"] == "a.b" and env["GIT_CONFIG_KEY_1"] == "color.ui"
    assert env["GIT_CONFIG_COUNT"] == str(1 + len(paths.GIT_PINNED))
    assert paths.pin_git_output({"GIT_CONFIG_COUNT": "x"})["GIT_CONFIG_COUNT"] == str(len(paths.GIT_PINNED))


def test_the_dev_cli_pins_git_output_for_its_children(repo):
    _sh(repo, "git", "config", "color.ui", "always")
    code = "from devkit import cli, paths; import sys; sys.argv = ['dev', 'docs', '--help']\ntry:\n    cli.main()\nexcept SystemExit:\n    pass\nprint(paths.git('grep', '-l', 'X', '--', 'pkg.py'))"
    out = subprocess.run([sys.executable, "-c", code], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert out.strip().endswith("pkg.py") and "\x1b" not in out
