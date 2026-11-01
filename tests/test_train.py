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

from devkit import board, cli, paths, train, workflow
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
                    "statusCheckRollup": pr.get("checks", []),
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
    assert cli.run(cli.parse(["integrate", "--train", "3", "1", "2", "3", "--harness-ok"])) == 0
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


def test_harness_files_without_harness_ok_hold_the_member_and_send_nothing_back(world, capsys):
    open_pr(world, 1, {"devkit/x.py": "x = 1\n"})
    open_pr(world, 2, {"devkit/y.py": "y = 1\n"})
    assert train.train([1, 2], harness_ok=False, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert {v["outcome"] for v in res["members"].values()} == {"harness-review"}
    assert res["gates_run"] == [] and sent_back(world) == [] and closed(world) == []


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
    monkeypatch.setattr(workflow, "required_contexts", lambda: (["check"], []))
    monkeypatch.setattr(workflow, "actions_enabled", lambda: False)
    monkeypatch.setattr(train.config, "get", lambda: train.config.Config(local_ci={"check": []}))
    assert train.train([1, 2], harness_ok=True, dry_run=False, local_ci=True) == 0
    posts = [e for e in world["events"] if e[:4] == ("gh", "api", "-X", "POST")]
    heads = {out(world["origin"], "git", "rev-parse", f"refs/pull/{n}/head") for n in (1, 2)}
    assert {e[4].rsplit("/", 1)[1] for e in posts} == heads and all("context=check" in e for e in posts)


def test_the_cli_refuses_a_count_that_does_not_match(world, capsys):
    assert cli.run(cli.parse(["integrate", "--train", "3", "1", "2"])) == 2
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
