"""`dev autopilot` (#499): one lead iteration in code. The train's fixture repository (a bare origin, the lead's clone,
GitHub faked over the origin's refs) plus `gh pr list`; integrate and the train run for real on it."""

import json
import subprocess

import pytest

from devkit import autopilot, board, cli, config, frontmatter, quota, train, workflow
from tests.test_merge_hygiene import ROOT, commit, note, out, sh
from tests.test_train import closed, main_log, open_pr, sent_back, world  # noqa: F401 - `world` is a fixture

GREEN = [{"__typename": "CheckRun", "name": "check", "status": "COMPLETED", "conclusion": "SUCCESS"}]
RUNNING = [{"__typename": "CheckRun", "name": "check", "status": "IN_PROGRESS", "conclusion": None}]
RED = [{"__typename": "CheckRun", "name": "check", "status": "COMPLETED", "conclusion": "FAILURE"}]


@pytest.fixture
def pilot(world, monkeypatch):  # noqa: F811 - the imported fixture
    """`world` plus `gh pr list` over its PRs, each with its `checks` rollup and `reviews`, a quota reading under the
    cap and a board with nothing to sync or dispatch."""
    fake, w = board.gh, world

    def gh(*args, **kw):
        if args[:2] == ("pr", "list") and args[args.index("--base") + 1 if "--base" in args else 0] == "main":
            w["events"].append(("gh", *args))
            rows = []
            for n, pr in sorted(w["prs"].items()):
                if n in closed(w):
                    continue
                rows.append({
                    "number": n,
                    "headRefName": pr["branch"],
                    "headRefOid": pr.get("listed") or out(w["origin"], "git", "rev-parse", f"refs/heads/{pr['branch']}"),
                    "baseRefName": "main",
                    "isCrossRepository": False,
                    "isDraft": pr.get("draft", False),
                    "statusCheckRollup": pr.get("checks", [GREEN])[0],
                    "latestReviews": [],
                    "reviewDecision": "",
                })  # fmt: skip
            return json.dumps(rows)
        return fake(*args, **kw)

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(
        board,
        "pr_feedback",
        lambda n: {"since": "", "comments": [], "threads": [], "reviews": w["prs"][n].get("reviews", [])},
    )
    monkeypatch.setattr(quota, "quota_reading", lambda: (10, 80))
    monkeypatch.setattr(board, "sync", lambda: [])
    monkeypatch.setattr(board, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(board, "closed_numbers", set)
    return w


def pr_with(w: dict, n: int, files: dict[str, str], **extra) -> None:
    open_pr(w, n, files)
    w["prs"][n] |= extra


def tick(capsys, *flags: str) -> tuple[int, list[str]]:
    code = cli.run(cli.parse(["autopilot", *flags]))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) <= autopilot.LINES, lines
    return code, lines


def merges(w: dict) -> list[str]:
    return [e for e in w["events"] if e[:3] == ("gh", "pr", "merge")]


def test_a_ready_content_pr_is_integrated_and_reported_in_three_lines(pilot, capsys):
    pr_with(pilot, 1, note(pilot["clone"], "n1"))
    code, lines = tick(capsys)
    assert code == 0
    assert lines[1] == "merged #1: #101 -> done" and lines[-1] == "AUTOPILOT: continue" and len(lines) == 3
    assert len(merges(pilot)) == 1


def test_several_ready_prs_go_through_one_train(pilot, capsys):
    for n in (1, 2, 3):
        pr_with(pilot, n, note(pilot["clone"], f"n{n}"))
    code, lines = tick(capsys)
    assert code == 0, lines
    assert lines[1:4] == [f"merged #{n}: #{n + 100} -> done" for n in (1, 2, 3)]
    assert pilot["gates"] == [[]] and closed(pilot) == [1, 2, 3]  # one gate for the three


def test_a_dry_run_merges_nothing_and_syncs_nothing(pilot, capsys, monkeypatch):
    monkeypatch.setattr(board, "sync", lambda: pytest.fail("a dry run syncs nothing"))
    pr_with(pilot, 1, note(pilot["clone"], "n1"))
    before = main_log(pilot)
    code, lines = tick(capsys, "--dry-run")
    assert code == 0 and lines[1] == "would-merge #1" and "(dry run)" in lines[0]
    assert main_log(pilot) == before and merges(pilot) == [] and closed(pilot) == []


def test_with_the_trust_key_off_a_harness_pr_waits_for_the_leads_read(pilot, capsys):
    """The default: no verdict asks for a reviewer; even a merge verdict on the head waits for the lead."""
    tip = open_pr(pilot, 1, {"devkit/x.py": "x = 1\n"})
    code, lines = tick(capsys)
    assert code == 0 and lines[1] == "review #1 (harness): no verdict; spawn harness-ops:reviewer"
    pilot["prs"][1]["reviews"] = [
        {"author": {"login": "lead"}, "body": f"Verdict: merge, round 1, {tip[:7]}", "submittedAt": "2026-11-19T00:00"}
    ]
    code, lines = tick(capsys)
    assert code == 0 and lines[1].startswith("lead #1 (harness): verdict merge;") and "--harness-ok" in lines[1]
    assert merges(pilot) == [] and closed(pilot) == [] and sent_back(pilot) == []


def test_a_verdict_by_anyone_but_the_gh_user_does_not_count(pilot, capsys):
    tip = open_pr(pilot, 1, {"devkit/x.py": "x = 1\n"})
    pilot["prs"][1]["reviews"] = [{"author": {"login": "stranger"}, "body": f"Verdict: merge, round 1, {tip[:7]}"}]
    _, lines = tick(capsys)
    assert "no verdict" in lines[1]


def test_running_failing_and_draft_prs_are_left_alone(pilot, capsys):
    pr_with(pilot, 1, note(pilot["clone"], "n1"), checks=[RUNNING])
    pr_with(pilot, 2, note(pilot["clone"], "n2"), checks=[RED])
    pr_with(pilot, 3, note(pilot["clone"], "n3"), draft=True)
    code, lines = tick(capsys)
    assert code == 0 and "3 open PRs (1 draft, 1 checks running)" in lines[0]
    assert lines[1] == "resume #2: check check: failing (FAILURE); dev board feedback 2" and len(lines) == 3
    assert merges(pilot) == [] and closed(pilot) == [] and sent_back(pilot) == [] and pilot["gates"] == []


def test_an_integrate_that_does_not_merge_stops_the_loop(pilot, capsys):
    pr_with(pilot, 1, {"docs/research/a.md": "one\nTWO by 1\nthree\n"})
    commit(pilot["clone"], {"docs/research/a.md": "one\nTWO on main\nthree\n"}, "main moves")
    sh(pilot["clone"], "git", "push", "-q", "origin", "main")
    code, lines = tick(capsys)
    assert code == 1 and lines[-1] == "AUTOPILOT: stop (an integrate did not merge)"
    assert lines[1].startswith("STOP integrate #1 conflict") and lines[1].endswith("dev integrate 1 --why")
    assert merges(pilot) == [] and sent_back(pilot) == [1]  # integrate's own fix loop sent it back


def test_a_train_member_integrate_refuses_is_not_merged_and_stops(pilot, capsys, monkeypatch):
    monkeypatch.setattr(workflow, "expected_title", lambda info: '"feat(x): pr 2 (#102)"')
    for n in (1, 2):
        pr_with(pilot, n, note(pilot["clone"], f"n{n}"))
    pilot["prs"][2]["title"] = "pr two"
    code, lines = tick(capsys)
    assert code == 1 and closed(pilot) == [1]
    assert any(ln.startswith("STOP integrate #2 refused") for ln in lines) and "merged #1: #101 -> done" in lines


def test_the_quota_cap_stops_before_anything_is_read(pilot, capsys, monkeypatch):
    monkeypatch.setattr(quota, "quota_reading", lambda: (80, 80))
    pr_with(pilot, 1, note(pilot["clone"], "n1"))
    code, lines = tick(capsys)
    assert code == 1 and lines == [
        "AUTOPILOT: stop (quota 80% at or over the cap 80%): start nothing; commit, update HANDOFF"
    ]
    assert pilot["events"] == []


def test_nothing_to_do_is_idle(pilot, capsys):
    code, lines = tick(capsys)
    assert code == 0 and lines[-1] == "AUTOPILOT: idle" and len(lines) == 2


def test_many_items_are_capped_at_the_line_budget(pilot, capsys):
    for n in range(1, 21):
        pr_with(pilot, n, note(pilot["clone"], f"n{n}"), checks=[RED])
    _, lines = tick(capsys)
    assert len(lines) == autopilot.LINES and lines[-2].startswith("... ") and lines[-1] == "AUTOPILOT: continue"


def test_startable_items_with_disjoint_owns_are_offered_for_dispatch(pilot, capsys, monkeypatch):
    def issue(n: int, owns: str, status: str = "ready") -> board.Issue:
        labels = [{"name": f"status:{status}"}, {"name": "type:build"}, {"name": "P1"}]
        return board._issue({"number": n, "title": f"item {n}", "labels": labels, "body": f"## Owns\n- `{owns}`\n"})

    monkeypatch.setattr(board, "fetch", lambda *a, **k: [issue(1, "a.py"), issue(2, "a.py"), issue(3, "b.py"),
                                                          issue(4, "c.py", "in-progress"), issue(5, "c.py")])  # fmt: skip
    _, lines = tick(capsys)
    assert [ln.split(":")[0] for ln in lines[1:-1]] == ["dispatch #1 P1 build", "dispatch #3 P1 build"]


def test_github_not_answering_stops_in_one_line(pilot, capsys, monkeypatch):
    def down():
        raise board.BoardError("gh: HTTP 502")

    monkeypatch.setattr(board, "sync", down)
    code, lines = tick(capsys)
    assert code == 1 and lines == ["AUTOPILOT: stop (cannot read GitHub or the board: gh: HTTP 502)"]


def test_the_lead_skill_runs_autopilot_in_its_loop():
    _, body = frontmatter.read(ROOT / "plugins/harness-ops/skills/lead/SKILL.md")
    loop = body.split("## 2. Close the loop first", 1)[1].split("\n## ", 1)[0]
    assert "`uv run dev autopilot`" in loop.splitlines()[1]


def verdict(word: str, sha: str, login: str = "lead", by: str = "harness-ops:reviewer") -> dict:
    body = f"Verdict: {word}, round 1, {sha[:7]}\n- a finding\n" + (f"Reviewed by {by}." if by else "")
    return {"author": {"login": login}, "body": body, "submittedAt": "2026-11-19"}


@pytest.fixture
def trusting(pilot, monkeypatch):
    """This repository's setting (#499, owner decision): a merge verdict on the current head clears a harness PR."""
    monkeypatch.setattr(config, "get", lambda: config.Config(autopilot_trust_review="harness-ops:reviewer"))
    return pilot


def push_more(w: dict, n: int) -> str:
    """A new commit on PR n's branch after its review: GitHub's head and refs/pull/n/head move to it."""
    clone, branch = w["clone"], w["prs"][n]["branch"]
    sh(clone, "git", "checkout", "-q", branch)
    tip = commit(clone, {"devkit/later.py": "y = 2\n"}, "later")
    sh(clone, "git", "push", "-q", "origin", branch)
    sh(w["origin"], "git", "update-ref", f"refs/pull/{n}/head", tip)
    sh(clone, "git", "checkout", "-q", "-f", "main")
    return tip


def test_a_trusted_merge_verdict_on_the_current_head_integrates_a_harness_pr(trusting, capsys):
    tip = open_pr(trusting, 1, {"devkit/x.py": "x = 1\n"})
    trusting["prs"][1]["reviews"] = [verdict("merge", tip)]
    code, lines = tick(capsys)
    assert code == 0 and lines[1] == "merged #1: #101 -> done" and len(merges(trusting)) == 1


def test_a_push_after_autopilot_read_the_verdict_is_not_cleared(trusting, capsys):
    """The list (autopilot's read) still shows the reviewed head; integrate reads the pushed one: --harness-ok named
    the reviewed SHA, never a bare True, so nothing merges."""
    tip = open_pr(trusting, 1, {"devkit/x.py": "x = 1\n"})
    trusting["prs"][1] |= {"reviews": [verdict("merge", tip)], "listed": tip}
    push_more(trusting, 1)
    code, lines = tick(capsys)
    assert code == 1 and lines[1].startswith("STOP integrate #1 harness-review") and merges(trusting) == []


@pytest.mark.parametrize("word", ["send back", "reconsider"])
def test_a_trusted_verdict_that_is_not_merge_integrates_nothing(trusting, capsys, word):
    tip = open_pr(trusting, 1, {"devkit/x.py": "x = 1\n"})
    trusting["prs"][1]["reviews"] = [verdict(word, tip)]
    code, lines = tick(capsys)
    assert code == 0 and lines[1].startswith(f"resume #1 (harness): verdict {word}")
    assert merges(trusting) == [] and closed(trusting) == [] and sent_back(trusting) == []


def test_a_merge_verdict_on_an_earlier_head_integrates_nothing(trusting, capsys):
    tip = open_pr(trusting, 1, {"devkit/x.py": "x = 1\n"})
    trusting["prs"][1]["reviews"] = [verdict("merge", tip)]
    push_more(trusting, 1)
    _, lines = tick(capsys)
    assert lines[1].startswith(f"review #1 (harness): verdict merge on {tip[:7]}") and lines[1].endswith("re-review")
    assert merges(trusting) == [] and closed(trusting) == []


def test_a_merge_verdict_by_another_login_integrates_nothing(trusting, capsys):
    tip = open_pr(trusting, 1, {"devkit/x.py": "x = 1\n"})
    trusting["prs"][1]["reviews"] = [verdict("merge", tip, login="stranger")]
    _, lines = tick(capsys)
    assert "no verdict" in lines[1] and merges(trusting) == []


@pytest.mark.parametrize("path", ["devkit/x.py", ".claude/settings.json", ".github/workflows/ci.yml", "CLAUDE.md"])
def test_a_merge_verdict_naming_no_reviewer_integrates_nothing(trusting, capsys, path):
    """Round 1 Blocking: every agent posts as the gh user, so only a verdict naming the trusted reviewer counts, on
    every harness path (required_reviews covers only some)."""
    tip = open_pr(trusting, 1, {path: "x\n"})
    trusting["prs"][1]["reviews"] = [verdict("merge", tip, by=""), verdict("merge", tip, by="harness-ops:builder")]
    _, lines = tick(capsys)
    assert lines[1] == "review #1 (harness): no verdict naming harness-ops:reviewer; spawn harness-ops:reviewer"
    assert merges(trusting) == [] and closed(trusting) == []


@pytest.mark.parametrize("path", [".claude/settings.json", ".github/workflows/ci.yml", "CLAUDE.md"])
def test_a_named_merge_verdict_clears_any_harness_path(trusting, capsys, path):
    tip = open_pr(trusting, 1, {path: "x\n"})
    trusting["prs"][1]["reviews"] = [verdict("merge", tip)]
    code, lines = tick(capsys)
    assert code == 0 and lines[1] == "merged #1: #101 -> done"


def test_an_empty_trust_key_trusts_no_reviewer(pilot, capsys, monkeypatch):
    monkeypatch.setattr(config, "get", lambda: config.Config(autopilot_trust_review=""))
    tip = open_pr(pilot, 1, {"devkit/x.py": "x = 1\n"})
    pilot["prs"][1]["reviews"] = [verdict("merge", tip)]
    _, lines = tick(capsys)
    assert lines[1].startswith("lead #1 (harness): verdict merge;") and merges(pilot) == []


def test_a_trusted_harness_pr_still_needs_its_required_review(trusting, capsys, monkeypatch):
    cfg = config.Config(
        autopilot_trust_review="harness-ops:reviewer", required_reviews={"devkit/": "security-reviewer"}
    )
    monkeypatch.setattr(config, "get", lambda: cfg)
    tip = open_pr(trusting, 1, {"devkit/x.py": "x = 1\n"})
    trusting["prs"][1]["reviews"] = [verdict("merge", tip)]
    _, lines = tick(capsys)
    assert lines[1].startswith("review #1: devkit/x.py needs a review by security-reviewer") and merges(trusting) == []


def test_trusted_harness_prs_train_with_each_cleared_at_its_own_head(trusting, capsys):
    for n in (1, 2):
        tip = open_pr(trusting, n, {f"devkit/x{n}.py": "x = 1\n"})
        trusting["prs"][n]["reviews"] = [verdict("merge", tip)]
    code, lines = tick(capsys)
    assert code == 0 and lines[1:3] == ["merged #1: #101 -> done", "merged #2: #102 -> done"]
    assert trusting["gates"] == [[]] and closed(trusting) == [1, 2]


def test_a_head_cleared_by_sha_is_the_only_one_cleared(world, capsys):  # noqa: F811 - the imported fixture
    """A push between autopilot's read and integrate's own: the SHA no longer matches, so it is harness-review."""
    old = open_pr(world, 1, {"devkit/x.py": "x = 1\n"})
    push_more(world, 1)
    assert workflow.integrate(1, old, dry_run=False) == 2
    assert '"outcome": "harness-review"' in capsys.readouterr().out and merges(world) == []
    assert not train.harness_cleared(1, {1: old}, "f" * 40) and train.harness_cleared(1, {1: old}, old)
    assert not train.harness_cleared(1, {1: old}, "")


def test_a_failed_subprocess_in_the_post_merge_conflict_check_does_not_crash_a_train(world, capsys, monkeypatch):  # noqa: F811
    def boom(n):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

    monkeypatch.setattr(workflow, "send_back_conflicting", boom)
    for n in (1, 2):
        open_pr(world, n, note(world["clone"], f"n{n}"))
    assert train.train([1, 2], harness_ok=True, dry_run=False) == 0
    assert "conflict check of the new main skipped" in capsys.readouterr().out and closed(world) == [1, 2]
