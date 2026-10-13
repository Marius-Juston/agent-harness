"""`dev cite-check` with no paths: which changed files under the research dir count as notes. Real git, no network."""

import subprocess

import pytest

from devkit import cli, config


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository on `main` with one commit; the test works on a branch off it."""
    monkeypatch.setattr(config, "get", lambda: config.Config())
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "x.txt").write_text("x\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "feature")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs/research").mkdir(parents=True)
    return tmp_path


def cite_check(capsys) -> tuple[int, str]:
    code = cli.run(cli.build().parse_args(["cite-check"]))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("committed", [True, False])
def test_branch_adding_only_readme_and_index_has_no_notes(repo, capsys, committed):
    for name in ("README.md", "INDEX.md"):
        (repo / "docs/research" / name).write_text("# About this directory\n")
    if committed:
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "template files")
    code, out = cite_check(capsys)
    assert code == 0
    assert "no research notes changed" in out


def test_real_note_next_to_readme_is_still_checked(repo, capsys):
    (repo / "docs/research/README.md").write_text("# About\n")
    (repo / "docs/research/topic.md").write_text("# Topic\n\nNo citations here.\n")
    code, out = cite_check(capsys)
    assert code == 1
    assert "in 1 note(s)" in out
