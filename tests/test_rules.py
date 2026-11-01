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


# Claude Code 2.1.316's verdict on each (paths:, file), measured with its bundled node-ignore module
# (docs/research/devkit-reuse-survey.md §2.2, scripts/b_globs.py).
CLAUDE_PROBES = [
    (["*.md"], "a.md", True),
    (["*.md"], "docs/a.md", True),  # gitignore: a pattern without a slash matches at any depth
    (["src/**"], "src/a/b.ts", True),
    (["src/**/*.ts"], "src/a.ts", True),
    (["src/*.ts"], "src/a/b.ts", False),
    (["**/*.ts"], "a.ts", True),
    ("src/**, lib/**", "lib/x.py", True),  # a comma-separated string, which the docs allow
    (["*.{ts,tsx}"], "web/a.tsx", True),
    (["src/*.{ts,tsx}"], "src/a.tsx", True),
    (["/README.md"], "README.md", True),
    (["docs/"], "docs/a.md", True),
    (["tests/**", "!tests/fixtures/**"], "tests/fixtures/a.py", True),  # node-ignore: no re-include under a parent
    (["photos \\[2024/**"], "photos [2024/a.jpg", True),
    ([".env*"], "config/.env.local", True),
    (["src"], "src/a.py", True),
    (["a/**/b"], "a/b", True),
    (["src/*"], "src/a/b.ts", True),  # a matched directory carries its contents
    (["*.MD"], "a.md", True),  # node-ignore ignores case
    (["devkit/*.py"], "devkit/rules.py", True),
    (["plugins/**/agents/*.md"], "plugins/harness-ops/agents/builder.md", True),
]
# Held out from the survey (the #416 audit), with node-ignore's answers from the same 2.1.316 module.
HELD_OUT_PROBES = [
    (["**/test_*.py"], "tests/test_a.py", True),
    (["docs/**/*.md"], "docs/a.md", True),
    (["*.py"], "a/b/c.py", True),
    (["foo/*/bar"], "foo/x/y/bar", False),
    (["?.md"], "docs/a.md", True),
    (["[abc].md"], "b.md", True),
    (["a/b"], "x/a/b", False),
    (["**/node_modules"], "web/node_modules/x.js", True),
    (["src/**/*.test.ts"], "src/a/b.test.ts", True),
    (["!*.md"], "a.md", False),
    (["#foo"], "#foo", False),
    (["\\#foo"], "#foo", True),
    ("*.md, !README.md", "README.md", False),
    (["docs/*"], "docs/sub/a.md", True),
    (["src/**/*"], "src/a.py", True),
    (["**/*.{md,mdx}"], "a/b.mdx", True),
    (["{src,lib}/**"], "lib/a.py", True),
    (["Makefile"], "sub/Makefile", True),
    (["*.Py"], "a.PY", True),
    ([".github/workflows/*.yml"], ".github/workflows/ci.yml", True),
    (["a/**/b/**"], "a/x/b/c", True),
    (["*/a.md"], "x/y/a.md", False),
    (["src/a.ts/"], "src/a.ts", False),
    (["tests/"], "tests/a.py", True),
    (["src/[!a]*.ts"], "src/b.ts", False),  # node-ignore reads `[!a]` differently: the one known disagreement
    (["*.py", "!keep.py"], "keep.py", False),
    (["docs/**", "!docs/keep.md"], "docs/keep.md", True),
    (["docs/*.md", "!docs/keep.md"], "docs/keep.md", False),
    (["src/", "!src/a.py"], "src/a.py", True),
    (["**/*.md"], "a/b/c/d.md", True),
    (["src/**/**/x"], "src/x", True),
    (["a/*.md"], "A/b.md", True),
    (["*"], "a/b", True),
    (["src/**/test/**"], "src/test/a.py", True),
    (["src/{a,b}/{c,d}.ts"], "src/b/d.ts", True),
]
KNOWN_MISSES = [(["src/[!a]*.ts"], "src/b.ts")]


def test_loads_agrees_with_claude_code_on_every_survey_probe():
    missed = [(v, rel) for v, rel, want in CLAUDE_PROBES if rules.loads(rel, {"paths": v}) != want]
    assert len(CLAUDE_PROBES) == 20
    assert missed == []


def test_loads_agrees_with_claude_code_on_the_held_out_probes_but_the_listed_misses():
    missed = [(v, rel) for v, rel, want in HELD_OUT_PROBES if rules.loads(rel, {"paths": v}) != want]
    assert len(HELD_OUT_PROBES) == 35
    assert missed == KNOWN_MISSES


def test_a_pattern_that_does_not_compile_is_dropped_not_raised():
    assert not rules.loads("foo", {"paths": ["foo\\"]})
    assert rules.loads("src/a.py", {"paths": ["src/**", "foo\\"]})
    assert not rules.matches("foo", "foo\\")


def test_check_warns_on_a_pattern_claude_code_drops(repo):
    _tree(repo, {".claude/rules/x.md": '---\npaths: ["src/**", "foo\\\\"]\n---\n- x (#1, 2026-01-01)\n'})
    ran = rules.check_rules(_base(repo))
    assert ran.problems == []
    assert ran.warnings == [".claude/rules/x.md: paths pattern `foo\\` does not compile; Claude Code drops it"]


def test_patterns_split_commas_expand_braces_and_cut_a_trailing_globstar():
    assert rules.patterns("src/**, lib/*.{ts,tsx}") == ["src", "lib/*.ts", "lib/*.tsx"]
    assert rules.patterns(["a/{b,c}/**", 3, " ", "{x}"]) == ["a/b", "a/c", "x"]  # non-strings dropped, as Claude does
    assert rules.patterns("{a,b},{c,d}") == ["a", "b", "c", "d"]


def test_brace_expansion_over_the_1000_pattern_budget_is_left_unexpanded():
    glob = "{a,b,c,d,e,f,g,h,i,j}" * 3  # 1,000 patterns: within budget
    assert len(rules.expand_braces(glob)) == 1000
    over = glob + "{k,l}"  # 2,000
    assert rules.expand_braces(over) == [over]


def test_brace_budget_is_shared_across_a_paths_value():
    glob = "{a,b,c,d,e,f,g,h,i,j}" * 3
    assert rules.patterns([glob, "x/{y,z}"])[-1] == "x/{y,z}"  # the first glob spent the budget


def test_paths_only_globstar_loads_unconditionally():
    assert rules.loads("anything/at/all.txt", {"paths": ["**"]})
    assert rules.loads("x.py", {"paths": "**/**"})


def test_check_passes_globs_claude_code_loads_that_full_match_missed(repo):
    text = '---\npaths: "*.md, src, /pyproject.toml, src/*, *.PY"\n---\n- x (#1, 2026-01-01)\n'
    _tree(repo, {".claude/rules/x.md": text})
    assert rules.check_rules(_base(repo)).problems == []


def test_check_fails_a_glob_claude_code_never_loads(repo):
    _tree(repo, {".claude/rules/x.md": '---\npaths: ["src/*.md", "docs/*.py"]\n---\n- x (#1, 2026-01-01)\n'})
    assert rules.check_rules(_base(repo)).problems == [
        ".claude/rules/x.md: paths glob `src/*.md` matches no file",
        ".claude/rules/x.md: paths glob `docs/*.py` matches no file",
    ]


def test_an_unanchored_glob_finds_gitignored_output_at_any_depth(repo):
    _tree(repo, {"build/deep/out.log": "", ".claude/rules/logs.md": '---\npaths: ["*.LOG", "build/deep/*.log"]\n---\n'})
    assert rules.check_rules(_base(repo)).problems == []


def test_disk_fallback_never_enters_environments_or_caches(repo, monkeypatch):
    pruned = ["node_modules/pkg", ".venv/lib", "env/lib", "src/__pycache__", ".git/objects"]
    _tree(repo, {f"{d}/{i}/x.log": "" for d in pruned for i in range(20)} | {"env/pyvenv.cfg": ""})
    _tree(repo, {"build/out.log": ""})
    entered = []
    real_walk = rules.os.walk

    def counting_walk(top, *a, **kw):
        for here, dirs, names in real_walk(top, *a, **kw):
            entered.append(Path(here).relative_to(repo).as_posix())
            yield here, dirs, names

    monkeypatch.setattr(rules.os, "walk", counting_walk)
    assert rules.glob_hits("*.log", [])  # gitignored build/out.log, found without the pruned trees
    assert not rules.glob_hits("*.nothing", [])
    tops = {e.split("/")[0] for e in entered} | {"/".join(e.split("/")[:2]) for e in entered}
    assert not tops & {"node_modules", ".venv", "env", ".git", "src/__pycache__"}
    assert "build" in tops


def test_add_picks_a_rules_file_scoped_by_a_comma_string(repo):
    _tree(repo, {".claude/rules/both.md": '---\npaths: "docs/**, src/app/**"\n---\n- x (#1, 2026-01-01)\n'})
    path, _ = rules.add("src/app/main.py", "use the factory", 9, TODAY)
    assert path == repo / ".claude/rules/both.md"


def test_add_skips_a_file_whose_negation_excludes_the_path(repo):
    _tree(repo, {".claude/rules/py.md": '---\npaths: ["*.py", "!lib.py"]\n---\n- x (#1, 2026-01-01)\n'})
    path, _ = rules.add("src/lib.py", "keep it pure", 9, TODAY)
    assert path == repo / ".claude/rules/src.md"
