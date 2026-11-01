"""Gate and integrate integrity: the gate cannot be talked into a pass, and integrate merges what it gated."""

import contextlib
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, branches, ci_status, config, gate, paths, workflow

REAL_POPEN = subprocess.Popen


class FinishedGate:
    """The gate's Popen, already exited with `code` (a real one would sleep on the fake clocks)."""

    def __init__(self, code):
        self.code, self.pid = code, -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self, timeout=None):
        return self.code


def _sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _sh(r, "git", "init", "-q", "-b", "main")
    _sh(r, "git", "config", "user.email", "t@t")
    _sh(r, "git", "config", "user.name", "t")
    (r / ".gitignore").write_text("*.pyc\n__pycache__/\n.scratch/\n")
    (r / "m.py").write_text("X = 1\n")
    _sh(r, "git", "add", ".")
    _sh(r, "git", "commit", "-q", "-m", "init")
    return r


def test_a_conftest_that_skips_every_test_fails_the_gate(tmp_path):
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='skip all'))\n"
    )
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    ran = gate._run("tests", [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tmp_path)])
    assert not ran.ok
    assert any("0 passed" in p for p in ran.problems)


def test_a_header_saying_passed_while_everything_skips_fails_the_gate(tmp_path):
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n"
        "def pytest_report_header(config):\n"
        "    return '7 passed'\n\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='skip all'))\n"
    )
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tmp_path)]
    assert "7 passed" in subprocess.run(cmd, capture_output=True, text=True).stdout
    ran = gate._run("tests", cmd)
    assert not ran.ok and ran.checked == 0


def test_passed_count_reads_only_the_final_summary_and_the_junit_report(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite><testcase name="a"/><testcase name="b"/><testcase name="c"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    assert gate.passed_count("header: 9 passed\n2 passed, 1 skipped in 0.10s\n", junit) == 2
    assert gate.passed_count("==== 5 passed in 65.12s (0:01:05) ====\n", junit) == 2  # junit says 2
    assert gate.passed_count("2 passed in 0.1s\n", tmp_path / "missing.xml") == 0
    assert gate.passed_count("no summary here, 2 passed\n", junit) == 0


def test_a_real_pass_is_counted(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
    ran = gate._run("tests", [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tmp_path)])
    assert ran.ok and ran.checked == 1


def test_floor_problems_fail_below_a_floor_and_suggest_raising_one_left_behind():
    assert gate.floor_problems(100, 80.0, {}) == ([], [])
    problems, _ = gate.floor_problems(9, None, {"min_tests": 10})
    assert problems == [
        "9 tests passed, below the floor min_tests = 10 (.devkit/ratchets.toml): did a test file drop out?"
    ]
    problems, _ = gate.floor_problems(10, 76.9, {"min_tests": 10, "min_coverage": 77})
    assert problems == ["line coverage 76.9% is below the floor min_coverage = 77 (.devkit/ratchets.toml)"]
    problems, _ = gate.floor_problems(10, None, {"min_coverage": 77})
    assert problems and "no coverage data" in problems[0]
    problems, notes = gate.floor_problems(200, 90.4, {"min_tests": 100, "min_coverage": 77})
    assert problems == []
    assert notes == ["raise min_tests to 190 (200 passed)", "raise min_coverage to 90 (90.4% measured)"]
    assert gate.floor_problems(804, 78.2, {"min_tests": 759, "min_coverage": 77}) == ([], [])  # close: no nagging


@given(st.integers(0, 5000), st.integers(0, 5000))
def test_the_test_floor_fails_exactly_below_it(passed, floor):
    problems, _ = gate.floor_problems(passed, None, {"min_tests": floor})
    assert bool(problems) == (passed < floor)


def _project(r, body="X = 1\n"):
    (r / "m.py").write_text(body)
    (r / "test_m.py").write_text("import m\n\n\ndef test_x():\n    assert m.X == 1\n")


def test_tests_stage_enforces_floors_and_prints_report_only_diff_coverage(repo):
    _project(repo)
    _sh(repo, "git", "add", ".")
    _sh(repo, "git", "commit", "-q", "-m", "tests")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    _project(repo, "X = 1\n\n\ndef untested():\n    return 2\n")
    _sh(repo, "git", "commit", "-qam", "change")
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly", "test_m.py"]
    ran = gate.tests_stage(cmd, {"min_tests": 1, "min_coverage": 50}, base, cwd=repo)
    assert ran.ok, ran.problems
    assert ran.checked == 1
    (diff,) = [n for n in ran.notes if n.startswith("diff coverage")]
    assert "50% of 2 changed" in diff and "m.py:5" in diff  # report-only: uncovered, still ok
    assert not list(repo.glob(".coverage*"))  # the data file lives in a temporary directory
    ran = gate.tests_stage(cmd, {"min_tests": 2, "min_coverage": 99}, base, cwd=repo)
    assert not ran.ok and len(ran.problems) == 2


def test_tests_stage_without_a_coverage_floor_does_not_measure_coverage(repo):
    _project(repo)
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_m.py"]
    ran = gate.tests_stage(cmd, {}, None, cwd=repo)
    assert ran.ok and ran.notes == []


def test_a_failing_run_reports_its_own_output_not_the_floors(repo):
    (repo / "test_m.py").write_text("def test_x():\n    assert False\n")
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_m.py"]
    ran = gate.tests_stage(cmd, {"min_tests": 5, "min_coverage": 90}, None, cwd=repo)
    assert not ran.ok and not any("floor" in p for p in ran.problems)


def test_each_tests_stage_appends_per_test_outcome_and_duration_to_the_history(repo, monkeypatch):
    (repo / "test_m.py").write_text(
        "import pytest\n\n\ndef test_ok():\n    pass\n\n\ndef test_bad():\n    assert False\n"
    )
    history = repo / ".scratch" / "test-history.jsonl"
    monkeypatch.setattr(gate, "HISTORY_CLOCK", lambda: 1234.5)
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_m.py"]
    gate.tests_stage(cmd, {}, None, cwd=repo, history=history)
    gate.tests_stage(cmd, {}, None, cwd=repo, history=history)
    runs = [json.loads(line) for line in history.read_text().splitlines()]
    assert len(runs) == 2 and runs[0]["at"] == 1234
    tests = runs[-1]["tests"]
    assert tests["test_m::test_ok"][0] == "passed" and tests["test_m::test_bad"][0] == "failed"
    assert all(isinstance(seconds, float) and seconds >= 0 for _, seconds in tests.values())


def test_the_history_keeps_only_the_latest_runs_and_skips_an_unreadable_report(tmp_path):
    junit, history = tmp_path / "j.xml", tmp_path / "h.jsonl"
    junit.write_text(
        '<testsuites><testsuite><testcase classname="t" name="a" time="0.25"/>'
        '<testcase classname="t" name="b"><skipped/></testcase>'
        '<testcase classname="t" name="c" time="x"><error/></testcase></testsuite></testsuites>'
    )
    for at in range(5):
        gate.record_history(junit, history, at=at, keep=3)
    runs = [json.loads(line) for line in history.read_text().splitlines()]
    assert [r["at"] for r in runs] == [2, 3, 4]
    assert runs[-1]["tests"] == {"t::a": ["passed", 0.25], "t::b": ["skipped", 0.0], "t::c": ["error", 0.0]}
    gate.record_history(tmp_path / "missing.xml", history, at=9, keep=3)
    assert len(history.read_text().splitlines()) == 3  # nothing recorded, nothing lost


@pytest.mark.parametrize("workflow_file", [".github/workflows/ci.yml", "template/.github/workflows/gate.yml"])
def test_ci_uploads_the_test_history_after_the_gate(workflow_file):
    steps = yaml.safe_load((gate.root() / workflow_file).read_text())["jobs"]["check"]["steps"]
    gate_at = next(i for i, s in enumerate(steps) if s.get("run", "").startswith("uv run dev check"))
    (upload,) = [s for s in steps if s.get("with", {}).get("name") == "test-history"]
    assert steps.index(upload) > gate_at and "!cancelled()" in upload["if"]
    assert (
        upload["uses"].startswith("actions/upload-artifact@")
        and upload["with"]["path"] == f".git/devkit/{gate.HISTORY_FILE}"
    )
    assert upload["with"]["include-hidden-files"] is True  # .git is hidden, and skipped by default


def test_gates_writing_the_history_at_the_same_time_lose_no_run(tmp_path):
    junit, history = tmp_path / "j.xml", tmp_path / "state" / "h.jsonl"
    junit.write_text('<testsuites><testsuite><testcase classname="t" name="a" time="0.1"/></testsuite></testsuites>')
    writer = (
        "import sys; from pathlib import Path; from devkit import gate\n"
        "for at in range(int(sys.argv[3]), int(sys.argv[3]) + 25):\n"
        "    gate.record_history(Path(sys.argv[1]), Path(sys.argv[2]), at=at, keep=1000)\n"
    )
    procs = [
        REAL_POPEN([sys.executable, "-c", writer, str(junit), str(history), str(w * 100)], cwd=gate.root())
        for w in range(4)
    ]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    runs = [json.loads(line) for line in history.read_text().splitlines()]  # every line whole
    assert sorted(r["at"] for r in runs) == [w * 100 + i for w in range(4) for i in range(25)]
    assert sorted(p.name for p in history.parent.iterdir()) == [history.name, history.name + ".lock"]  # no tmp left


def _shared_state(monkeypatch, common):
    """The machine state dir under `common`, as every worktree of one clone shares its git common dir; returns it."""
    monkeypatch.setattr(paths, "common_dir", lambda: common)
    return gate.state_dir()


def _worktrees(repo, tmp_path, files):
    """`repo` with `files` committed on main, and a second worktree on branch `feature` at the same commit."""
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _sh(repo, "git", "add", ".")
    _sh(repo, "git", "commit", "-q", "-m", "tests")
    other = tmp_path / "feature"
    _sh(repo, "git", "worktree", "add", "-q", "-b", "feature", str(other))
    return other


def test_two_worktrees_gating_at_once_each_read_only_their_own_test_output(repo, tmp_path, monkeypatch):
    # Both gates fail in the same second, share one state dir and one history: each must report its own output and
    # point at its own full log (#463).
    test = "import os\n\n\ndef test_{0}():\n    for i in range(30):\n        print('{0}-line', i)\n    assert False\n"
    other = _worktrees(repo, tmp_path, {"test_m.py": test.format("main")})
    (other / "test_m.py").write_text(test.format("feature"))
    _sh(other, "git", "commit", "-qam", "feature")
    state = _shared_state(monkeypatch, tmp_path / "common")
    monkeypatch.setattr(gate, "FAIL_LINES", 3)
    monkeypatch.setattr(gate.time, "time", lambda: 1000.0)  # the same second for both
    history = state / gate.HISTORY_FILE
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-s", "test_m.py"]
    barrier, results = threading.Barrier(2), {}

    def gate_in(name, cwd):
        barrier.wait()
        results[name] = gate.tests_stage(cmd, {}, None, cwd=cwd, history=history)

    threads = [threading.Thread(target=gate_in, args=a) for a in (("main", repo), ("feature", other))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    logs = {}
    for name, ran in results.items():
        mine, theirs = f"{name}-line", "feature-line" if name == "main" else "main-line"
        assert not ran.ok
        text = "\n".join(ran.problems)
        assert theirs not in text
        (log,) = [p.removeprefix("(full output: ").removesuffix(")") for p in ran.problems if p.startswith("(full")]
        logs[name] = Path(log).read_text()
        assert mine in logs[name] and theirs not in logs[name]
    assert set(logs) == {"main", "feature"} and len(list((state / "logs").iterdir())) == 2
    runs = [json.loads(line)["tests"] for line in history.read_text().splitlines()]
    assert sorted(next(iter(r)) for r in runs) == ["test_m::test_feature", "test_m::test_main"]


def test_the_gate_names_its_worktree_interpreter_and_devkit_on_its_first_line():
    first, *rest = gate.identity()
    assert str(gate.root().resolve()) in first and str(Path(gate.__file__).parent.resolve()) in first
    assert Path(sys.executable).name in first and rest == []


def test_the_gate_warns_only_when_devkit_or_python_come_from_another_worktree_of_the_repo(repo, tmp_path):
    other = _worktrees(repo, tmp_path, {"devkit/__init__.py": ""})
    nested = repo / "wt" / "nested"  # a worktree inside the main checkout, as agent worktrees often are
    _sh(repo, "git", "worktree", "add", "-q", "-b", "nested", str(nested))
    own_code, own_python = repo / "devkit", repo / ".venv/bin/python"

    def warnings(top, code=own_code, python=own_python):
        return [line for line in gate.identity(top, code, python)[1:] if "WARNING" in line]

    assert warnings(repo) == []
    (devkit,) = warnings(repo, code=other / "devkit")
    assert str(other.resolve()) in devkit and devkit.startswith("gate: WARNING: devkit")
    (python,) = warnings(repo, python=other / ".venv/bin/python")
    assert python.startswith("gate: WARNING: python")
    assert len(warnings(other)) == 2  # the gate of `other` running main's devkit and venv
    # a setup, not a mix-up: an editable harness checkout, a venv outside the project
    assert warnings(repo, code=tmp_path / "harness/devkit", python=tmp_path / "venvs/p/bin/python") == []
    assert warnings(nested, code=nested / "devkit", python=nested / ".venv/bin/python") == []  # deepest owner wins
    assert len(warnings(nested)) == 2  # the main checkout around it is another worktree


BRANCH_SCOPED_CONFTEST = """import subprocess


def pytest_collection_modifyitems(config, items):
    branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    if branch != "main":  # a feature branch runs only its own chapter
        out = [i for i in items if "chapter_b" in i.nodeid]
        config.hook.pytest_deselected(items=out)
        items[:] = [i for i in items if i not in out]
"""


CHAPTERS = {
    "tests/conftest.py": BRANCH_SCOPED_CONFTEST,
    "tests/test_chapter_a.py": "def test_a():\n    pass\n",
    "tests/test_chapter_b.py": "def test_b():\n    pass\n",
}


def test_a_scoped_pass_on_a_feature_branch_is_not_reused_on_main_at_the_same_tree(repo, tmp_path):
    other = _worktrees(repo, tmp_path, CHAPTERS)
    assert gate.tree_key(None, repo) == gate.tree_key(None, other)  # the tree key alone cannot tell them apart
    scoped, everything = gate.selection(False, other), gate.selection(False, repo)
    assert scoped and everything and scoped != everything
    record = tmp_path / "state" / "key.json"
    record.parent.mkdir()
    gate.save_pass(record, 12.0, scoped)  # the feature worktree's gate passed
    assert gate.cached_pass(record, False, cwd=repo) is None  # main re-runs
    assert gate.cached_pass(record, False, cwd=other)["seconds"] == 12  # the same selection still reuses it


def test_a_pass_record_without_a_test_selection_is_never_reused(repo, tmp_path):
    record = tmp_path / "old.json"
    record.write_text(json.dumps({"when": "2026-01-01 00:00", "seconds": 1, "root": str(repo)}))
    assert gate.cached_pass(record, False, cwd=repo) is None
    record.write_text("not json")
    assert gate.cached_pass(record, False, cwd=repo) is None
    gate.save_pass(tmp_path / "none.json", 1.0, None)  # collection failed: nothing recorded
    assert not (tmp_path / "none.json").exists()
    assert gate.selection(False, cwd=repo) is None  # no tests dir: collection fails, no fingerprint


def test_dev_check_on_main_reruns_after_a_scoped_pass_in_a_feature_worktree_at_the_same_tree(
    repo, tmp_path, monkeypatch, capsys
):
    other = _worktrees(repo, tmp_path, CHAPTERS)
    _shared_state(monkeypatch, tmp_path / "common")  # both worktrees share one record dir, as one clone's do
    monkeypatch.setattr(gate, "clean_bytecode", lambda: True)  # already the re-run interpreter
    monkeypatch.setattr(gate, "stages", lambda *a: [])  # the stages pass; what is reused is the question
    monkeypatch.setattr(gate, "gate_lock", lambda *a, **k: contextlib.nullcontext())

    def check_in(cwd):
        monkeypatch.chdir(cwd)
        paths.root.cache_clear()  # a new process per gate, as two worktrees' gates are
        config.get.cache_clear()
        assert gate.check({}) == 0
        return capsys.readouterr().out

    assert "cached" not in check_in(other)  # the feature worktree's scoped pass is recorded
    assert "RESULT: passed (cached" in check_in(other)
    out = check_in(repo)  # main, same tree and merge base, selects every chapter
    assert "selected other tests" in out and "RESULT: passed (cached" not in out
    assert "RESULT: passed (cached" in check_in(repo)  # main's own pass is recorded in turn


def test_diff_coverage_never_fails_the_stage_when_it_cannot_measure(repo, monkeypatch):
    assert gate.diff_coverage(repo / "missing.xml", "HEAD", cwd=repo)[0].startswith("diff coverage: not measured")
    monkeypatch.setattr(gate, "tool", lambda name: None)
    assert "diff-cover not found" in gate.diff_coverage(repo / "missing.xml", "HEAD", cwd=repo)[0]


@given(st.sets(st.integers(1, 200)))
def test_ranges_round_trip(lines):
    spans = [s.split("-") for s in gate._ranges(list(lines)).split(",") if s]
    assert {n for s in spans for n in range(int(s[0]), int(s[-1]) + 1)} == lines


def test_bad_or_missing_floors_read_as_none(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "root", lambda: tmp_path)
    assert gate._floors() == {}
    (tmp_path / ".devkit").mkdir()
    (tmp_path / ".devkit/ratchets.toml").write_text("[tests]\nmin_tests = -3\n")
    assert gate._floors() == {}
    (tmp_path / ".devkit/ratchets.toml").write_text("[tests]\nmin_tests = 3\n")
    assert gate._floors() == {"min_tests": 3}


@pytest.mark.parametrize(
    "path",
    [
        "docs/research/agent-harness.md",
        "docs/research/_scratch/notes.md",
        "docs/log/2026-10-11.md",
        "docs/questions/OPEN_QUESTIONS.md",
    ],
)
def test_content_paths_are_not_harness(path):
    assert not workflow.is_harness(path)


@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "conftest.py",
        ".claude/skills/lead/SKILL.md",
        ".mcp.json",
        "plugins/harness-ops/hooks/register.tsx",
        ".pre-commit-config.yaml",
        "uv.lock",
        ".gitignore",
        ".worktreeinclude",
        ".claude/quota_cap",
        "AGENTS.md",
        ".claude/rules/x.md",
        ".claude/commands/x.md",
        "evals/README.md",
        "scripts/claude-memory.sh",
        ".gitattributes",
        "src/app/main.py",
        "docs/specs/multi-agent.md",
        "docs/HANDOFF.md",
        "docs/research/conftest.py",
        "docs/research/x.py",
        "docs/research/CLAUDE.md",
        "docs/research/sub/AGENTS.md",
        "docs/research/.claude/skills/x/SKILL.md",
        "evals/scenarios/../../devkit/gate.py",
        "docs/researchx/a.md",
        "evals/scenarios/uv-not-pip.yaml",
        "docs/research/Claude.md",
        "docs/questions/x/skill.MD",
    ],
)
def test_everything_else_is_harness(path):
    assert workflow.is_harness(path)


def test_the_install_allowlist_is_harness_even_under_a_content_directory(monkeypatch):
    """#392: an allowlist entry lets a branch past the install check, so the lead reads it."""
    cfg = config.Config(install_allowlist="docs/research/allow.txt")
    monkeypatch.setattr(config, "get", lambda: cfg)
    assert workflow.is_harness("docs/research/allow.txt")
    assert not workflow.is_harness("docs/research/other.txt")


def test_cache_key_covers_untracked_ignored_python_and_python_version(repo, monkeypatch):
    clean = gate.tree_key("base1", repo)
    assert clean and clean == gate.tree_key("base1", repo)
    assert gate.tree_key("base2", repo) != clean
    (repo / ".scratch").mkdir()
    (repo / ".scratch/helper.py").write_text("Y = 2\n")  # ignored module
    with_ignored = gate.tree_key("base1", repo)
    assert with_ignored not in (None, clean)
    (repo / ".scratch/helper.py").unlink()
    (repo / "sourceless.pyc").write_bytes(b"\x00bytecode")  # ignored sourceless bytecode is importable
    assert gate.tree_key("base1", repo) not in (None, clean)
    (repo / "sourceless.pyc").unlink()
    (repo / "__pycache__").mkdir()  # the gate never reads __pycache__ (fresh PYTHONPYCACHEPREFIX): keys stay stable
    (repo / "__pycache__/m.cpython-313.pyc").write_bytes(b"\x00")
    assert gate.tree_key("base1", repo) == clean
    monkeypatch.setattr(sys, "version", "3.99.0 (other)")
    assert gate.tree_key("base1", repo) != clean


def test_dirty_or_untracked_module_tree_never_reads_a_cached_pass(repo):
    _sh(repo, "git", "config", "status.showUntrackedFiles", "no")
    (repo / "extra.py").write_text("Z = 3\n")  # untracked module, hidden from a plain `git status`
    assert gate.tree_key(None, repo) is None
    (repo / "extra.py").unlink()
    (repo / "m.py").write_text("X = 2\n")
    assert gate.tree_key(None, repo) is None


def test_check_reruns_itself_under_a_fresh_per_run_bytecode_prefix(monkeypatch):
    monkeypatch.delenv(gate.CLEAN_FLAG, raising=False)
    seen = {}

    real = subprocess.run

    def run(cmd, env=None, **kw):
        if cmd[0] == "git":  # root() and friends
            return real(cmd, env=env, **kw)
        seen.update(cmd=cmd, prefix=env["PYTHONPYCACHEPREFIX"], flag=env[gate.CLEAN_FLAG])
        seen["existed"] = Path(env["PYTHONPYCACHEPREFIX"]).is_dir() and not any(Path(seen["prefix"]).iterdir())
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.check({}) == 3
    assert "from devkit.cli import main" in " ".join(seen["cmd"])
    assert seen["prefix"] == seen["flag"] and seen["existed"]  # a fresh, empty dir ...
    assert not Path(seen["prefix"]).exists()  # ... used for this run only


def test_hermetic_env_drops_only_what_redirects_python():
    env = {
        "PATH": "/bin",
        "PYTHONPATH": "/opt/ros/lib",
        "PYTEST_ADDOPTS": "",
        "PYTHONOPTIMIZE": "1",
        "PYTHONWARNINGS": "error",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    clean, dropped = gate.hermetic_env(env)
    assert clean == {"PATH": "/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    assert dropped == ["PYTHONPATH", "PYTHONOPTIMIZE", "PYTHONWARNINGS"]  # an empty one goes too, with nothing to tell


def test_dev_test_keeps_asserts_when_the_shell_sets_pythonoptimize(tmp_path, monkeypatch, capsys):
    test = tmp_path / "test_debug.py"
    test.write_text(
        "def test_asserts_run():\n    if not __debug__:\n        raise RuntimeError('optimized: asserts are off')\n"
    )
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    cmd = gate.pytest_cmd([str(test)], n=1)
    optimized = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert optimized.returncode != 0 and "asserts are off" in optimized.stdout  # the trap works
    assert gate.test([str(test)], affected=False, slow=False, n=1) == 0
    assert "ignoring PYTHONOPTIMIZE" in capsys.readouterr().out


def test_the_rerun_gate_env_leaves_out_the_shells_pythonpath():
    shell = {"PATH": "/bin", "PYTHONPATH": "/opt/ros/humble/lib/python3.10/site-packages"}
    env, dropped = gate.rerun_env(shell, "<prefix>")
    assert env == {"PATH": "/bin", "PYTHONPYCACHEPREFIX": "<prefix>", gate.CLEAN_FLAG: "<prefix>"}
    assert dropped == ["PYTHONPATH"]


def test_dev_test_runs_cleanly_with_a_ros_like_pythonpath(tmp_path, ros_like_site, monkeypatch, capsys):
    test = tmp_path / "test_one.py"
    test.write_text("def test_one():\n    assert True\n")
    monkeypatch.setenv("PYTHONPATH", str(ros_like_site))
    cmd = gate.pytest_cmd([str(test)], n=1)
    broken = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert broken.returncode != 0 and "lark_that_is_not_installed" in broken.stdout + broken.stderr  # the trap works
    assert gate.test([str(test)], affected=False, slow=False, n=1) == 0
    assert "ignoring PYTHONPATH" in capsys.readouterr().out


def test_the_rerun_gate_imports_bytecode_only_from_its_prefix(tmp_path):
    env = os.environ | {"PYTHONPYCACHEPREFIX": str(tmp_path), gate.CLEAN_FLAG: str(tmp_path)}
    code = "import sys; from devkit import gate; print(gate.clean_bytecode(), sys.modules['devkit.gate'].__cached__)"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
    assert out.startswith("True") and str(tmp_path) in out and "/devkit/__pycache__/" not in out


def test_a_waiting_gate_keeps_the_holders_pid(tmp_path):
    path = tmp_path / "gate.lock"
    holder = open(path, "w")  # noqa: SIM115 (held across the thread)
    fcntl.flock(holder, fcntl.LOCK_EX)
    holder.write("111 /holder\n")
    holder.flush()
    waiting, acquired = threading.Event(), threading.Event()

    def second():
        with gate.gate_lock(path, on_wait=waiting.set):
            acquired.set()

    t = threading.Thread(target=second)
    t.start()
    assert waiting.wait(10)
    assert path.read_text() == "111 /holder\n"
    assert not acquired.is_set()
    fcntl.flock(holder, fcntl.LOCK_UN)
    holder.close()
    t.join(10)
    assert acquired.is_set()
    assert path.read_text().split()[1] != "/holder"


@pytest.mark.parametrize("round_", range(10))
def test_integrates_gate_takes_the_lock_ahead_of_a_waiting_worker_gate(tmp_path, round_):
    """#385: a worker gate holds the lock while a worker gate and then integrate's (priority) gate wait for it; when
    the holder ends, integrate's gate gets it next, every time (flock alone grants it in no fixed order)."""
    path = tmp_path / "gate.lock"
    holder = open(path, "a+")  # noqa: SIM115 (held across the threads)
    fcntl.flock(holder, fcntl.LOCK_EX)
    order: list[str] = []
    waiting = {"worker": threading.Event(), "integrate": threading.Event()}

    def gate_run(name, priority):
        with gate.gate_lock(path, on_wait=waiting[name].set, priority=priority):
            order.append(name)
            time.sleep(0.05)

    threads = {n: threading.Thread(target=gate_run, args=(n, n == "integrate")) for n in waiting}
    for name in ("worker", "integrate"):
        threads[name].start()
        assert waiting[name].wait(10)
    holder.close()
    for t in threads.values():
        t.join(10)
    assert order == ["integrate", "worker"]


def test_a_worker_gate_waits_while_an_integrate_gate_queues_even_with_the_lock_free(tmp_path):
    """#385: a worker gate that gets the free lock while a priority gate waits hands it back, says it waits, and
    takes it only once no priority gate waits."""
    path = tmp_path / "gate.lock"
    queue = open(tmp_path / "gate.lock.priority", "a+")  # noqa: SIM115 (an integrate gate queued: released below)
    fcntl.flock(queue, fcntl.LOCK_SH)
    waiting, acquired = threading.Event(), threading.Event()

    def worker():
        with gate.gate_lock(path, on_wait=waiting.set):
            acquired.set()

    t = threading.Thread(target=worker)
    t.start()
    assert waiting.wait(10)
    assert not acquired.wait(0.3)
    queue.close()
    t.join(10)
    assert acquired.is_set()


def test_the_gate_takes_integrates_role_out_of_the_environment_its_stages_see(tmp_path):
    """#385 review: pytest and a nested `dev check` under the gate must not inherit priority or the held file."""
    env = {gate.PRIORITY_ENV: "1", gate.HELD_ENV: str(tmp_path / "held"), "OTHER": "x"}
    assert gate.take_lock_role(env) == {"priority": True, "held": tmp_path / "held"}
    assert env == {"OTHER": "x"}
    rerun, _ = gate.rerun_env({gate.PRIORITY_ENV: "1", gate.HELD_ENV: "h"}, "/p")  # the re-run gate keeps them
    assert rerun[gate.PRIORITY_ENV] == "1" and rerun[gate.HELD_ENV] == "h"


def test_the_gate_marks_when_it_holds_the_lock_and_takes_its_role_from_the_environment(tmp_path):
    held = tmp_path / "held"
    role = gate.lock_role({gate.PRIORITY_ENV: "1", gate.HELD_ENV: str(held)})
    assert role == {"priority": True, "held": held}
    assert gate.lock_role({}) == {"priority": False, "held": None}
    with gate.gate_lock(tmp_path / "gate.lock", **role):
        assert held.exists()


def _hold(path, holder=None):
    """A gate holding `path`, as another process's would look to flock (and to dev status, given `holder`)."""
    f = open(path, "a+")  # noqa: SIM115 (held across the test, closed by it)
    fcntl.flock(f, fcntl.LOCK_EX)
    if holder:
        f.write(f"{holder}\n")
        f.flush()
    return f


def test_slot_paths_keep_the_first_slot_at_the_one_lock_path(tmp_path):
    """#461: slot 0 is `gate.lock` itself, so gate_slots = 1 is the old single lock."""
    path = tmp_path / "gate.lock"
    assert gate.slot_paths(path, 1) == [path]
    assert gate.slot_paths(path, 3) == [path, tmp_path / "gate.lock.1", tmp_path / "gate.lock.2"]


def test_three_slots_hold_three_gates_at_once_and_a_fourth_waits(tmp_path):
    """#461: with 3 slots, three gates hold one each at once and none says it waits (not even one that meets another
    passing through the wait queue to a free slot); a fourth says it waits, and gets the
    slot freed first."""
    path = tmp_path / "gate.lock"
    inside, release = threading.Barrier(4), threading.Event()
    holder_waited, waiting, fourth_in = threading.Event(), threading.Event(), threading.Event()

    def holder():
        with gate.gate_lock(path, slots=3, on_wait=holder_waited.set):
            inside.wait(10)
            release.wait(10)

    def fourth():
        with gate.gate_lock(path, slots=3, on_wait=waiting.set):
            fourth_in.set()

    holders = [threading.Thread(target=holder) for _ in range(3)]
    for t in holders:
        t.start()
    inside.wait(10)  # all three inside at once
    assert not holder_waited.is_set()
    assert len(gate.holders(path)) == 3
    t4 = threading.Thread(target=fourth)
    t4.start()
    assert waiting.wait(10)
    assert not fourth_in.wait(0.3)
    release.set()
    for t in [*holders, t4]:
        t.join(10)
    assert fourth_in.is_set()


@pytest.mark.parametrize("round_", range(5))
def test_integrates_gate_takes_the_next_free_slot_ahead_of_queued_worker_gates(tmp_path, round_):
    """#461 with #385: both slots busy, two worker gates and then integrate's wait; the first slot freed goes to
    integrate's gate, ahead of the worker gates that queued before it."""
    path = tmp_path / "gate.lock"
    busy = [_hold(p) for p in gate.slot_paths(path, 2)]
    order: list[str] = []
    names = ["worker-1", "worker-2", "integrate"]
    waiting = {n: threading.Event() for n in names}
    go = threading.Event()

    def gate_run(name):
        with gate.gate_lock(path, slots=2, on_wait=waiting[name].set, priority=name == "integrate"):
            order.append(name)
            go.wait(10)

    threads = {n: threading.Thread(target=gate_run, args=(n,)) for n in names}
    for name in names:
        threads[name].start()
        assert waiting[name].wait(10)
    busy[1].close()  # one slot frees; the other stays busy
    deadline = time.monotonic() + 10
    while not order and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.3)  # a worker gate that wrongly took the slot would show up here
    assert order == ["integrate"]
    busy[0].close()
    go.set()
    for t in threads.values():
        t.join(10)
    assert order[0] == "integrate" and sorted(order[1:]) == ["worker-1", "worker-2"]


def test_each_slot_records_its_holder_and_held_is_marked_once_a_slot_is_held(tmp_path):
    """#461: every holder's pid and root is recorded per slot; integrate's deadline starts when its slot is held."""
    path = tmp_path / "gate.lock"
    first = _hold(path, "111 /first")
    held = tmp_path / "held"
    with gate.gate_lock(path, slots=2, held=held):
        assert held.exists()
        assert gate.holders(path) == ["111 /first", f"{os.getpid()} {gate.root()}"]
    first.close()
    assert gate.holders(path) == []


def test_status_lists_every_gate_holder(monkeypatch, repo):
    monkeypatch.chdir(repo)
    assert workflow.gate_holder() == "idle"
    state = workflow.state_dir()
    a, b = _hold(state / "gate.lock", "1 /a"), _hold(state / "gate.lock.2", "2 /b")
    assert workflow.gate_holder() == "running (1 /a; 2 /b)"
    a.close()
    b.close()


def test_a_gate_configured_for_fewer_slots_still_takes_a_free_slot_another_gate_made(tmp_path):
    """#477 review: a gate with slots=1 (the suite audit, or a worktree whose pyproject still says gate_slots = 1)
    must not wait at the head of the queue while a slot made by a gate_slots = 3 gate is free: it uses every slot file
    on disk."""
    path = tmp_path / "gate.lock"
    for p in gate.slot_paths(path, 3):
        p.touch()  # as a gate_slots = 3 gate left them
    assert gate.effective_slots(path, 1) == 3 and gate.effective_slots(path, 5) == 5
    busy = _hold(path, "1 /three-slot-gate")
    waited, inside = threading.Event(), threading.Event()

    def narrow():
        with gate.gate_lock(path, slots=1, on_wait=waited.set):
            inside.set()

    t = threading.Thread(target=narrow)
    t.start()
    assert inside.wait(10)
    t.join(10)
    assert not waited.is_set()
    busy.close()


@given(cores=st.integers(1, 512), gb=st.floats(0, 4096), slots=st.integers(1, 64))
def test_workers_share_the_machine_among_the_gate_slots(cores, gb, slots):
    """#461: N concurrent gates together take at most half the cores; each takes at most one worker per 2 GB free now
    (free memory is already net of the gates running, so it is not divided again)."""
    n = gate.workers(slots, cores=cores, gb=gb)
    assert 1 <= n <= 16
    assert n == 1 or (n * slots <= cores // 2 and n * 2 <= gb)
    assert n == max(1, min(cores // 2 // slots, int(gb // 2), 16))
    assert gate.workers(1, cores=cores, gb=gb) == max(1, min(cores // 2, int(gb // 2), 16))


@pytest.mark.parametrize("value", [0, -1, True, "3", 1.5])
def test_gate_slots_must_be_a_positive_integer(value):
    with pytest.raises(config.ConfigError, match="gate_slots"):
        config.parse({"gate_slots": value})


def test_gate_slots_defaults_to_one_and_the_gate_reads_it(monkeypatch):
    assert config.Config().gate_slots == 1
    assert config.parse({"gate_slots": 4}).gate_slots == 4
    monkeypatch.setattr(gate.config, "get", lambda: config.Config(gate_slots=3))
    assert gate.gate_slots() == 3


def _info(**over):
    info = {"baseRefName": "main", "isCrossRepository": False, "headRefOid": "a" * 40, "state": "OPEN"}
    return info | over


def test_integrate_refuses_forks_and_non_main_bases():
    assert workflow.refusal(_info()) is None
    assert "main" in workflow.refusal(_info(baseRefName="dev"))
    assert "fork" in workflow.refusal(_info(isCrossRepository=True))


def test_merge_rechecks_both_shas_and_matches_the_head(monkeypatch):
    calls = []
    monkeypatch.setattr(board, "gh", lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(workflow, "_remote_shas", lambda n: ("h1", "m1"))
    assert workflow._merge_if_unchanged(7, "h1", "m1") is None
    assert calls[-1][:3] == ("pr", "merge", "7")
    assert calls[-1][calls[-1].index("--match-head-commit") + 1] == "h1"
    calls.clear()
    assert "head" in workflow._merge_if_unchanged(7, "h0", "m1")
    assert "main" in workflow._merge_if_unchanged(7, "h1", "m0")
    assert not calls


def test_integrate_reads_the_fields_it_checks(monkeypatch):
    seen = {}

    def gh(*args, **_):
        seen["fields"] = set(args[args.index("--json") + 1].split(","))
        return json.dumps(_info(isCrossRepository=True, isDraft=False, files=[], title="t", body=""))

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    assert workflow.integrate(7, harness_ok=True, dry_run=True) == 1
    assert {"headRefOid", "baseRefName", "isCrossRepository"} <= seen["fields"]


HEAD, MAIN = "h" * 40, "m" * 40


def _run(name, status="COMPLETED", conclusion="SUCCESS", workflow="ci", started="2026-10-11T12:00:00Z"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "workflowName": workflow,
        "status": status,
        "conclusion": conclusion,
        "startedAt": started,
        "completedAt": started if status == "COMPLETED" else "0001-01-01T00:00:00Z",
    }


def _thread(path="a.py", line=3, resolved=False, who="bot"):
    c = {"author": {"login": who}, "body": "please fix", "createdAt": "2026-10-11T00:00:00Z", "url": "u"}
    return {"isResolved": resolved, "path": path, "line": line, "comments": {"nodes": [c]}}


def _graphql(threads=(), comments=(), reviews=(), committed="2026-10-11T12:00:00Z", more_threads=False):
    pr = {
        "commits": {"nodes": [{"commit": {"committedDate": committed}}]},
        "comments": {"nodes": list(comments)},
        "reviews": {"nodes": list(reviews)},
        "reviewThreads": {"pageInfo": {"hasNextPage": more_threads}, "nodes": list(threads)},
    }
    return {"data": {"repository": {"pullRequest": pr}}}


@pytest.fixture
def fake_integrate(monkeypatch, tmp_path):
    """integrate against fake git, gh and subprocess: records every call in order. Knobs: fetched_head, gate_code,
    files (what git diff reports), checks / reviews / decision (PR view fields), threads / more_threads (GraphQL
    review threads), during_gate (knob updates applied while the gate runs), tip (HEAD of the merged tree: not HEAD
    when main was merged in or files were regenerated), merge_code / conflicts (merging main into the head),
    after_push (the rollups GitHub shows once the head moved, one per PR read; the last one repeats)."""
    world = {
        "events": [],
        "fetched_head": HEAD,
        "gate_code": 0,
        "files": ["src/app.py"],
        "checks": [_run("check")],
        "reviews": [],
        "decision": "",
        "threads": [],
        "more_threads": False,
        "during_gate": {},
        "tip": HEAD,
        "remote_head": HEAD,
        "merge_code": 0,
        "conflicts": [],
        "blocked": [],
        "status": [],
        "after_push": [],
        "git_version": "git version 2.43.0",
    }
    ev = world["events"]

    def git(*args, cwd=None, check=True):
        ev.append(("git", *args))
        if args[:2] == ("worktree", "add"):
            Path(args[3]).mkdir(parents=True, exist_ok=True)  # the gate process starts in it
        if args[0] == "rev-parse":
            return world["tip"] if args[1] == "HEAD" else world["fetched_head"] if "pr-" in args[1] else MAIN
        if args[0] == "--version":
            return world["git_version"]
        if args[0] == "diff" and "--diff-filter=U" in args:
            return "\n".join(world["conflicts"])
        if args[0] == "push":
            world["remote_head"] = world["tip"]
        if args[0] == "merge-base":
            return "b" * 40
        if args[0] == "diff":
            return "\n".join(world["files"])
        if args[0] == "ls-remote":
            return f"{MAIN}\trefs/heads/main"
        return ""

    def gh(*args, **_):
        ev.append(("gh", *args))
        if args[:2] == ("pr", "view"):
            fields = args[args.index("--json") + 1]
            rollup = world["checks"]
            if world["remote_head"] != HEAD and world["after_push"] and fields != "headRefOid":
                rollup = world["after_push"][0] if len(world["after_push"]) == 1 else world["after_push"].pop(0)
            return json.dumps(
                {"headRefOid": world["remote_head"]}
                if fields == "headRefOid"
                else _info(
                    headRefOid=world["remote_head"],
                    headRefName="5-x",
                    isDraft=False,
                    title="feat: t (#5)",
                    body="Closes #5",
                    statusCheckRollup=rollup,
                    latestReviews=world["reviews"],
                    reviewDecision=world["decision"],
                )
            )
        if args[:2] == ("api", "graphql"):
            return json.dumps(_graphql(threads=world["threads"], more_threads=world["more_threads"]))
        return ""

    def run(cmd, **_):
        ev.append(("run", *cmd))
        if "check" in cmd:
            world.update(world["during_gate"])
        code = world["gate_code"] if "check" in cmd else world["merge_code"] if "merge" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, "", "")

    monkeypatch.setattr(workflow, "git", git)
    monkeypatch.setattr(branches, "git", git)
    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "comment", lambda *a, **k: None)
    monkeypatch.setattr(board, "sync", lambda: [])
    claim = board.Claim(host="box", agent="builder-5", branch="5-x", at="", token=None, cid="")
    monkeypatch.setattr(board, "get", lambda n: SimpleNamespace(state="OPEN", status="review", claim=lambda: claim))
    monkeypatch.setattr(board, "block", lambda n, reason: world["blocked"].append((n, reason)))
    monkeypatch.setattr(board, "set_status", lambda n, status: world["status"].append((n, status)))
    monkeypatch.setattr(workflow.time, "sleep", lambda s: None)
    monkeypatch.setattr(branches, "worktrees", lambda: [])
    monkeypatch.setattr(workflow, "main_checkout", lambda: tmp_path)
    monkeypatch.setattr(workflow, "state_dir", lambda *p: _made(tmp_path.joinpath(*p)))
    monkeypatch.setattr(ci_status, "state_dir", lambda *p: _made(tmp_path.joinpath(*p)))
    workflow.config.get()  # resolve this repository's config with the real git, before subprocess.run is faked
    monkeypatch.setattr(workflow.subprocess, "run", run)
    monkeypatch.setattr(workflow.subprocess, "Popen", lambda cmd, **kw: FinishedGate(run(cmd, **kw).returncode))
    return world


def _made(path):
    path.mkdir(parents=True, exist_ok=True)  # as paths.state_dir does
    return path


def _index(events, pred):
    return next(i for i, e in enumerate(events) if pred(e))


def test_integrate_fetches_the_pull_ref_gates_then_merges_the_pinned_head(fake_integrate):
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    fetch = _index(ev, lambda e: e[:2] == ("git", "fetch"))
    assert any(a.startswith("+refs/pull/7/head:") for a in ev[fetch])
    gate_run = _index(ev, lambda e: e[0] == "run" and "check" in e)
    assert ("git", "worktree", "add", "--detach", str(workflow.state_dir("integrate", "pr-7")), HEAD) in ev[
        fetch:gate_run
    ]
    driver = [a for k, v in workflow.GENERATED_MERGE for a in ("-c", f"{k}={v}")]
    merge_cmd = ("run", "git", f"--attr-source={MAIN}", *driver, "merge", "--no-ff", "--no-edit", MAIN)
    assert merge_cmd in ev[fetch:gate_run]  # main into the head, by main's merge rules
    assert ("run", "uv", "run", "--quiet", "dev", "docs") in ev[fetch:gate_run]  # regenerated before the gate
    merge = _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))
    assert fetch < gate_run < merge
    assert ev[merge][ev[merge].index("--match-head-commit") + 1] == HEAD
    assert _index(ev, lambda e: e[:2] == ("git", "ls-remote")) < merge  # main re-read after the gate


def test_integrate_stops_when_the_fetched_head_is_not_the_prs(fake_integrate):
    fake_integrate["fetched_head"] = "x" * 40
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert not any(e[0] == "run" for e in fake_integrate["events"])
    assert not any(e[:3] == ("gh", "pr", "merge") for e in fake_integrate["events"])


def test_integrate_never_merges_on_a_gate_failure(fake_integrate):
    fake_integrate["gate_code"] = 1
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert not any(e[:3] == ("gh", "pr", "merge") for e in fake_integrate["events"])


def test_integrate_judges_harness_from_the_git_diff_of_the_pinned_shas(fake_integrate):
    fake_integrate["files"] = ["docs/research/a.md", "tests/conftest.py"]
    assert workflow.integrate(7, harness_ok=False, dry_run=False) == 2
    ev = fake_integrate["events"]
    diff = ev[_index(ev, lambda e: e[:2] == ("git", "diff"))]
    assert "--no-renames" in diff and diff[-1] == HEAD
    assert not any(e[0] == "run" for e in ev)


def _merged(ev):
    return any(e[:3] == ("gh", "pr", "merge") for e in ev)


@pytest.mark.parametrize(
    ("knob", "value", "expect"),
    [
        ("checks", [_run("check", conclusion="FAILURE")], "check: failing"),
        # a check still pending alone is waited for, not refused (#322): tests/test_workflow.py
        ("checks", [{"__typename": "StatusContext", "context": "ci/x", "state": "ERROR"}], "ci/x: failing"),
        ("reviews", [{"author": {"login": "ann"}, "state": "CHANGES_REQUESTED"}], "changes requested by ann"),
        ("decision", "CHANGES_REQUESTED", "changes requested"),
        ("threads", [_thread("devkit/x.py", 9)], "unresolved review thread devkit/x.py:9"),
        ("more_threads", True, "more than 100 review threads"),
        # duplicate runs of one check: the latest decides, whatever order the rollup lists them in
        ("checks", [_run("board", started="2026-10-11T13:00:00Z", conclusion="FAILURE"), _run("board")], "board"),
    ],
)
def test_integrate_refuses_unfinished_feedback_before_gating(fake_integrate, capsys, knob, value, expect):
    fake_integrate[knob] = value
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    out = capsys.readouterr().out
    assert "RESULT: refused" in out and expect in out and "--override" in out
    assert not any(e[0] == "run" for e in fake_integrate["events"])
    assert not _merged(fake_integrate["events"])


def test_integrate_passes_green_neutral_skipped_checks_and_resolved_threads(fake_integrate):
    fake_integrate["checks"] = [_run("a"), _run("b", conclusion="NEUTRAL"), _run("c", conclusion="SKIPPED")]
    fake_integrate["threads"] = [_thread(resolved=True)]
    fake_integrate["reviews"] = [{"author": {"login": "ann"}, "state": "APPROVED"}]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert _merged(fake_integrate["events"])


def test_integrate_judges_each_check_by_its_latest_run(fake_integrate):
    """Real rollups repeat a check per run (a downstream PR had two `board` runs): a later pass clears an earlier fail."""
    fake_integrate["checks"] = [
        _run("board", conclusion="FAILURE"),
        _run("board", started="2026-10-11T13:00:00Z"),
        _run("board", workflow="other", started="2026-10-11T11:00:00Z"),
    ]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert _merged(fake_integrate["events"])


def test_latest_checks_keeps_one_run_per_workflow_and_name():
    runs = [_run("a", conclusion="FAILURE"), _run("a", started="2026-10-11T13:00:00Z"), _run("a", workflow="w2")]
    status = {"__typename": "StatusContext", "context": "ci/x", "state": "SUCCESS"}
    latest = ci_status.latest_checks([*runs, status])
    assert [c is runs[1] or c is runs[2] or c is status for c in latest] == [True] * 3 and len(latest) == 3


@pytest.mark.parametrize(
    ("knob", "value"),
    [
        ("threads", [_thread("late.py", 5)]),
        ("checks", [_run("check"), _run("late", status="QUEUED", conclusion="")]),
        ("reviews", [{"author": {"login": "ann"}, "state": "CHANGES_REQUESTED"}]),
    ],
)
def test_integrate_refuses_feedback_that_arrives_during_the_gate(fake_integrate, capsys, knob, value):
    fake_integrate["during_gate"] = {knob: value}
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert "during the gate" in capsys.readouterr().out
    assert any(e[0] == "run" for e in fake_integrate["events"]) and not _merged(fake_integrate["events"])


def test_override_covers_only_the_feedback_it_was_given(fake_integrate, capsys):
    fake_integrate["threads"] = [_thread("a.py", 1)]
    fake_integrate["during_gate"] = {"threads": [_thread("a.py", 1), _thread("b.py", 2)]}
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="owner said so") == 1
    out = capsys.readouterr().out
    assert "b.py:2" in out and "a.py:1" not in out.split("during the gate")[1]
    assert not _merged(fake_integrate["events"])
    [sent] = _comments(fake_integrate["events"])  # the fix loop's send-back (#110), never the override's log
    assert board.FIX_MARK.search(sent) and "b.py:2" in sent and "owner said so" not in sent


def test_integrate_override_merges_and_logs_the_reason_on_the_pr(fake_integrate):
    fake_integrate["checks"] = [_run("citations", conclusion="FAILURE")]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="flaky link checker, see #12") == 0
    ev = fake_integrate["events"]
    log = _index(ev, lambda e: e[:3] == ("gh", "pr", "comment"))
    body = ev[log][ev[log].index("--body") + 1]
    assert "flaky link checker, see #12" in body and "citations: failing" in body
    assert "Overriding" in body and "Merged" not in body  # posted before a merge that may still not happen
    assert log < _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))


def test_integrate_override_needs_a_reason_and_dry_run_logs_nothing(fake_integrate, capsys):
    fake_integrate["threads"] = [_thread()]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="  ") == 1
    assert not _merged(fake_integrate["events"])
    assert workflow.integrate(7, harness_ok=True, dry_run=True, override="reviewed by hand") == 0
    assert not any(e[:3] == ("gh", "pr", "comment") for e in fake_integrate["events"])
    assert "would log" in capsys.readouterr().out


def _last_json(out):
    last = out.strip().splitlines()[-1]
    assert last.startswith("RESULT-JSON: "), last
    return json.loads(last.removeprefix("RESULT-JSON: "))


def test_a_conflict_is_sent_back_to_the_named_worker_and_keeps_the_claim(fake_integrate, capsys):
    """#38, #110: a real conflict comments the paths and the rebase steps on the PR, names the worker and moves the
    issue back to in-progress with its claim kept (no release, no block)."""
    fake_integrate["merge_code"], fake_integrate["conflicts"] = 1, ["docs/research/n.md", "src/app.py"]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    ev = fake_integrate["events"]
    assert not any(e[0] == "run" and "check" in e for e in ev) and not _merged(ev)
    assert ("git", "merge", "--abort") in ev
    note = next(e for e in ev if e[:3] == ("gh", "pr", "comment"))
    body = note[note.index("--body") + 1]
    assert "`docs/research/n.md`" in body and "`src/app.py`" in body and "builder-5@box" in body
    assert "merge `origin/main`" in body and board.FIX_MARK.search(body)
    assert fake_integrate["status"] == [(5, "in-progress")] and fake_integrate["blocked"] == []
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "conflict" and res["conflicts"] == fake_integrate["conflicts"]
    assert res["resume"] == ["builder-5@box"] and res["branch"] == "5-x" and res["exit"] == 1


def test_a_dry_run_conflict_comments_and_blocks_nothing(fake_integrate, capsys):
    fake_integrate["merge_code"], fake_integrate["conflicts"] = 1, ["a.py"]
    assert workflow.integrate(7, harness_ok=True, dry_run=True) == 1
    assert not any(e[:3] == ("gh", "pr", "comment") for e in fake_integrate["events"])
    assert fake_integrate["blocked"] == [] and _last_json(capsys.readouterr().out)["outcome"] == "conflict"


def test_a_pr_behind_main_is_updated_then_merged_at_the_gated_tip(fake_integrate, capsys):
    """#38: when the merged tree is not the head, integrate pushes it to the PR branch, then merges exactly it."""
    tip = "t" * 40
    fake_integrate["tip"] = tip
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    gate_run = _index(ev, lambda e: e[0] == "run" and "check" in e)
    push = _index(ev, lambda e: e[:2] == ("git", "push"))
    merge = _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))
    assert gate_run < push < merge
    assert ev[push] == ("git", "push", "origin", f"{tip}:refs/heads/5-x")  # a plain push: never forced
    assert ev[merge][ev[merge].index("--match-head-commit") + 1] == tip
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "merged" and res["updated"] and res["head"] == tip


def test_an_up_to_date_pr_is_not_pushed(fake_integrate, capsys):
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert not any(e[:2] == ("git", "push") for e in fake_integrate["events"])
    assert _last_json(capsys.readouterr().out)["updated"] is False


def test_a_dry_run_never_pushes_the_update(fake_integrate, capsys):
    fake_integrate["tip"] = "t" * 40
    assert workflow.integrate(7, harness_ok=True, dry_run=True) == 0
    assert not any(e[:2] == ("git", "push") for e in fake_integrate["events"])
    assert not _merged(fake_integrate["events"])
    assert _last_json(capsys.readouterr().out)["outcome"] == "would-merge"


def test_integrate_stops_when_github_never_shows_the_pushed_head(fake_integrate, monkeypatch, capsys):
    fake_integrate["tip"] = "t" * 40
    monkeypatch.setattr(workflow, "git", _no_push(workflow.git))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert not _merged(fake_integrate["events"])
    assert "not visible" in _last_json(capsys.readouterr().out)["reason"]


def _no_push(git):
    """A push that GitHub never reflects: the PR head stays where it was."""

    def wrapped(*args, **kw):
        return "" if args[0] == "push" else git(*args, **kw)

    return wrapped


@pytest.fixture
def clock(monkeypatch):
    """A fake clock that only `time.sleep` advances; records each sleep."""
    state = {"now": 0.0, "sleeps": []}

    def sleep(s):
        state["sleeps"].append(s)
        state["now"] += s

    monkeypatch.setattr(workflow.time, "sleep", sleep)
    monkeypatch.setattr(ci_status, "CHECKS_CLOCK", lambda: state["now"])
    return state


def _status(context, state):
    return {"__typename": "StatusContext", "context": context, "state": state, "startedAt": "", "completedAt": ""}


def test_pending_checks_counts_running_and_not_yet_registered_checks():
    info = {"statusCheckRollup": [_run("a", status="IN_PROGRESS"), _run("b"), _status("bot", "PENDING")]}
    assert ci_status.pending_checks(info, ["ci/b", "ci/c"]) == ["ci/a", "/bot", "ci/c (not started)"]
    done = {"statusCheckRollup": [_run("a", conclusion="FAILURE"), _status("bot", "ERROR")]}
    assert ci_status.pending_checks(done, ["ci/a", "/bot"]) == []  # failed is complete: unfinished() judges it


def test_after_pushing_an_update_integrate_waits_for_every_check_on_the_tip_then_merges(fake_integrate, clock, capsys):
    """#72: a required check that has not even registered, an optional job still running: both are waited for, with
    jittered sleeps from a floor, before the merge; nothing about branch protection is consulted."""
    tip = "t" * 40
    fake_integrate["tip"] = tip
    fake_integrate["checks"] = [_run("check"), _run("template")]
    fake_integrate["after_push"] = [
        [],
        [_run("check", status="QUEUED")],
        [_run("check", status="IN_PROGRESS"), _run("template", status="IN_PROGRESS")],
        [_run("check"), _run("template")],
    ]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    assert _index(ev, lambda e: e[:2] == ("git", "push")) < _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))
    assert len(clock["sleeps"]) >= 3 and fake_integrate["after_push"] == [[_run("check"), _run("template")]]
    floor = ci_status.CHECKS_FLOOR_S
    assert all(floor <= s <= floor + ci_status.CHECKS_CAP_S for s in clock["sleeps"])
    assert len(set(clock["sleeps"])) > 1  # jittered, not a fixed interval
    assert _last_json(capsys.readouterr().out)["outcome"] == "merged"


def test_a_check_that_fails_on_the_pushed_tip_refuses_the_merge(fake_integrate, clock, capsys):
    fake_integrate["tip"] = "t" * 40
    fake_integrate["after_push"] = [[_run("check", status="IN_PROGRESS")], [_run("check", conclusion="FAILURE")]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert not _merged(fake_integrate["events"])
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "refused" and res["feedback"] == ["check check: failing (FAILURE)"]


def test_an_override_still_covers_the_same_failure_on_the_pushed_tip(fake_integrate, clock):
    fake_integrate["tip"] = "t" * 40
    fake_integrate["checks"] = [_run("links", conclusion="FAILURE")]
    fake_integrate["after_push"] = [[_run("links", status="IN_PROGRESS")], [_run("links", conclusion="FAILURE")]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="flaky link checker") == 0


def test_a_timeout_reports_waiting_and_a_rerun_resumes_without_pushing(fake_integrate, clock, capsys, monkeypatch):
    """#72: checks still running at the deadline: outcome `waiting` with the tip; the rerun waits on that tip (it does
    not refuse its pending checks), pushes nothing, and merges once they pass."""
    tip = "t" * 40
    monkeypatch.setattr(workflow.config, "get", lambda: workflow.config.Config(checks_wait_s=120))
    fake_integrate["tip"] = tip
    fake_integrate["after_push"] = [[_run("check", status="IN_PROGRESS")]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = _last_json(capsys.readouterr().out)
    assert (res["outcome"], res["head"], res["pending"]) == ("waiting", tip, ["ci/check"])
    assert sum(clock["sleeps"]) == pytest.approx(120) and not _merged(fake_integrate["events"])

    fake_integrate["events"].clear()
    fake_integrate["fetched_head"] = tip  # GitHub now has the pushed tip as the head; main has not moved
    fake_integrate["after_push"] = [[_run("check", status="IN_PROGRESS")], [_run("check")]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    assert not any(e[:2] == ("git", "push") for e in ev)
    assert ev[_index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))][-2] == tip
    assert _last_json(capsys.readouterr().out)["outcome"] == "merged"
    assert not list(workflow.state_dir("integrate").glob("*.waiting.json"))


def test_a_head_that_moves_while_waiting_stops(fake_integrate, clock, capsys, monkeypatch):
    fake_integrate["tip"] = "t" * 40
    gh = board.gh

    def moved(*args, **kw):
        out = gh(*args, **kw)
        if args[:2] == ("pr", "view") and fake_integrate["remote_head"] != HEAD and "statusCheckRollup" in args[-1]:
            return json.dumps(json.loads(out) | {"headRefOid": "w" * 40})
        return out

    monkeypatch.setattr(board, "gh", moved)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert not _merged(fake_integrate["events"])
    assert "moved" in _last_json(capsys.readouterr().out)["reason"]


def test_issues_move_only_after_the_merge(fake_integrate, monkeypatch, capsys):
    """#38: an issue closes only after integrate merged; a board failure then still reports the merge."""
    ev = fake_integrate["events"]

    def merged_pr(*args):
        ev.append(("merged_pr", *args))
        raise board.BoardError("gh issue close: boom")

    monkeypatch.setattr(board, "merged_pr", merged_pr)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert _index(ev, lambda e: e[:3] == ("gh", "pr", "merge")) < _index(ev, lambda e: e[0] == "merged_pr")
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "merged" and "boom" in res["board_error"]


def test_no_issue_moves_when_the_merge_does_not_happen(fake_integrate, monkeypatch):
    monkeypatch.setattr(board, "merged_pr", lambda *a: pytest.fail("issue moved without a merge"))
    fake_integrate["gate_code"] = 1
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    fake_integrate["gate_code"] = 0
    assert workflow.integrate(7, harness_ok=True, dry_run=True) == 0


@pytest.mark.parametrize(
    ("knob", "value", "harness_ok", "outcome", "code"),
    [
        ("checks", [_run("check", conclusion="FAILURE")], True, "refused", 1),
        ("gate_code", 1, True, "gate-failed", 1),
        ("files", ["devkit/x.py"], False, "harness-review", 2),
        ("fetched_head", "x" * 40, True, "stopped", 1),
        ("tip", HEAD, True, "merged", 0),
    ],
)
def test_integrate_always_ends_with_one_machine_readable_line(
    fake_integrate, capsys, knob, value, harness_ok, outcome, code
):
    """#38: the lead branches on the last line, whatever happened."""
    fake_integrate[knob] = value
    assert workflow.integrate(7, harness_ok=harness_ok, dry_run=False) == code
    res = _last_json(capsys.readouterr().out)
    assert (res["pr"], res["outcome"], res["exit"]) == (7, outcome, code)


def test_integrate_needs_a_git_with_attr_source(fake_integrate, capsys):
    fake_integrate["git_version"] = "git version 2.39.5"
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "stopped" and "git >= 2.40" in res["reason"] and "2.39" in res["reason"]
    assert not any(e[0] == "run" and "merge" in e for e in fake_integrate["events"])
    assert not _merged(fake_integrate["events"])


def test_a_failed_merge_without_conflicted_paths_is_stopped_not_sent_back(fake_integrate, capsys):
    fake_integrate["merge_code"] = 128  # e.g. a hook or a lock, not a conflict: --diff-filter=U lists nothing
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "stopped" and "without a conflict" in res["reason"]
    assert fake_integrate["blocked"] == []
    assert not any(e[:3] == ("gh", "pr", "comment") for e in fake_integrate["events"])


def test_a_crash_after_the_push_still_ends_with_a_result_line(fake_integrate, monkeypatch, capsys):
    """Reviewer on #73: every exit prints RESULT-JSON, even an unexpected error once the PR branch was updated."""
    fake_integrate["tip"] = "t" * 40
    gh = board.gh

    def flaky(*args, **kw):
        if args[:2] == ("pr", "merge"):
            raise ConnectionError("network down")
        return gh(*args, **kw)

    monkeypatch.setattr(board, "gh", flaky)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert any(e[:2] == ("git", "push") for e in fake_integrate["events"])
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "stopped" and "ConnectionError: network down" in res["reason"]


def test_changed_paths_lists_both_sides_of_a_rename(repo, monkeypatch):
    monkeypatch.chdir(repo)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    (repo / "docs/research").mkdir(parents=True)
    _sh(repo, "git", "mv", "m.py", "docs/research/m.md")
    _sh(repo, "git", "commit", "-q", "-m", "rename")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    paths = workflow.changed_paths(base, head)
    assert set(paths) == {"m.py", "docs/research/m.md"}
    assert any(workflow.is_harness(p) for p in paths)


def test_a_stale_registered_worktree_is_pruned_before_adding(repo, tmp_path):
    wt = tmp_path / "wt"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    _sh(repo, "git", "worktree", "add", "--detach", str(wt), head)
    _sh(repo, "rm", "-rf", str(wt))  # registered, folder gone
    workflow._add_worktree(wt, head, cwd=repo)
    assert (wt / "m.py").exists()


# Workflow and lockfile hygiene: actionlint, zizmor and `uv lock --check` are gate stages.

PINNED = "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7"
CLEAN_WORKFLOW = f"""name: t
on: [push]
permissions: {{}}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - {PINNED}
        with: {{ persist-credentials: false }}
      - run: echo hi
"""


def _workflows(tmp_path, text):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/t.yml").write_text(text)
    return tmp_path


def test_clean_workflows_pass_actionlint_and_zizmor(tmp_path):
    root = _workflows(tmp_path, CLEAN_WORKFLOW)
    for ran in (gate.actionlint(root), gate.zizmor(root)):
        assert ran.ok, (ran.name, ran.problems)
        assert ran.checked == 1


def test_a_broken_workflow_fails_actionlint(tmp_path):
    root = _workflows(
        tmp_path, CLEAN_WORKFLOW.replace("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    needs: nope")
    )
    ran = gate.actionlint(root)
    assert not ran.ok
    assert any("nope" in p for p in ran.problems)


def test_an_unpinned_action_fails_zizmor(tmp_path):
    root = _workflows(tmp_path, CLEAN_WORKFLOW.replace(PINNED, "uses: actions/checkout@v7"))
    ran = gate.zizmor(root)
    assert not ran.ok
    assert any("unpinned-uses" in p for p in ran.problems)


def test_a_stale_lockfile_fails_the_lock_stage(tmp_path):
    project = '[project]\nname = "t"\nversion = "0.1.0"\nrequires-python = ">=3.13"\ndependencies = []\n'
    (tmp_path / "pyproject.toml").write_text(project)
    subprocess.run([gate.tool("uv") or "uv", "lock", "--offline"], cwd=tmp_path, check=True, capture_output=True)
    assert gate.lock_check(tmp_path).ok
    (tmp_path / "pyproject.toml").write_text(project.replace('">=3.13"', '">=3.12"'))
    ran = gate.lock_check(tmp_path)
    assert not ran.ok
    assert ran.problems


def test_a_missing_tool_is_skipped_with_a_notice_locally_and_fails_in_ci(tmp_path, monkeypatch):
    root = _workflows(tmp_path, CLEAN_WORKFLOW)
    monkeypatch.setattr(gate, "tool", lambda name: None)
    monkeypatch.delenv("CI", raising=False)
    ran = gate.zizmor(root)
    assert ran.ok
    assert any("not found" in w and "skipped" in w for w in ran.warnings)
    monkeypatch.setenv("CI", "true")
    assert not gate.zizmor(root).ok


def test_the_gate_runs_the_hygiene_stages():
    names = {getattr(s, "__name__", "") for s in gate.stages(False, {}, None)}
    assert {"lock_check", "actionlint", "zizmor"} <= names


def test_maintenance_runs_a_weekly_pinned_lychee_link_check():
    wf = yaml.safe_load((gate.root() / "template/.github/workflows/maintenance.yml").read_text())
    cron = wf[True]["schedule"][0]["cron"].split()  # PyYAML reads the key `on` as True
    assert cron[2:4] == ["*", "*"] and cron[4] != "*"  # one day of the week: weekly
    uses = [s["uses"] for job in wf["jobs"].values() for s in job["steps"] if "uses" in s]
    assert any(u.startswith("lycheeverse/lychee-action@") for u in uses)
    assert all(len(u.split("@")[1]) == 40 for u in uses)  # every action pinned to a commit hash


def test_maintenance_keeps_one_broken_links_issue_open_or_closes_it():
    wf = yaml.safe_load((gate.root() / "template/.github/workflows/maintenance.yml").read_text())
    job = wf["jobs"]["external-links"]
    assert job["permissions"]["issues"] == "write"
    steps = job["steps"]
    i = next(n for n, s in enumerate(steps) if s.get("uses", "").startswith("lycheeverse/lychee-action@"))
    lychee = steps[i]
    assert "docs/" in lychee["with"]["args"]
    assert lychee["with"]["fail"] is False  # the issue reports; a failed step would skip it
    report = steps[i + 1]
    script = report["run"]
    assert lychee["with"]["output"] in script
    assert f"steps.{lychee['id']}.outputs.exit_code" in report["env"]["EXIT_CODE"]
    assert 'title="Broken links"' in script
    for needed in ("gh issue list --state open --label type:bug", "gh issue edit", "gh issue create", "gh issue close"):
        assert needed in script
    assert "${{" not in script  # expressions only via env (no template injection)


def test_maintenance_link_check_skips_research_scratch():
    wf = yaml.safe_load((gate.root() / "template/.github/workflows/maintenance.yml").read_text())
    lychee = next(s for s in wf["jobs"]["external-links"]["steps"] if s.get("id") == "lychee")
    assert "--exclude-path 'docs/research/_scratch/'" in lychee["with"]["args"]  # #120: most of 1,245 errors


@pytest.mark.parametrize("lines", [3, 20_000])
def test_maintenance_issue_body_stays_under_githubs_limit(tmp_path, lines):
    """#120: an oversized report made `gh issue create` fail ("Body is too long (maximum is 65536 characters)")."""
    wf = yaml.safe_load((gate.root() / "template/.github/workflows/maintenance.yml").read_text())
    report = wf["jobs"]["external-links"]["steps"][-1]
    (tmp_path / "lychee").mkdir()
    errors = "".join(f"* [404] <https://example.com/é/{n}> | Failed: Network error\n" for n in range(lines))
    (tmp_path / "lychee/out.md").write_text(f"# Summary\n\n| Errors | {lines} |\n\n## Errors\n{errors}")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text('#!/bin/sh\nfor last; do :; done\n[ "$2" = create ] && cp "$last" "$CAPTURE"\nexit 0\n')
    (fake / "gh").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake}:{os.environ['PATH']}",
        "EXIT_CODE": "2",
        "CAPTURE": str(tmp_path / "sent.md"),
        "RUN_URL": "https://github.com/o/r/actions/runs/1",
    }
    run = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", report["run"]], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr
    body = (tmp_path / "sent.md").read_text()
    assert len(body) < 65_536 and body.startswith("# Summary") and "https://github.com/o/r/actions/runs/1" in body
    assert ("truncated" in body.lower()) == (lines > 3)
    assert f"example.com/é/{lines - 1}>" in run.stdout  # the full report is in the run log


# --- #76: merging with no Actions minutes (`dev integrate --local-ci`) ----------------------------------------------


def _job(name, job, conclusion="FAILURE"):
    return _run(name, conclusion=conclusion) | {"detailsUrl": f"https://github.com/o/r/actions/runs/1/job/{job}"}


@pytest.fixture
def actions(fake_integrate, monkeypatch, clock):
    """GitHub's REST side for --local-ci on top of fake_integrate. Knobs: protection (required_status_checks, None
    when unreadable), enabled (Actions permissions), steps (job id -> steps it ran), commands (config local_ci);
    statuses records each status posted."""
    knobs = {
        "protection": {"contexts": ["check"], "checks": [{"context": "check", "app_id": None}]},
        "enabled": True,
        "steps": {},
        "commands": {"check": []},
        "statuses": [],
    }
    inner = board.gh

    def gh(*args, **kw):
        if args[0] != "api" or args[1] in ("graphql", "user") or any(a.endswith("/comments") for a in args):
            return inner(*args, **kw)  # the PR conversation and the gh user (#110's fix loop) are fake_integrate's
        fake_integrate["events"].append(("gh", *args))
        path = next(a for a in args[1:] if a.startswith("repos/"))
        if path.endswith("/protection/required_status_checks"):
            if knobs["protection"] is None:
                raise board.BoardError("gh api: 404 Branch not protected")
            return json.dumps(knobs["protection"])
        if path.endswith("/actions/permissions"):
            return json.dumps({"enabled": knobs["enabled"]})
        if "/actions/jobs/" in path:
            return json.dumps({"steps": [{}] * knobs["steps"].get(path.rsplit("/", 1)[1], 0)})
        if "/statuses/" in path:
            fields = dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("repos/"))
            knobs["statuses"].append({"sha": path.rsplit("/", 1)[1], **fields})
            return "{}"
        raise AssertionError(args)

    monkeypatch.setattr(board, "gh", gh)
    cfg = workflow.config.Config(checks_wait_s=900, local_ci_wait_s=600, local_ci=knobs["commands"])
    monkeypatch.setattr(workflow.config, "get", lambda: cfg)
    return knobs


def _comments(ev):
    return [e[e.index("--body") + 1] for e in ev if e[:3] == ("gh", "pr", "comment")]


def _log_sha(tmp_path, tip):
    return hashlib.sha256((tmp_path / "integrate" / f"pr-7-{tip[:12]}.log").read_bytes()).hexdigest()


def test_billing_blocked_checks_are_run_locally_posted_as_statuses_with_the_log_hash_then_merged(
    fake_integrate, actions, capsys, tmp_path
):
    """#76: Actions failed the required job without running a step (a billing block): the local gate on the merged
    tree stands in; its pass is posted as the `check` status on that exact commit, with the log's hash; the PR says
    CI ran locally; the merge goes through."""
    fake_integrate["checks"] = [_job("check", 11)]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    ev = fake_integrate["events"]
    digest = _log_sha(tmp_path, HEAD)
    (status,) = actions["statuses"]
    assert (status["sha"], status["state"], status["context"]) == (HEAD, "success", "check")
    assert digest[:16] in status["description"]
    gate_run = _index(ev, lambda e: e[0] == "run" and "check" in e)
    post = _index(ev, lambda e: e[:3] == ("gh", "api", "-X") and "/statuses/" in e[4])
    assert gate_run < post < _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))
    (body,) = _comments(ev)
    assert "CI ran locally" in body and digest in body and HEAD in body
    assert _last_json(capsys.readouterr().out)["outcome"] == "merged"


def test_with_actions_disabled_nothing_is_waited_for(fake_integrate, actions, clock):
    fake_integrate["checks"] = []
    actions["enabled"] = False
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert [s["context"] for s in actions["statuses"]] == ["check"] and clock["sleeps"] == []


def test_a_required_check_that_never_queues_counts_as_actions_unavailable_after_the_wait(
    fake_integrate, actions, clock
):
    fake_integrate["checks"] = []
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert 600 <= sum(clock["sleeps"]) < 600 + ci_status.CHECKS_FLOOR_S + ci_status.CHECKS_CAP_S
    assert [s["context"] for s in actions["statuses"]] == ["check"]


def test_a_check_actions_ran_and_failed_is_never_covered_by_a_local_status(fake_integrate, actions, capsys):
    fake_integrate["checks"] = [_job("check", 11)]
    actions["steps"] = {"11": 5}  # the job ran its steps: a real failure
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 1
    assert actions["statuses"] == [] and not _merged(fake_integrate["events"])
    assert _last_json(capsys.readouterr().out)["outcome"] == "refused"


def test_a_check_actions_runs_is_waited_for_and_nothing_is_posted(fake_integrate, actions, clock):
    fake_integrate["tip"] = "t" * 40
    fake_integrate["checks"] = [_run("check", status="QUEUED")]
    fake_integrate["after_push"] = [[_run("check", status="IN_PROGRESS")], [_run("check")]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert actions["statuses"] == [] and clock["sleeps"]
    assert not any("CI ran locally" in b for b in _comments(fake_integrate["events"]))


def test_after_a_push_the_status_goes_on_the_gated_tip_not_the_old_head(fake_integrate, actions, tmp_path):
    tip = "t" * 40
    fake_integrate["tip"] = tip
    fake_integrate["checks"] = [_job("check", 11)]
    fake_integrate["after_push"] = [[_job("check", 12)]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    (status,) = actions["statuses"]
    assert status["sha"] == tip and _log_sha(tmp_path, tip)[:16] in status["description"]
    ev = fake_integrate["events"]
    assert _index(ev, lambda e: e[:2] == ("git", "push")) < _index(ev, lambda e: "/statuses/" in " ".join(e))


def test_a_failing_gate_posts_no_status(fake_integrate, actions, capsys):
    fake_integrate["checks"] = [_job("check", 11)]
    fake_integrate["gate_code"] = 1
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 1
    [sent] = _comments(fake_integrate["events"])  # the fix loop's send-back (#110) only
    assert actions["statuses"] == [] and board.FIX_MARK.search(sent)
    assert _last_json(capsys.readouterr().out)["outcome"] == "gate-failed"


def test_each_required_context_runs_its_own_stand_in_and_a_failure_posts_nothing(
    fake_integrate, actions, monkeypatch, capsys
):
    """`template` is not what the gate checks: its configured commands run in the merged tree ({tmpdir} filled)."""
    actions["protection"] = {"contexts": ["check", "template"], "checks": []}
    actions["commands"]["template"] = [["scripts/render_check.sh", "{tmpdir}/p"]]
    fake_integrate["checks"] = [_job("check", 11), _job("template", 12)]
    ran, code = [], {"stand_in": 0}
    run = workflow.subprocess.run

    def stand_in(cmd, **kw):
        if cmd[0] == "scripts/render_check.sh":
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, code["stand_in"])
        return run(cmd, **kw)

    monkeypatch.setattr(workflow.subprocess, "run", stand_in)
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert "{tmpdir}" not in ran[0][1] and ran[0][1].endswith("/p")
    assert sorted(s["context"] for s in actions["statuses"]) == ["check", "template"]
    assert "scripts/render_check.sh" in _comments(fake_integrate["events"])[0]

    actions["statuses"].clear()
    code["stand_in"] = 1
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 1
    assert actions["statuses"] == [] and _last_json(capsys.readouterr().out)["outcome"] == "gate-failed"


def test_local_ci_refuses_before_the_gate_when_a_status_cannot_count(fake_integrate, actions, capsys):
    """A required check bound to the GitHub Actions app ignores statuses from anyone else; a required check with no
    configured stand-in would be posted unverified. Both refuse, naming the fix."""
    actions["protection"] = {"contexts": ["check"], "checks": [{"context": "check", "app_id": 15368}]}
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 1
    reason = _last_json(capsys.readouterr().out)["reason"]
    assert '"app_id": -1' in reason and "required_status_checks" in reason
    actions["protection"] = {"contexts": ["check", "plugin"], "checks": []}
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 1
    assert "plugin" in _last_json(capsys.readouterr().out)["reason"]
    ev = fake_integrate["events"]
    assert not any(e[0] == "run" and "check" in e for e in ev) and actions["statuses"] == []


def test_unreadable_protection_falls_back_to_the_configured_contexts(fake_integrate, actions):
    actions["protection"] = None
    fake_integrate["checks"] = [_job("check", 11)]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert [s["context"] for s in actions["statuses"]] == ["check"]


def test_a_local_ci_dry_run_posts_nothing(fake_integrate, actions, capsys):
    fake_integrate["checks"] = [_job("check", 11)]
    assert workflow.integrate(7, harness_ok=True, dry_run=True, local_ci=True) == 0
    assert actions["statuses"] == [] and not _comments(fake_integrate["events"])
    assert "would wait for Actions" in capsys.readouterr().out


def test_without_local_ci_a_billing_blocked_check_still_refuses(fake_integrate, actions, capsys):
    fake_integrate["checks"] = [_job("check", 11)]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert actions["statuses"] == [] and _last_json(capsys.readouterr().out)["outcome"] == "refused"


def test_orient_syncs_the_board_first(monkeypatch, tmp_path):
    """#76: blockers re-sync at every orient (and integrate), so the scheduled board workflow is optional."""
    calls = []
    monkeypatch.setattr(board, "sync", lambda: calls.append("sync") or ["#4 -> ready"])
    monkeypatch.setattr(board, "fetch", lambda *a, **k: calls.append("fetch") or [])
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    monkeypatch.setattr(workflow, "root", lambda: tmp_path)
    monkeypatch.setattr(workflow, "status", lambda: "")
    out = workflow.orient()
    assert calls[:2] == ["sync", "fetch"] and "board sync: #4 -> ready" in out

    def down():
        raise board.BoardError("rate limited")

    monkeypatch.setattr(board, "sync", down)
    assert "board sync: failed (rate limited)" in workflow.orient()


@pytest.mark.parametrize("n", [1, 2])
def test_a_test_that_sleeps_past_the_timeout_fails_the_gate_with_a_timeout_message(tmp_path, monkeypatch, n):
    """#43: pytest-timeout ends a hung test at `test_timeout_s`, with or without xdist workers; the gate fails and
    says why, long before the sleep ends."""
    monkeypatch.setattr(gate.config, "get", lambda: gate.config.Config(test_timeout_s=1))
    (tmp_path / "test_hang.py").write_text("import time\n\ndef test_hang():\n    time.sleep(60)\n")
    cmd = gate.pytest_cmd([str(tmp_path)], n=n)
    assert "--timeout=1" in cmd
    started = time.monotonic()
    ran = gate._run("tests", [*cmd, "-p", "no:cacheprovider"], cwd=tmp_path)
    assert time.monotonic() - started < 30
    assert not ran.ok
    assert any("Timeout" in p and "pytest-timeout" in p for p in ran.problems), ran.problems


def test_bounded_run_kills_the_whole_process_group_at_its_deadline(tmp_path):
    """#43: a gate killed at its deadline must not leave a process under it (a pytest worker) holding the gate lock."""
    pid_file = tmp_path / "pid"
    started = time.monotonic()
    assert workflow.bounded_run([sys.executable, "-c", _spawner(pid_file)], 3) is None
    assert time.monotonic() - started < 20
    assert _ends(int(pid_file.read_text()))
    assert workflow.bounded_run([sys.executable, "-c", "raise SystemExit(3)"], 30) == 3


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_bounded_run_kills_the_group_when_its_caller_is_terminated(tmp_path, sig):
    """#43 review: the gate runs in a session of its own, so a SIGTERM or SIGHUP to integrate never reaches it;
    bounded_run kills the group on the way out instead of leaving the gate holding the lock."""
    pid_file = tmp_path / "pid"
    caller = (
        "import sys\nfrom devkit import workflow\n"
        f"workflow.bounded_run([sys.executable, '-c', {_spawner(pid_file)!r}], 60)\n"
    )
    proc = REAL_POPEN([sys.executable, "-c", caller])
    try:
        waited = time.monotonic() + 20
        while not pid_file.exists() or not pid_file.read_text():
            assert time.monotonic() < waited and proc.poll() is None, "the gate never started"
            time.sleep(0.05)
        proc.send_signal(sig)
        assert proc.wait(timeout=20) == 128 + sig
    finally:
        proc.kill()
    assert _ends(int(pid_file.read_text()))


def _spawner(pid_file):
    """A stand-in gate: starts a sleeping worker, writes the worker's pid to `pid_file`, then sleeps."""
    return (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )


def _ends(pid, within=10, read=Path.read_text):
    deadline = time.monotonic() + within
    while _alive(pid, read) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid, read)


def _alive(pid, read=Path.read_text):
    try:
        status = read(Path(f"/proc/{pid}/status"))
    except (FileNotFoundError, ProcessLookupError):  # #318: reaped between open and read (ESRCH) is dead too
        return False
    return "\nState:\tZ" not in status  # a zombie is dead, only not yet reaped


@pytest.mark.parametrize("gone", [FileNotFoundError, ProcessLookupError])
def test_a_process_reaped_while_its_status_is_read_counts_as_dead(gone):
    """#318: /proc/<pid>/status can vanish between open and read (ProcessLookupError); that pid is dead, not an
    error, and a live process still reads as alive."""
    assert _alive(os.getpid())

    def reaped(path):
        raise gone(3, "No such process")

    assert not _alive(os.getpid(), reaped)
    assert _ends(os.getpid(), within=1, read=reaped)


def test_integrate_stops_when_the_gate_runs_past_its_deadline(fake_integrate, monkeypatch, capsys):
    """#43: a hung gate is killed at `gate_deadline_s` and integrate ends `stopped`, never merging."""
    monkeypatch.setattr(workflow.config, "get", lambda: workflow.config.Config(gate_deadline_s=1))
    monkeypatch.setattr(workflow.subprocess, "Popen", REAL_POPEN)  # a real process, sleeping past the deadline
    hung = f"import os, pathlib, time; pathlib.Path(os.environ[{gate.HELD_ENV!r}]).touch(); time.sleep(60)"
    monkeypatch.setattr(workflow, "GATE", [sys.executable, "-c", hung])  # holds the lock, then hangs
    started = time.monotonic()
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert time.monotonic() - started < 30
    out = capsys.readouterr().out
    assert "RESULT: stopped" in out and "deadline" in out
    res = _last_json(out)
    assert res["outcome"] == "stopped" and "1 s" in res["reason"]
    assert not _merged(fake_integrate["events"])


def test_integrate_stops_when_its_gate_waits_past_the_lock_wait_bound(fake_integrate, monkeypatch, capsys):
    """#385: a gate that never gets the machine's gate lock is stopped at `gate_lock_wait_s`, and says so."""
    cfg = workflow.config.Config(gate_deadline_s=60, gate_lock_wait_s=1)
    monkeypatch.setattr(workflow.config, "get", lambda: cfg)
    monkeypatch.setattr(workflow, "HELD_POLL_S", 0.05)
    monkeypatch.setattr(workflow.subprocess, "Popen", REAL_POPEN)
    monkeypatch.setattr(workflow, "GATE", [sys.executable, "-c", "import time; time.sleep(60)"])
    started = time.monotonic()
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert time.monotonic() - started < 30
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "stopped" and "gate_lock_wait_s" in res["reason"]
    assert not _merged(fake_integrate["events"])


def _release_once_waited(worker, queue: Path, wait_s: float, seen: dict, bound_s: float = 30) -> None:
    """Close `worker` (the held gate lock) `wait_s` after a priority gate is seen waiting for it, a shared lock on
    `queue` (gate.gate_lock), so the wait is measured from an event, not from when the test started. Gives up after
    `bound_s` without a waiter. `seen` gets `waiting` and `released` (time.monotonic, shared across processes)."""
    tick = threading.Event()  # fake_integrate makes time.sleep a no-op; Event.wait still blocks
    deadline = time.monotonic() + bound_s
    with open(queue, "a+") as probe:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                seen["waiting"] = time.monotonic()
                break
            fcntl.flock(probe, fcntl.LOCK_UN)
            tick.wait(0.01)
    if "waiting" in seen:
        while (left := seen["waiting"] + wait_s - time.monotonic()) > 0:
            tick.wait(left)
    seen["released"] = time.monotonic()
    worker.close()


def test_integrate_whose_gate_waits_for_the_lock_longer_than_its_deadline_is_not_stopped(
    fake_integrate, monkeypatch, capsys, tmp_path
):
    """#385: gate_deadline_s counts the gate's run, not its wait for the machine's gate lock. A worker gate holds the
    lock until 3 s after integrate's gate (the real gate_lock, as integrate's environment sets it) is seen waiting for
    it; that gate then runs well under its 2 s deadline, and integrate goes on to merge. #407: every interval is
    measured from an event (waiting seen, lock released, lock acquired), never raced against a timer."""
    cfg = workflow.config.Config(gate_deadline_s=2, gate_lock_wait_s=60)
    monkeypatch.setattr(workflow.config, "get", lambda: cfg)
    monkeypatch.setattr(workflow, "HELD_POLL_S", 0.05)
    monkeypatch.setattr(workflow.subprocess, "Popen", REAL_POPEN)
    lock, acquired = tmp_path / "gate.lock", tmp_path / "acquired"
    stand_in = (
        "import os, time\nfrom pathlib import Path\nfrom devkit import gate\n"
        f"os.chdir({str(Path(__file__).parents[1])!r})  # gate_lock names the holding repo\n"
        f"with gate.gate_lock(Path({str(lock)!r}), **gate.lock_role(os.environ)):\n"
        f"    Path({str(acquired)!r}).write_text(repr(time.monotonic()))\n    time.sleep(0.5)\n"
    )
    monkeypatch.setattr(workflow, "GATE", [sys.executable, "-c", stand_in])
    worker = open(lock, "a+")  # noqa: SIM115 (released by the releaser)
    fcntl.flock(worker, fcntl.LOCK_EX)
    seen: dict = {}
    started = time.monotonic()
    queue = lock.with_name(lock.name + ".priority")
    release = threading.Thread(target=_release_once_waited, args=(worker, queue, 3, seen))
    release.start()
    try:
        workflow.integrate(7, harness_ok=True, dry_run=False)
    finally:
        release.join()
    res = _last_json(capsys.readouterr().out)
    assert "waiting" in seen, f"integrate's gate never waited for the held lock: {res}"
    assert acquired.exists(), f"integrate's gate never took the lock: {res}"
    assert float(acquired.read_text()) >= seen["released"] >= seen["waiting"] + 3  # it did wait, past the deadline
    assert time.monotonic() - started >= 3
    assert res["outcome"] != "stopped", res
    assert _merged(fake_integrate["events"]), res


def _dev_command_stage(**kw):
    (stage,) = [s for s in gate.stages(False, {"x": set()}, None, **kw) if "check_dev_commands" in s.__code__.co_names]
    return stage


def test_the_dev_command_stage_uses_the_parser_factory_it_is_handed(monkeypatch):
    """#449: cli hands gate its parser factory, so checks never imports cli to build one."""
    from devkit import checks, cli

    seen = []
    monkeypatch.setattr(checks, "check_dev_commands", lambda commands, parser: seen.append((commands, parser)))
    built = cli.build()
    _dev_command_stage(parser=lambda: built)()
    assert seen == [({"x": set()}, built)]


def test_the_dev_command_stage_refuses_to_run_without_a_parser_factory():
    with pytest.raises(ValueError, match="parser"):
        _dev_command_stage()()
