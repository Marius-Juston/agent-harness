"""Gate and integrate integrity: the gate cannot be talked into a pass, and integrate merges what it gated."""

import fcntl
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from devkit import board, gate, workflow


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


def _run(name, status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def _thread(path="a.py", line=3, resolved=False, who="bot"):
    c = {"author": {"login": who}, "body": "please fix", "createdAt": "2026-10-11T00:00:00Z", "url": "u"}
    return {"isResolved": resolved, "path": path, "line": line, "comments": {"nodes": [c]}}


def _graphql(threads=(), comments=(), reviews=(), committed="2026-10-11T12:00:00Z"):
    pr = {
        "commits": {"nodes": [{"commit": {"committedDate": committed}}]},
        "comments": {"nodes": list(comments)},
        "reviews": {"nodes": list(reviews)},
        "reviewThreads": {"nodes": list(threads)},
    }
    return {"data": {"repository": {"pullRequest": pr}}}


@pytest.fixture
def fake_integrate(monkeypatch, tmp_path):
    """integrate against fake git, gh and subprocess: records every call in order. Knobs: fetched_head, gate_code,
    files (what git diff reports), checks / reviews / decision (PR view fields), threads (GraphQL review threads)."""
    world = {
        "events": [],
        "fetched_head": HEAD,
        "gate_code": 0,
        "files": ["src/app.py"],
        "checks": [_run("check")],
        "reviews": [],
        "decision": "",
        "threads": [],
    }
    ev = world["events"]

    def git(*args, cwd=None, check=True):
        ev.append(("git", *args))
        if args[0] == "rev-parse":
            return world["fetched_head"] if "pr-" in args[1] else MAIN
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
            return json.dumps(
                {"headRefOid": HEAD}
                if fields == "headRefOid"
                else _info(
                    headRefOid=HEAD,
                    isDraft=False,
                    title="t",
                    body="",
                    statusCheckRollup=world["checks"],
                    latestReviews=world["reviews"],
                    reviewDecision=world["decision"],
                )
            )
        if args[:2] == ("api", "graphql"):
            return json.dumps(_graphql(threads=world["threads"]))
        return ""

    def run(cmd, **_):
        ev.append(("run", *cmd))
        return subprocess.CompletedProcess(cmd, world["gate_code"] if "check" in cmd else 0, "", "")

    monkeypatch.setattr(workflow, "git", git)
    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "comment", lambda *a, **k: None)
    monkeypatch.setattr(board, "sync", lambda: [])
    monkeypatch.setattr(workflow, "worktrees", lambda: [])
    monkeypatch.setattr(workflow, "main_checkout", lambda: tmp_path)
    monkeypatch.setattr(workflow, "state_dir", lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr(workflow.subprocess, "run", run)
    return world


def _index(events, pred):
    return next(i for i, e in enumerate(events) if pred(e))


def test_integrate_fetches_the_pull_ref_gates_then_merges_the_pinned_head(fake_integrate):
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    fetch = _index(ev, lambda e: e[:2] == ("git", "fetch"))
    assert any(a.startswith("+refs/pull/7/head:") for a in ev[fetch])
    gate_run = _index(ev, lambda e: e[0] == "run" and "check" in e)
    assert ("run", "git", "merge", "--no-ff", "--no-edit", HEAD) in ev[fetch:gate_run]
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
        ("checks", [_run("citations", status="IN_PROGRESS", conclusion="")], "citations: pending"),
        ("checks", [{"__typename": "StatusContext", "context": "ci/x", "state": "PENDING"}], "ci/x: pending"),
        ("checks", [{"__typename": "StatusContext", "context": "ci/x", "state": "ERROR"}], "ci/x: failing"),
        ("reviews", [{"author": {"login": "ann"}, "state": "CHANGES_REQUESTED"}], "changes requested by ann"),
        ("decision", "CHANGES_REQUESTED", "changes requested"),
        ("threads", [_thread("devkit/x.py", 9)], "unresolved review thread devkit/x.py:9"),
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


def test_integrate_override_merges_and_logs_the_reason_on_the_pr(fake_integrate):
    fake_integrate["checks"] = [_run("citations", conclusion="FAILURE")]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="flaky link checker, see #12") == 0
    ev = fake_integrate["events"]
    log = _index(ev, lambda e: e[:3] == ("gh", "pr", "comment"))
    body = ev[log][ev[log].index("--body") + 1]
    assert "flaky link checker, see #12" in body and "citations: failing" in body
    assert log < _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))


def test_integrate_override_needs_a_reason_and_dry_run_logs_nothing(fake_integrate, capsys):
    fake_integrate["threads"] = [_thread()]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="  ") == 1
    assert not _merged(fake_integrate["events"])
    assert workflow.integrate(7, harness_ok=True, dry_run=True, override="reviewed by hand") == 0
    assert not any(e[:3] == ("gh", "pr", "comment") for e in fake_integrate["events"])
    assert "would log" in capsys.readouterr().out


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
