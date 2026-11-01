"""Merge hygiene in throwaway git repositories (#11, #38): generated files never block a merge, notes agents rewrite
conflict instead of interleaving, and integrate brings a PR that is behind main up to date by itself."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, paths, setup, workflow

ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTES = ROOT / "template/.gitattributes"
INDEX = "docs/research/INDEX.md"
REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen


def sh(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return REAL_RUN(args, cwd=cwd, capture_output=True, text=True, check=check)


def out(cwd: Path, *args: str) -> str:
    return sh(cwd, *args).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", message)
    return out(repo, "git", "rev-parse", "HEAD")


def index(tree: Path) -> str:
    """What `dev docs` writes: one row per research note."""
    notes = sorted(p.stem for p in (tree / "docs/research").glob("*.md") if p.name != "INDEX.md")
    return "# Index\n" + "".join(f"- {n}\n" for n in notes)


def note(tree: Path, name: str, text: str = "body\n") -> dict[str, str]:
    """A new note plus the index row its PR regenerates."""
    (tree / f"docs/research/{name}.md").parent.mkdir(parents=True, exist_ok=True)
    (tree / f"docs/research/{name}.md").write_text(text)
    return {f"docs/research/{name}.md": text, INDEX: index(tree)}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "git", "init", "-q", "-b", "main")
    sh(r, "git", "config", "user.email", "t@t")
    sh(r, "git", "config", "user.name", "t")
    shutil.copy(ATTRIBUTES, r / ".gitattributes")
    commit(r, {"docs/research/a.md": "one\ntwo\nthree\n", INDEX: "# Index\n- a\n"}, "init")
    return r


def branch(repo: Path, name: str, files: dict[str, str], base: str = "main") -> None:
    sh(repo, "git", "checkout", "-q", "-b", name, base)
    commit(repo, files, name)
    sh(repo, "git", "checkout", "-q", "main")


# --- .gitattributes: union only for append-only files ------------------------------------------------------------


def test_two_prs_rewriting_one_note_conflict_instead_of_interleaving(repo):
    """#38: fold-ins rewrite notes; a union merge would keep both rewrites silently."""
    branch(repo, "a", {"docs/research/a.md": "one\nTWO by a\nthree\n"})
    branch(repo, "b", {"docs/research/a.md": "one\nTWO by b\nthree\n"})
    sh(repo, "git", "merge", "-q", "--no-edit", "a")
    merge = sh(repo, "git", "merge", "--no-edit", "b", check=False)
    assert merge.returncode != 0 and "docs/research/a.md" in merge.stdout
    assert "<<<<<<<" in (repo / "docs/research/a.md").read_text()


def test_the_old_union_rule_would_have_interleaved_the_rewrites(repo):
    """The hazard the template no longer has: same merge, notes marked merge=union."""
    commit(repo, {".gitattributes": "docs/research/*.md merge=union\n"}, "union notes")
    branch(repo, "a", {"docs/research/a.md": "one\nTWO by a\nthree\n"})
    branch(repo, "b", {"docs/research/a.md": "one\nTWO by b\nthree\n"})
    sh(repo, "git", "merge", "-q", "--no-edit", "a")
    assert sh(repo, "git", "merge", "--no-edit", "b", check=False).returncode == 0
    assert (repo / "docs/research/a.md").read_text() == "one\nTWO by a\nTWO by b\nthree\n"


@pytest.mark.parametrize(
    "path", ["docs/research/_scratch/r1/log.md", "docs/log/2026-10.md", "docs/questions/OPEN_QUESTIONS.md"]
)
def test_append_only_files_keep_both_sides(repo, path):
    commit(repo, {path: "# Log\n"}, "seed")
    branch(repo, "a", {path: "# Log\n- from a\n"})
    branch(repo, "b", {path: "# Log\n- from b\n"})
    sh(repo, "git", "merge", "-q", "--no-edit", "a")
    sh(repo, "git", "merge", "-q", "--no-edit", "b")
    assert (repo / path).read_text() == "# Log\n- from a\n- from b\n"


def test_the_template_marks_only_append_only_paths_union():
    union = [line.split()[0] for line in ATTRIBUTES.read_text().splitlines() if "merge=union" in line]
    assert union == ["docs/research/_scratch/**/*.md", "docs/log/*.md", "docs/questions/OPEN_QUESTIONS.md"]
    assert (ROOT / ".gitattributes").read_text() == ATTRIBUTES.read_text()


# --- the generated-file merge driver (`dev setup`) --------------------------------------------------------------


def _generated_branches(repo: Path) -> None:
    branch(repo, "a", note(repo, "b"))
    sh(repo, "git", "checkout", "-q", "-f", "main")
    sh(repo, "git", "clean", "-qfd")
    branch(repo, "c", note(repo, "c"))
    sh(repo, "git", "checkout", "-q", "-f", "main")
    sh(repo, "git", "clean", "-qfd")
    sh(repo, "git", "merge", "-q", "--no-edit", "a")


def test_without_the_driver_two_index_rows_conflict(repo):
    _generated_branches(repo)
    assert sh(repo, "git", "merge", "--no-edit", "c", check=False).returncode != 0


def test_setup_installs_a_merge_driver_so_generated_files_never_conflict(repo):
    """#11: after `dev setup`'s git config, a plain `git merge` keeps one side; `dev docs` then regenerates."""
    for _, cmd in setup.git_steps():
        sh(repo, *cmd)
    _generated_branches(repo)
    sh(repo, "git", "merge", "-q", "--no-edit", "c")
    assert (repo / "docs/research/c.md").exists()
    assert (repo / INDEX).read_text() == "# Index\n- a\n- b\n"  # one side kept, stale: the gate catches it
    assert index(repo) == "# Index\n- a\n- b\n- c\n"


# --- integrate end to end: a real origin, fake GitHub and fake `uv run dev docs|check` ---------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A bare origin and the lead's clone (cwd). GitHub is faked over the origin's refs; `dev docs` writes the index,
    `dev check` fails on a stale one. Returns a world dict: events, origin, clone, blocked."""
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh(tmp_path, "git", "clone", "-q", str(origin), str(clone))
    sh(clone, "git", "config", "user.email", "t@t")
    sh(clone, "git", "config", "user.name", "t")
    shutil.copy(ATTRIBUTES, clone / ".gitattributes")
    commit(clone, {"docs/research/a.md": "one\ntwo\nthree\n", INDEX: "# Index\n- a\n"}, "init")
    sh(clone, "git", "push", "-q", "origin", "main")
    world = {
        "events": [],
        "origin": origin,
        "clone": clone,
        "blocked": [],
        "status": [],
        "branch": "12-c",
        "body": "Closes #12",
    }

    def head() -> str:
        return out(origin, "git", "rev-parse", f"refs/heads/{world['branch']}")

    def gh(*args, **_):
        world["events"].append(("gh", *args))
        if args[:2] == ("pr", "view"):
            if args[args.index("--json") + 1] == "headRefOid":
                return json.dumps({"headRefOid": head()})
            return json.dumps(
                {
                    "baseRefName": "main",
                    "isCrossRepository": False,
                    "headRefOid": head(),
                    "headRefName": world["branch"],
                    "state": "OPEN",
                    "isDraft": False,
                    "title": "t",
                    "body": world["body"],
                    "statusCheckRollup": [],
                    "latestReviews": [],
                    "reviewDecision": "",
                }
            )
        return ""

    def run(cmd, **kw):
        if list(cmd[:2]) == ["uv", "run"]:
            tree = Path(kw["cwd"])
            world["events"].append(("uv", *cmd[4:]))
            if "docs" in cmd:
                (tree / INDEX).write_text(index(tree))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            fresh = (tree / INDEX).read_text() == index(tree)
            return subprocess.CompletedProcess(cmd, 0 if fresh else 1, "", "")
        return REAL_RUN(cmd, **kw)

    def popen(cmd, **kw):
        """The gate integrate starts (bounded_run): `run` decides its outcome, a real process exits with it."""
        if list(cmd[:2]) != ["uv", "run"]:
            return REAL_POPEN(cmd, **kw)
        return REAL_POPEN([sys.executable, "-c", f"raise SystemExit({run(cmd, **kw).returncode})"], **kw)

    claim = board.Claim(host="box", agent="builder-12", branch="12-c", at="", token=None, cid="")
    paths.root.cache_clear()  # root() and common_dir() are cached per process: drop what other tests resolved
    paths.common_dir.cache_clear()
    monkeypatch.chdir(clone)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "pr_feedback", lambda n: {"since": "", "comments": [], "reviews": [], "threads": []})
    monkeypatch.setattr(board, "get", lambda n: type("I", (), {"claim": lambda self: claim})())
    monkeypatch.setattr(board, "block", lambda n, reason: world["blocked"].append((n, reason)))
    monkeypatch.setattr(board, "set_status", lambda n, status: world["status"].append((n, status)))
    monkeypatch.setattr(board, "merged_pr", lambda *a: world["events"].append(("merged_pr", *a)) or ["#12 -> done"])
    monkeypatch.setattr(workflow, "main_checkout", lambda: clone)
    monkeypatch.setattr(workflow, "state_dir", lambda *p: tmp_path.joinpath("state", *p))
    yield world
    paths.root.cache_clear()
    paths.common_dir.cache_clear()


def open_pr(world: dict, files: dict[str, str], number: int = 7) -> str:
    """Push the PR branch (cut from the current main) and its refs/pull/N/head, as GitHub would."""
    clone, name = world["clone"], world["branch"]
    sh(clone, "git", "checkout", "-q", "-b", name)
    tip = commit(clone, files, name)
    sh(clone, "git", "push", "-q", "origin", name)
    sh(world["origin"], "git", "update-ref", f"refs/pull/{number}/head", tip)
    sh(clone, "git", "checkout", "-q", "-f", "main")
    sh(clone, "git", "clean", "-qfd")
    return tip


def land_on_main(world: dict, files: dict[str, str]) -> None:
    """Another PR merged first: main moves on."""
    clone = world["clone"]
    commit(clone, files, "another PR")
    sh(clone, "git", "push", "-q", "origin", "main")


def last_json(text: str) -> dict:
    return json.loads(text.strip().splitlines()[-1].removeprefix("RESULT-JSON: "))


def test_integrate_updates_a_pr_behind_main_and_two_new_notes_integrate_cleanly(project, capsys):
    """#11 and #38: PR c adds a note after PR b's index row landed. Its index is stale and conflicts textually;
    integrate merges main in, regenerates, gates, pushes the update to the PR branch and merges it, unaided."""
    clone = project["clone"]
    old = open_pr(project, note(clone, "c"))
    land_on_main(project, note(clone, "b"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "merged" and res["updated"] and res["regenerated"]
    origin = project["origin"]
    tip = out(origin, "git", "rev-parse", "refs/heads/12-c")
    assert tip == res["head"] != old
    assert out(origin, "git", "show", f"{tip}:{INDEX}") == "# Index\n- a\n- b\n- c"
    main = out(origin, "git", "rev-parse", "refs/heads/main")
    assert sh(origin, "git", "merge-base", "--is-ancestor", main, tip, check=False).returncode == 0
    assert sh(origin, "git", "merge-base", "--is-ancestor", old, tip, check=False).returncode == 0
    ev = project["events"]
    merge = next(i for i, e in enumerate(ev) if e[:3] == ("gh", "pr", "merge"))
    assert ev[merge][ev[merge].index("--match-head-commit") + 1] == tip
    assert next(i for i, e in enumerate(ev) if e[0] == "merged_pr") > merge
    assert not (project["clone"].parent / "state/integrate/pr-7").exists()


def test_integrate_leaves_a_current_pr_alone(project, capsys):
    clone = project["clone"]
    old = open_pr(project, note(clone, "c"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert last_json(capsys.readouterr().out)["updated"] is False
    assert out(project["origin"], "git", "rev-parse", "refs/heads/12-c") == old


def test_integrate_sends_a_real_conflict_back(project, capsys):
    """#38, #110: both sides rewrite one note: the PR gets the paths, the worker is named, the issue goes back to
    in-progress with its claim."""
    old = open_pr(project, {"docs/research/a.md": "one\nTWO by the PR\nthree\n"})
    land_on_main(project, {"docs/research/a.md": "one\nTWO on main\nthree\n"})
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "conflict" and res["conflicts"] == ["docs/research/a.md"]
    assert res["resume"] == ["builder-12@box"]
    assert project["status"] == [(12, "in-progress")] and project["blocked"] == []
    assert res["cycle"] == 1 and res["fix"] == "in-progress"
    comment = next(e for e in project["events"] if e[:3] == ("gh", "pr", "comment"))
    assert "`docs/research/a.md`" in comment[comment.index("--body") + 1]
    assert not any(e[:3] == ("gh", "pr", "merge") or e[:2] == ("uv", "check") for e in project["events"])
    assert out(project["origin"], "git", "rev-parse", "refs/heads/12-c") == old  # nothing pushed
    assert not any(e[0] == "merged_pr" for e in project["events"])


def test_an_old_pr_cannot_bring_back_union_merges_for_notes(project, capsys):
    """Reviewer HIGH on #73: merging main into the head reads the head's .gitattributes. A PR cut before #38 keeps
    `docs/research/*.md merge=union`; integrate must still merge by main's rules, so the rewrites conflict."""
    old_rules = "docs/research/*.md merge=union\n" + ATTRIBUTES.read_text()
    old = open_pr(project, {".gitattributes": old_rules, "docs/research/a.md": "one\nTWO by the PR\nthree\n"})
    land_on_main(project, {"docs/research/a.md": "one\nTWO on main\nthree\n"})
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "conflict" and res["conflicts"] == ["docs/research/a.md"]
    assert out(project["origin"], "git", "rev-parse", "refs/heads/12-c") == old


# --- squash merges (#292): the commit on main is the PR title and body, never the branch's messages -------------

LEAK_BODY = f"Closes #12\n\nsummary\n\nClaude-Session: https://{workflow.SESSION_URL}_x"


def _visibility(monkeypatch, seen: str) -> None:
    """GitHub reports the repository as `seen`; the `project` fixture's gh answers nothing (read as public)."""
    real = board.gh
    reply = json.dumps({"visibility": seen})
    monkeypatch.setattr(board, "gh", lambda *a, **k: reply if a[:2] == ("repo", "view") else real(*a, **k))


def _merge_call(world: dict) -> tuple[str, ...]:
    return next(e for e in world["events"] if e[:3] == ("gh", "pr", "merge"))


def test_integrate_squash_merges_with_the_pr_title_and_body(project, capsys):
    project["body"] = "Closes #12\n\nwhat changed\n"
    tip = open_pr(project, note(project["clone"], "c"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert last_json(capsys.readouterr().out)["outcome"] == "merged"
    call = _merge_call(project)
    assert "--squash" in call and "--merge" not in call and "--rebase" not in call
    assert call[call.index("--subject") + 1] == "t (#7)"
    assert call[call.index("--body") + 1] == "Closes #12\n\nwhat changed"  # not the branch commit message "12-c"
    assert call[call.index("--match-head-commit") + 1] == tip


def test_a_review_trailer_is_the_last_paragraph_of_the_squash_body():
    info = {"title": "Consolidate", "body": "Refs #138\n\nsplit board.py\n"}
    assert workflow.squash_message(9, info, review=138) == (
        "Consolidate (#9)",
        "Refs #138\n\nsplit board.py\n\nConsolidation-Review: #138",
    )
    assert workflow.squash_message(9, {}, review=138) == ("(#9)", "Consolidation-Review: #138")
    assert workflow.squash_message(9, info) == ("Consolidate (#9)", "Refs #138\n\nsplit board.py")


def test_a_worker_written_review_trailer_never_reaches_the_squash_body():
    info = {"title": "x", "body": "Refs #3\n\nconsolidation-review : #3\nmore\n\nConsolidation-Review: #3"}
    assert workflow.squash_message(9, info) == ("x (#9)", "Refs #3\n\nmore")
    assert workflow.squash_message(9, info, review=4)[1] == "Refs #3\n\nmore\n\nConsolidation-Review: #4"


def test_the_squash_message_is_never_empty_and_tags_the_pr_once():
    assert workflow.squash_message(9, {"title": "fix x (#9)", "body": " \n"}) == (
        "fix x (#9)",
        "PR (#9) has no description.",
    )
    assert workflow.squash_message(9, {"title": "fix x (#8)"})[0] == "fix x (#8) (#9)"  # an issue number stays


@pytest.mark.parametrize("field", ["body", "title"])
def test_integrate_refuses_a_session_leak_in_a_public_repo_before_the_gate(project, capsys, monkeypatch, field):
    _visibility(monkeypatch, "PUBLIC")
    if field == "body":
        project["body"] = LEAK_BODY
    else:
        real = board.gh
        leak = f'"title": "see https://{workflow.SESSION_URL}_1"'
        monkeypatch.setattr(board, "gh", lambda *a, **k: real(*a, **k).replace('"title": "t"', leak))
    open_pr(project, note(project["clone"], "c"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "refused" and "public" in res["reason"]
    assert not any(e[:3] == ("gh", "pr", "merge") or e[0] == "uv" for e in project["events"])


def test_a_private_repo_may_carry_session_trailers(project, capsys, monkeypatch):
    _visibility(monkeypatch, "PRIVATE")
    project["body"] = LEAK_BODY
    open_pr(project, note(project["clone"], "c"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    call = _merge_call(project)
    assert call[call.index("--body") + 1] == LEAK_BODY


def test_a_body_edited_to_leak_during_the_gate_stops_the_merge(monkeypatch):
    calls = []
    monkeypatch.setattr(board, "gh", lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(workflow, "_remote_shas", lambda n: ("h1", "m1"))
    reason = workflow._merge_if_unchanged(7, "h1", "m1", info={"title": "t", "body": LEAK_BODY})
    assert reason and "public" in reason
    assert not [c for c in calls if c[:2] == ("pr", "merge")]


def test_visibility_fails_closed(monkeypatch):
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    for reply, public in (('{"visibility": "PRIVATE"}', False), ('{"visibility": "PUBLIC"}', True), ("", True)):
        monkeypatch.setattr(board, "gh", lambda *a, reply=reply, **k: reply)
        assert workflow.repo_public() is public

    def offline(*a, **k):
        raise board.BoardError("gh: offline")

    monkeypatch.setattr(board, "gh", offline)
    assert workflow.repo_public() is True


@given(
    st.text(),
    st.text(),
    st.sampled_from(
        [f"https://{workflow.SESSION_URL}_1", f"HTTP://{workflow.SESSION_URL.upper()}_AB", "\nClaude-Session: x"]
    ),
)
def test_a_session_link_or_trailer_anywhere_is_a_leak(before, after, leak):
    assert workflow.session_leaks(body=before + leak + after) == ["body"]


def test_text_about_the_rule_is_not_a_leak():
    """Issue #292's own wording names the path and the trailer; only a real link or a trailer line is a leak."""
    issue = f"no {workflow.SESSION_URL} URL or Claude-Session trailer in public commits, PRs or issues"
    assert workflow.session_leaks(issue=issue, rule=workflow.SESSION_RULE, none=None) == []
    assert workflow.session_leaks(mid_line="the rule bans a Claude-Session: trailer") == []
    assert workflow.session_leaks(bare=f"https://{workflow.SESSION_URL}s", word=f"{workflow.SESSION_URL}_01") == []


def test_stacked_prs_are_retargeted_to_main_before_the_parent_merges(monkeypatch, capsys):
    calls = []

    def gh(*a, **k):
        calls.append(a)
        if a[:2] == ("pr", "list"):
            return json.dumps([{"number": 8, "headRefName": "13-stacked"}])
        return ""

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(workflow, "_remote_shas", lambda n: ("h1" * 10, "m1"))
    info = {"title": "parent", "body": "Closes #12", "headRefName": "12-parent"}
    assert workflow._merge_if_unchanged(7, "h1" * 10, "m1", info=info) is None
    listing = next(i for i, c in enumerate(calls) if c[:2] == ("pr", "list"))
    assert calls[listing][calls[listing].index("--base") + 1] == "12-parent"
    edit = calls.index(("pr", "edit", "8", "-R", "o/r", "--base", "main"))
    assert edit < next(i for i, c in enumerate(calls) if c[:2] == ("pr", "merge"))
    said = capsys.readouterr().out
    assert "#8" in said and f"--format=%h {('h1' * 10)[:12]}..origin/13-stacked" in said  # the #305 route
    calls.clear()
    assert workflow._merge_if_unchanged(7, "h1" * 10, "m1", dry_run=True, info=info) is None
    assert not [c for c in calls if c[:2] in (("pr", "edit"), ("pr", "merge"))]
    assert "would retarget stacked PR #8" in capsys.readouterr().out


# Readers of main's history on squash merges, and why the rest need no test here: `board.merged_pr` and the board
# lint read the PR's body and branch from GitHub, not commits; the test-weakening check diffs the branch against its
# merge base; `suite_audit` and `consolidation.due` count first-parent commits, and a squashed main is linear (one
# commit per PR, as in `test_first_parent_commits_since_the_review_trigger_it`); devkit replays no merges.


def _squash(repo: Path, branch_files: list[dict[str, str]], subject: str, body: str) -> tuple[list[str], str]:
    """A branch of commits squashed onto main as GitHub does it: (the branch's commits, the squash commit)."""
    name = subject.split(" (")[0].replace(" ", "-")
    sh(repo, "git", "checkout", "-q", "-b", name)
    commits = [commit(repo, files, f"wip {k}\n\nClaude-Session: x") for k, files in enumerate(branch_files)]
    sh(repo, "git", "checkout", "-q", "main")
    sh(repo, "git", "merge", "-q", "--squash", name)
    sh(repo, "git", "commit", "-q", "-m", subject, "-m", body)
    return commits, out(repo, "git", "rev-parse", "HEAD")


def test_the_review_trailer_of_a_squash_commit_resets_consolidation(repo):
    from devkit import consolidation

    _squash(repo, [{"x.py": "x = 1\n"}], "plain (#3)", "Closes #3\n\nsummary")
    assert consolidation.last_review(repo, {})[1] == "root"
    subject, body = workflow.squash_message(4, {"title": "review", "body": "Refs #12\n\nsplit"}, review=12)
    _, sha = _squash(repo, [{"y.py": "y = 1\n"}, {"y.py": "y = 2\n"}], subject, body)
    assert consolidation.last_review(repo, {}) == (sha, "review")
    log = out(repo, "git", "log", "--format=%B", "main")
    assert "Claude-Session" not in log and "wip" not in log  # branch messages never reach main


def test_a_worker_written_review_trailer_does_not_reset_consolidation(repo):
    from devkit import consolidation

    info = {"title": "sneaky", "body": "Closes #5\n\nConsolidation-Review: #5"}
    _squash(repo, [{"z.py": "z = 1\n"}], *workflow.squash_message(6, info))
    assert consolidation.last_review(repo, {})[1] == "root"


def test_a_suite_audit_on_a_squashed_branch_counts_from_its_fork_point(repo):
    from devkit import suite_audit

    fork = out(repo, "git", "rev-parse", "HEAD")
    sh(repo, "git", "checkout", "-q", "-b", "audit")
    commit(repo, {"t.py": "x\n"}, "audit work")
    assert suite_audit.fork_point(repo) == fork
    sh(repo, "git", "checkout", "-q", "main")
    snap = {"commit": "0" * 40, "fork": suite_audit.fork_point(repo), "proxy": 0}  # the branch commit is gone
    assert suite_audit.due(repo, snap)["base"] == fork[:9]
    assert suite_audit._base(repo, {"commit": "0" * 40}) is None


def test_agent_stats_blames_squash_commits_to_their_pr(repo):
    from devkit import stats

    intro_branch, intro = _squash(repo, [{"m.py": "a = 0\n"}, {"m.py": "a = 0\nx = 1\n"}], "add x (#1)", "Closes #5")
    fix_branch, fix = _squash(repo, [{"m.py": "a = 0\nx = 2\n"}], "fix x (#2)", "Closes #9")
    prs = [
        {"number": 1, "headRefName": "5-x", "mergedAt": "2026-10-11T00:00:00Z", "closingIssuesReferences": [],
         "mergeCommit": {"oid": intro}, "commits": [{"oid": c} for c in intro_branch]},
        {"number": 2, "headRefName": "9-fix-x", "mergedAt": "2026-10-13T00:00:00Z", "closingIssuesReferences": [],
         "mergeCommit": {"oid": fix}, "commits": [{"oid": c} for c in fix_branch]},
    ]  # fmt: skip
    bugs = {9: {"createdAt": "2026-10-12T00:00:00Z", "title": "x is wrong"}}
    pairs = stats.trace(prs, bugs, lambda *a: out(repo, "git", *a))
    assert pairs == [{"intro": 1, "fix": 2, "bugs": [9], "after_merge": True, "path": "m.py", "lines": 1}]
