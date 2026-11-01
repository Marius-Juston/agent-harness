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


def kinds(found: list[str]) -> list[str]:
    """The kind of each finding: the text between `file:line: ` and the next `: `."""
    return [f.split(": ")[1] for f in found]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("    assert f(2) == 4", "    assert f(2) >= 0"),
        ("    assert f(2) == 4", "    assert f(2)"),
        ("    assert len(xs) == 3", "    assert len(xs) != 0"),
        ("    assert x is None", "    assert x in (None, 1)"),
    ],
)
def test_a_loosened_assert_is_flagged(before, after):
    found = weaken.findings(diff("tests/test_x.py", removed=[(4, before)], added=[(4, after)]))
    assert kinds(found) == ["loosened assert"]
    assert found[0].startswith("tests/test_x.py:4:")


def test_a_tightened_or_rewritten_assert_is_not_loosened():
    tightened = diff("tests/test_x.py", removed=[(4, "    assert f(2) >= 0")], added=[(4, "    assert f(2) == 4")])
    other_lhs = diff("tests/test_x.py", removed=[(4, "    assert f(2) == 4")], added=[(4, "    assert g(2) > 1")])
    assert weaken.findings(tightened) == []
    assert weaken.findings(other_lhs) == []


@pytest.mark.parametrize(
    "line", ["assert True", "assert 1", "assert x == x", "assert f() or True", "assert (f(), 'msg')", "assert not 0"]
)
def test_an_assert_that_cannot_fail_is_flagged(line):
    found = weaken.findings(diff("tests/test_x.py", removed=[(4, "    assert f(2) == 4")], added=[(4, f"    {line}")]))
    assert kinds(found) == ["trivial assert"]


def test_an_ordinary_new_assert_is_not_trivial():
    for line in ("assert x", "assert x == y", "assert f() or g()", "assert not x"):
        assert weaken.findings(diff("tests/test_x.py", added=[(4, f"    {line}")])) == []


@pytest.mark.parametrize(
    "handler",
    [
        "    except AssertionError:",
        "    except (ValueError, AssertionError):",
        "    except:",
        "    except BaseException as e:",
        "    with contextlib.suppress(AssertionError):",
    ],
)
def test_a_swallowed_assert_is_flagged(handler):
    found = weaken.findings(
        diff(
            "tests/test_x.py",
            removed=[(4, "    assert f(2) == 4")],
            added=[(4, "    try:"), (5, "        assert f(2) == 4"), (6, handler), (7, "        pass")],
        )
    )
    assert kinds(found) == ["swallowed assert"]
    assert found[0].startswith("tests/test_x.py:6:")


def test_catching_a_specific_error_is_not_swallowing():
    assert weaken.findings(diff("tests/test_x.py", added=[(6, "    except ValueError:")])) == []
    quoted = '    "with contextlib.suppress(AssertionError):",'  # regression: a test's own case strings
    assert weaken.findings(diff("tests/test_x.py", added=[(6, quoted)])) == []
    assert weaken.findings(diff("devkit/x.py", added=[(6, "    except AssertionError:")])) == []


def test_a_test_renamed_out_of_collection_is_flagged():
    found = weaken.findings(diff("tests/test_x.py", removed=[(5, "def test_f():")], added=[(5, "def check_f():")]))
    assert kinds(found) == ["removed test"]


def test_a_conftest_patch_or_collection_hook_is_flagged():
    autouse = diff(
        "tests/conftest.py",
        added=[
            (3, "@pytest.fixture(autouse=True)"),
            (4, "def _p(monkeypatch): monkeypatch.setattr('pkg.mod.f', lambda x: 4)"),
        ],
    )
    assert kinds(weaken.findings(autouse)) == ["conftest edit", "conftest edit"]
    hook = diff("conftest.py", added=[(9, "def pytest_collection_modifyitems(config, items):")])
    assert kinds(weaken.findings(hook)) == ["conftest edit"]
    helper = diff("tests/conftest.py", added=[(9, "def make_repo(tmp_path):")])
    assert weaken.findings(helper) == []


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("pyproject.toml", 'addopts = "-q"', "addopts = \"-q -k 'not test_f'\""),
        ("pytest.ini", "addopts = -q", "addopts = -q --deselect tests/test_x.py::test_f"),
        ("setup.cfg", "testpaths = tests", "testpaths = tests/unit"),
        ("pyproject.toml", "fail_under = 90", "fail_under = 50"),
        ("template/pyproject.toml.jinja", "xfail_strict = true", "xfail_strict = false"),
        (".coveragerc", "omit = a.py", "omit = *"),
    ],
)
def test_a_pytest_or_coverage_config_edit_is_flagged(path, before, after):
    found = weaken.findings(diff(path, removed=[(7, before)], added=[(7, after)]))
    assert kinds(found) == ["test config edit"]
    assert found[0].startswith(f"{path}:7:")


def test_a_removed_config_option_and_unrelated_config_edits():
    removed = weaken.findings(diff("pyproject.toml", removed=[(7, "xfail_strict = true")]))
    assert kinds(removed) == ["test config edit"]
    assert weaken.findings(diff("pyproject.toml", removed=[(2, 'version = "1"')], added=[(2, 'version = "2"')])) == []


def test_turning_xfail_strict_on_is_not_a_weakening():
    added = diff("pyproject.toml", added=[(8, "xfail_strict = true  # an xfail that passes fails the run")])
    assert weaken.findings(added) == []
    flipped = diff(
        "template/pyproject.toml.jinja", removed=[(8, "xfail_strict = false")], added=[(8, "xfail_strict = True")]
    )
    assert weaken.findings(flipped) == []
    assert kinds(weaken.findings(diff("pyproject.toml", added=[(8, "xfail_strict = false")]))) == ["test config edit"]


def test_an_edited_golden_file_is_flagged_once_and_a_new_one_is_not():
    edited = weaken.findings(
        diff("tests/fixtures/expected.json", removed=[(1, '{"f": 4}'), (2, '{"g": 1}')], added=[(1, '{"f": 5}')])
    )
    assert edited == ['tests/fixtures/expected.json:1: golden file edit: {"f": 4}']
    assert weaken.findings(diff("tests/fixtures/expected.json", added=[(1, '{"f": 5}')])) == []
    assert weaken.findings(diff("docs/expected.json", removed=[(1, '{"f": 4}')])) == []


def test_patching_the_function_an_assert_calls_is_flagged():
    body = "def test_f(monkeypatch):\n    assert mod.f(2) == 4\n"
    patched = diff("tests/test_x.py", added=[(2, "    monkeypatch.setattr(mod, 'f', lambda x: 4)")])
    found = weaken.findings(patched, read=lambda path: body if path == "tests/test_x.py" else "")
    assert kinds(found) == ["patch of code under test"]
    for line in (
        "    monkeypatch.setattr('pkg.mod.f', lambda x: 4)",
        "    mocker.patch.object(mod, 'f')",
        "    with mock.patch('pkg.mod.f', return_value=4):",
    ):
        found = weaken.findings(diff("tests/test_x.py", added=[(2, line)]), read=lambda _: body)
        assert kinds(found) == ["patch of code under test"]


def test_patching_a_seam_no_assert_calls_is_not_flagged():
    body = "def test_f(monkeypatch):\n    assert mod.f(2) == 4\n"
    seam = diff("tests/test_x.py", added=[(2, "    monkeypatch.setattr(mod, 'root', lambda: tmp)")])
    assert weaken.findings(seam, read=lambda _: body) == []


def test_fewer_parametrize_cases_are_flagged():
    fewer = diff(
        "tests/test_x.py",
        removed=[(3, "@pytest.mark.parametrize('x', [1, 2, 3])")],
        added=[(3, "@pytest.mark.parametrize('x', [1])")],
    )
    assert kinds(weaken.findings(fewer)) == ["fewer parametrize cases"]
    more = diff(
        "tests/test_x.py",
        removed=[(3, "@pytest.mark.parametrize('x', [1, 2])")],
        added=[(3, "@pytest.mark.parametrize('x', [1, 2, 3])")],
    )
    assert weaken.findings(more) == []
    param = diff("tests/test_x.py", removed=[(5, "        pytest.param(3, 9, id='three'),")])
    assert kinds(weaken.findings(param)) == ["removed parametrize case"]


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


def test_the_gate_reads_test_config_goldens_and_the_test_file(repo, monkeypatch):
    path, _ = repo
    (path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = "-q"\n')
    (path / "tests/expected.json").write_text('{"f": 4}\n')
    (path / "tests/test_x.py").write_text("def test_f():\n    assert mod.f(2) == 4\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "more"], cwd=path, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()
    (path / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = \"-q -k 'not test_f'\"\n")
    (path / "tests/expected.json").write_text('{"f": 5}\n')
    (path / "tests/test_x.py").write_text(
        "def test_f(monkeypatch):\n    monkeypatch.setattr(mod, 'f', lambda x: 4)\n    assert mod.f(2) == 4\n"
    )
    fake_issue(monkeypatch, "## DONE WHEN")
    ran = weaken.check_weakening(base)
    assert not ran.ok
    assert ran.checked == 3
    assert {p.split(": ")[1] for p in ran.problems[:-1]} == {
        "test config edit",
        "golden file edit",
        "patch of code under test",
    }


def test_weaken_parse_stays_importable_and_is_the_diffparse_parser():
    """#449 moved the diff parser to devkit.diffparse; external importers of devkit.weaken.parse keep working."""
    from devkit import diffparse
    from devkit.weaken import parse

    assert parse is diffparse.parse
    (f,) = parse(diff("x.py", [(3, "old")], [(3, "new")]))
    assert (f.old, f.new) == ("x.py", "x.py")
    assert f.removed == [diffparse.Line("x.py", 3, "old")] and f.added == [diffparse.Line("x.py", 3, "new")]
