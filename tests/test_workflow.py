"""`dev pr`: the PR body comes from a file or, with `--summary -`, from stdin (subagents may not write report files)."""

import io
from types import SimpleNamespace

import pytest

from devkit import board, workflow


@pytest.fixture
def opened(monkeypatch):
    """Run `workflow.pr` with git and GitHub faked; returns the body each call sent to `gh pr create`."""
    sent = []
    monkeypatch.setattr(workflow, "git", lambda *a: "33-x" if a[0] == "branch" else "")
    monkeypatch.setattr(workflow, "_gate_passed", lambda: True)
    monkeypatch.setattr(board, "get", lambda n: SimpleNamespace(title="t"))
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "comment", lambda n, text: None)
    monkeypatch.setattr(board, "set_status", lambda n, s: None)

    def gh(*args, input=None):
        sent.append(input)
        return "https://example/pr/1\n"

    monkeypatch.setattr(board, "gh", gh)
    return sent


def test_summary_dash_reads_the_body_from_stdin(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Closes #33\n\nstdin body `$x` ünï\n"))
    assert workflow.pr(33, "-", draft=False) == "https://example/pr/1"
    assert opened == ["Closes #33\n\nstdin body `$x` ünï\n"]


def test_a_body_without_a_ref_line_closes_its_issue(opened, monkeypatch):
    """#38: a complete hand-back gets 'Closes #N', so the merge closes the issue (PRs said 'Refs' and reopened it)."""
    monkeypatch.setattr("sys.stdin", io.StringIO("just text\n"))
    workflow.pr(33, "-", draft=False)
    assert opened == ["Closes #33\n\njust text\n"]


def test_a_partial_hand_back_refs_its_issue(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("just text\n"))
    workflow.pr(33, "-", draft=False, partial=True)
    assert opened == ["Refs #33\n\njust text\n"]


def test_an_explicit_ref_line_wins_over_the_default(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Refs #33\n\nhalf done\n"))
    workflow.pr(33, "-", draft=False)
    assert opened == ["Refs #33\n\nhalf done\n"]


def test_empty_stdin_is_refused_before_anything_is_pushed(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  \n"))
    with pytest.raises(board.BoardError, match="stdin"):
        workflow.pr(33, "-", draft=False)
    assert opened == []


def test_summary_file_still_works(opened, tmp_path):
    f = tmp_path / "s.md"
    f.write_text("Refs #33\n\nfrom a file\n")
    workflow.pr(33, str(f), draft=False)
    assert opened == ["Refs #33\n\nfrom a file\n"]


def test_partial_with_a_closes_line_is_refused_before_anything_is_pushed(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Closes #33\n\nhalf done\n"))
    with pytest.raises(board.BoardError, match="--partial"):
        workflow.pr(33, "-", draft=False, partial=True)
    assert opened == []
