"""Consolidation review trigger and report (#113; design: docs/research/architecture-consolidation.md §8)."""

import json
import subprocess
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


def test_orient_shows_the_review_line(monkeypatch, tmp_path):
    from devkit import board

    monkeypatch.setattr(board, "sync", lambda: [])
    monkeypatch.setattr(board, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    monkeypatch.setattr(workflow, "root", lambda: tmp_path)
    monkeypatch.setattr(workflow, "status", lambda: "  wt")
    monkeypatch.setattr(consolidation, "orient_line", lambda path, c=None: "## Consolidation review due: x")
    assert "## Consolidation review due: x" in workflow.orient()
    monkeypatch.setattr(consolidation, "orient_line", lambda path, c=None: None)
    assert "Consolidation" not in workflow.orient()

    def boom(path, c=None):
        raise RuntimeError("git broke")

    monkeypatch.setattr(consolidation, "orient_line", boom)
    assert "consolidation trigger failed: git broke" in workflow.orient()  # reported, never fatal


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
    reach = {v: consolidation.reach(graph, {v}) for v in graph}
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
