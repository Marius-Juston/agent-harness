"""Consolidation review trigger and report (#113; design: docs/research/architecture-consolidation.md §8)."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import cli, config, consolidation, workflow

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        out = subprocess.run(["git", *args], cwd=self.path, env=ENV, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def commit(self, files: dict[str, str | None], msg: str = "change") -> str:
        for name, text in files.items():
            p = self.path / name
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")


def code(n: int, tag: str = "x") -> str:
    """n code lines, plus comments and blank lines that must not count."""
    return "# comment\n\n" + "".join(f"{tag}{k} = {k}\n" for k in range(n))


def cfg(**over) -> dict:
    return {**config.CONSOLIDATION, **over}


@pytest.fixture
def repo(tmp_path) -> Repo:
    return Repo(tmp_path)


# ---------------------------------------------------------------- config
def test_config_defaults_are_the_research_note_values():
    c = config.Config().consolidation
    assert c["growth"] == 2.0 and c["growth_min_loc"] == 5000 and c["fp_commits"] == 100
    assert c["dormant_k"] == 30 and c["dormant_share"] == 0.05
    assert (c["churny_loc"], c["churny_commits"]) == (1000, 30)
    assert c["lazy_cycle"] == 10 and c["lazy_share"] == 0.10 and c["lazy_min_imports"] == 50
    assert any("migrations" in g for g in c["exclude"])


def test_config_merges_a_partial_table_over_the_defaults():
    c = config.parse({"consolidation": {"fp_commits": 40, "exclude": ["alembic/*"]}}).consolidation
    assert c["fp_commits"] == 40 and c["exclude"] == ["alembic/*"] and c["growth"] == 2.0


@pytest.mark.parametrize(
    "table",
    [{"fp_comits": 40}, {"growth": "2"}, {"fp_commits": 4.5}, {"fp_commits": True}, {"exclude": "x/*"}],
)
def test_config_rejects_unknown_or_mistyped_consolidation_keys(table):
    with pytest.raises(config.ConfigError, match="consolidation"):
        config.parse({"consolidation": table})


def test_config_accepts_an_int_where_a_float_is_expected():
    assert config.parse({"consolidation": {"growth": 3}}).consolidation["growth"] == 3


# ---------------------------------------------------------------- signals
def test_signals_count_code_lines_only_and_honour_exclude_globs(repo):
    repo.commit({"app/a.py": code(10), "README.md": "# t\nwords\n", "migrations/v1.py": code(50)})
    s = consolidation.signals(repo.path, "HEAD", cfg())
    assert s["code_loc"] == 10  # comments, blanks, docs and the excluded migrations tree do not count
    s = consolidation.signals(repo.path, "HEAD", cfg(exclude=[]))
    assert s["code_loc"] == 60


def test_orphaned_dormant_unit_is_a_smell_unless_an_active_unit_imports_it(repo):
    repo.commit({"old/pkg/__init__.py": "", "old/pkg/core.py": code(100), "app/main/run.py": code(5)})
    for k in range(3):
        repo.commit({"app/main/run.py": code(5 + k)})
    c = cfg(dormant_k=2, dormant_share=0.5)
    s = consolidation.signals(repo.path, "HEAD", c)
    assert "dormant" in s["smells"] and s["orphaned_dormant_units"] == ["old/pkg"]
    repo.commit({"app/main/run.py": "import old.pkg.core\n" + code(8)})
    repo.commit({"app/main/run.py": "import old.pkg.core\n" + code(9)})
    s = consolidation.signals(repo.path, "HEAD", c)
    assert "dormant" not in s["smells"] and s["orphaned_dormant_units"] == []


def test_import_cycle_and_function_level_imports_are_smells(repo):
    a = "import pkg.b\n\ndef f():\n    import json\n    import os\n    return json, os\n"
    b = "from pkg import a\n"
    repo.commit({"pkg/__init__.py": "", "pkg/a.py": a, "pkg/b.py": b})
    s = consolidation.signals(repo.path, "HEAD", cfg(lazy_cycle=2, lazy_min_imports=3, lazy_share=0.5))
    assert s["largest_import_cycle"] == 2 and s["import_cycle"] == ["pkg.a", "pkg.b"]
    assert s["indented_import_share"] == 0.5
    assert {"import-cycle", "lazy-imports"} <= set(s["smells"])
    s = consolidation.signals(repo.path, "HEAD", cfg(lazy_cycle=3, lazy_min_imports=50))
    assert not {"import-cycle", "lazy-imports"} & set(s["smells"])


def test_large_churny_file_is_a_smell_but_not_in_tests(repo):
    for k in range(3):
        repo.commit({"big.py": code(20 + k), "tests/test_big.py": code(20 + k, "t")})
    s = consolidation.signals(repo.path, "HEAD", cfg(churny_loc=10, churny_commits=3))
    assert s["churny_files"] == ["big.py"] and "churny" in s["smells"]


# ---------------------------------------------------------------- the trigger
def test_growth_from_the_root_is_due_and_names_the_reason(repo):
    repo.commit({"a.py": code(10)})
    repo.commit({"a.py": code(30)})
    r = consolidation.due(repo.path, cfg(growth_min_loc=15))
    assert r["review_due"] and r["base_source"] == "root"
    assert "code grew 10 -> 30 LOC" in r["why"]


def test_growth_below_the_floor_or_the_factor_is_not_due(repo):
    repo.commit({"a.py": code(10)})
    repo.commit({"a.py": code(30)})
    assert not consolidation.due(repo.path, cfg(growth_min_loc=25))["review_due"]
    assert not consolidation.due(repo.path, cfg(growth_min_loc=5, growth=4.0))["review_due"]


def test_a_review_trailer_resets_the_trigger(repo):
    repo.commit({"a.py": code(10)})
    repo.commit({"a.py": code(30)})
    sha = repo.commit({}, "review done\n\nConsolidation-Review: #7")
    r = consolidation.due(repo.path, cfg(growth_min_loc=15))
    assert not r["review_due"] and r["base"] == sha[:9] and r["base_source"] == "review"
    repo.commit({"a.py": code(70)})
    assert consolidation.due(repo.path, cfg(growth_min_loc=15))["review_due"]


def test_only_a_first_parent_trailer_block_resets_the_trigger(repo):
    root = repo.commit({"a.py": code(10)})
    repo.git("checkout", "-q", "-b", "feature")
    repo.commit({"b.py": code(5)}, "a worker writes it\n\nConsolidation-Review: #9")  # on the merged branch only
    repo.commit({}, "Consolidation-Review: #8\n\nprose after it, so not a trailer block")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "--no-edit", "feature")
    assert consolidation.last_review(repo.path, cfg()) == (root, "root")
    repo.git("checkout", "-q", "-b", "f2")
    repo.commit({"c.py": code(5)})
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge pull request #3\n\nConsolidation-Review: #12", "f2")
    assert consolidation.last_review(repo.path, cfg()) == (repo.git("rev-parse", "HEAD"), "review")


def test_integrate_writes_the_trailer_into_the_merge_commit(monkeypatch):
    from devkit import board

    calls = []
    monkeypatch.setattr(board, "gh", lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(workflow, "_remote_shas", lambda n: ("h1", "m1"))
    assert workflow._merge_if_unchanged(7, "h1", "m1", review=138) is None
    assert calls[-1][calls[-1].index("--body") + 1] == "Consolidation-Review: #138"
    assert workflow._merge_if_unchanged(7, "h1", "m1") is None
    assert "Consolidation-Review" not in calls[-1][calls[-1].index("--body") + 1]  # #292: squash always sets a body
    args = cli.build().parse_args(["integrate", "5", "--consolidation-review", "138"])
    assert args.consolidation_review == 138


def test_first_parent_commits_since_the_review_trigger_it(repo):
    repo.commit({"a.py": code(10)}, "start\n\nConsolidation-Review: adopted")
    for k in range(3):
        repo.commit({"a.py": code(11 + k)})
    assert consolidation.due(repo.path, cfg(fp_commits=3))["why"] == ["3 first-parent commits since the last review"]
    assert not consolidation.due(repo.path, cfg(fp_commits=4))["review_due"]


def test_a_smell_present_at_the_review_is_not_new(repo):
    for k in range(3):
        repo.commit({"big.py": code(20 + k)})
    repo.commit({}, "review\n\nConsolidation-Review: #1")
    repo.commit({"other.py": code(2)})
    c = cfg(churny_loc=10, churny_commits=3)
    assert not consolidation.due(repo.path, c)["review_due"]
    for k in range(3):
        repo.commit({"big2.py": code(20 + k)})
    assert consolidation.due(repo.path, c)["why"] == ["more large churny files: big2.py"]


def test_never_reviewed_every_standing_smell_counts_as_new(repo):
    for k in range(3):
        repo.commit({"big.py": code(20 + k)})
    r = consolidation.due(repo.path, cfg(churny_loc=10, churny_commits=3))
    assert "new smell: churny (big.py)" in r["why"]


def test_adoption_commit_is_the_base_when_there_is_no_review(repo):
    repo.commit({"a.py": code(5000)})
    sha = repo.commit({".copier-answers.yml": "_commit: v1\n", "b.py": code(5)}, "adopt the harness")
    repo.commit({"b.py": code(10)})
    r = consolidation.due(repo.path, cfg())
    assert r["base"] == sha[:9] and r["base_source"] == "adoption" and not r["review_due"]


def test_configured_since_wins_over_trailers(repo):
    first = repo.commit({"a.py": code(10)})
    repo.commit({"a.py": code(30)}, "x\n\nConsolidation-Review: #2")
    r = consolidation.due(repo.path, cfg(since=first, growth_min_loc=15))
    assert r["base"] == first[:9] and r["base_source"] == "config" and r["review_due"]


def test_orient_line_only_when_due(repo):
    repo.commit({"a.py": code(10)})
    assert consolidation.orient_line(repo.path, cfg(growth_min_loc=15)) is None
    repo.commit({"a.py": code(30)})
    line = consolidation.orient_line(repo.path, cfg(growth_min_loc=15))
    assert line is not None and "code grew 10 -> 30 LOC" in line and "harness-ops:architect" in line


def test_the_trailer_search_reads_at_most_max_walk_commits(monkeypatch, repo):
    repo.commit({"a.py": code(10)}, "x\n\nConsolidation-Review: #1")
    for k in range(3):
        repo.commit({"a.py": code(11 + k)})
    assert consolidation.last_review(repo.path, cfg())[1] == "review"
    monkeypatch.setattr(consolidation, "MAX_WALK", 2)
    assert consolidation.last_review(repo.path, cfg())[1] == "root"


def test_orient_skips_the_trigger_outside_a_git_repository(tmp_path):
    assert consolidation.orient_line(tmp_path, cfg()) is None


# ---------------------------------------------------------------- the report
def test_report_lists_parse_errors_growth_and_command_overlap(repo):
    repo.commit({"app/a.py": code(10)}, "start\n\nConsolidation-Review: #1")
    cli_src = 'import argparse\np = argparse.ArgumentParser()\ns = p.add_subparsers()\ns.add_parser("orient")\n'
    repo.commit({"tool/cli.py": cli_src, "tool/new.py": code(40), "app/bad.py": "def f(:\n    pass\n"})
    r = consolidation.report(repo.path, {"orient", "board"}, cfg())
    assert r["parse_errors"] == {"count": 1, "paths": ["app/bad.py"]}
    assert r["growth_by_dir"][0][:3] == ["tool", 0, 44]
    assert r["command_overlap"] == {"tool/cli.py": ["orient"]}
    text = consolidation.render(r)
    assert "WARNING: 1 Python file(s) failed to parse" in text and "app/bad.py" in text
    assert "orient" in text and "tool" in text


def test_report_ignores_the_harness_own_cli_when_it_is_tracked_here():
    names = {n for top, subs in cli.command_map().items() for n in (top, *subs)}
    r = consolidation.report(Path(__file__).resolve().parents[1], names, cfg())
    assert "devkit/cli.py" not in r["command_overlap"]
    assert r["parse_errors"]["count"] == 0


def test_this_repository_keeps_path_loaded_trees_and_research_scratch_out_of_the_trigger():
    # #451: the owner excluded research evidence and trees reached only by path; a table replaces the default list.
    here = Path(__file__).resolve().parents[1]
    table = tomllib.loads((here / "pyproject.toml").read_text())["tool"]["devkit"]["consolidation"]
    exclude = config.parse({"consolidation": table}).consolidation["exclude"]
    assert set(config.CONSOLIDATION["exclude"]) <= set(exclude)
    loaded = ("docs/research/_scratch/", "evals/conflicting/", "evals/reuse/", "tests/fixtures/", "template/")
    paths = [p for _, p in consolidation.code_files(here, "HEAD", exclude)]
    assert paths and not [p for p in paths if p.startswith(loaded)]


# ---------------------------------------------------------------- the CLI
def test_cli_consolidation_status_and_report(monkeypatch, repo, capsys):
    repo.commit({"a.py": code(10)})
    monkeypatch.setattr(cli, "root", lambda: repo.path)
    assert cli.run(cli.build().parse_args(["consolidation", "status", "--json"])) == 0
    assert json.loads(capsys.readouterr().out)["review_due"] is False
    assert cli.run(cli.build().parse_args(["consolidation", "report"])) == 0
    assert "Consolidation report" in capsys.readouterr().out
    assert "consolidation" in cli.command_map() and {"status", "report"} <= cli.command_map()["consolidation"]


# ---------------------------------------------------------------- pure logic
@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.integers(0, 7), st.sets(st.integers(0, 7), max_size=4), max_size=8))
def test_tarjan_matches_mutual_reachability(edges):
    graph = {str(k): {str(v) for v in vs} for k, vs in edges.items()}
    for vs in list(graph.values()):
        for v in vs:
            graph.setdefault(v, set())

    def reachable(v: str) -> set[str]:
        seen, todo = {v}, [v]
        while todo:
            for w in graph[todo.pop()] - seen:
                seen.add(w)
                todo.append(w)
        return seen

    reach = {v: reachable(v) for v in graph}
    want = {frozenset(w for w in graph if w in reach[v] and v in reach[w]) for v in graph}
    want = {c for c in want if len(c) > 1}
    assert {frozenset(c) for c in consolidation.tarjan(graph)} == want


@pytest.mark.parametrize(
    ("src", "mod", "is_pkg", "want"),
    [
        ("import a.b, c as d\n", "m", False, ["a.b", "c"]),
        ("from . import x\n", "p.q", False, ["p", "p.x"]),
        ("from .. import x\n", "p.q.r", False, ["p", "p.x"]),
        ("from .s import (t, u)\n", "p", True, ["p.s", "p.s.t", "p.s.u"]),
        ("    from os import path\n", "m", False, ["os", "os.path"]),
    ],
)
def test_regex_import_targets(src, mod, is_pkg, want):
    assert consolidation.regex_import_targets(src, mod, is_pkg) == want


# ---------------------------------------------------------------- the architect is read-only, enforced (#113 review)
ARCHITECT_OK = [
    "uv run dev consolidation report --tools",
    "uv run dev board new --type epic --title 'x' --context \"$(cat <<'X'\nbody > text\nX\n)\"",
    "uv run dev code refs Config",
    "uv run dev --help",
    "git log --oneline -5 -- devkit",
    "git -C /tmp/r show HEAD:pyproject.toml",
    "git diff --stat main && git status --short",
    "gh issue view 12 --comments",
    "gh issue create --title t --body b",
    "gh api repos/o/r/issues",
    "cat README.md | head -20",
    "grep -rn 'def ' devkit | wc -l",
    "sed -n 1,40p devkit/cli.py",
    "find devkit -name '*.py' -exec grep -l import {} +",
    "ls -la 2>/dev/null; echo done >&2",
    "cd devkit && wc -l *.py",
]
ARCHITECT_DENIED = [
    "rm -rf devkit",
    "rm old.py",
    "git rm -r scripts",
    "git mv a.py b.py",
    "git commit -m x",
    "git push origin HEAD",
    "git checkout -- .",
    "git diff --output=x.patch",
    "echo x > devkit/cli.py",
    "cat a >> b",
    "> empty.py",
    "sed -i s/a/b/ devkit/cli.py",
    "sed -n '1w out' a.py",
    "mv a.py b.py",
    "cp a b",
    "touch new.py",
    "python3 -c 'print(1)'",
    "bash -c 'ls'",
    "find . -name '*.pyc' -delete",
    "find . -exec rm {} +",
    "ls | xargs rm",
    "tee out.txt < a",
    "awk '{print > \"f\"}' a",
    "uv run dev integrate 5",
    "uv run dev pr 5",
    "uv add requests",
    "gh pr merge 5",
    "gh issue delete 5",
    "gh issue close 5",
    "gh repo delete o/r",
    "gh api -X DELETE repos/o/r",
    "gh api repos/o/r/issues -f title=x",
    "uvx pyscn analyze .",
]


@pytest.mark.parametrize("agent", ["harness-ops:architect", "architect"])
@pytest.mark.parametrize("cmd", ARCHITECT_OK)
def test_architect_may_read_measure_and_file_issues(hook, cmd, agent):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "agent_type": agent})
    assert code == 0, err


@pytest.mark.parametrize("cmd", ARCHITECT_DENIED)
def test_architect_may_not_write_move_or_delete(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "agent_type": "harness-ops:architect"})
    assert code == 2 and "read-only" in err, (cmd, err)


@pytest.mark.parametrize("cmd", ["mv a.py b.py", "touch new.py", "uv run dev pr 5", "sed -i s/a/b/ x"])
def test_other_agents_and_the_main_session_are_not_read_only(hook, cmd):
    for extra in ({}, {"agent_type": "harness-ops:builder"}):
        code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, **extra})
        assert code == 0, err
