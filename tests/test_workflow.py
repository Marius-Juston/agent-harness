"""`dev pr`: the PR body comes from a file or, with `--summary -`, from stdin (subagents may not write report files).
The PR summary's `Reuse:` line (#173), which the completion gate checks."""

import io
import subprocess
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


PYPROJECT = '[project]\nname = "p"\ndependencies = ["rich>=13"]\n[dependency-groups]\ndev = ["pytest"]\n'


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True)


@pytest.fixture
def branch(tmp_path, monkeypatch):
    """A repository whose main has pyproject.toml, pkg/old.py and one research note, on issue branch 9-x. Returns a
    function that commits one file."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (repo / "pkg/old.py").write_text("".join(f"X{i} = {i}\n" for i in range(10)))
    (repo / "docs/research").mkdir(parents=True)
    (repo / "docs/research/http.md").write_text("# http clients\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "."],
        ["commit", "-q", "-m", "base"],
        ["checkout", "-qb", "9-x"],
    ):
        _git(repo, *args)
    monkeypatch.chdir(repo)

    def commit(path: str, text: str) -> None:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", path)

    commit.repo = repo
    return commit


def test_a_new_dependency_needs_a_reuse_line(branch):
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', '"rich>=13", "HTTPX[http2]>=0.27"'))
    assert workflow.reuse_needs("main") == ["dependency httpx"]
    assert "has no `Reuse:` line" in workflow.reuse_problem("Closes #9\n\nadded httpx")
    assert workflow.reuse_problem("Closes #9\n\nReuse: httpx pass (chosen), requests HOLD dormant") is None


def test_a_new_dependency_group_entry_counts_too(branch):
    branch("pyproject.toml", PYPROJECT.replace('["pytest"]', '["pytest", "hypothesis"]'))
    assert workflow.reuse_needs("main") == ["dependency hypothesis"]


def test_an_unparsable_pyproject_adds_no_dependency(branch):
    """A broken pyproject.toml fails the gate on its own; the reuse check does not guess at its names."""
    branch("pyproject.toml", PYPROJECT + "[broken\n")
    assert workflow.reuse_needs("main") == []


def test_a_new_non_test_module_needs_a_reuse_line(branch):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_needs("main") == ["module pkg/new.py"]
    assert "module pkg/new.py" in workflow.reuse_problem("Closes #9")


def test_neither_needs_no_line(branch):
    """Tests, edits to existing modules, renames, version bumps and non-Python files do not count."""
    branch("tests/test_new.py", "def test(): pass\n")
    branch("pkg/conftest.py", "")
    branch("pkg/old.py", "".join(f"X{i} = {i}\n" for i in range(9)))
    branch("pyproject.toml", PYPROJECT.replace("rich>=13", "rich>=14"))
    branch("docs/new.md", "text\n")
    _git(branch.repo, "mv", "pkg/old.py", "pkg/moved.py")
    _git(branch.repo, "commit", "-qm", "mv")
    assert workflow.reuse_needs("main") == []
    assert workflow.reuse_problem("Closes #9") is None


@pytest.mark.parametrize(
    "line",
    [
        "Reuse: httpx pass (chosen), requests HOLD dormant",
        "- **Reuse:** aiohttp FAIL (archived); none of the rest fit",
        "Reuse: docs/research/http.md weighed them",
        "Reuse: none: a 20-line parser over our own TOML keys",
        "reuse: None - glue code over devkit.board",
    ],
)
def test_each_documented_form_is_accepted(branch, line):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_problem(f"Closes #9\n\nwhat changed\n{line}\n") is None


@pytest.mark.parametrize(
    "line",
    ["Reuse: looked around", "Reuse: docs/research/missing.md", "Reuse: none:", "Reuse:", "We did not Reuse: anything"],
)
def test_a_line_in_none_of_the_forms_is_refused(branch, line):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_problem(f"Closes #9\n\n{line}\n") is not None


def test_the_forms_are_documented_in_one_place():
    for form in ("Reuse: httpx pass", "Reuse: docs/research/", "Reuse: none: <reason>"):
        assert form in workflow.REUSE_FORMS
