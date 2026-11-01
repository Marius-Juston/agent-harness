"""`dev integrate --train` (#464): several ready PRs merged with one gate, bisected on failure. A real bare origin and
the lead's clone; GitHub is faked over the origin's refs, `dev docs` writes the index and `dev check` fails on a
stale index or on a tree holding a `BREAK` file."""

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, ci_status, cli, fixloop, paths, prtext, train, workflow
from tests.test_merge_hygiene import ATTRIBUTES, INDEX, REAL_POPEN, REAL_RUN, commit, index, note, out, sh


@pytest.fixture
def world(tmp_path, monkeypatch):
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh(tmp_path, "git", "clone", "-q", str(origin), str(clone))
    sh(clone, "git", "config", "user.email", "t@t")
    sh(clone, "git", "config", "user.name", "t")
    shutil.copy(ATTRIBUTES, clone / ".gitattributes")
    commit(clone, {"docs/research/a.md": "one\ntwo\nthree\n", INDEX: "# Index\n- a\n"}, "init")
    sh(clone, "git", "push", "-q", "origin", "main")
    w = {"origin": origin, "clone": clone, "events": [], "prs": {}, "status": [], "blocked": [], "gates": []}

    def head(n: int) -> str:
        return out(origin, "git", "rev-parse", f"refs/heads/{w['prs'][n]['branch']}")

    def gh(*args, **_):
        w["events"].append(("gh", *args))
        if args[:2] == ("pr", "view"):
            n = int(args[2])
            pr = w["prs"][n]
            if args[args.index("--json") + 1] == "headRefOid":
                return json.dumps({"headRefOid": head(n)})
            return json.dumps(
                {
                    "baseRefName": "main",
                    "isCrossRepository": False,
                    "headRefOid": head(n),
                    "headRefName": pr["branch"],
                    "state": "OPEN",
                    "isDraft": False,
                    "title": pr["title"],
                    "body": pr["body"],
                    "statusCheckRollup": pr["checks"].pop(0)
                    if len(pr.get("checks", [])) > 1
                    else pr.get("checks", [[]])[0],
                    "latestReviews": [],
                    "reviewDecision": "",
                }
            )
        return ""

    def run(cmd, **kw):
        if list(cmd[:2]) == ["uv", "run"]:
            tree = Path(kw["cwd"])
            if "docs" in cmd:
                (tree / INDEX).write_text(index(tree))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            fresh = (tree / INDEX).read_text() == index(tree)
            broken = sorted(p.name for p in tree.glob("BREAK*"))
            w["gates"].append(broken)
            ok = fresh and not broken
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "")
        return REAL_RUN(cmd, **kw)

    def popen(cmd, **kw):
        if list(cmd[:2]) != ["uv", "run"]:
            return REAL_POPEN(cmd, **kw)
        code = run(cmd, **kw).returncode
        script = f"print('FAIL tests'); print('  broken'); raise SystemExit({code})" if code else "raise SystemExit(0)"
        return REAL_POPEN([sys.executable, "-c", script], **kw)

    claim = board.Claim(host="box", agent="builder", branch="x", at="", token=None, cid="")
    paths.root.cache_clear()
    paths.common_dir.cache_clear()
    monkeypatch.chdir(clone)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "gh_user", lambda: "lead")
    monkeypatch.setattr(board, "pr_feedback", lambda n: {"since": "", "comments": [], "reviews": [], "threads": []})
    monkeypatch.setattr(board, "fix_comments", lambda n: [])
    monkeypatch.setattr(board, "get", lambda n: type("I", (), {"claim": lambda self: claim})())
    monkeypatch.setattr(board, "block", lambda n, reason: w["blocked"].append((n, reason)))
    monkeypatch.setattr(board, "set_status", lambda n, status: w["status"].append((n, status)))
    monkeypatch.setattr(
        board, "merged_pr", lambda n, *a: w["events"].append(("merged_pr", n)) or [f"#{n + 100} -> done"]
    )
    monkeypatch.setattr(workflow, "main_checkout", lambda: clone)
    monkeypatch.setattr(workflow, "state_dir", lambda *p: tmp_path.joinpath("state", *p))
    monkeypatch.setattr(fixloop, "state_dir", lambda *p: tmp_path.joinpath("state", *p))
    monkeypatch.setattr(workflow, "repo_public", lambda: True)
    monkeypatch.setattr(train.handoff, "after_integrate", lambda: "")
    yield w
    paths.root.cache_clear()
    paths.common_dir.cache_clear()


def open_pr(w: dict, n: int, files: dict[str, str], title: str | None = None) -> str:
    """PR n on branch `<issue>-pr<n>` cut from the current main, closing issue n + 100."""
    clone, branch = w["clone"], f"{n + 100}-pr{n}"
    sh(clone, "git", "checkout", "-q", "-b", branch)
    tip = commit(clone, files, branch)
    sh(clone, "git", "push", "-q", "origin", branch)
    sh(w["origin"], "git", "update-ref", f"refs/pull/{n}/head", tip)
    sh(clone, "git", "checkout", "-q", "-f", "main")
    sh(clone, "git", "clean", "-qfd")
    w["prs"][n] = {"branch": branch, "title": title or f"feat(x): pr {n} (#{n + 100})", "body": f"Closes #{n + 100}"}
    return tip


def last_json(text: str) -> dict:
    return json.loads(text.strip().splitlines()[-1].removeprefix("RESULT-JSON: "))


def main_log(w: dict) -> list[str]:
    return out(w["origin"], "git", "log", "--format=%s", "refs/heads/main").splitlines()


def closed(w: dict) -> list[int]:
    return [int(e[3]) for e in w["events"] if e[:3] == ("gh", "pr", "close")]


def sent_back(w: dict) -> list[int]:
    return [int(e[3]) for e in w["events"] if e[:3] == ("gh", "pr", "comment")]


def three(w: dict, broken: int | None = None) -> None:
    """Three PRs, each adding a note (their index rows conflict textually, the generated-file driver merges them);
    `broken` also adds a file that fails the gate."""
    for n in (1, 2, 3):
        (w["clone"] / f"docs/research/n{n}.md").parent.mkdir(parents=True, exist_ok=True)
        files = note(w["clone"], f"n{n}") | ({f"BREAK{n}": "x\n"} if n == broken else {})
        open_pr(w, n, files)


def test_three_ready_prs_merge_with_one_gate_each_as_its_own_squash_commit(world, capsys):
    three(world)
    code = cli.run(cli.parse(["integrate", "--train", "3", "1", "2", "3"]))  # the CLI entry point
    assert code == 0
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "merged" and len(res["gates_run"]) == 1 and res["gates_run"][0]["members"] == [1, 2, 3]
    assert {k: v["outcome"] for k, v in res["members"].items()} == {"1": "merged", "2": "merged", "3": "merged"}
    assert main_log(world)[:3] == [
        "feat(x): pr 3 (#103) (#3)",
        "feat(x): pr 2 (#102) (#2)",
        "feat(x): pr 1 (#101) (#1)",
    ]
    assert out(world["origin"], "git", "show", f"refs/heads/main:{INDEX}") == "# Index\n- a\n- n1\n- n2\n- n3"
    assert out(world["origin"], "git", "rev-parse", "refs/heads/main") == res["main"]
    assert closed(world) == [1, 2, 3]
    note_ = next(e for e in world["events"] if e[:3] == ("gh", "pr", "close"))
    assert "train (#1, #2, #3)" in note_[note_.index("--comment") + 1]
    assert [e[1] for e in world["events"] if e[0] == "merged_pr"] == [1, 2, 3]
    assert not any(e[:3] == ("gh", "pr", "merge") for e in world["events"])  # landed by one push of main


def test_one_breaking_member_is_isolated_by_bisection_and_only_it_is_sent_back(world, capsys):
    three(world, broken=2)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    outcomes = {k: v["outcome"] for k, v in res["members"].items()}
    assert outcomes == {"1": "merged", "2": "gate-failed", "3": "merged"} and res["outcome"] == "partial"
    assert [g["members"] for g in res["gates_run"]] == [[1, 2, 3], [1], [1, 2], [1, 3]]
    assert len(res["gates_run"]) - 1 <= train.bisect_bound(3)
    assert sent_back(world) == [2] and world["status"] == [(102, "in-progress")]
    assert res["members"]["2"]["cycle"] == 1 and "FAIL tests" in res["members"]["2"]["gate"]
    assert main_log(world)[:2] == ["feat(x): pr 3 (#103) (#3)", "feat(x): pr 1 (#101) (#1)"]
    assert closed(world) == [1, 3]


def test_a_conflicting_member_is_sent_back_before_any_gate(world, capsys):
    open_pr(world, 1, {"docs/research/a.md": "one\nTWO by 1\nthree\n"})
    open_pr(world, 2, {"docs/research/a.md": "one\nTWO by 2\nthree\n"})
    open_pr(world, 3, note(world["clone"], "n3"))
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["members"]["2"]["outcome"] == "conflict" and res["members"]["2"]["conflicts"] == ["docs/research/a.md"]
    assert [g["members"] for g in res["gates_run"]] == [[1, 3]]
    assert world["gates"] == [[]]  # one gate, run after the conflict was sent back
    assert sent_back(world) == [2] and res["members"]["2"]["cycle"] == 0  # another member's doing: not counted
    assert closed(world) == [1, 3]


def test_a_member_failing_admission_is_skipped_and_the_rest_merge(world, capsys, monkeypatch):
    three(world)
    monkeypatch.setattr(workflow, "expected_title", lambda info: '"feat(x): pr 2 (#102)"')
    world["prs"][2]["title"] = "pr two"
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["members"]["2"]["outcome"] == "refused" and "Conventional" in res["members"]["2"]["reason"]
    assert [g["members"] for g in res["gates_run"]] == [[1, 3]]
    assert sent_back(world) == [2] and closed(world) == [1, 3]


def test_harness_ok_names_the_members_it_clears_and_holds_the_others_unsent(world, capsys):
    open_pr(world, 1, {"devkit/x.py": "x = 1\n"})
    open_pr(world, 2, {"devkit/y.py": "y = 1\n"})
    code = cli.run(cli.parse(["integrate", "--train", "2", "1", "2", "--harness-ok", "2"]))
    assert code == 1
    res = last_json(capsys.readouterr().out)
    assert {k: v["outcome"] for k, v in res["members"].items()} == {"1": "harness-review", "2": "merged"}
    assert [g["members"] for g in res["gates_run"]] == [[2]] and sent_back(world) == [] and closed(world) == [2]


def test_a_bare_harness_ok_is_refused_for_a_train(world, capsys):
    code = cli.run(cli.parse(["integrate", "--train", "2", "1", "2", "--harness-ok"]))
    assert code == 2
    assert "--harness-ok 12 15" in capsys.readouterr().out


def test_a_dry_run_gates_but_lands_and_sends_back_nothing(world, capsys):
    three(world, broken=3)
    before = main_log(world)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=True) == 1
    res = last_json(capsys.readouterr().out)
    assert {k: v["outcome"] for k, v in res["members"].items()} == {"1": "would-merge", "2": "would-merge",
                                                                      "3": "gate-failed"}  # fmt: skip
    assert main_log(world) == before and sent_back(world) == [] and closed(world) == []


def test_local_ci_posts_statuses_on_each_member_head(world, capsys, monkeypatch):
    three(world)
    monkeypatch.setattr(ci_status, "required_contexts", lambda: (["check"], []))
    monkeypatch.setattr(ci_status, "actions_enabled", lambda: False)
    monkeypatch.setattr(train.config, "get", lambda: train.config.Config(local_ci={"check": []}))
    assert train.train([1, 2], harness_ok=True, dry_run=False, local_ci=True) == 0
    posts = [e for e in world["events"] if e[:4] == ("gh", "api", "-X", "POST")]
    heads = {out(world["origin"], "git", "rev-parse", f"refs/pull/{n}/head") for n in (1, 2)}
    assert {e[4].rsplit("/", 1)[1] for e in posts} == heads and all("context=check" in e for e in posts)


def test_the_cli_refuses_a_count_that_does_not_match(world, capsys):
    code = cli.run(cli.parse(["integrate", "--train", "3", "1", "2"]))
    assert code == 2
    assert "exactly N PRs" in capsys.readouterr().out


@given(st.integers(1, 64), st.data())
def test_first_breaking_finds_the_first_failing_prefix_within_the_bound(n, data):
    k = data.draw(st.integers(1, n))
    seen = []
    found, gates = train.first_breaking(n, lambda m: seen.append(m) or m < k)
    assert found == k and gates == len(seen) <= math.ceil(math.log2(n))


def test_members_after_the_culprit_that_still_fail_are_deferred_not_sent_back(world, capsys):
    for n in (1, 2, 3):
        open_pr(world, n, note(world["clone"], f"n{n}") | ({f"BREAK{n}": "x\n"} if n > 1 else {}))
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    outcomes = {k: v["outcome"] for k, v in res["members"].items()}
    assert outcomes == {"1": "merged", "2": "gate-failed", "3": "deferred"}
    assert len(res["gates_run"]) - 1 == train.bisect_bound(3)
    assert sent_back(world) == [2] and closed(world) == [1]


def test_feedback_arriving_during_the_gate_sends_that_member_back_and_lands_nothing(world, capsys, monkeypatch):
    three(world)
    before = main_log(world)
    thread = {"path": "docs/research/n2.md", "line": 1, "comments": {"nodes": [{"body": "why?", "url": "u"}]}}

    def feedback(n):
        late = n == 2 and world["gates"]
        return {"since": "", "comments": [], "reviews": [], "threads": [thread] if late else []}

    monkeypatch.setattr(board, "pr_feedback", feedback)
    code = train.train([1, 2, 3], harness_ok=True, dry_run=False)
    assert code == 1
    res = last_json(capsys.readouterr().out)
    outcomes = {k: v["outcome"] for k, v in res["members"].items()}
    assert outcomes == {"1": "stopped", "2": "refused", "3": "stopped"} and res["outcome"] == "stopped"
    assert main_log(world) == before and closed(world) == [] and sent_back(world) == [2]


def during_gate(monkeypatch, act) -> None:
    """Run `act` once, right after the first gate finished."""
    real, done = workflow.run_gate, []

    def run_gate(*a, **kw):
        passed = real(*a, **kw)
        if not done:
            done.append(act())
        return passed

    monkeypatch.setattr(workflow, "run_gate", run_gate)


def outcomes_of(res: dict) -> dict[str, str]:
    return {k: v["outcome"] for k, v in res["members"].items()}


def test_main_moving_before_the_push_lands_nothing(world, capsys, monkeypatch):
    three(world)
    clone = world["clone"]

    def land_elsewhere():
        commit(clone, {"other.txt": "x\n"}, "another merge")
        sh(clone, "git", "push", "-q", "origin", "main")

    during_gate(monkeypatch, land_elsewhere)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert set(outcomes_of(res).values()) == {"stopped"} and "main moved" in res["reason"]
    assert main_log(world) == ["another merge", "init"] and closed(world) == [] and sent_back(world) == []


def test_a_member_head_moving_during_the_gate_lands_nothing(world, capsys, monkeypatch):
    three(world)
    origin, clone = world["origin"], world["clone"]

    def push_to_pr2():
        sh(clone, "git", "checkout", "-q", "102-pr2")
        commit(clone, {"later.txt": "x\n"}, "later")
        sh(clone, "git", "push", "-q", "origin", "102-pr2")
        sh(clone, "git", "checkout", "-q", "-f", "main")

    during_gate(monkeypatch, push_to_pr2)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert outcomes_of(res) == {"1": "stopped", "2": "stopped", "3": "stopped"}
    assert res["members"]["2"]["reason"] == "the PR head moved during the gate"
    assert main_log(world) == ["init"] and closed(world) == [] and out(origin, "git", "log", "-1", "--format=%s",
                                                                       "102-pr2") == "later"  # fmt: skip


def test_a_session_link_in_a_squash_subject_at_recheck_is_sent_back_and_lands_nothing(world, capsys, monkeypatch):
    three(world)
    link = "https://" + prtext.SESSION_URL + "_abc123"
    during_gate(monkeypatch, lambda: world["prs"][2].update(title=f"feat(x): pr 2 {link} (#102)"))
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert outcomes_of(res) == {"1": "stopped", "2": "refused", "3": "stopped"}
    assert workflow.SESSION_RULE in res["members"]["2"]["feedback"][0]
    assert main_log(world) == ["init"] and closed(world) == [] and sent_back(world) == [2]
    posted = next(e for e in world["events"] if e[:3] == ("gh", "pr", "comment"))
    assert prtext.SESSION_URL not in posted[posted.index("--body") + 1]  # scrubbed by the fix loop


def _deleting_pr(w: dict, n: int, path: str) -> None:
    clone, branch = w["clone"], f"{n + 100}-pr{n}"
    sh(clone, "git", "checkout", "-q", "-b", branch)
    sh(clone, "git", "rm", "-q", path)
    sh(clone, "git", "commit", "-q", "-m", branch)
    sh(clone, "git", "push", "-q", "origin", branch)
    sh(w["origin"], "git", "update-ref", f"refs/pull/{n}/head", out(clone, "git", "rev-parse", "HEAD"))
    sh(clone, "git", "checkout", "-q", "-f", "main")
    w["prs"][n] = {"branch": branch, "title": f"feat(x): pr {n} (#{n + 100})", "body": f"Closes #{n + 100}"}


def test_a_member_deleting_what_another_open_issue_owns_is_sent_back_before_the_gate(world, capsys, monkeypatch):
    open_pr(world, 1, note(world["clone"], "n1"))
    _deleting_pr(world, 2, "docs/research/a.md")
    open_pr(world, 3, note(world["clone"], "n3"))
    owned = "docs/research/a.md"
    monkeypatch.setattr(board, "fetch", lambda *a, **kw: [])
    monkeypatch.setattr(board, "deleting_owns", lambda _i, deleted, *_a: {300: [owned]} if owned in deleted else {})
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert outcomes_of(res) == {"1": "merged", "2": "refused", "3": "merged"}
    assert res["members"]["2"]["owns"] == {"300": [owned]} and res["members"]["2"]["cycle"] == 1
    assert [g["members"] for g in res["gates_run"]] == [[1, 3]] and sent_back(world) == [2]
    assert out(world["origin"], "git", "cat-file", "-t", f"refs/heads/main:{owned}") == "blob"


def test_the_owns_check_fails_closed_when_the_board_cannot_be_read(world, capsys, monkeypatch):
    open_pr(world, 1, note(world["clone"], "n1"))
    _deleting_pr(world, 2, "docs/research/a.md")

    def unreadable(*a, **kw):
        raise board.BoardError("gh: rate limited")

    monkeypatch.setattr(board, "fetch", unreadable)
    assert train.train([1, 2], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert set(outcomes_of(res).values()) == {"stopped"} and "deleted Owns" in res["reason"]
    assert res["gates_run"] == [] and main_log(world) == ["init"] and closed(world) == []


def test_local_ci_waits_for_a_check_running_at_admission_and_a_later_failure_keeps_it_off_main(
    world, capsys, monkeypatch
):
    three(world)
    run = {"__typename": "CheckRun", "name": "check", "workflowName": "ci", "status": "IN_PROGRESS"}
    failed = run | {"status": "COMPLETED", "conclusion": "FAILURE"}
    world["prs"][2]["checks"] = [[run], [failed]]  # running when the train reads the PR, failed when it waits
    monkeypatch.setattr(ci_status, "required_contexts", lambda: (["check"], []))
    monkeypatch.setattr(ci_status, "actions_enabled", lambda: False)
    monkeypatch.setattr(train.config, "get", lambda: train.config.Config(local_ci={"check": []}))
    assert train.train([1, 2], harness_ok=True, dry_run=False, local_ci=True) == 1
    res = last_json(capsys.readouterr().out)
    assert outcomes_of(res) == {"1": "merged", "2": "refused"} and "check check: failing" in str(res["members"]["2"])
    assert closed(world) == [1] and [g["members"] for g in res["gates_run"]] == [[1]]
    head2 = out(world["origin"], "git", "rev-parse", "refs/pull/2/head")
    assert not any(e[:4] == ("gh", "api", "-X", "POST") and head2 in e[4] for e in world["events"])


def test_local_ci_statuses_are_posted_only_after_the_push(world, capsys, monkeypatch):
    three(world)
    monkeypatch.setattr(ci_status, "required_contexts", lambda: (["check"], []))
    monkeypatch.setattr(ci_status, "actions_enabled", lambda: False)
    monkeypatch.setattr(train.config, "get", lambda: train.config.Config(local_ci={"check": []}))
    main_at_post = []
    real = board.gh

    def gh(*args, **kw):
        if args[:3] == ("api", "-X", "POST") and "/statuses/" in args[3]:
            main_at_post.append(out(world["origin"], "git", "log", "-1", "--format=%s", "refs/heads/main"))
        return real(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    assert train.train([1, 2], harness_ok=True, dry_run=False, local_ci=True) == 0
    assert main_at_post == ["feat(x): pr 2 (#102) (#2)"] * 2


def test_a_close_github_refuses_keeps_the_member_merged_and_closes_the_rest(world, capsys, monkeypatch):
    three(world)
    real = board.gh

    def gh(*args, **kw):
        if args[:3] == ("pr", "close", "1"):
            raise board.BoardError("gh: 502")
        return real(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    res = last_json(capsys.readouterr().out)
    assert set(outcomes_of(res).values()) == {"merged"} and res["members"]["1"]["close_error"] == "gh: 502"
    assert closed(world) == [2, 3] and [e[1] for e in world["events"] if e[0] == "merged_pr"] == [1, 2, 3]


def test_a_flaky_full_gate_is_confirmed_once_and_then_lands_every_member(world, capsys, monkeypatch):
    three(world)
    real, calls = workflow.run_gate, []

    def run_gate(tree, contexts, log):
        calls.append(1)
        if len(calls) == 1:  # the first full gate fails once, for no reason in the tree
            log.write_text("FAIL tests\n  flaky\n")
            return False
        return real(tree, contexts, log)

    monkeypatch.setattr(workflow, "run_gate", run_gate)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    res = last_json(capsys.readouterr().out)
    assert [g["members"] for g in res["gates_run"]] == [[1, 2, 3], [1], [1, 2], [1, 2, 3]]
    assert len(res["gates_run"]) - 1 <= train.bisect_bound(3)
    assert set(outcomes_of(res).values()) == {"merged"} and res["members"]["3"]["flaky_gate"] is True
    assert sent_back(world) == []


def test_a_last_member_that_fails_twice_is_blamed_by_what_was_measured(world, capsys):
    three(world, broken=3)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert [g["members"] for g in res["gates_run"]] == [[1, 2, 3], [1], [1, 2], [1, 2, 3]]
    assert outcomes_of(res) == {"1": "merged", "2": "merged", "3": "gate-failed"}
    posted = next(e for e in world["events"] if e[:3] == ("gh", "pr", "comment"))
    assert "with #1, #2 merged passed the gate and adding this PR failed it (gated twice)" in posted[-1]


def test_every_squash_commit_on_main_carries_its_regenerated_index(world, capsys):
    three(world)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    shas = out(world["origin"], "git", "log", "--format=%H", "-3", "refs/heads/main").splitlines()[::-1]
    rows = [out(world["origin"], "git", "show", f"{s}:{INDEX}") for s in shas]
    assert rows == ["# Index\n- a\n- n1", "# Index\n- a\n- n1\n- n2", "# Index\n- a\n- n1\n- n2\n- n3"]


def _branch_on(repo: Path, name: str) -> bool:
    return bool(out(repo, "git", "branch", "--list", name))


def test_a_landed_member_leaves_no_branch_behind_locally_or_on_origin(world, capsys):
    three(world)
    assert all(_branch_on(world["clone"], f"10{n}-pr{n}") for n in (1, 2, 3))
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    for n in (1, 2, 3):
        assert not _branch_on(world["clone"], f"10{n}-pr{n}")  # drop_branch: the gated head contains it
        assert not _branch_on(world["origin"], f"10{n}-pr{n}")  # deleted under a lease on the gated head
    close = next(e for e in world["events"] if e[:3] == ("gh", "pr", "close"))
    assert "--delete-branch" not in close


def test_a_head_moving_after_the_push_keeps_its_pr_open_and_its_branch(world, capsys, monkeypatch):
    three(world)
    clone, real, moved = world["clone"], board.gh, []

    def gh(*args, **kw):
        if args[:3] == ("pr", "view", "2") and args[-1] == "headRefOid" and not moved:
            sh(clone, "git", "checkout", "-q", "102-pr2")
            moved.append(commit(clone, {"late.txt": "x\n"}, "late"))
            sh(clone, "git", "push", "-q", "origin", "102-pr2")
            sh(clone, "git", "checkout", "-q", "-f", "main")
        return real(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    res = last_json(capsys.readouterr().out)
    assert set(outcomes_of(res).values()) == {"merged"} and "stays open" in res["members"]["2"]["kept_open"]
    assert closed(world) == [1, 3] and [e[1] for e in world["events"] if e[0] == "merged_pr"] == [1, 3]
    assert out(world["origin"], "git", "rev-parse", "refs/heads/102-pr2") == moved[0]  # branch kept, not deleted


def test_a_pr_stacked_on_a_member_is_retargeted_to_main_before_the_close(world, capsys, monkeypatch):
    three(world)
    real = board.gh

    def gh(*args, **kw):
        if args[:2] == ("pr", "list") and "--base" in args and args[args.index("--base") + 1] == "101-pr1":
            world["events"].append(("gh", *args))
            return json.dumps([{"number": 9, "headRefName": "109-x"}])
        return real(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    ev = world["events"]
    edit = next(i for i, e in enumerate(ev) if e[:4] == ("gh", "pr", "edit", "9"))
    assert ev[edit][-2:] == ("--base", "main")
    assert edit < next(i for i, e in enumerate(ev) if e[:4] == ("gh", "pr", "close", "1"))
    assert "retargeted stacked PR #9" in capsys.readouterr().out


def test_a_board_update_github_refuses_keeps_the_member_merged(world, capsys, monkeypatch):
    three(world)

    def merged_pr(n, *a):
        if n == 1:
            raise board.BoardError("gh: 502")
        world["events"].append(("merged_pr", n))
        return [f"#{n + 100} -> done"]

    monkeypatch.setattr(board, "merged_pr", merged_pr)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 0
    res = last_json(capsys.readouterr().out)
    assert set(outcomes_of(res).values()) == {"merged"}
    assert res["members"]["1"]["board_error"] == "gh: 502" and res["members"]["1"]["moved"] == []
    assert res["members"]["2"]["moved"] == ["#102 -> done"] and closed(world) == [1, 2, 3]


def test_a_regenerate_failing_on_an_intermediate_step_stops_with_its_own_reason(world, capsys, monkeypatch):
    three(world)
    real, calls = workflow.regenerate, []

    def regenerate(tree):
        calls.append(tree)
        if len(calls) > 1:  # the candidate's regenerate passed; the first intermediate step's fails
            raise RuntimeError("`uv run dev docs` failed in the merged tree: boom")
        return real(tree)

    monkeypatch.setattr(workflow, "regenerate", regenerate)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["reason"].startswith("regenerating generated files on PR #1's step failed")
    assert "push" not in res["reason"] and set(outcomes_of(res).values()) == {"stopped"}
    assert main_log(world) == ["init"] and closed(world) == []


def test_the_regenerated_tree_is_checked_for_deleted_owns_too(world, capsys, monkeypatch):
    three(world)
    real, calls = workflow.deleted_owns, []

    def deleted_owns(tree, base, info):
        calls.append(info["headRefName"])
        if len(calls) > 3 and info["headRefName"] == "102-pr2":  # past the three merge-time checks
            return {300: ["docs/research/owned.md"]}
        return real(tree, base, info)

    monkeypatch.setattr(workflow, "deleted_owns", deleted_owns)
    assert train.train([1, 2, 3], harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert outcomes_of(res) == {"1": "stopped", "2": "refused", "3": "stopped"}
    assert res["members"]["2"]["owns"] == {"300": ["docs/research/owned.md"]} and sent_back(world) == [2]
    assert len(res["gates_run"]) == 1 and main_log(world) == ["init"] and closed(world) == []


def test_a_bare_harness_ok_on_a_single_integrate_still_clears_it(world, monkeypatch):
    seen = []
    monkeypatch.setattr(workflow, "integrate", lambda pr, ok, *a: seen.append((pr, ok)) or 0)
    for argv in (["integrate", "7", "--harness-ok"], ["integrate", "7", "--harness-ok", "7"], ["integrate", "7"]):
        code = cli.run(cli.parse(argv))
        assert code == 0
    assert seen == [(7, True), (7, True), (7, False)]


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["integrate", "--train", "3", "7", "7", "8"], "named twice"),
        (["integrate", "--train", "2", "7", "8", "--harness-ok", "9"], "not in the train"),
        (["integrate", "7", "--harness-ok", "9"], "not PR 7"),
    ],
)
def test_the_cli_refuses_duplicate_members_and_stray_harness_ok_numbers(world, capsys, argv, says):
    code = cli.run(cli.parse(argv))
    assert code == 2
    assert says in capsys.readouterr().out and world["events"] == []
