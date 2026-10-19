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
from tests.test_merge_hygiene import land_on_main, last_json, out, project, sh  # noqa: F401 - project is a fixture


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
