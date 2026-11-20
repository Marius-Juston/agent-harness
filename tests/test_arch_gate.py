"""The gate's arch stage (#529): contracts kept, the import cycle within its ratchet, modules over the LOC ceiling
pinned and never above their pin, raises of any of those through an owner-approved [[raise]], and the contracts this
repository and the template carry."""

import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import jinja2
import pytest

from devkit import arch, checks, config, gate
from devkit.paths import root

OWNER = "Marius-Juston"
RATCHETS = ".devkit/ratchets.toml"


def init(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    return path


def write(path: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)


def lines(n: int) -> str:
    return "".join(f"x{k} = {k}\n" for k in range(n))


def contracts_of(cfg: config.Config) -> list[dict]:
    return cast(list[dict], cfg.arch["contracts"])


@pytest.fixture
def devkit_copy(tmp_path) -> tuple[Path, config.Config]:
    """This repository's devkit package, pyproject.toml and ratchets.toml in a fresh repository."""
    repo = init(tmp_path)
    shutil.copytree(root() / "devkit", repo / "devkit", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / ".devkit").mkdir()
    shutil.copy(root() / RATCHETS, repo / RATCHETS)
    shutil.copy(root() / "pyproject.toml", repo / "pyproject.toml")
    data = tomllib.loads((repo / "pyproject.toml").read_text())
    return repo, config.parse(data["tool"]["devkit"])


# ---------------------------------------------------------------- contracts
def test_gate_fails_on_from_devkit_import_cli_inside_a_function_in_checks(devkit_copy):
    repo, cfg = devkit_copy
    ran = arch.check_arch(repo, cfg)
    assert ran.ok, ran.problems
    checks_py = repo / "devkit/checks.py"
    checks_py.write_text(checks_py.read_text() + "\n\ndef _seeded():\n    from devkit import cli\n\n    return cli\n")
    ran = arch.check_arch(repo, cfg)
    assert not ran.ok
    broken = [p for p in ran.problems if p.startswith("contract BROKEN")]
    assert len(broken) == 1 and "devkit.checks -> devkit.cli" in broken[0]
    assert any("import cycle of" in p for p in ran.problems)  # and the cycle ratchet (2) fails too


def test_this_repository_declares_the_cli_contract_and_carries_the_acyclic_one_commented():
    names = [c["name"] for c in contracts_of(config.get())]
    assert names == ["only the entry point imports the CLI"]
    text = (root() / "pyproject.toml").read_text()
    start = text.index("# [[tool.devkit.arch.contracts]]")
    block = "\n".join(line.removeprefix("# ") for line in text[start:].split("\n\n", 1)[0].splitlines())
    (contract,) = contracts_of(config.parse(tomllib.loads(block)["tool"]["devkit"]))
    assert contract == {
        "name": "devkit modules form no import cycle",
        "type": "acyclic_siblings",
        "ancestors": ["devkit"],
    }


def test_gate_passes_on_this_tree():
    ran = arch.check_arch()
    assert ran.ok, ran.problems


def test_the_gate_runs_the_arch_stage():
    assert arch.check_arch in gate.stages(False, {}, None)


# ---------------------------------------------------------------- the ratchets in the tree
def project(tmp_path: Path, ratchets: str, files: dict[str, str]) -> Path:
    repo = init(tmp_path)
    write(repo, {RATCHETS: ratchets, **files})
    return repo


CEILING = "[arch]\nmodule_loc_ceiling = 10\n"


def test_a_module_crossing_the_ceiling_without_a_pin_fails(tmp_path):
    repo = project(tmp_path, CEILING, {"a.py": lines(10), "b.py": lines(11), "tests/test_big.py": lines(50)})
    ran = arch.check_arch(repo, config.Config())
    assert [p.split(":")[0] for p in ran.problems] == ["b.py"] and "crossed the 10-LOC" in ran.problems[0]


def test_a_pinned_module_may_shrink_but_not_grow(tmp_path):
    repo = project(tmp_path, CEILING + "[arch.module_loc]\n'b.py' = 12\n'gone.py' = 20\n", {"b.py": lines(12)})
    ran = arch.check_arch(repo, config.Config())
    assert ran.ok and any("gone.py" in w and "remove" in w for w in ran.warnings)
    (repo / "b.py").write_text(lines(13))
    assert "b.py: 13 LOC > its pin 12" in arch.check_arch(repo, config.Config()).problems[0]
    (repo / "b.py").write_text(lines(11))
    ran = arch.check_arch(repo, config.Config())
    assert ran.ok and any("lower its [arch.module_loc] pin from 12" in w for w in ran.warnings)


def test_the_cycle_may_not_grow_past_its_ratchet(tmp_path):
    files = {"p/__init__.py": "", "p/a.py": "import p.b\n", "p/b.py": "def f():\n    import p.a\n"}
    repo = project(tmp_path, "[arch]\nmax_import_cycle = 0\n", files)
    assert (
        "import cycle of 2 modules > [arch] max_import_cycle = 0" in arch.check_arch(repo, config.Config()).problems[0]
    )
    (repo / RATCHETS).write_text("[arch]\nmax_import_cycle = 2\n")
    assert arch.check_arch(repo, config.Config()).ok


def test_no_arch_table_means_the_defaults(tmp_path):
    repo = project(tmp_path, "[tests]\nmin_tests = 1\n", {"a.py": lines(1001)})
    assert "crossed the 1000-LOC" in arch.check_arch(repo, config.Config()).problems[0]


@pytest.mark.parametrize(
    "table",
    ["[arch]\nceiling = 3\n", "[arch]\nmax_import_cycle = -1\n", "[arch.module_loc]\n'a.py' = 'big'\n", "arch = 3\n"],
)
def test_a_malformed_arch_table_fails_the_stage(tmp_path, table):
    repo = project(tmp_path, table, {"a.py": lines(1)})
    assert "[arch]" in arch.check_arch(repo, config.Config()).problems[0]


def test_an_empty_project_passes(tmp_path):
    assert arch.check_arch(project(tmp_path, "", {}), config.Config()).ok


# ---------------------------------------------------------------- raising a ratchet against main
def ratchets(arch_table=None, raises=()) -> dict:
    return {"words": {}, "defaults": {}, "raise": list(raises), "tests": {}, "arch": arch_table}


def raise_(key, to, who=OWNER) -> dict:
    return {"key": key, "to": to, "reason": "r", "approved_by": who}


MAIN = {"max_import_cycle": 0, "module_loc": {"devkit/board.py": 1475}}


@pytest.mark.parametrize(
    ("now", "key"),
    [
        ({**MAIN, "module_loc": {"devkit/board.py": 1476}}, "arch.module_loc.devkit/board.py"),
        ({**MAIN, "module_loc": {**MAIN["module_loc"], "devkit/gate.py": 1001}}, "arch.module_loc.devkit/gate.py"),
        ({**MAIN, "max_import_cycle": 3}, "arch.max_import_cycle"),
        ({**MAIN, "module_loc_ceiling": 1500}, "arch.module_loc_ceiling"),
    ],
)
def test_raising_an_arch_value_needs_an_owner_approved_raise(now, key):
    (problem,) = checks.ratchet_raises(ratchets(now), ratchets(MAIN), [], OWNER)
    assert key in problem and "without an owner-approved [[raise]]" in problem
    value = arch.ratchet_keys(arch.ratchet_table(now))[key]
    assert checks.ratchet_raises(ratchets(now, [raise_(key, value)]), ratchets(MAIN), [], OWNER) == []
    assert checks.ratchet_raises(ratchets(now, [raise_(key, value, "someone")]), ratchets(MAIN), [], OWNER)
    assert (
        "above its [[raise]]"
        in checks.ratchet_raises(ratchets(now, [raise_(key, value - 1)]), ratchets(MAIN), [], OWNER)[0]
    )


def test_tightening_or_adopting_the_arch_table_needs_no_raise():
    tighter = {"max_import_cycle": 0, "module_loc": {"devkit/board.py": 1400}}
    assert checks.ratchet_raises(ratchets(tighter), ratchets(MAIN), [], OWNER) == []
    assert checks.ratchet_raises(ratchets({"max_import_cycle": 0, "module_loc": {}}), ratchets(MAIN), [], OWNER) == []
    assert checks.ratchet_raises(ratchets({"max_import_cycle": 9}), ratchets(None), [], OWNER) == []  # adoption


# ---------------------------------------------------------------- the template
def test_the_template_carries_the_contracts_commented_with_the_package_name():
    text = (root() / "template/pyproject.toml.jinja").read_text()
    env = jinja2.Environment(autoescape=jinja2.select_autoescape(default_for_string=False))  # TOML, not HTML
    rendered = env.from_string(text).render(
        project_name="demo-project", description="d", python_version="3.13", harness_ref="v0", harness_path=""
    )
    data = tomllib.loads(rendered)
    assert "arch" not in data["tool"]["devkit"]  # commented out: no contract until the project declares one
    start = rendered.index("# [[tool.devkit.arch.contracts]]")
    block, rest = rendered[start:].split("\n\n", 1)
    uncommented = rendered[:start] + "\n".join(line.removeprefix("#").strip() for line in block.splitlines())
    uncommented += "\n\n" + rest
    contracts = contracts_of(config.parse(tomllib.loads(uncommented)["tool"]["devkit"]))
    assert [c["type"] for c in contracts] == ["acyclic_siblings", "forbidden"]
    assert contracts[0]["ancestors"] == ["demo_project"] and contracts[1]["forbidden_modules"] == ["demo_project.cli"]


def test_the_template_ratchets_adopt_the_arch_table():
    rat = arch.ratchet((root() / "template" / RATCHETS).read_text())
    assert rat == {"module_loc_ceiling": 1000, "max_import_cycle": 0, "module_loc": {}}
