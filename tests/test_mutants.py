import hashlib
import subprocess
import textwrap
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import mutants
from devkit.mutants import Mutant, Outcome

ARID_SAMPLE = textwrap.dedent(
    '''\
    """Module docstring."""
    import os
    from pathlib import Path


    @decorator
    def f(
        a: int = 1,
    ) -> int:
        """Docstring."""
        log.info("x %s", a)
        logger.debug(
            "y",
        )
        print("z")
        warnings.warn("w")
        p.add_argument(
            "--flag", help="h"
        )
        s = sub.add_parser("cmd", help="h")
        if a and os.sep:
            return a + 1
        return Path(a) < 2


    class C:
        pass
    '''
)


def test_the_arid_filter_skips_imports_docstrings_logging_print_argparse_decorators_and_signatures():
    arid = mutants.arid_lines(ARID_SAMPLE)
    lines = ARID_SAMPLE.splitlines()
    live = {n for n in range(1, len(lines) + 1) if n not in arid and lines[n - 1].strip()}
    assert {lines[n - 1].strip() for n in live} == {"if a and os.sep:", "return a + 1", "return Path(a) < 2"}
    for text in ('"""Docstring."""', "import os", "@decorator", "a: int = 1,", '"--flag", help="h"', '"y",'):
        assert lines.index(next(x for x in lines if x.strip() == text)) + 1 in arid, text


def test_family_keeps_the_research_operators_and_drops_noisy_ones():
    assert mutants.family("core/ReplaceAndWithOr") == "LCR"
    assert mutants.family("core/ReplaceComparisonOperator_Lt_Gt") == "ROR"
    assert mutants.family("core/ReplaceComparisonOperator_Eq_Is") is None  # == -> is: mostly equivalent
    assert mutants.family("core/ReplaceComparisonOperator_Is_IsNot") == "ROR"
    assert mutants.family("core/ReplaceBinaryOperator_Add_Sub") == "AOR"
    assert mutants.family("core/AddNot") == "UOI"
    assert mutants.family("core/NumberReplacer") == "CRP"
    assert mutants.family("core/ZeroIterationForLoop") == "LOOP"
    for dropped in ("core/ExceptionReplacer", "core/RemoveDecorator", "core/VariableReplacer"):
        assert mutants.family(dropped) is None


def test_candidates_make_one_mutant_per_line_from_the_first_family_with_an_operator():
    src = "def f(a, b):\n    return a < b and a + 1 > 0\n\ndef g(a):\n    return a * 3\n"
    out = mutants.candidates("pkg/m.py", src, [2, 5])
    assert [(m.line, m.family) for m in out] == [(2, "LCR"), (5, "AOR")]
    assert out[0].before == "return a < b and a + 1 > 0"
    assert out[0].after == "return a < b or a + 1 > 0"
    assert out[0].mutated.splitlines()[1].strip() == out[0].after
    assert mutants.candidates("pkg/m.py", src, [2, 5]) == out  # stable


def test_candidates_leave_f_string_fields_and_comments_alone():
    src = 'def f(x):\n    msg = f"main {x[:10]}"\n    return x[:10]  # ten\n'
    out = mutants.candidates("m.py", src, [2, 3])
    assert [m.line for m in out] == [3]
    assert out[0].before == "return x[:10]"
    assert "#" not in out[0].after


def _mutant(path: str, line: int) -> Mutant:
    return Mutant(path, line, "ROR", "core/x", 0, "a", "b", "")


@given(
    st.sets(st.tuples(st.sampled_from(["a.py", "b/c.py", "d.py"]), st.integers(1, 400)), max_size=150),
    st.integers(0, 60),
    st.randoms(use_true_random=False),
)
def test_pick_caps_the_mutants_and_does_not_depend_on_input_order(keys, cap, rnd):
    pool = [_mutant(p, n) for p, n in keys]
    chosen, over = mutants.pick(pool, cap)
    assert len(chosen) == min(cap, len(pool))
    assert over == len(pool) - len(chosen)
    assert set(chosen) <= set(pool)
    assert chosen == sorted(chosen, key=lambda m: (m.path, m.line))
    shuffled = list(pool)
    rnd.shuffle(shuffled)
    assert mutants.pick(shuffled, cap) == (chosen, over)


def test_the_default_cap_is_forty_mutants():
    chosen, over = mutants.pick([_mutant("a.py", n) for n in range(1, 101)])
    assert (len(chosen), over) == (40, 60)


def test_changed_lines_keep_added_source_lines_and_drop_tests():
    diff = textwrap.dedent(
        """\
        diff --git a/pkg/m.py b/pkg/m.py
        --- a/pkg/m.py
        +++ b/pkg/m.py
        @@ -3,0 +4,2 @@
        +x = 1
        +y = 2
        @@ -9 +11 @@
        -old
        +new
        diff --git a/tests/test_m.py b/tests/test_m.py
        --- a/tests/test_m.py
        +++ b/tests/test_m.py
        @@ -1,0 +2 @@
        +assert 1
        diff --git a/gone.py b/gone.py
        --- a/gone.py
        +++ /dev/null
        @@ -1 +0,0 @@
        -x
        """
    )
    out = mutants.changed_lines(diff, {"new.py": 3, "conftest.py": 4, "notes.md": 2})
    assert out == {"pkg/m.py": {4, 5, 11}, "new.py": {1, 2, 3}}


def test_parse_run_reads_passes_and_summed_durations():
    stdout = (
        "0.20s call     tests/test_a.py::test_x[a b]\n0.05s setup    tests/test_a.py::test_x[a b]\n"
        "0.01s call     tests/test_a.py::test_y\n"
        "PASSED tests/test_a.py::test_x[a b]\nFAILED tests/test_a.py::test_y - assert 0\n"
    )
    passed, took = mutants.parse_run(stdout)
    assert passed == {"tests/test_a.py::test_x[a b]"}
    assert took == {"tests/test_a.py::test_x[a b]": pytest.approx(0.25), "tests/test_a.py::test_y": 0.01}


def test_tests_for_uses_passing_tests_and_import_time_lines():
    passed = {"t::a", "t::b"}
    line_map = {3: {"t::a", "t::failing"}, 5: {""}, 7: {"t::failing"}}
    assert mutants.tests_for(line_map, (3, 4, False), passed) == {"t::a"}
    assert mutants.tests_for(line_map, (5, 5, False), passed) == passed  # module level: run at import
    assert mutants.tests_for(line_map, (7, 7, False), passed) is None  # only a failing test runs it
    assert mutants.tests_for(line_map, (9, 9, False), passed) is None


def test_the_time_budget_stops_starting_mutants():
    chosen = [_mutant("a.py", 1), _mutant("a.py", 2)]
    out = mutants._run_all(chosen, {("a.py", 1): [], ("a.py", 2): []}, {}, [Path("/nonexistent")], 100.0, lambda: 99.0)
    assert [o.status for o in out] == ["not run", "not run"]


def test_report_lists_survivors_as_file_line_before_after_and_untested_lines():
    m = Mutant("pkg/m.py", 7, "ROR", "core/x", 0, "if a < b:", "if a > b:", "")
    text = mutants.report(
        [Outcome(m, "survived"), Outcome(_mutant("a.py", 1), "killed"), Outcome(_mutant("a.py", 2), "not run")],
        {"pkg/m.py": [3, 4, 5, 9]},
        12.0,
    )
    assert "mutants: 2 run, 1 killed, 0 timed out, 1 survived, 1 not run (time budget)" in text
    assert "  pkg/m.py:7 [ROR] if a < b: -> if a > b:" in text
    assert "  pkg/m.py:3-5,9" in text


BASE = "def add(a, b):\n    return a + b\n"
FEATURE = textwrap.dedent(
    '''\
    import logging

    log = logging.getLogger(__name__)


    def add(a, b):
        return a + b


    def clamp(x, lo, hi):
        """Clamp x into [lo, hi]."""
        log.debug("clamp %s", x)
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x


    def untested(x):
        return x * 2
    '''
)


def _tree_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts):
        h.update(f.relative_to(path).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


def test_a_run_reports_a_weakly_tested_comparison_and_untested_lines_without_touching_the_tree(
    tmp_path, monkeypatch, capsys
):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/calc.py").write_text(BASE)
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "7-clamp")
    (tmp_path / "pkg/calc.py").write_text(FEATURE)
    git("add", ".")
    git("commit", "-qm", "clamp")
    (tmp_path / "tests").mkdir()  # the test is uncommitted: the working tree is what runs
    (tmp_path / "tests/test_calc.py").write_text(
        "from pkg.calc import clamp\n\n\ndef test_clamp():\n    assert clamp(5, 0, 10) == 5\n    assert clamp(-1, 0, 10) == 0\n"
    )
    before = _tree_digest(tmp_path)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True).stdout
    monkeypatch.chdir(tmp_path)

    assert mutants.run(base="main", workers=1) == 0

    out = capsys.readouterr().out
    survivors = [ln for ln in out.splitlines() if ln.startswith("  pkg/calc.py:") and "->" in ln]
    # neither bound is tested at its edge, so a relational mutant of one of them survives
    assert any(
        ln.startswith(("  pkg/calc.py:13 [ROR] if x < lo: -> ", "  pkg/calc.py:15 [ROR] if x > hi: -> "))
        for ln in survivors
    ), out
    assert "1 files, 19 changed lines, 5 of them arid" in out  # import, two def headers, docstring, log.debug
    assert not any(":12 " in ln for ln in survivors)  # log.debug is arid
    assert "changed lines no test runs (2):\n  pkg/calc.py:16,21" in out  # `return hi` and `untested`
    assert _tree_digest(tmp_path) == before
    assert (
        subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True).stdout == status
    )


def test_without_cosmic_ray_it_says_how_to_install_and_exits_2(monkeypatch, capsys):
    import builtins

    real = builtins.__import__

    def no_cosmic(name, *args, **kwargs):
        if name.startswith("cosmic_ray"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_cosmic)
    assert mutants.run() == 2
    assert "agent-harness[mutants]" in capsys.readouterr().out


@pytest.mark.parametrize(("agent", "command"), [("builder", "uv run dev mutants"), ("reviewer", "dev mutants --rev")])
def test_the_builder_and_reviewer_run_mutants_on_hand_back_as_advice(agent, command):
    text = (Path(__file__).resolve().parents[1] / f"plugins/harness-ops/agents/{agent}.md").read_text()
    line = next(ln for ln in text.splitlines() if command in ln)
    assert "advisory" in line


def test_is_test_knows_the_pytest_file_names():
    for path in ("tests/x.py", "pkg/test_m.py", "pkg/m_test.py", "pkg/conftest.py"):
        assert mutants.is_test(path), path
    for path in ("pkg/m.py", "pkg/testing.py", "pkg/contest.py"):
        assert not mutants.is_test(path), path


def test_select_tests_puts_the_change_s_own_tests_first_then_the_heaviest_importers(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    files = {
        "test_own.py": "",
        "test_one.py": "from pkg import a\n",
        "test_two.py": "import pkg.a\nfrom pkg.b import f\n",
        "test_none.py": "import os\n",
    }
    for name, text in files.items():
        (tmp_path / "tests" / name).write_text(text)
    own = ["tests/test_own.py", "tests/test_deleted.py"]  # a deleted test is not run
    out = mutants.select_tests(tmp_path, ["pkg/a.py", "pkg/b.py"], own)
    assert out == ["tests/test_own.py", "tests/test_two.py", "tests/test_one.py"]
    monkeypatch.setattr(mutants, "TEST_FILES", 2)
    assert mutants.select_tests(tmp_path, ["pkg/a.py", "pkg/b.py"], own) == out[:2]


def test_merge_base_tries_origin_main_then_main_unless_a_base_is_named(tmp_path, monkeypatch):
    def git(*args):
        out = subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)
        return out.stdout.strip()

    who = ("-c", "user.email=t@example.com", "-c", "user.name=t")
    git("init", "-q", "-b", "main")
    git(*who, "commit", "-q", "--allow-empty", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "topic")
    git(*who, "commit", "-q", "--allow-empty", "-m", "tip")
    monkeypatch.chdir(tmp_path)
    assert mutants.merge_base(None, "HEAD") == ("main", base)
    assert mutants.merge_base("topic", "HEAD") == ("topic", git("rev-parse", "HEAD"))
    assert mutants.merge_base("nope", "HEAD") is None


def test_order_ids_runs_the_change_s_own_tests_first_then_the_fastest(monkeypatch):
    took = {"tests/a.py::slow": 3.0, "tests/a.py::quick": 0.1, "tests/b.py::fastest": 0.01}
    out = mutants.order_ids(took, took, {"tests/a.py"})
    assert out == ["tests/a.py::quick", "tests/a.py::slow", "tests/b.py::fastest"]
    monkeypatch.setattr(mutants, "IDS_PER_MUTANT", 1)
    assert mutants.order_ids(took, took, set()) == ["tests/b.py::fastest"]
