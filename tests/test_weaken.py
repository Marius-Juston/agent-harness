import subprocess

import pytest

from devkit import board, weaken

OWNER = "octo"
# Spelled in pieces so this file's own diff does not add a skip marker.
SKIP_MARK = "@pytest.mark." + "skip"
XFAIL_CALL = "pytest." + "xfail("


def diff(path: str, removed: list[tuple[int, str]] = (), added: list[tuple[int, str]] = ()) -> str:
    lines = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    for n, text in removed:
        lines += [f"@@ -{n} +{n},0 @@", f"-{text}"]
    for n, text in added:
        lines += [f"@@ -{n},0 +{n} @@", f"+{text}"]
    return "\n".join(lines) + "\n"


def test_a_removed_assert_is_flagged_with_file_and_line():
    (finding,) = weaken.findings(diff("tests/test_x.py", removed=[(12, "    assert f(2) == 4")]))
    assert finding.startswith("tests/test_x.py:12: removed assert")


def test_added_skip_and_xfail_are_flagged_with_file_and_line():
    found = weaken.findings(diff("tests/test_x.py", added=[(3, SKIP_MARK), (9, f"    {XFAIL_CALL}'flaky')")]))
    assert [f.split(": ")[0] for f in found] == ["tests/test_x.py:3", "tests/test_x.py:9"]


def test_a_removed_test_function_is_flagged():
    (finding,) = weaken.findings(diff("tests/test_x.py", removed=[(5, "def test_parses():")]))
    assert "tests/test_x.py:5: removed test" in finding


def test_an_edited_or_moved_assert_is_not_a_removal():
    edited = diff("tests/test_x.py", removed=[(4, "    assert f(2) == 4")], added=[(4, "    assert f(2) == 4.0")])
    moved = diff("tests/test_a.py", removed=[(4, "    assert f(2) == 4")]) + diff(
        "tests/test_b.py", added=[(8, "        assert f(2) == 4")]
    )
    assert weaken.findings(edited) == []
    assert weaken.findings(moved) == []


def test_non_test_code_comments_and_non_python_files_are_ignored():
    assert weaken.findings(diff("devkit/x.py", removed=[(1, "    assert ok")])) == []
    assert weaken.findings(diff("tests/test_x.py", added=[(1, f"# {SKIP_MARK} was here")])) == []
    assert weaken.findings(diff("docs/x.md", added=[(1, SKIP_MARK)])) == []


def test_approval_needs_the_owners_marker():
    marker = "weakening-ok: the spec changed in #12"
    assert weaken.approved({"author": {"login": "Octo"}, "body": marker, "comments": []}, OWNER)
    assert weaken.approved(
        {"author": {"login": "bot"}, "comments": [{"author": {"login": OWNER}, "body": marker}]}, OWNER
    )
    assert not weaken.approved({"author": {"login": "bot"}, "body": marker, "comments": []}, OWNER)
    assert not weaken.approved({"author": {"login": OWNER}, "body": "weakening-ok:", "comments": []}, OWNER)
    assert not weaken.approved({"author": {"login": OWNER}, "body": marker}, None)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("def test_f():\n    assert 1 + 1 == 2\n")
    run("add", ".")
    run("commit", "-qm", "base")
    run("checkout", "-qb", "55-weaken")
    monkeypatch.setattr(weaken, "root", lambda: tmp_path)
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    monkeypatch.setattr(weaken, "repo_owner", lambda: OWNER)
    monkeypatch.setattr(board, "repo", lambda: "octo/r")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    return tmp_path, base


def fake_issue(monkeypatch, body: str):
    monkeypatch.setattr(
        board, "gh", lambda *a, **k: f'{{"author": {{"login": "{OWNER}"}}, "body": "{body}", "comments": []}}'
    )


def test_the_gate_fails_a_weakening_diff_and_passes_it_once_approved(repo, monkeypatch):
    path, base = repo
    (path / "tests/test_x.py").write_text(f"{SKIP_MARK}\ndef test_f():\n    pass\n")
    fake_issue(monkeypatch, "## DONE WHEN")
    ran = weaken.check_weakening(base)
    assert not ran.ok
    assert any(p.startswith("tests/test_x.py:2: removed assert") for p in ran.problems)
    assert any(p.startswith("tests/test_x.py:1: added skip/xfail") for p in ran.problems)
    assert "board block" in ran.problems[-1]
    fake_issue(monkeypatch, "weakening-ok: the test asserted the old spec")
    ran = weaken.check_weakening(base)
    assert ran.ok
    assert len(ran.warnings) == 2


def test_the_gate_passes_an_unweakened_diff_without_reading_the_issue(repo, monkeypatch):
    path, base = repo
    (path / "tests/test_x.py").write_text("def test_f():\n    assert 1 + 1 == 2\n    assert 2 * 2 == 4\n")
    monkeypatch.setattr(board, "gh", lambda *a, **k: pytest.fail("no finding needs no issue"))
    ran = weaken.check_weakening(base)
    assert ran.ok
    assert ran.checked == 1


def test_an_unreadable_issue_fails_closed(repo, monkeypatch):
    path, base = repo
    (path / "tests/test_x.py").write_text("def test_f():\n    pass\n")

    def down(*a, **k):
        raise board.BoardError("gh: not logged in")

    monkeypatch.setattr(board, "gh", down)
    ran = weaken.check_weakening(base)
    assert not ran.ok
    assert "#55 could not be read" in ran.problems[-1]
