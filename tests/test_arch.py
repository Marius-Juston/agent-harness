"""`dev arch` (#527): the import graph, contracts, largest cycle, hotspots and the LOC ceiling, each line on a fixture
repository, and the replay at b8e2e49 the research note measured (docs/research/architecture-grading.md §3-§4)."""

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import arch, cli, config, consolidation
from devkit.paths import root

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}
ACYCLIC = {"name": "pkg acyclic", "type": "acyclic_siblings", "ancestors": ["pkg"]}
NO_CLI = {
    "name": "only main imports cli",
    "type": "forbidden",
    "source_modules": ["pkg.*"],
    "forbidden_modules": ["pkg.cli"],
}


class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        out = subprocess.run(["git", *args], cwd=self.path, env=ENV, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def commit(self, files: dict[str, str], msg: str = "change") -> str:
        for name, text in files.items():
            p = self.path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")


def code(n: int, tag: str = "x") -> str:
    return "# comment\n\n" + "".join(f"{tag}{k} = {k}\n" for k in range(n))


def cfg(contracts=(), type_checking=False) -> config.Config:
    return dataclasses.replace(
        config.Config(), arch={**config.ARCH, "contracts": list(contracts), "type_checking_imports": type_checking}
    )


def graph_of(files: dict[str, str], type_checking=False):
    return arch.build_graph(files, type_checking)


@pytest.fixture
def repo(tmp_path) -> Repo:
    return Repo(tmp_path)


# ---------------------------------------------------------------- the import graph: the three explicit cases
def test_function_level_import_is_an_edge_of_kind_function():
    g, _ = graph_of({"pkg/__init__.py": "", "pkg/a.py": "def f():\n    from pkg import b\n", "pkg/b.py": ""})
    assert g["pkg.a"] == {"pkg.b": [arch.Import(2, "function")]}


def test_type_checking_import_is_left_out_unless_configured_and_its_else_branch_counts():
    src = (
        "from typing import TYPE_CHECKING\nimport typing\n"
        "if TYPE_CHECKING:\n    from pkg import b\nelse:\n    from pkg import c\n"
        "if typing.TYPE_CHECKING:\n    import pkg.d\n"
    )
    files = {"pkg/__init__.py": "", "pkg/a.py": src, "pkg/b.py": "", "pkg/c.py": "", "pkg/d.py": ""}
    g, _ = graph_of(files)
    assert set(g["pkg.a"]) == {"pkg.c", "typing"}
    g, _ = graph_of(files, type_checking=True)
    assert g["pkg.a"]["pkg.b"] == [arch.Import(4, "type-checking")]
    assert g["pkg.a"]["pkg.d"] == [arch.Import(8, "type-checking")]


def test_type_checking_only_cycle_is_no_cycle():
    files = {"pkg/__init__.py": "", "pkg/a.py": "import pkg.b\n", "pkg/b.py": "if TYPE_CHECKING:\n    import pkg.a\n"}
    assert arch.largest_cycle(*graph_of(files)) == []
    assert arch.largest_cycle(*graph_of(files, type_checking=True)) == ["pkg.a", "pkg.b"]


def test_relative_imports_resolve_against_the_importing_module():
    files = {
        "pkg/__init__.py": "from . import a\n",
        "pkg/a.py": "from . import b\nfrom .sub import c\nfrom .b import thing\n",
        "pkg/b.py": "",
        "pkg/sub/__init__.py": "",
        "pkg/sub/c.py": "from .. import b\nfrom ..a import f\nfrom . import d\n",
        "pkg/sub/d.py": "",
    }
    g, _ = graph_of(files)
    assert set(g["pkg"]) == {"pkg.a"}
    assert set(g["pkg.a"]) == {"pkg.b", "pkg.sub.c"}
    assert set(g["pkg.sub.c"]) == {"pkg.b", "pkg.a", "pkg.sub.d"}


def test_from_package_import_name_targets_the_package_when_the_name_is_no_module():
    g, _ = graph_of({"pkg/__init__.py": "X = 1\n", "pkg/a.py": "from pkg import X\nimport pkg.a\n"})
    assert set(g["pkg.a"]) == {"pkg"}  # the self-import is dropped


def test_a_file_that_does_not_parse_falls_back_to_the_regex():
    g, _ = graph_of({"pkg/__init__.py": "", "pkg/a.py": "import pkg.b\ndef f(:\n", "pkg/b.py": ""})
    assert "pkg.b" in g["pkg.a"]


# ---------------------------------------------------------------- contracts
CYCLIC = {
    "pkg/__init__.py": "",
    "pkg/cli.py": "from pkg import core\n",
    "pkg/core.py": "import pkg.util\n",
    "pkg/util.py": "def f():\n    from pkg import cli\n",
    "main.py": "from pkg import cli\n",
}


def test_acyclic_contract_names_the_lightest_edge_to_cut():
    g, known = graph_of(CYCLIC)
    (r,) = arch.check_contracts([ACYCLIC], g, known)
    assert not r["kept"] and [(e["from"], e["to"], e["lines"]) for e in r["edges"]] == [("pkg.util", "pkg.cli", [2])]


def test_forbidden_contract_reports_the_edge_into_the_forbidden_module_only():
    files = {**CYCLIC, "pkg/deep.py": "import pkg.util\n"}  # reaches cli only through util: not a second line
    g, known = graph_of(files)
    (r,) = arch.check_contracts([NO_CLI], g, known)
    assert not r["kept"] and [(e["from"], e["to"]) for e in r["edges"]] == [("pkg.util", "pkg.cli")]


def test_contracts_kept_once_the_cycle_is_cut():
    g, known = graph_of({**CYCLIC, "pkg/util.py": "def f():\n    pass\n"})
    assert [r["kept"] for r in arch.check_contracts([ACYCLIC, NO_CLI], g, known)] == [True, True]


def test_forbidden_contract_on_an_external_module():
    g, known = graph_of({"pkg/__init__.py": "", "pkg/a.py": "import requests.adapters\n"})
    c = {"name": "no http", "type": "forbidden", "source_modules": ["pkg"], "forbidden_modules": ["requests"]}
    (r,) = arch.check_contracts([c], g, known)
    assert [(e["from"], e["to"]) for e in r["edges"]] == [("pkg.a", "requests.adapters")]


def test_acyclic_siblings_squashes_descendants_to_the_sibling():
    files = {
        "pkg/__init__.py": "",
        "pkg/a/__init__.py": "",
        "pkg/a/x.py": "import pkg.b\n",
        "pkg/b.py": "import pkg.a.x\n",
    }
    g, known = graph_of(files)
    (r,) = arch.check_contracts([ACYCLIC], g, known)
    assert not r["kept"] and len(r["edges"]) == 1


@settings(max_examples=150, deadline=None)
@given(st.dictionaries(st.integers(0, 6), st.sets(st.integers(0, 6), max_size=4), max_size=7))
def test_edges_to_cut_leave_no_cycle_and_each_one_is_needed(edges):
    adj = {str(k): {str(v) for v in vs if v != k} for k, vs in edges.items()}
    for vs in list(adj.values()):
        for v in vs:
            adj.setdefault(v, set())
    cut = arch.edges_to_cut(adj, {})
    left = {u: vs - {v for x, v in cut if x == u} for u, vs in adj.items()}
    assert consolidation.tarjan(left) == []
    for u, v in cut:  # minimal: putting any one back closes a cycle
        assert consolidation.tarjan({**left, u: left[u] | {v}})


# ---------------------------------------------------------------- config
def test_config_validates_contracts():
    ok = config.parse({"arch": {"contracts": [ACYCLIC, NO_CLI]}}).arch
    assert ok["contracts"] == [ACYCLIC, NO_CLI] and ok["type_checking_imports"] is False
    assert config.Config().arch["contracts"] == []
    for bad in (
        {"contracts": [{**ACYCLIC, "type": "layers"}]},
        {"contracts": [ACYCLIC, ACYCLIC]},
        {"contracts": [{**NO_CLI, "forbidden_modules": []}]},
        {"contracts": [{**NO_CLI, "forbidden_modules": ["pkg/cli"]}]},
        {"contracts": [{**ACYCLIC, "extra": 1}]},
        {"type_checking_imports": "yes"},
        {"ceiling": 3},
    ):
        with pytest.raises(config.ConfigError, match="arch"):
            config.parse({"arch": bad})


# ---------------------------------------------------------------- the report, line by line
def test_each_line_on_a_fixture_repository(repo):
    repo.commit({**CYCLIC, "big.py": code(12), "small.py": code(3)})
    for k in range(1, 4):
        repo.commit({"big.py": code(12 + k)})
    repo.commit({"small.py": code(4), "mid.py": code(11)})
    repo.commit({".devkit/ratchets.toml": "[arch]\nmodule_loc_ceiling = 10\n[arch.module_loc]\n'big.py' = 16\n"})
    r = arch.measure(repo.path, "HEAD", cfg([ACYCLIC, NO_CLI]))
    lines = arch.render(r)
    assert lines == [
        "contract BROKEN 'pkg acyclic': cut util -> cli",
        "contract BROKEN 'only main imports cli': imports pkg.util -> pkg.cli",
        "largest import cycle: 3 modules (ratchet 0): pkg.cli, pkg.core, pkg.util",
        "hotspot: big.py 4 of 6 recent commits x 15 LOC",  # 60
        "hotspot: mid.py 1 of 6 recent commits x 11 LOC",  # 11
        "hotspot: small.py 2 of 6 recent commits x 4 LOC",  # 8, ahead of pkg/util.py's 1 x 2
        "over the 10-LOC module ceiling: big.py 15 (pin 16), mid.py 11 (no pin)",
    ]


def test_hotspot_churn_resets_at_a_consolidation_review_commit(repo):
    repo.commit({"a.py": code(5), "b.py": code(50)})
    for k in range(4):
        repo.commit({"b.py": code(50 + k)})
    repo.commit({"a.py": code(6)}, msg="merge the review\n\nConsolidation-Review: #9")
    repo.commit({"a.py": code(7)})
    r = arch.measure(repo.path, "HEAD", cfg())
    assert r["churn_commits"] == 1 and [(h["path"], h["commits"]) for h in r["hotspots"]] == [("a.py", 1)]
    r = arch.measure(repo.path, "HEAD~1", cfg())
    assert r["hotspots"] == [] and "hotspots: none" in "\n".join(arch.render(r))


def test_hotspot_window_is_the_last_30_first_parent_commits_and_skips_tests(repo):
    repo.commit({"old.py": code(100)})
    for k in range(arch.WINDOW):
        repo.commit({"a.py": code(5 + k % 2), "tests/test_a.py": code(500 + k % 2)})
    r = arch.measure(repo.path, "HEAD", cfg())
    assert r["churn_commits"] == arch.WINDOW and [h["path"] for h in r["hotspots"]] == ["a.py"]


def test_kept_contracts_and_no_contracts_are_one_line(repo):
    repo.commit({"pkg/__init__.py": "", "pkg/a.py": "x = 1\n"})
    assert arch.render(arch.measure(repo.path, "HEAD", cfg([ACYCLIC, NO_CLI])))[0] == "contracts: 2 kept"
    assert arch.render(arch.measure(repo.path, "HEAD", cfg()))[0].startswith("contracts: none declared")


def test_working_tree_mode_sees_uncommitted_and_untracked_files(repo):
    repo.commit({"pkg/__init__.py": "", "pkg/cli.py": ""})
    (repo.path / "pkg/new.py").write_text("def f():\n    from pkg import cli\n")
    r = arch.measure(repo.path, None, cfg([NO_CLI]))
    assert not r["contracts"][0]["kept"] and r["rev"] == "working tree"


def test_measure_in_a_repository_without_commits(repo):
    (repo.path / "a.py").write_text(code(3))
    r = arch.measure(repo.path, None, cfg())
    assert r["hotspots"] == [] and r["loc"] == {"a.py": 3}


report = st.fixed_dictionaries(
    {
        "contracts": st.lists(
            st.fixed_dictionaries(
                {
                    "name": st.text(min_size=1, max_size=5),
                    "type": st.sampled_from(["acyclic_siblings", "forbidden"]),
                    "kept": st.booleans(),
                    "edges": st.lists(
                        st.fixed_dictionaries({"from": st.just("a.b"), "to": st.just("a.c")}), max_size=6
                    ),
                }
            ),
            max_size=6,
        ),
        "largest_import_cycle": st.lists(st.text(min_size=1, max_size=3), max_size=20),
        "max_import_cycle": st.integers(0, 9),
        "churn_commits": st.integers(0, 30),
        "hotspots": st.lists(
            st.fixed_dictionaries({"path": st.just("p.py"), "commits": st.integers(1, 30), "loc": st.integers(1, 9)}),
            max_size=3,
        ),
        "module_loc_ceiling": st.integers(1, 2000),
        "over_ceiling": st.lists(
            st.fixed_dictionaries({"path": st.just("p.py"), "loc": st.integers(), "pin": st.none() | st.integers()}),
            max_size=10,
        ),
    }
)


@settings(max_examples=300, deadline=None)
@given(report)
def test_render_never_prints_more_than_8_lines(r):
    lines = arch.render(r)
    assert 3 <= len(lines) <= arch.MAX_LINES
    assert lines[-1].startswith("over the ")  # the ceiling line is never the one cut


def test_cli_arch_prints_lines_and_json(monkeypatch, repo, capsys):
    repo.commit({"a.py": code(3)})
    monkeypatch.setattr(cli, "root", lambda: repo.path)
    assert cli.run(cli.build().parse_args(["arch"])) == 0
    out = capsys.readouterr().out.splitlines()
    assert 1 < len(out) <= arch.MAX_LINES and out[-1].startswith("over the 1,000-LOC module ceiling")
    assert cli.run(cli.build().parse_args(["arch", "--json", "--rev", "HEAD"])) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rev"] == "HEAD" and data["hotspots"][0]["path"] == "a.py" and "loc" not in data


# ---------------------------------------------------------------- the replay the note measured
def test_replay_at_b8e2e49_ranks_workflow_first_and_reports_checks_to_cli():
    contracts = [
        {"name": "acyclic", "type": "acyclic_siblings", "ancestors": ["devkit"]},
        {"name": "no cli", "type": "forbidden", "source_modules": ["devkit.*"], "forbidden_modules": ["devkit.cli"]},
    ]
    r = arch.measure(root(), "b8e2e49", dataclasses.replace(config.get(), arch={**config.ARCH, "contracts": contracts}))
    assert r["hotspots"][0]["path"] == "devkit/workflow.py"
    acyclic, forbidden = r["contracts"]
    assert ("devkit.checks", "devkit.cli") in {(e["from"], e["to"]) for e in acyclic["edges"]}
    assert [(e["from"], e["to"], e["lines"]) for e in forbidden["edges"]] == [("devkit.checks", "devkit.cli", [421])]
    lines = arch.render(r)
    assert "checks -> cli" in lines[0] and len(lines) <= arch.MAX_LINES
