"""The suite audit (#114): tiers, the nine checks, the trigger orient shows, and the issues it keeps. Design and
thresholds: docs/research/test-suite-design.md §9-§10."""

import json
import subprocess
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, cli, frontmatter, suite_audit, workflow
from devkit.paths import root
from devkit.suite_audit import Static, scan

NOW = 1_800_000_000.0
FIXTURES = Path(__file__).parent / "fixtures/suite_audit"  # .txt: the weakening check reads them as text
DAY = 86400


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir()
        _sh(path, "git", "init", "-q", "-b", "main")
        _sh(path, "git", "config", "user.email", "t@t")
        _sh(path, "git", "config", "user.name", "t")
        _sh(path, "git", "config", "commit.gpgsign", "false")

    def commit(self, files: dict[str, str], msg: str = "change") -> str:
        for name, text in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(text)
        _sh(self.path, "git", "add", "-A")
        _sh(self.path, "git", "commit", "-q", "--allow-empty", "-m", msg)
        return _sh(self.path, "git", "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path) -> Repo:
    return Repo(tmp_path / "repo")


def suite_of(n: int) -> str:
    return "".join(f"def test_{i}():\n    assert {i} == {i}\n\n" for i in range(n))


# ---------------------------------------------------------------- tiers


@pytest.mark.parametrize(
    ("tests", "gate_s", "sloc", "tier"),
    [
        (999, None, 0, "S"),
        (1000, None, 0, "M"),
        (5000, None, 0, "M"),
        (5001, None, 0, "L"),
        (10, 119.9, 0, "S"),
        (10, 120, 0, "M"),
        (10, 601, 0, "L"),  # the higher of the two measures
        (6000, 10, 0, "L"),
        (0, None, 4999, "S"),  # no tests yet: production size
        (0, None, 5000, "M"),
        (0, None, 50001, "L"),
    ],
)
def test_tier_is_the_higher_of_test_count_and_gate_time_or_production_size(tests, gate_s, sloc, tier):
    assert suite_audit.tier_of(tests, gate_s, sloc) == tier


@given(st.integers(1, 20_000), st.integers(1, 20_000), st.one_of(st.none(), st.floats(0, 5000)))
def test_tier_never_falls_as_the_suite_grows(a, b, gate_s):
    lo, hi = sorted((a, b))
    assert "SML".index(suite_audit.tier_of(lo, gate_s)) <= "SML".index(suite_audit.tier_of(hi, gate_s))


# ---------------------------------------------------------------- the static scan


SUITE = (FIXTURES / "suite.py.txt").read_text()


def test_scan_counts_oracles_markers_and_quarantine_per_test():
    s = scan([("tests/test_a.py", SUITE), ("pkg/m.py", "# c\n\nx = 1\ny = 2\n")])
    assert s.tests == 8 and s.prod_sloc == 2
    assert s.oracles == 1 + 2 + 0 + 1 + 1 + 1 + 1 + 1
    assert s.single == 6 and s.no_oracle == ["tests.test_a::test_none"]
    assert s.slow == {"tests.test_a::test_slow", "tests.test_a.TestGroup::test_in_class"}
    assert s.flaky == [("tests.test_a::test_quarantined", [12]), ("tests.test_a::test_quarantined_bare", [])]
    assert s.xfail == 1 and s.e2e == 8  # the module's pytestmark reaches every test


def test_scan_counts_an_unparsable_test_file_by_regex_and_names_it():
    s = scan([("tests/test_b.py", "def test_a(:\n    pass\ndef test_b():\n    pass\n")])
    assert s.tests == 2 and s.unparsed == ["tests/test_b.py"]


def test_py_files_reads_one_commit_and_skips_docs_and_scratch(repo):
    first = repo.commit(
        {"tests/test_a.py": suite_of(3), "pkg/m.py": "x = 1\n", "docs/research/test_x.py": "def test_z(): pass\n"}
    )
    repo.commit({"tests/test_a.py": suite_of(5)})
    assert suite_audit.test_count(repo.path, first) == 3
    assert suite_audit.test_count(repo.path, "HEAD") == 5
    paths = [p for p, _ in suite_audit.py_files(repo.path, "HEAD")]
    assert paths == ["pkg/m.py", "tests/test_a.py"]


# ---------------------------------------------------------------- the checks


def run(**tests) -> dict:
    return {"at": 1, "tests": {k: list(v) for k, v in tests.items()}}


def test_mixed_outcomes_on_one_tree_are_flaky_and_skips_are_not():
    runs = [
        run(a=("passed", 0.1), b=("passed", 0.1), c=("skipped", 0)),
        run(a=("failed", 0.1), b=("passed", 0.1), c=("passed", 0)),
    ]
    assert suite_audit.mixed(runs) == ["a"]
    c = suite_audit.check_flaky(runs, [])
    assert [p.split(":")[0] for p in c.problems] == ["a"]


@given(st.lists(st.sampled_from(["passed", "failed", "error", "skipped"]), min_size=1, max_size=8))
def test_an_id_with_one_outcome_kind_is_never_flaky(outs):
    runs = [run(t=(o, 0)) for o in outs]
    has_pass, has_fail = "passed" in outs, bool({"failed", "error"} & set(outs))
    assert (suite_audit.mixed(runs) == ["t"]) == (has_pass and has_fail)


def test_without_reruns_the_flaky_check_is_skipped_and_history_ids_are_suspects():
    c = suite_audit.check_flaky([], [run(a=("passed", 0)), run(a=("error", 0))])
    assert c.skipped and not c.problems and "a" in c.info


def test_parity_compares_both_directions_and_ignores_flaky_and_skipped():
    tree = {"a": "passed", "b": "failed", "f": "passed", "s": "skipped", "only_tree": "passed"}
    clone = {"a": "passed", "b": "passed", "f": "failed", "s": "passed", "only_clone": "passed"}
    c = suite_audit.check_parity(tree, clone, {"f"})
    assert [p.split(":")[0] for p in c.problems] == ["b", "only_clone", "only_tree"]
    assert suite_audit.check_parity(None, None, set()).skipped


def test_quarantine_needs_an_open_young_issue_per_marker_and_a_cap():
    s = Static(tests=100, flaky=[("t1", [1]), ("t2", []), ("t3", [3]), ("t4", [4])])
    issues = {1: ("OPEN", "2027-01-14T00:00:00+00:00"), 3: ("CLOSED", "2027-01-14T00:00:00+00:00")}
    issues[4] = ("OPEN", "2026-12-01T00:00:00+00:00")
    c = suite_audit.check_quarantine(s, "S", issues, NOW)  # NOW is 2027-01-15
    text = "\n".join(c.problems)
    assert "4 quarantined tests, over the cap of 3" in text
    assert "t2: `flaky` marker names no issue" in text and "t3: its issue #3 is closed" in text
    assert "t4: quarantined" in text and "t1" not in text
    assert suite_audit.check_quarantine(Static(tests=1000), "L", {}, NOW).problems == []


def ci_run(name: str, conclusion: str, hours_ago: float, event: str = "push") -> dict:
    from datetime import UTC, datetime

    when = datetime.fromtimestamp(NOW - hours_ago * 3600, UTC).isoformat().replace("+00:00", "Z")
    return {"workflowName": name, "conclusion": conclusion, "createdAt": when, "event": event, "status": "completed"}


def test_ci_flags_three_red_runs_a_day_of_red_and_a_red_schedule():
    runs = [ci_run("ci", "success", 10), *(ci_run("ci", "failure", h) for h in (3, 2, 1))]
    runs += [ci_run("slow", "success", 50), ci_run("slow", "failure", 30)]  # 1 red run, but for 30 h
    runs += [ci_run("weekly", "failure", 5, "schedule"), ci_run("weekly", "success", 4, "push")]
    c = suite_audit.check_ci(runs, NOW, NOW, True)
    assert [p.split(":")[0] for p in c.problems] == ["ci", "slow", "weekly"]


def test_ci_green_is_clean_but_stale_or_absent_ci_is_not():
    green = [ci_run("ci", "success", 1)]
    assert suite_audit.check_ci(green, NOW, NOW, True).problems == []
    assert "older than main" in suite_audit.check_ci(green, NOW + 3 * DAY, NOW, True).problems[0]
    assert "Actions disabled" in suite_audit.check_ci([], NOW, NOW, True).problems[0]
    assert suite_audit.check_ci([], NOW, NOW, False).problems == []
    assert suite_audit.check_ci(None, NOW, NOW, True).skipped


def test_never_run_lists_ids_no_job_runs_stray_files_and_lax_xfail():
    c = suite_audit.check_never_run({"a", "b", "c"}, {"a", "b"}, ["src/test_x.py"], 2, False, [], 0)
    assert len(c.problems) == 3 and c.problems[0].startswith("c:")
    assert suite_audit.check_never_run({"a"}, {"a"}, [], 2, True, [], 0).problems == []
    rose = suite_audit.check_never_run({"a"}, {"a"}, [], 0, False, ["x", "y"], 1)
    assert rose.problems == ["tests skipped in CI rose from 1 to 2"] and "x" in rose.info
    first = suite_audit.check_never_run({"a"}, {"a"}, [], 0, False, ["x", "y"], None)  # no audit to compare with
    assert first.problems == [] and "x" in first.info


def test_test_files_outside_testpaths_except_in_nested_projects(repo):
    repo.commit(
        {
            "tests/test_a.py": "",
            "pkg/test_stray.py": "",
            "pkg/util_test.py": "",
            "template/pyproject.toml.jinja": "",
            "template/tests/test_project.py": "",
            "docs/research/test_x.py": "",
        }
    )
    assert suite_audit.outside_testpaths(repo.path, ["tests"]) == ["pkg/test_stray.py", "pkg/util_test.py"]


def test_ci_pytest_commands_are_read_from_workflows():
    wf = {"ci.yml": "run: uv run dev check --no-cache\n      - run: uv run pytest -m live tests/test_security.py\n"}
    assert suite_audit.ci_pytest_selections(wf) == (False, [("live", ["tests/test_security.py"])])
    assert suite_audit.ci_pytest_selections({"n.yml": "run: uv run dev check --full\n"})[0]


def test_slow_flags_unmarked_slow_tests_and_a_gate_over_budget():
    runs = [run(**{"m.t::fast": ("passed", 0.1), "m.t::heavy[1]": ("passed", 9), "m.t::marked": ("passed", 30)})]
    c = suite_audit.check_slow(runs, {"m.t::marked"}, "S", [100.0, 130.0])
    assert [p.split(":")[0] for p in c.problems[:1]] == ["m.t"] and "heavy[1]" in c.problems[0]
    assert any("over the tier S budget" in p for p in c.problems)
    assert suite_audit.check_slow(runs, {"m.t::marked"}, "M", [100.0]).problems == []  # 9 s is fine in tier M
    outlier = suite_audit.check_slow([], set(), "S", [10, 11, 10, 12, 11, 10, 11, 12, 10, 60])
    assert any("outlier" in p for p in outlier.problems)
    assert suite_audit.check_slow([], set(), "S", []).skipped


def test_drift_flags_a_falling_ratio_or_oracle_count_and_a_rising_single_oracle_share():
    then = Static(tests=100, test_sloc=1000, prod_sloc=1000, oracles=250, single=30)
    same = suite_audit.check_drift(then, then, "S")
    assert same.problems == []
    worse = Static(tests=100, test_sloc=800, prod_sloc=1000, oracles=200, single=40)
    assert len(suite_audit.check_drift(then, worse, "S").problems) == 3
    thin = Static(tests=10, test_sloc=100, prod_sloc=1000, oracles=20, single=1)
    assert "below the tier S floor" in suite_audit.check_drift(None, thin, "S").problems[0]


def test_floor_is_stale_by_the_gates_own_rule():
    assert suite_audit.check_floor(1000, {"min_tests": 950}).problems == []
    assert suite_audit.check_floor(1000, {"min_tests": 800}).problems == ["raise min_tests to 950 (1000 passed)"]
    assert suite_audit.check_floor(None, {"min_tests": 1}).skipped


def test_fix_commits_must_touch_a_test_80_percent_of_the_time():
    ok = [(f"{i:040x}", "fix: x", ["pkg/m.py", "tests/test_m.py"]) for i in range(4)]
    bare = ("f" * 40, "Fix the parser bug", ["pkg/p.py"])
    docs = ("e" * 40, "fix typo", ["README.md"])
    assert suite_audit.check_fixes([*ok, bare, docs]).problems == []  # 4 of 5 code fixes
    c = suite_audit.check_fixes([*ok[:3], bare, ("d" * 40, "hotfix", ["pkg/q.py"])])
    assert [p.split()[0] for p in c.problems] == ["fffffffff", "ddddddddd"]


def test_judgement_items_are_oracle_less_tests_and_a_missing_end_to_end_marker():
    assert suite_audit.judgement(Static(tests=3, e2e=1)) == []
    items = suite_audit.judgement(Static(tests=3, no_oracle=["m::t"]))
    assert items[0].startswith("1 test(s) with no assert") and items[1] == "  m::t" and "end-to-end" in items[2]


# ---------------------------------------------------------------- the trigger


def test_never_audited_is_due_once_there_are_tests(repo):
    repo.commit({"pkg/m.py": "x = 1\n"})
    assert not suite_audit.due(repo.path, None)["due"]
    repo.commit({"tests/test_a.py": suite_of(2)})
    assert suite_audit.due(repo.path, None)["why"] == ["no audit recorded"]


def test_due_after_100_first_parent_commits_growth_or_a_tier_change(repo):
    base = repo.commit({"tests/test_a.py": suite_of(200)})
    snap = {"commit": base, "proxy": 200, "at": NOW - 3 * DAY}
    for i in range(99):
        repo.commit({}, f"c{i}")
    d = suite_audit.due(repo.path, snap, now=NOW)
    assert not d["due"] and d["days_since"] == 3.0
    repo.commit({}, "c99")
    assert suite_audit.due(repo.path, snap)["why"] == ["100 first-parent commits since the last audit"]
    small = {"commit": base, "proxy": 100}  # +100 tests and 100%: growth
    assert "tests grew 100 -> 200" in suite_audit.due(repo.path, small)["why"]
    border = {"commit": base, "proxy": 190}  # +10: below the 50-test minimum
    assert all("grew" not in w for w in suite_audit.due(repo.path, border)["why"])
    big = {"commit": base, "proxy": 1200}
    assert "tier M -> S" in suite_audit.due(repo.path, big)["why"]
    gone = {"commit": "0" * 40, "proxy": 200}
    assert "not in this history" in suite_audit.due(repo.path, gone)["why"][0]


def test_orient_lines_are_empty_outside_a_repository_and_never_raise(tmp_path, monkeypatch, repo):
    assert suite_audit.orient_lines(tmp_path) == []
    repo.commit({"tests/test_a.py": suite_of(1)})

    def boom(*a, **k):
        raise board.BoardError("rate limited")

    # board.repo() reads the origin remote, which a fresh clone points at a path: pin it (found by this audit's own
    # fresh-clone parity check, the test passed in the worktree only)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "gh", boom)
    assert suite_audit.orient_lines(repo.path) == ["suite audit trigger failed: rate limited"]
    monkeypatch.setattr(suite_audit, "_orient", lambda r: time.sleep(5) or [])
    assert "skipped: over 0.2 s" in suite_audit.orient_lines(repo.path, budget_s=0.2)[0]


def test_dev_orient_shows_a_due_audit_with_what_the_lead_runs(monkeypatch, repo):
    repo.commit({"tests/test_a.py": suite_of(1)})
    runs = json.dumps([ci_run("ci", "success", 1)])
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "gh", lambda *a, input=None: runs if a[:2] == ("run", "list") else "[]")
    monkeypatch.setattr(suite_audit, "state_dir", lambda *a: repo.path / ".git")  # not this machine's test history
    monkeypatch.setattr(board, "sync", lambda: [])
    monkeypatch.setattr(board, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    monkeypatch.setattr(workflow, "root", lambda: repo.path)
    monkeypatch.setattr(workflow, "status", lambda: "")
    line = next(x for x in workflow.orient().splitlines() if x.startswith("## Suite audit due"))
    assert "(no audit recorded)" in line and "uv run dev suite-audit report --file" in line
    assert "harness-ops:suite-auditor" in line


# ---------------------------------------------------------------- runs in a fresh clone


FLAKY_SUITE = (FIXTURES / "flaky_suite.py.txt").read_text()


def test_reruns_in_a_fresh_clone_find_a_flaky_test_and_a_tree_only_pass(repo, tmp_path, monkeypatch):
    """The clone sees HEAD only: a test that passes on an untracked file passes in the tree and fails there. The
    clone has no origin (#148 review), so a test that needs none passes there only."""
    repo.commit({"tests/test_s.py": FLAKY_SUITE, ".gitignore": "local.txt\n"})
    _sh(repo.path, "git", "remote", "add", "origin", "https://github.com/o/r.git")
    (repo.path / "tests/local.txt").write_text("x")
    monkeypatch.setenv("SUITE_AUDIT_COUNTER", str(tmp_path / "counter"))
    monkeypatch.setattr(suite_audit.gate, "workers", lambda: 1)
    runs, tree = suite_audit.rerun(repo.path, [1, 2])
    assert len(runs) == 2 and suite_audit.mixed(runs) == ["tests.test_s::test_alternates"]
    parity = suite_audit.check_parity(tree, suite_audit.outcomes(runs[0]), set(suite_audit.mixed(runs)))
    assert parity.problems == [
        "tests.test_s::test_needs_an_untracked_file: passed in the working tree, failed in a fresh clone",
        "tests.test_s::test_runs_where_no_origin_is_set: failed in the working tree, passed in a fresh clone",
    ]


def test_at_tier_m_only_the_history_suspects_rerun_after_one_whole_run(repo, tmp_path, monkeypatch):
    """Note §9: whole-suite reruns at tier S only; above it the suite runs once (parity) and suspects rerun."""
    repo.commit({"tests/test_s.py": FLAKY_SUITE, ".gitignore": "local.txt\n"})
    monkeypatch.setenv("SUITE_AUDIT_COUNTER", str(tmp_path / "counter"))
    monkeypatch.setattr(suite_audit.gate, "workers", lambda: 1)
    ledger = [run(**{"tests.test_s::test_alternates": ("failed", 0), "tests.test_s::test_steady": ("passed", 0)})]
    ledger.append(run(**{"tests.gone::test_old": ("error", 0)}))  # a test no longer in the tree is skipped
    assert suite_audit.suspects(ledger) == ["tests.gone::test_old", "tests.test_s::test_alternates"]
    runs, _ = suite_audit.rerun(repo.path, [1, 2, 3], suite_audit.suspects(ledger))
    assert [len(r["tests"]) for r in runs] == [4, 1, 1, 1]
    assert suite_audit.mixed(runs) == ["tests.test_s::test_alternates"]


def test_node_id_maps_junit_ids_back_to_files_and_classes(repo):
    repo.commit({"tests/test_x.py": "", "tests/sub/test_y.py": ""})
    assert suite_audit.node_id(repo.path, "tests.test_x::t[1]") == "tests/test_x.py::t[1]"
    assert suite_audit.node_id(repo.path, "tests.sub.test_y.TestC::t") == "tests/sub/test_y.py::TestC::t"
    assert suite_audit.node_id(repo.path, "tests.gone::t") is None


def test_collect_reads_node_ids_after_marker_deselection(repo):
    repo.commit(
        {"tests/test_m.py": "import pytest\n\ndef test_a():\n    pass\n\n@pytest.mark.slow\ndef test_b():\n    pass\n"}
    )
    every = suite_audit.collect(repo.path, None, ["tests"])
    assert every == {"tests/test_m.py::test_a", "tests/test_m.py::test_b"}
    assert suite_audit.collect(repo.path, "not slow", ["tests"]) == {"tests/test_m.py::test_a"}


# ---------------------------------------------------------------- filing


class FakeGh:
    def __init__(self, issues: list[dict]):
        self.issues, self.calls = issues, []

    def __call__(self, *args, input=None):
        self.calls.append((args, input))
        if args[:2] == ("issue", "list"):
            state = args[args.index("--state") + 1]
            return json.dumps([i for i in self.issues if state == "all" or i["state"] == "OPEN"])
        if args[:2] == ("issue", "create"):
            return "https://github.com/o/r/issues/99\n"
        return ""


def report_with(problems: dict[str, list[str]], skipped: tuple[str, ...] = ()) -> dict:
    checks = [
        {
            "key": k,
            "title": t,
            "done": "d",
            "problems": problems.get(k, []),
            "info": [],
            "skipped": "x" if k in skipped else "",
        }
        for k, t in (
            ("flaky", "flaky tests"),
            ("slow", "slow tests"),
            ("floor", "stale test floor"),
            ("ci", "red CI on main"),
        )
    ]
    snap = {"commit": "a" * 40, "proxy": 10, "collected": 12, "tier": "S", "ci_skipped": 0, "at": int(NOW)}
    return {
        "commit": "aaaaaaaaa", "tier": "S", "collected": 12, "gate_selected": 12, "prod_sloc": 100, "gate_s": 9.0,
        "reruns": 5, "base": "", "tracker": None, "checks": checks, "judgement": [], "notes": [], "snapshot": snap,
        "seconds": 1.0,
    }  # fmt: skip


def test_file_report_opens_updates_closes_and_records_the_snapshot(monkeypatch):
    open_issues = [
        {"number": 5, "title": "Suite health: slow tests", "state": "OPEN", "body": ""},
        {"number": 6, "title": "Suite health: stale test floor", "state": "OPEN", "body": ""},
        {"number": 7, "title": "Suite health: red CI on main", "state": "OPEN", "body": ""},
    ]
    gh = FakeGh(open_issues)
    created = []
    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(
        board, "new", lambda title, kind, prio, ctx, done, *a: created.append((title, kind, done)) or "url"
    )
    r = report_with({"flaky": ["t: passed and failed"], "slow": ["s: 9 s"]}, skipped=("ci",))
    out = suite_audit.file_report(r)
    assert created == [("Suite health: flaky tests", "test", ["d"])]
    verbs = {(a[0], a[1], a[2]) for a, _ in gh.calls if a[0] == "issue"}
    assert ("issue", "edit", "5") in verbs and ("issue", "close", "6") in verbs
    assert ("issue", "close", "7") not in verbs  # a skipped check measured nothing: its issue stays open
    tracker_body = next(i for a, i in gh.calls if a[:2] == ("issue", "create"))
    assert any("#99 Suite health: audit recorded" in line for line in out)
    gh.issues.append({"number": 99, "title": "Suite health", "state": "CLOSED", "body": tracker_body})
    assert suite_audit.last_snapshot() == (99, r["snapshot"])


def test_issue_bodies_stay_under_githubs_limit():
    c = {
        "title": "never-run tests",
        "done": "d",
        "problems": [f"tests/x.py::t{i}: " + "y" * 4000 for i in range(500)],
        "info": [],
    }
    body = suite_audit.issue_body(c, "abc")
    assert len(body) < 65_536 and "and 480 more" in body and "## DONE WHEN" in body
    assert len(suite_audit.cap("z\n" * 100_000)) <= suite_audit.BODY_CAP


def test_render_marks_each_check_and_names_the_auditor_for_judgement_items():
    r = report_with({"flaky": ["t: passed and failed"]}, skipped=("ci",))
    r["judgement"] = ["1 test(s) with no assert in their body (...):", "  m::t"]
    text = suite_audit.render(r)
    assert "[FAIL] flaky tests: 1" in text and "[ok  ] slow tests" in text and "[skip] red CI on main" in text
    assert "judgement items for harness-ops:suite-auditor (1):" in text


# ---------------------------------------------------------------- the agent and the lead


def test_the_suite_auditor_gets_read_tools_only():
    """Read-only like the architect (#113): it files issues and never edits or deletes a test (note §7, §10)."""
    meta, body = frontmatter.read(root() / "plugins/harness-ops/agents/suite-auditor.md")
    assert meta is not None
    assert [t.strip() for t in str(meta["tools"]).split(",")] == ["Read", "Grep", "Glob", "Bash"]
    assert not {"disallowedTools", "permissionMode", "hooks"} & set(meta)
    assert "at most 5" in body and "never edit" in body


def test_the_lead_runs_the_audit_only_when_orient_says_it_is_due():
    text = (root() / "plugins/harness-ops/skills/lead/SKILL.md").read_text()
    assert "`Suite audit due`" in text and "harness-ops:suite-auditor" in text


# ---------------------------------------------------------------- the suite-auditor is read-only, enforced (#146)
AUDITOR_OK = [
    "uv run dev suite-audit report --quick",
    "uv run dev suite-audit report --quick --json",
    "uv run dev suite-audit status",
    "uv run dev suite-audit status --json",
    "uv run dev code show test_x",
    "uv run dev board new --type test --title 'x: y' --done 'z'",
    "gh issue list --search 'in:title flaky'",
    "grep -rn 'def test' tests | wc -l",
]
AUDITOR_DENIED = [
    "uv run dev suite-audit report --file",  # editing and closing the check issues stays with the lead
    "uv run dev suite-audit report --quick --fi",  # any prefix of --file (argparse abbreviations, #148 review)
    "uv run dev suite-audit report --quick --f",
    "uv run dev suite-audit report --quick --json --fil",
    "uv run dev suite-audit report --quick --file=x",
    "uv run dev suite-audit report --json",  # a full report runs the suite, in the working tree too
    "uv run dev suite-audit report",
    "uv run dev suite-audit report --qui",
    "uv run dev consolidation report",  # the architect's report, not this agent's
    "sed -i s/assert/pass/ tests/test_x.py",
    "rm tests/test_x.py",
    "git rm tests/test_x.py",
    "git commit -m x",
    "echo 'import pytest' > tests/conftest.py",
    "uv run pytest tests",
    "gh issue close 5",
]


@pytest.mark.parametrize(
    "argv",
    [["suite-audit", "report", "--fi"], ["suite-audit", "report", "--quick", "--f"], ["board", "new", "--ti", "x"]],
)
def test_dev_refuses_abbreviated_options_at_every_level(argv):
    """#148 review: `--fi` must not mean `--file`, or the guard's allowlist is bypassed; subparsers inherit."""
    assert cli.build().parse_args(["suite-audit", "report", "--quick", "--file"]).file
    with pytest.raises(SystemExit):
        cli.build().parse_args(argv)


@pytest.mark.parametrize("cmd", AUDITOR_OK)
def test_suite_auditor_may_read_measure_and_file_issues(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "agent_type": "harness-ops:suite-auditor"})
    assert code == 0, err


@pytest.mark.parametrize("cmd", AUDITOR_DENIED)
def test_suite_auditor_may_not_edit_delete_or_close(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "agent_type": "harness-ops:suite-auditor"})
    assert code == 2 and "read-only" in err, (cmd, err)


def test_the_architect_does_not_gain_the_suite_audit(hook):
    code, _, err = hook(
        "guard_bash.py", {"tool_input": {"command": "uv run dev suite-audit status"}, "agent_type": "architect"}
    )
    assert code == 2 and "read-only" in err
