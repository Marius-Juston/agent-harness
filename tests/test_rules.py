"""Path-scoped lessons: `uv run dev gotcha` writes one deduped line with provenance, and the gate's rules stage holds
every rule line to provenance and to paths that exist (#78)."""

from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import checks, gate, rules

TODAY = date(2026, 10, 31)


def _tree(tmp_path: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)


@pytest.fixture
def repo(monkeypatch, tmp_path):
    _tree(tmp_path, {"src/app/main.py": "", "src/lib.py": "", "docs/guide.md": "", "pyproject.toml": ""})
    for mod in (checks, rules):
        monkeypatch.setattr(mod, "root", lambda: tmp_path)
    monkeypatch.setattr(rules, "tracked", lambda: sorted(p for p in tmp_path.rglob("*") if p.is_file()))
    return tmp_path


def test_add_creates_a_scoped_rules_file_with_provenance(repo):
    path, wrote = rules.add("src/app/main.py", "Reset the cache before `src/lib.py` reloads", 78, TODAY)
    assert wrote and path == repo / ".claude/rules/src.md"
    assert path.read_text() == (
        '---\npaths:\n  - "src/**"\n---\n- Reset the cache before `src/lib.py` reloads (#78, 2026-10-31)\n'
    )
    assert rules.check_rules().problems == []


def test_add_appends_to_the_most_specific_matching_file(repo):
    _tree(
        repo,
        {
            ".claude/rules/any.md": '---\npaths: ["**/*.py"]\n---\n- old (#1, 2026-01-01)',  # no trailing newline
            ".claude/rules/app.md": "---\npaths:\n  - src/app/**/*.py\n---\n- older (#2, 2026-01-02)\n",
        },
    )
    path, wrote = rules.add("src/app/main.py", "use the factory", 9, TODAY)
    assert wrote and path == repo / ".claude/rules/app.md"
    assert path.read_text().endswith("- older (#2, 2026-01-02)\n- use the factory (#9, 2026-10-31)\n")
    path, _ = rules.add("src/lib.py", "keep it pure", 9, TODAY)
    assert path == repo / ".claude/rules/any.md"
    assert path.read_text().endswith("- old (#1, 2026-01-01)\n- keep it pure (#9, 2026-10-31)\n")


def test_add_dedupes_a_lesson_already_there(repo):
    rules.add("src/lib.py", "Keep it pure.", 9, TODAY)
    before = (repo / ".claude/rules/src.md").read_text()
    path, wrote = rules.add("src/lib.py", "  keep   IT pure ", 12, date(2026, 11, 2))
    assert not wrote and path.read_text() == before


def test_add_never_picks_an_unscoped_rules_file(repo):
    _tree(repo, {".claude/rules/general.md": "- loads every session (#1, 2026-01-01)\n"})
    path, _ = rules.add("docs/guide.md", "link the issue", 3, TODAY)
    assert path == repo / ".claude/rules/docs.md"
    assert '  - "docs/**"' in path.read_text()


def test_add_scopes_a_top_level_file_to_itself(repo):
    path, _ = rules.add("pyproject.toml", "bump all three versions together", 3, TODAY)
    assert path == repo / ".claude/rules/pyproject-toml.md"
    assert '  - "pyproject.toml"' in path.read_text()


def test_add_does_not_overwrite_a_same_named_file_that_does_not_match(repo):
    _tree(repo, {".claude/rules/src.md": '---\npaths: ["src/other/**"]\n---\n- x (#1, 2026-01-01)\n'})
    path, _ = rules.add("src/lib.py", "keep it pure", 3, TODAY)
    assert path == repo / ".claude/rules/src-2.md"
    assert (repo / ".claude/rules/src.md").read_text().endswith("- x (#1, 2026-01-01)\n")


@pytest.mark.parametrize(
    ("path", "lesson", "error"),
    [
        ("src/gone.py", "a lesson", "does not exist"),
        ("src/lib.py", "", "empty"),
        ("src/lib.py", "two\nlines", "one line"),
        ("src/lib.py", "see `src/gone.py` first", "src/gone.py"),
        ("src/lib.py", "already dated (#3, 2026-10-31)", "provenance"),
        ("../outside.py", "a lesson", "inside the repository"),
    ],
)
def test_add_refuses_a_line_the_gate_would_fail(repo, path, lesson, error):
    with pytest.raises(ValueError, match=error):
        rules.add(path, lesson, 3, TODAY)
    assert not (repo / ".claude/rules").exists()


def test_check_fails_a_line_without_provenance(repo):
    _tree(
        repo,
        {
            ".claude/rules/src.md": (
                '---\npaths: ["src/**"]\n---\n# Heading is not a rule\n\n'
                "- dated (#4, 2026-10-30)\n- undated\n* no issue (2026-10-30)\n1. bad date (#4, 2026-13-01)\n"
            )
        },
    )
    ran = rules.check_rules()
    assert ran.checked == 4 + 1  # four rule lines and one glob
    assert ran.problems == [
        ".claude/rules/src.md:7: no provenance: end the line with (#<issue>, YYYY-MM-DD)",
        ".claude/rules/src.md:8: no provenance: end the line with (#<issue>, YYYY-MM-DD)",
        ".claude/rules/src.md:9: 2026-13-01 is not a date",
    ]


def test_check_fails_a_line_naming_a_missing_path(repo):
    _tree(
        repo,
        {
            ".claude/rules/src.md": (
                '---\npaths: ["src/**"]\n---\n'
                "- `src/lib.py` and `pyproject.toml` exist; `uv run dev check` is a command (#4, 2026-10-30)\n"
                "- `src/old.py` was renamed, and `README.md` is gone (#4, 2026-10-30)\n"
                "- placeholders `src/<name>.py`, globs `src/**`, urls `https://x/y` are skipped (#4, 2026-10-30)\n"
            )
        },
    )
    assert rules.check_rules().problems == [
        ".claude/rules/src.md:5: names `src/old.py`, which does not exist",
        ".claude/rules/src.md:5: names `README.md`, which does not exist",
    ]


def test_check_fails_a_paths_glob_that_matches_nothing(repo):
    # A renamed directory leaves a scoped rule that never loads again, silently.
    _tree(repo, {".claude/rules/old.md": "---\npaths:\n  - lib/**/*.py\n  - src/*.py\n---\n- x (#1, 2026-01-01)\n"})
    assert rules.check_rules().problems == [".claude/rules/old.md: paths glob `lib/**/*.py` matches no file"]


def test_check_reports_frontmatter_that_does_not_parse(repo):
    _tree(repo, {".claude/rules/bad.md": "---\npaths: [\n---\n- x (#1, 2026-01-01)\n"})
    (problem,) = rules.check_rules().problems
    assert problem.startswith(".claude/rules/bad.md: frontmatter does not parse")


def test_check_with_no_rules_passes_empty(repo):
    ran = rules.check_rules()
    assert ran.ok and ran.checked == 0


def test_check_is_a_gate_stage():
    assert rules.check_rules in gate.stages(False, {}, None)


def test_checkable_lesson_gets_an_issue_template_not_a_rule(repo):
    text = rules.checkable_issue("src/lib.py", "never call `requests` directly", 78)
    assert text.startswith("uv run dev board new --type build ")
    assert "hook or test" in text and "--owns src/lib.py" in text and "--refs 78" in text
    assert not (repo / ".claude/rules").exists()


def test_issue_from_branch():
    assert rules.issue_from_branch("78-gotcha-command") == 78
    assert rules.issue_from_branch("main") is None


@given(st.text(alphabet=st.characters(blacklist_categories=["Cs"]), max_size=40))
def test_normalize_is_idempotent_and_ignores_case_and_spacing(text):
    once = rules.normalize(text)
    assert rules.normalize(once) == once
    assert rules.normalize("  " + text.upper() + " . ") == rules.normalize(text.upper())


@given(st.lists(st.sampled_from(["a", "b", "{x,y}", "/", "*", ","]), max_size=8))
def test_expand_braces_leaves_no_brace_group(parts):
    for glob in rules.expand_braces("".join(parts)):
        assert "{x,y}" not in glob


def test_dev_gotcha_writes_then_dedupes(repo, monkeypatch, capsys):
    from devkit import cli

    monkeypatch.setattr(cli, "root", lambda: repo)
    argv = ["gotcha", "src/lib.py", "keep", "it", "pure", "--issue", "7"]
    assert cli.run(cli.build().parse_args(argv)) == 0
    assert cli.run(cli.build().parse_args(argv)) == 0
    assert capsys.readouterr().out.splitlines() == ["added to .claude/rules/src.md", "already in .claude/rules/src.md"]
    assert cli.run(cli.build().parse_args(["gotcha", "src/gone.py", "x", "--issue", "7"])) == 1
    assert "not written: src/gone.py does not exist" in capsys.readouterr().out
