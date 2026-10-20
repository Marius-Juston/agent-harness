"""`dev pr`: the PR body comes from a file or, with `--summary -`, from stdin (subagents may not write report files).
The PR summary's `Reuse:` line (#173), which the completion gate checks."""

import io
import json
import subprocess
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, workflow
from tests.test_board import mk
from tests.test_gate import (  # noqa: F401 - actions, clock and fake_integrate are fixtures
    HEAD,
    _index,
    _job,
    _last_json,
    _merged,
    _run,
    _thread,
    actions,
    clock,
    fake_integrate,
)
from tests.test_merge_hygiene import (  # noqa: F401 - project is a fixture
    commit,
    land_on_main,
    last_json,
    note,
    open_pr,
    out,
    project,
    sh,
)


@pytest.fixture
def opened(monkeypatch):
    """Run `workflow.pr` with git and GitHub faked; returns the body each call sent to `gh pr create`."""
    sent = []
    monkeypatch.setattr(workflow, "git", lambda *a: "33-x" if a[0] == "branch" else "")
    monkeypatch.setattr(workflow, "_gate_passed", lambda: True)
    monkeypatch.setattr(board, "get", lambda n: SimpleNamespace(title="t"))
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "comment", lambda n, text: None)
    monkeypatch.setattr(board, "set_status", lambda n, s: None)

    def gh(*args, input=None):
        sent.append(input)
        return "https://example/pr/1\n"

    monkeypatch.setattr(board, "gh", gh)
    return sent


def test_summary_dash_reads_the_body_from_stdin(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Closes #33\n\nstdin body `$x` ünï\n"))
    assert workflow.pr(33, "-", draft=False).splitlines()[0] == "https://example/pr/1"
    assert opened == ["Closes #33\n\nstdin body `$x` ünï\n"]


def test_a_successful_dev_pr_prints_the_url_and_one_status_line(opened, monkeypatch, capsys):
    """#261: long output got piped (`2>&1 | tail`), and Claude Code refuses a piped `dev pr` in a worktree."""
    from devkit import cli

    monkeypatch.setattr("sys.stdin", io.StringIO("just text\n"))
    assert cli.run(cli.parse(["pr", "33", "--summary", "-"])) in (None, 0)
    out = capsys.readouterr()
    lines = (out.out + out.err).splitlines()
    assert lines == ["https://example/pr/1", "#33 status:review, Closes #33"]


def test_the_status_line_names_the_ref_the_body_carries(opened, monkeypatch):
    """The body may close another issue than the CLI number: print what the PR will do, not what was asked."""
    monkeypatch.setattr("sys.stdin", io.StringIO("Refs #40\n\nfollow-up\n"))
    assert workflow.pr(33, "-", draft=False).splitlines() == ["https://example/pr/1", "#33 status:review, Refs #40"]


def test_a_body_without_a_ref_line_closes_its_issue(opened, monkeypatch):
    """#38: a complete hand-back gets 'Closes #N', so the merge closes the issue (PRs said 'Refs' and reopened it)."""
    monkeypatch.setattr("sys.stdin", io.StringIO("just text\n"))
    workflow.pr(33, "-", draft=False)
    assert opened == ["Closes #33\n\njust text\n"]


def test_a_partial_hand_back_refs_its_issue(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("just text\n"))
    workflow.pr(33, "-", draft=False, partial=True)
    assert opened == ["Refs #33\n\njust text\n"]


def test_an_explicit_ref_line_wins_over_the_default(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Refs #33\n\nhalf done\n"))
    workflow.pr(33, "-", draft=False)
    assert opened == ["Refs #33\n\nhalf done\n"]


def test_empty_stdin_is_refused_before_anything_is_pushed(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  \n"))
    with pytest.raises(board.BoardError, match="stdin"):
        workflow.pr(33, "-", draft=False)
    assert opened == []


@pytest.mark.parametrize(
    ("body", "log"),
    [
        (f"Closes #33\n\nsee https://{workflow.SESSION_URL}_1\n", ""),
        ("Closes #33\n\nfine\n", f"fix x\n\nCo-Authored-By: C <c@x>\nClaude-Session: https://{workflow.SESSION_URL}\n"),
    ],
)
def test_dev_pr_refuses_a_session_leak_in_a_public_repo(opened, monkeypatch, body, log):
    """#292, owner rule 2026-10-05: no session URL or Claude-Session trailer in a public repo's PR or commits."""
    pushed, seen = [], ["PUBLIC"]
    monkeypatch.setattr(
        workflow, "git", lambda *a: "33-x" if a[0] == "branch" else log if a[0] == "log" else pushed.append(a) or ""
    )
    monkeypatch.setattr(workflow.checks, "merge_base", lambda: "base")
    create = board.gh
    monkeypatch.setattr(
        board,
        "gh",
        lambda *a, **k: json.dumps({"visibility": seen[0]}) if a[:2] == ("repo", "view") else create(*a, **k),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    with pytest.raises(board.BoardError, match="public"):
        workflow.pr(33, "-", draft=False)
    assert opened == [] and not [a for a in pushed if a[0] == "push"]
    seen[0] = "PRIVATE"
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    workflow.pr(33, "-", draft=False)
    assert opened == [body]


def test_summary_file_still_works(opened, tmp_path):
    f = tmp_path / "s.md"
    f.write_text("Refs #33\n\nfrom a file\n")
    workflow.pr(33, str(f), draft=False)
    assert opened == ["Refs #33\n\nfrom a file\n"]


def test_partial_with_a_closes_line_is_refused_before_anything_is_pushed(opened, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Closes #33\n\nhalf done\n"))
    with pytest.raises(board.BoardError, match="--partial"):
        workflow.pr(33, "-", draft=False, partial=True)
    assert opened == []


PYPROJECT = '[project]\nname = "p"\ndependencies = ["rich>=13"]\n[dependency-groups]\ndev = ["pytest"]\n'


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True)


@pytest.fixture
def branch(tmp_path, monkeypatch):
    """A repository whose main has pyproject.toml, pkg/old.py and one research note, on issue branch 9-x. Returns a
    function that commits one file."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (repo / "pkg/old.py").write_text("".join(f"X{i} = {i}\n" for i in range(10)))
    (repo / "docs/research").mkdir(parents=True)
    (repo / "docs/research/http.md").write_text("# http clients\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "."],
        ["commit", "-q", "-m", "base"],
        ["checkout", "-qb", "9-x"],
    ):
        _git(repo, *args)
    monkeypatch.chdir(repo)

    def commit(path: str, text: str) -> None:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", path)

    commit.repo = repo
    return commit


def test_a_new_dependency_needs_a_reuse_line(branch):
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', '"rich>=13", "HTTPX[http2]>=0.27"'))
    assert workflow.reuse_needs("main") == ["dependency httpx"]
    assert "has no `Reuse:` line" in workflow.reuse_problem("Closes #9\n\nadded httpx")
    assert workflow.reuse_problem("Closes #9\n\nReuse: httpx PASS (chosen), requests HOLD dormant; fit: unsure") is None


def test_a_new_dependency_group_entry_counts_too(branch):
    branch("pyproject.toml", PYPROJECT.replace('["pytest"]', '["pytest", "hypothesis"]'))
    assert workflow.reuse_needs("main") == ["dependency hypothesis"]


def test_a_uv_dev_dependency_counts_and_the_project_itself_does_not(branch):
    """Review on #186: [tool.uv] dev-dependencies is uv's legacy group, and `p[extra]` is a self-reference."""
    extra = '[project.optional-dependencies]\nall = ["P[dev]"]\n[tool.uv]\ndev-dependencies = ["ruff"]\n'
    branch("pyproject.toml", PYPROJECT.replace("[dependency-groups]", extra + "[dependency-groups]"))
    assert workflow.reuse_needs("main") == ["dependency ruff"]


def test_an_unparsable_pyproject_adds_no_dependency(branch):
    """A broken pyproject.toml fails the gate on its own; the reuse check does not guess at its names."""
    branch("pyproject.toml", PYPROJECT + "[broken\n")
    assert workflow.reuse_needs("main") == []


def test_a_new_non_test_module_needs_a_reuse_line(branch):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_needs("main") == ["module pkg/new.py"]
    assert "module pkg/new.py" in workflow.reuse_problem("Closes #9")


def test_neither_needs_no_line(branch):
    """Tests, edits to existing modules, renames, version bumps, non-Python files and empty or whitespace-only
    modules (package markers) do not count."""
    branch("tests/test_new.py", "def test(): pass\n")
    branch("pkg/conftest.py", "")
    branch("pkg/sub/__init__.py", "")
    branch("pkg/blank.py", " \n\n\t\n")
    branch("pkg/old.py", "".join(f"X{i} = {i}\n" for i in range(9)))
    branch("pyproject.toml", PYPROJECT.replace("rich>=13", "rich>=14"))
    branch("docs/new.md", "text\n")
    _git(branch.repo, "mv", "pkg/old.py", "pkg/moved.py")
    _git(branch.repo, "commit", "-qm", "mv")
    assert workflow.reuse_needs("main") == []
    assert workflow.reuse_problem("Closes #9") is None


@pytest.mark.parametrize(
    "line",
    [
        "Reuse: httpx PASS (chosen), requests HOLD dormant",
        "- **Reuse:** aiohttp FAIL (archived); none of the rest fit",
        "Reuse: requestz NOT FOUND, httpx: UNVERIFIED, pyside6 FLAG",
        "Reuse: docs/research/http.md weighed them",
        "Reuse: none: a 20-line parser over our own TOML keys",
        "Reuse: none: harness adoption",
        "reuse: None - glue code over devkit.board",
        "Reuse: none – en dash",
        "Reuse: none — em dash",
    ],
)
def test_each_documented_form_is_accepted(branch, line):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_problem(f"Closes #9\n\nwhat changed\n{line}\n") is None


@pytest.mark.parametrize(
    "line",
    [
        "Reuse: looked around",
        "Reuse: docs/research/missing.md",
        "Reuse: none:",
        "Reuse:",
        "We did not Reuse: anything",
        "Reuse: tests pass",
        "Reuse: httpx pass",
    ],
)
def test_a_line_in_none_of_the_forms_is_refused(branch, line):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_problem(f"Closes #9\n\n{line}\n") is not None


def test_the_forms_are_documented_in_one_place():
    for form in (
        "Reuse: httpx PASS",
        "Reuse: docs/research/",
        "Reuse: none: <reason>",
        "Reuse: none: harness adoption",
    ):
        assert form in workflow.REUSE_FORMS


# The fit clause (#196 F0, docs/research/software-discovery.md §10 item 9): each new dependency needs
# "fit: <package> does <need> via <API>" or "fit: unsure" in a `Reuse:` line; new modules alone do not.
HTTPX = '"rich>=13", "HTTPX[http2]>=0.27"'


@pytest.mark.parametrize(
    "line",
    [
        "Reuse: httpx PASS (chosen); fit: httpx does HTTP/2 requests via httpx.Client(http2=True)",
        "- **Reuse:** httpx PASS; FIT: HTTPX does async GET via `httpx.AsyncClient.get`",
        "Reuse: httpx PASS; fit: unsure",
        "Reuse: httpx PASS; fit: httpx unsure",
        "Reuse: httpx[http2] PASS; fit: httpx[http2] does HTTP/2 via httpx.Client(http2=True)",
        "Reuse: docs/research/http.md; fit: httpx does retries via httpx.HTTPTransport(retries=3)",
        "Reuse: none: harness adoption; fit: unsure",
    ],
)
def test_a_new_dependency_with_a_fit_clause_is_accepted(branch, line):
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', HTTPX))
    branch("docs/research/http.md", "note\n")
    assert workflow.reuse_problem(f"Closes #9\n\n{line}\n") is None


@pytest.mark.parametrize(
    "line",
    [
        "Reuse: httpx PASS (chosen), requests HOLD dormant",
        "Reuse: httpx PASS; fit: httpx",
        "Reuse: httpx PASS; fit: httpx does HTTP/2",
        "Reuse: httpx PASS; fit: httpx does via",
        "Reuse: httpx PASS; fit: requests does HTTP/2 requests via requests.get",
        "Reuse: httpx PASS; fit:",
        "Reuse: httpx PASS; fit: sure",
        "Reuse: httpx PASS; benefit: httpx does HTTP/2 via httpx.Client",
    ],
)
def test_a_new_dependency_without_a_fit_clause_is_refused(branch, line):
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', HTTPX))
    problem = workflow.reuse_problem(f"Closes #9\n\n{line}\n")
    assert problem is not None and "fit:" in problem and "httpx" in problem


def test_a_fit_clause_outside_the_reuse_line_does_not_count(branch):
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', HTTPX))
    assert workflow.reuse_problem("Closes #9\n\nReuse: httpx PASS\nfit: unsure\n") is not None


def test_each_new_dependency_needs_its_own_fit_or_a_bare_unsure(branch):
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', HTTPX + ', "tenacity>=9"'))
    named = "Reuse: httpx PASS, tenacity PASS; fit: httpx does HTTP/2 via httpx.Client"
    assert "tenacity" in workflow.reuse_problem(f"Closes #9\n\n{named}\n")
    both = named + "; fit: tenacity does retry with backoff via tenacity.retry"
    assert workflow.reuse_problem(f"Closes #9\n\n{both}\n") is None
    assert workflow.reuse_problem(f"Closes #9\n\n{named}; fit: unsure\n") is None
    split = "Reuse: httpx PASS, tenacity PASS; fit: httpx does HTTP/2 via httpx.Client\nReuse: fit: tenacity unsure"
    assert workflow.reuse_problem(f"Closes #9\n\n{split}\n") is None


def test_harness_adoption_exempts_new_dependencies_and_other_none_reasons_do_not(branch):
    """Lead on #221: template-added dependencies are not the agent's pick, so `fit: unsure` there is not asked for."""
    branch("pyproject.toml", PYPROJECT.replace('"rich>=13"', HTTPX))
    assert workflow.reuse_problem("Closes #9\n\nReuse: none: harness adoption\n") is None
    assert workflow.reuse_problem("Closes #9\n\n- **Reuse:** None — Harness adoption (v0.1.8)\n") is None
    for line in ("Reuse: none: glue code", "Reuse: none: not a harness adoption", "Reuse: httpx PASS"):
        assert "fit clause" in workflow.reuse_problem(f"Closes #9\n\n{line}\n")


def test_a_new_module_alone_needs_no_fit_clause(branch):
    branch("pkg/new.py", "Y = 2\n")
    assert workflow.reuse_problem("Closes #9\n\nReuse: none: glue code\n") is None


NAMES = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,20}", fullmatch=True).filter(lambda s: s.lower() != "unsure")
TEXT = st.from_regex(r"[A-Za-z0-9/()=.` ]{0,30}", fullmatch=True)


@given(names=st.lists(NAMES, min_size=1, max_size=4), need=TEXT, api=TEXT, covered=st.data())
def test_fit_missing_is_exactly_the_dependencies_without_a_named_clause(names, need, api, covered):
    """Property: a dependency is covered iff a `does ... via ...` clause names it (any case or separator spelling)."""
    deps = sorted({workflow._normalized(n) for n in names})
    chosen = covered.draw(st.lists(st.sampled_from(names), unique=True))
    clauses = "; ".join(f"fit: {n.upper()} does x{need} via y{api}" for n in chosen)
    body = f"Reuse: {names[0]} PASS; {clauses}\n"
    assert workflow.fit_missing(body, deps) == [d for d in deps if d not in {workflow._normalized(n) for n in chosen}]
    assert workflow.fit_missing(body + "Reuse: fit: unsure\n", deps) == []


def test_the_fit_forms_are_documented():
    for form in ("fit: <package> does <need> via <API>", "fit: unsure"):
        assert form in workflow.REUSE_FORMS


# --- integrate: a merge that deletes another open issue's Owns (#230) ---------------------------------------------


def _delete_in_pr(world: dict, path: str, number: int = 7) -> None:
    """Push PR `number`: its branch deletes `path` from the current main, as GitHub would hold it."""
    clone = world["clone"]
    sh(clone, "git", "checkout", "-q", "-b", world["branch"])
    sh(clone, "git", "rm", "-q", path)
    sh(clone, "git", "commit", "-q", "-m", "delete")
    sh(clone, "git", "push", "-q", "origin", world["branch"])
    sh(world["origin"], "git", "update-ref", f"refs/pull/{number}/head", out(clone, "git", "rev-parse", "HEAD"))
    sh(clone, "git", "checkout", "-q", "main")


@pytest.mark.parametrize(("others", "outcome"), [((15, 16), "refused"), ((), "merged")])
def test_integrate_refuses_deleting_a_path_another_open_issue_owns(project, capsys, monkeypatch, others, outcome):  # noqa: F811
    """docs/research/decision-records-and-drift.md check 2: PR 7 (branch 12-c, Closes #12, Refs #14) deletes
    old/x.py. Its own issues may own it; any other open issue that owns it refuses the merge, named."""
    land_on_main(project, {"old/x.py": "x\n"})
    project["body"] = "Closes #12\n\nRefs #14"
    _delete_in_pr(project, "old/x.py")
    body = "## Owns\n- `old/x.py`\n"
    issues = [mk(n, ["type:build"], body) for n in (12, 14, *others)]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    code = workflow.integrate(7, harness_ok=True, dry_run=False)
    printed = capsys.readouterr().out
    res = last_json(printed)
    assert res["outcome"] == outcome
    if others:
        assert code == 1 and res["owns"] == {"15": ["old/x.py"], "16": ["old/x.py"]}
        assert "#15" in printed and "#16" in printed and "#12" not in res["owns"]
        assert not [e for e in project["events"] if e[:3] == ("gh", "pr", "merge")]


def test_override_does_not_lift_the_deleted_owns_refusal_but_refs_does(project, capsys, monkeypatch):  # noqa: F811
    land_on_main(project, {"old/x.py": "x\n"})
    _delete_in_pr(project, "old/x.py")
    issues = [mk(n, ["type:build"], "## Owns\n- `old/x.py`\n") for n in (12, 15, 16)]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="moved on purpose") == 1
    assert last_json(capsys.readouterr().out)["outcome"] == "refused"
    project["body"] = "Closes #12\n\nRefs: #15, #16"
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert last_json(capsys.readouterr().out)["outcome"] == "merged"


@pytest.mark.parametrize("error", [board.BoardError("gh issue list: offline"), FileNotFoundError("gh")])
def test_a_deleting_merge_stops_when_the_board_cannot_be_read(project, capsys, monkeypatch, error):  # noqa: F811
    """Fail closed: no board, no proof the deletion is safe, no merge."""
    land_on_main(project, {"old/x.py": "x\n"})
    _delete_in_pr(project, "old/x.py")

    def offline(*a, **k):
        raise error

    monkeypatch.setattr(board, "fetch", offline)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert last_json(capsys.readouterr().out)["outcome"] == "stopped"
    assert not [e for e in project["events"] if e[:3] == ("gh", "pr", "merge")]


# --- integrate: checks still running on the PR's own head are waited for, not refused (#322) ----------------------


def _rollups(world: dict, monkeypatch, *rollups: list[dict]) -> None:
    """The PR's own head shows `rollups` in turn, one per read of its checks; the last one repeats."""
    queue, gh = list(rollups), board.gh

    def fake(*args, **kw):
        if args[:2] == ("pr", "view") and "statusCheckRollup" in args[-1]:
            world["checks"] = queue.pop(0) if len(queue) > 1 else queue[0]
        return gh(*args, **kw)

    monkeypatch.setattr(board, "gh", fake)


def _gated(ev: list) -> int:
    return _index(ev, lambda e: e[0] == "run" and "check" in e)


@pytest.mark.parametrize(
    "running",
    [
        _run("check", status="IN_PROGRESS", conclusion=""),
        {"__typename": "StatusContext", "context": "x", "state": "PENDING"},
    ],
)
def test_integrate_waits_for_checks_on_the_pr_head_then_merges(fake_integrate, clock, monkeypatch, capsys, running):  # noqa: F811
    """#322: the lead no longer waits by hand. The wait (bounded, jittered) comes before the gate, then the merge."""
    _rollups(fake_integrate, monkeypatch, [running], [running], [running], [_run("check"), _run("x")])
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    assert len(clock["sleeps"]) >= 2 and _gated(ev) < _index(ev, lambda e: e[:3] == ("gh", "pr", "merge"))
    assert _last_json(capsys.readouterr().out)["outcome"] == "merged"


@pytest.mark.parametrize(
    ("knob", "value", "expect"),
    [
        ("checks", [_run("check", conclusion="FAILURE")], "check check: failing (FAILURE)"),
        ("reviews", [{"author": {"login": "ann"}, "state": "CHANGES_REQUESTED"}], "changes requested by ann"),
        ("threads", [_thread("a.py", 4)], "unresolved review thread a.py:4"),
    ],
)
def test_pending_beside_other_feedback_refuses_at_once(fake_integrate, clock, capsys, knob, value, expect):  # noqa: F811
    fake_integrate[knob] = value
    fake_integrate["checks"] = [_run("slow", status="QUEUED", conclusion=""), *(value if knob == "checks" else [])]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "refused" and expect in res["feedback"]
    assert "check slow: pending (QUEUED)" in res["feedback"]
    assert clock["sleeps"] == [] and not any(e[0] == "run" for e in fake_integrate["events"])


def test_a_check_that_fails_while_waiting_refuses(fake_integrate, clock, monkeypatch, capsys):  # noqa: F811
    running = [_run("check", status="IN_PROGRESS")]
    _rollups(fake_integrate, monkeypatch, running, running, [_run("check", conclusion="FAILURE")])
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "refused" and res["feedback"] == ["check check: failing (FAILURE)"]
    assert clock["sleeps"] and not _merged(fake_integrate["events"])


def test_checks_running_at_the_deadline_end_in_waiting_then_resume(fake_integrate, clock, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(workflow.config, "get", lambda: workflow.config.Config(checks_wait_s=120))
    fake_integrate["checks"] = [_run("check", status="IN_PROGRESS")]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    printed = capsys.readouterr().out
    res = _last_json(printed)
    assert (res["outcome"], res["head"], res["pending"]) == ("waiting", HEAD, ["ci/check"])
    assert "RESULT: waiting" in printed and sum(clock["sleeps"]) == pytest.approx(120)
    assert not any(e[0] == "run" for e in fake_integrate["events"]) and not _merged(fake_integrate["events"])

    fake_integrate["checks"] = [_run("check")]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert _last_json(capsys.readouterr().out)["outcome"] == "merged"
    assert not list(workflow.state_dir("integrate").glob("*.waiting.json"))


def test_an_override_or_a_dry_run_does_not_wait(fake_integrate, clock, capsys):  # noqa: F811
    fake_integrate["checks"] = [_run("check", status="IN_PROGRESS")]
    assert workflow.integrate(7, harness_ok=True, dry_run=True) == 1
    assert _last_json(capsys.readouterr().out)["outcome"] == "refused"
    assert workflow.integrate(7, harness_ok=True, dry_run=False, override="the job never reports, see #9") == 0
    assert clock["sleeps"] == [] and _merged(fake_integrate["events"])


CODEQL = {"workflowName": "CodeQL", "name": "Analyze (python)"}


@pytest.mark.parametrize("required", [[], [_job("check", 11)]], ids=["not-registered", "billing-blocked"])
def test_local_ci_waits_on_others_not_stood_in(fake_integrate, actions, clock, monkeypatch, capsys, required):  # noqa: F811
    """Review on #333: under --local-ci a CodeQL job still running on the head was refused (PR 329). The required
    `check`, which Actions does not run and the local gate stands in for, is neither waited for nor judged."""
    running = [*required, _run("x", status="IN_PROGRESS") | CODEQL]
    _rollups(fake_integrate, monkeypatch, running, running, running, [*required, _run("x") | CODEQL])
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    printed = capsys.readouterr().out
    assert "waiting up to" in printed and _last_json(printed)["outcome"] == "merged"
    assert [s["context"] for s in actions["statuses"]] == ["check"]


def test_local_ci_refuses_another_check_that_fails_in_the_wait(fake_integrate, actions, clock, monkeypatch, capsys):  # noqa: F811
    """Review on #333: the CodeQL job is judged after the wait; the stood-in `check` gets no status."""
    running = [_job("check", 11), _run("x", status="IN_PROGRESS") | CODEQL]
    failed = [_job("check", 11), _run("x", conclusion="FAILURE") | CODEQL]
    _rollups(fake_integrate, monkeypatch, running, running, running, failed)
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 1
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "refused" and res["feedback"] == ["check Analyze (python): failing (FAILURE)"]
    assert clock["sleeps"] and actions["statuses"] == [] and not _merged(fake_integrate["events"])


def test_local_ci_does_not_wait_on_the_checks_it_stands_in_for(fake_integrate, actions, clock, capsys):  # noqa: F811
    fake_integrate["checks"] = [_run("check", status="QUEUED")]  # Actions queued it but never runs it
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    printed = capsys.readouterr().out
    assert "waiting up to" not in printed and _last_json(printed)["outcome"] == "merged"


@pytest.mark.parametrize("local_ci", [False, True])
def test_waiting_twice_on_one_head_points_past_a_rerun(fake_integrate, actions, clock, monkeypatch, capsys, local_ci):  # noqa: F811
    """Review on #333 (#305): a check stuck QUEUED forever costs a full wait per call. The second `waiting` on one
    head says so and names --local-ci or the owner instead of another rerun."""
    monkeypatch.setattr(workflow.config, "get", lambda: workflow.config.Config(checks_wait_s=60))
    fake_integrate["checks"] = [_run("x", status="QUEUED", conclusion="") | CODEQL]  # never stood in for
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=local_ci) == 1
    printed = capsys.readouterr().out
    first = _last_json(printed)
    assert first["outcome"] == "waiting" and "again" not in first
    assert printed.splitlines()[-2].endswith("rerun")
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=local_ci) == 1
    printed = capsys.readouterr().out
    again = _last_json(printed)
    assert (again["outcome"], again["head"], again["again"]) == ("waiting", HEAD, True)
    line = printed.splitlines()[-2]
    assert line.startswith("RESULT: waiting") and "again" in line and not line.endswith("rerun")
    assert "--local-ci" in line and "owner" in line
    assert not any(e[0] == "run" for e in fake_integrate["events"]) and not _merged(fake_integrate["events"])


def test_a_retargeted_stacked_pr_gets_the_cherry_pick_route_not_a_force_push(monkeypatch, capsys):
    """#305: auto mode refuses `git rebase --onto` plus --force-with-lease ('Git Destructive'). The hint names the
    route that worked (#281 -> #304): a new branch from main, the stacked PR's own commits cherry-picked by sha, a new PR
    via `dev pr`, the old PR closed."""
    calls = []

    def gh(*a, **k):
        calls.append(a)
        if a[:2] == ("pr", "list"):
            return json.dumps([{"number": 8, "headRefName": "13-stacked"}])
        if a[0] == "api":
            return "a" * 40 + "\n" + "b" * 40 + "\n"
        return ""

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    tip = "c" * 40
    assert workflow.retarget_stacked("12-parent", tip) == [8]
    assert next(c for c in calls if c[0] == "api")[1] == f"repos/o/r/compare/{tip}...13-stacked"
    said = capsys.readouterr().out
    assert "rebase" not in said and "force" not in said
    assert "git switch -c 13-stacked-v2 origin/main" in said
    assert f"git cherry-pick {'a' * 12} {'b' * 12}" in said
    assert "uv run dev pr 13" in said and "gh pr close 8" in said


def test_the_stacked_hint_without_a_readable_compare_still_names_the_route(monkeypatch, capsys):
    def gh(*a, **k):
        if a[:2] == ("pr", "list"):
            return json.dumps([{"number": 8, "headRefName": "13-stacked"}])
        if a[0] == "api":
            raise board.BoardError("gh api: offline")
        return ""

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    assert workflow.retarget_stacked("12-parent", "c" * 40) == [8]
    said = capsys.readouterr().out
    assert "rebase" not in said and "git switch -c 13-stacked-v2 origin/main" in said
    assert f"git log --reverse --format=%h {'c' * 12}..origin/13-stacked" in said and "gh pr close 8" in said


def _local(repo, branch: str) -> str:
    return sh(repo, "git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).stdout.strip()


@pytest.mark.parametrize("behind", [False, True])
def test_integrate_deletes_the_local_branch_the_merged_head_contains(project, capsys, behind):  # noqa: F811
    """#340: a squash merge leaves the local branch unmerged for `git branch -d`; integrate deletes it when its tip is
    the merged head or (main merged in and pushed) an ancestor of it."""
    clone = project["clone"]
    tip = open_pr(project, note(clone, "c"))
    sh(clone, "git", "config", "branch.12-c.remote", "origin")
    if behind:
        land_on_main(project, note(clone, "b"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    said = capsys.readouterr().out
    assert last_json(said)["updated"] is behind
    assert not _local(clone, "12-c") and f"deleted branch 12-c (was {tip[:10]})" in said
    assert sh(clone, "git", "config", "--get-regexp", r"^branch\.12-c\.", check=False).stdout == ""


def test_integrate_keeps_a_local_branch_with_commits_the_pr_lacks(project, capsys):  # noqa: F811
    clone = project["clone"]
    open_pr(project, note(clone, "c"))
    sh(clone, "git", "checkout", "-q", "12-c")
    extra = commit(clone, {"later.md": "not pushed\n"}, "unpushed")
    sh(clone, "git", "checkout", "-q", "main")
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    said = capsys.readouterr().out
    assert _local(clone, "12-c") == extra
    assert f"kept branch 12-c: its tip {extra[:10]} has 1 commit(s) the merged head does not" in said


def test_integrate_keeps_a_branch_checked_out_in_a_dirty_worktree(project, capsys, tmp_path):  # noqa: F811
    clone = project["clone"]
    tip = open_pr(project, note(clone, "c"))
    wt = tmp_path / "wt"
    sh(clone, "git", "worktree", "add", "-q", str(wt), "12-c")
    (wt / "scratch.txt").write_text("unsaved\n")
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    said = capsys.readouterr().out
    assert _local(clone, "12-c") == tip and wt.exists()
    assert f"kept branch 12-c: checked out in {wt}" in said


@pytest.fixture
def branches(tmp_path, monkeypatch):
    """A temp repository with local branches and a faked `gh pr list`; returns (repo, prs): tests append PR rows."""
    from devkit import paths

    repo = tmp_path / "r"
    sh(tmp_path, "git", "init", "-q", "-b", "main", str(repo))
    sh(repo, "git", "config", "user.email", "t@t")
    sh(repo, "git", "config", "user.name", "t")
    commit(repo, {"a.txt": "a\n"}, "init")
    prs: list[dict] = []
    paths.root.cache_clear()
    paths.common_dir.cache_clear()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "gh", lambda *a, **k: json.dumps(prs) if a[:2] == ("pr", "list") else "")
    yield repo, prs
    paths.root.cache_clear()
    paths.common_dir.cache_clear()


def _branch(repo, name: str, *files: str) -> str:
    """A branch off main with one commit per file; returns its tip."""
    sh(repo, "git", "checkout", "-q", "-b", name, "main")
    tip = ""
    for f in files:
        tip = commit(repo, {f: f"{name}\n"}, f"{name} {f}")
    sh(repo, "git", "checkout", "-q", "main")
    return tip


def _pr(number: int, branch: str, state: str, head: str, fork: bool = False) -> dict:
    return {"number": number, "state": state, "headRefName": branch, "headRefOid": head, "isCrossRepository": fork}


@pytest.mark.parametrize("dry_run", [False, True])
def test_worktree_prune_deletes_branches_their_merged_or_superseding_pr_contains(branches, dry_run, tmp_path):
    """#340: `dev worktree prune` deletes a squash-merged branch (tip at or behind its PR head) and a closed PR's
    branch another merged PR's head contains; it keeps one with unpushed commits, one checked out, and one whose merged
    head this clone lacks, and does not touch main, a branch with an open PR, or one with no PR of this repository (a
    fork's merged PR from a branch of the same name does not count)."""
    repo, prs = branches
    merged = _branch(repo, "1-merged", "x")
    later = _branch(repo, "2-behind", "y1", "y2")
    behind = out(repo, "git", "rev-parse", f"{later}~1")
    sh(repo, "git", "branch", "-f", "2-behind", behind)
    sh(repo, "git", "branch", "3-superseded", behind)
    ahead = _branch(repo, "4-ahead", "z")
    sh(repo, "git", "checkout", "-q", "4-ahead")
    extra = commit(repo, {"z2": "unpushed\n"}, "unpushed")
    sh(repo, "git", "checkout", "-q", "main")
    busy = _branch(repo, "5-busy", "w")
    sh(repo, "git", "worktree", "add", "-q", str(tmp_path / "wt"), "5-busy")
    open_ = _branch(repo, "6-open", "o")
    lost = _branch(repo, "7-lost", "l")
    nopr = _branch(repo, "8-nopr", "n")
    prs += [
        _pr(1, "1-merged", "MERGED", merged),
        _pr(2, "2-behind", "MERGED", later),
        _pr(3, "3-superseded", "CLOSED", behind),
        _pr(4, "4-ahead", "MERGED", ahead),
        _pr(5, "5-busy", "MERGED", busy),
        _pr(6, "6-open", "CLOSED", open_),
        _pr(16, "6-open", "OPEN", open_),
        _pr(7, "7-lost", "MERGED", "f" * 40),
        _pr(9, "main", "MERGED", merged),
        _pr(10, "8-nopr", "MERGED", nopr, fork=True),
    ]
    lines = workflow.prune_branches(dry_run)
    said = "\n".join(lines)
    gone = "would delete branch" if dry_run else "deleted branch"
    for name in ("1-merged", "2-behind", "3-superseded"):
        assert f"{gone} {name} " in said
        assert bool(_local(repo, name)) is dry_run
    assert f"kept branch 4-ahead: its tip {extra[:10]} has 1 commit(s)" in said and _local(repo, "4-ahead") == extra
    assert f"kept branch 5-busy: checked out in {tmp_path / 'wt'}" in said and _local(repo, "5-busy") == busy
    assert f"kept branch 7-lost: its tip {lost[:10]} has 1 commit(s)" in said and _local(repo, "7-lost") == lost
    assert _local(repo, "6-open") == open_ and _local(repo, "8-nopr") == nopr and _local(repo, "main")
    assert "6-open" not in said and "8-nopr" not in said and "main" not in said
    assert len(lines) == 6


def test_drop_branch_fails_closed_without_a_head_to_compare(branches):
    repo, _ = branches
    tip = _branch(repo, "1-x", "x")
    assert workflow.drop_branch("2-gone", [tip]) == "no local branch 2-gone"
    assert (
        workflow.drop_branch("1-x", ["--all", "HEAD"])
        == "kept branch 1-x: no merged head in this clone to compare it with"
    )
    assert "this clone lacks the merged head" in workflow.drop_branch("1-x", ["f" * 40])
    assert _local(repo, "1-x") == tip


def test_dev_worktree_prune_is_a_command(branches, capsys):
    from devkit import cli

    repo, prs = branches
    prs.append(_pr(1, "1-merged", "MERGED", _branch(repo, "1-merged", "x")))
    assert cli.run(cli.parse(["worktree", "prune", "--dry-run"])) in (None, 0)
    assert "would delete branch 1-merged" in capsys.readouterr().out and _local(repo, "1-merged")
    assert cli.run(cli.parse(["worktree", "prune"])) in (None, 0)
    assert "deleted branch 1-merged" in capsys.readouterr().out and not _local(repo, "1-merged")


def _stop_mid(repo, tmp_path, how: str) -> str:
    """A worktree of branch 1-x stopped mid-rebase (conflict or an `edit` stop) or mid-bisect: shown as detached."""
    tip = _branch(repo, "1-x", *(["a.txt"] if how == "conflict" else ["x", "y"]))
    commit(repo, {"a.txt": "main moved\n"}, "main moves")
    wt = tmp_path / "wt"
    sh(repo, "git", "worktree", "add", "-q", str(wt), "1-x")
    if how == "conflict":
        assert sh(wt, "git", "rebase", "main", check=False).returncode != 0
    elif how == "edit":
        sh(wt, "git", "-c", "sequence.editor=sed -i 1s/^pick/edit/", "rebase", "-i", "--onto", "main", "1-x~1")
    else:
        sh(wt, "git", "bisect", "start", "1-x", "main~1")
    assert "detached" in out(repo, "git", "worktree", "list", "--porcelain")
    return tip


@pytest.mark.parametrize("how", ["conflict", "edit", "bisect"])
def test_drop_branch_keeps_a_branch_a_worktree_is_rebasing_or_bisecting(branches, tmp_path, how):
    """Review of #343: `git worktree list` shows such a worktree as detached, but `rebase --continue` or
    `bisect reset` needs the branch back."""
    repo, _ = branches
    tip = _stop_mid(repo, tmp_path, how)
    assert workflow.drop_branch("1-x", [tip]) == f"kept branch 1-x: checked out in {tmp_path / 'wt'}"
    assert _local(repo, "1-x") == tip


def test_drop_branch_keeps_a_branch_that_moved_after_the_check(branches, monkeypatch):
    repo, _ = branches
    tip = _branch(repo, "1-x", "x")
    real = workflow.git

    def git(*args, **kw):
        if args[:2] == ("update-ref", "-d"):  # another push lands between the check and the delete
            sh(repo, "git", "branch", "-f", "1-x", "main")
        return real(*args, **kw)

    monkeypatch.setattr(workflow, "git", git)
    assert workflow.drop_branch("1-x", [tip]).startswith("kept branch 1-x: ")
    assert _local(repo, "1-x") == out(repo, "git", "rev-parse", "main")


def test_worktree_prune_refuses_when_the_pr_list_may_be_cut_off(branches, monkeypatch):
    repo, prs = branches
    tip = _branch(repo, "1-x", "x")
    monkeypatch.setattr(workflow, "PRUNE_LIMIT", 2)
    prs += [_pr(1, "1-x", "MERGED", tip), _pr(2, "2-y", "CLOSED", tip)]
    with pytest.raises(board.BoardError, match="nothing deleted"):
        workflow.prune_branches()
    assert _local(repo, "1-x") == tip


@pytest.mark.parametrize("answer", ["open", "error"])
def test_integrate_keeps_a_branch_another_open_pr_has_as_head(project, capsys, monkeypatch, answer):  # noqa: F811
    clone = project["clone"]
    tip = open_pr(project, note(clone, "c"))
    fake = board.gh

    def gh(*args, **kw):
        if args[:2] == ("pr", "list") and "--head" in args:
            if answer == "error":
                raise board.BoardError("gh: offline")
            return json.dumps([{"number": 8, "isCrossRepository": False}])
        return fake(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    said = capsys.readouterr().out
    assert _local(clone, "12-c") == tip
    why = "open PR #8 has it as head" if answer == "open" else "cannot read its open PRs from GitHub"
    assert f"kept branch 12-c: {why}" in said


def test_integrate_deletes_the_branch_when_a_stale_list_still_shows_the_merged_pr_open(project, capsys, monkeypatch):  # noqa: F811
    """Round-2 review of #343: right after the merge, `gh pr list --state open` can still show the PR just merged."""
    clone = project["clone"]
    tip = open_pr(project, note(clone, "c"))
    fake = board.gh

    def gh(*args, **kw):
        if args[:2] == ("pr", "list") and "--head" in args:
            return json.dumps([{"number": 7, "isCrossRepository": False}])
        return fake(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    said = capsys.readouterr().out
    assert not _local(clone, "12-c") and f"deleted branch 12-c (was {tip[:10]})" in said
