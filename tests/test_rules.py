"""Path-scoped lessons: `uv run dev gotcha` writes one deduped line with provenance, and the gate's rules stage holds
every rule line to provenance and to paths that exist (#78)."""

import shlex
import subprocess
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import checks, config, gate, rules
from devkit.paths import git

TODAY = date(2026, 10, 31)


def _tree(tmp_path: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)


@pytest.fixture
def repo(monkeypatch, tmp_path):
    files = {"src/app/main.py": "", "src/lib.py": "", "src/conftest.py": "", "src/__init__.py": ""}
    _tree(tmp_path, {**files, "docs/guide.md": "", "pyproject.toml": "", ".gitignore": "build/\n"})
    _sh(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path)
    for mod in (checks, rules):
        monkeypatch.setattr(mod, "root", lambda: tmp_path)
    ls = ("ls-files", "--cached", "--others", "--exclude-standard")
    monkeypatch.setattr(rules, "tracked", lambda: [tmp_path / p for p in git(*ls, cwd=tmp_path).splitlines()])
    return tmp_path


def _sh(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True)


def _commit(repo: Path) -> None:
    _sh(repo, "add", "-A")
    _sh(repo, "commit", "-q", "-m", "x")


def _base(repo: Path) -> str:
    return git("rev-parse", "HEAD", cwd=repo)


def test_add_creates_a_scoped_rules_file_with_provenance(repo):
    path, wrote = rules.add("src/app/main.py", "Reset the cache before `src/lib.py` reloads", 78, TODAY)
    assert wrote and path == repo / ".claude/rules/src.md"
    assert path.read_text() == (
        '---\npaths:\n  - "src/**"\n---\n- Reset the cache before `src/lib.py` reloads (#78, 2026-10-31)\n'
    )
    assert rules.check_rules(_base(repo)).problems == []


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
    ran = rules.check_rules(_base(repo))
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
                "- `src/old.py` was renamed, and `docs/old.md` is gone, `README.md` is a bare name (#4, 2026-10-30)\n"
                "- placeholders `src/<name>.py`, globs `src/**`, urls `https://x/y` are skipped (#4, 2026-10-30)\n"
            )
        },
    )
    assert rules.check_rules(_base(repo)).problems == [
        ".claude/rules/src.md:5: names `src/old.py`, which does not exist",
        ".claude/rules/src.md:5: names `docs/old.md`, which does not exist",
    ]


def test_check_fails_a_paths_glob_that_matches_nothing(repo):
    # A renamed directory leaves a scoped rule that never loads again, silently.
    _tree(repo, {".claude/rules/old.md": "---\npaths:\n  - lib/**/*.py\n  - src/*.py\n---\n- x (#1, 2026-01-01)\n"})
    assert rules.check_rules(_base(repo)).problems == [".claude/rules/old.md: paths glob `lib/**/*.py` matches no file"]


def test_check_reports_frontmatter_that_does_not_parse(repo):
    _tree(repo, {".claude/rules/bad.md": "---\npaths: [\n---\n- x (#1, 2026-01-01)\n"})
    (problem,) = rules.check_rules(_base(repo)).problems
    assert problem.startswith(".claude/rules/bad.md: frontmatter does not parse")


def test_check_with_no_rules_passes_empty(repo):
    ran = rules.check_rules(_base(repo))
    assert ran.ok and ran.checked == 0


def test_check_is_a_gate_stage_given_the_merge_base():
    (stage,) = [s for s in gate.stages(False, {}, "BASE") if "check_rules" in s.__code__.co_names]
    assert stage.__code__.co_freevars == ("base",)
    assert [c.cell_contents for c in stage.__closure__] == ["BASE"]


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


def test_hand_written_rules_at_the_base_never_fail_provenance(repo):
    _tree(repo, {".claude/rules/src.md": '---\npaths: ["src/**"]\n---\n- written by hand\n- also by hand\n'})
    _commit(repo)
    base = _base(repo)
    assert rules.check_rules(base).problems == []
    (repo / ".claude/rules/src.md").write_text(
        '---\npaths: ["src/**"]\n---\n- written by hand\n- new, undated\n- also by hand\n- dated (#5, 2026-10-31)\n'
    )
    assert rules.check_rules(base).problems == [
        ".claude/rules/src.md:5: no provenance: end the line with (#<issue>, YYYY-MM-DD)"
    ]


def test_without_a_merge_base_provenance_is_a_warning_not_a_failure(repo):
    _tree(repo, {".claude/rules/src.md": '---\npaths: ["src/**"]\n---\n- undated\n'})
    ran = rules.check_rules(None)
    assert ran.problems == [] and ran.ok
    assert ran.warnings == ["no merge base with main: provenance of new rule lines not checked"]


def test_fenced_code_and_indented_sub_bullets_are_not_rule_lines(repo):
    _tree(
        repo,
        {
            ".claude/rules/src.md": (
                '---\npaths: ["src/**"]\n---\n- a rule (#5, 2026-10-31)\n  - its detail, undated\n'
                "    1. deeper\n```\n- example in a fence `src/gone.py`\n```\n~~~md\n* another\n~~~\n"
            )
        },
    )
    ran = rules.check_rules(_base(repo))
    assert ran.problems == [] and ran.checked == 1 + 1  # one glob, one rule line


@pytest.mark.parametrize("token", ["origin/main", "owner/repo", "text/html", "conftest.py", "__init__.py", "a/b.py"])
def test_tokens_that_are_not_repository_paths_pass(repo, token):
    _tree(repo, {".claude/rules/src.md": f'---\npaths: ["src/**"]\n---\n- see `{token}` (#5, 2026-10-31)\n'})
    assert rules.check_rules(_base(repo)).problems == []
    path, wrote = rules.add("src/lib.py", f"mind `{token}`", 5, TODAY)
    assert wrote and path == repo / ".claude/rules/src.md"


def test_path_check_skip_covers_rules_files(repo, monkeypatch):
    _tree(repo, {".claude/rules/src.md": '---\npaths: ["src/**"]\n---\n- `src/gone.py` (#5, 2026-10-31)\n'})
    assert rules.check_rules(_base(repo)).problems == [
        ".claude/rules/src.md:4: names `src/gone.py`, which does not exist"
    ]
    skipping = config.Config(path_check_skip=[".claude/rules/"])
    monkeypatch.setattr(rules.config, "get", lambda: skipping)
    assert rules.check_rules(_base(repo)).problems == []


def test_a_glob_over_gitignored_output_matches_files_on_disk(repo):
    _tree(repo, {"build/out.js": "", ".claude/rules/build.md": '---\npaths: ["build/**/*.js"]\n---\n'})
    assert rules.check_rules(_base(repo)).problems == []


@pytest.mark.parametrize("glob", ["src/!(lib).py", "!src/x/**", "src/[^l]*.go", "src/v{1..3}/*.py", "src/@(a|b).rs"])
def test_unmatched_glob_with_syntax_beyond_ours_warns(repo, glob):
    _tree(repo, {".claude/rules/x.md": f"---\npaths:\n  - '{glob}'\n---\n"})
    ran = rules.check_rules(_base(repo))
    assert ran.problems == []
    assert ran.warnings == [f".claude/rules/x.md: paths glob `{glob}` matches no file"]


def test_checkable_issue_is_shell_quoted(repo):
    text = rules.checkable_issue("src/my lib.py", "don't run `rm -rf $HOME`; ever", 78)
    argv = shlex.split(text)
    assert argv[argv.index("--title") + 1] == "check: don't run `rm -rf $HOME`; ever"
    assert argv[argv.index("--owns") + 1] == "src/my lib.py"


def test_add_refuses_a_lesson_over_25_words(repo):
    with pytest.raises(ValueError, match="at most 25 words"):
        rules.add("src/lib.py", " ".join(["w"] * 26), 5, TODAY)
    assert rules.add("src/lib.py", " ".join(["w"] * 25), 5, TODAY)[1]
