"""Board robustness: claim races, blockers, Owns matching, pagination, label checks and output caps.

No test calls GitHub: `board.gh`, `board.get` and `board.repo` are replaced by an in-memory fake.
"""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, workflow

T0 = "2026-10-11T00:00:00Z"


def mk(n, labels, body="", comments=None, title=None):
    return board.Issue(n, title or f"t{n}", labels, body, "OPEN", T0, comments or [])


class FakeGitHub:
    """One issue's comment thread plus a label set, behind the `gh` argv the board sends."""

    def __init__(self, labels=None):
        self.comments: list[dict] = []
        self.labels = set(board.LABELS) if labels is None else set(labels)
        self.calls: list[tuple] = []
        self.next_id = 1000

    def gh(self, *args, input=None):
        self.calls.append(args)
        if args[:2] == ("issue", "comment"):
            self.next_id += 1
            url = f"https://github.com/o/r/issues/{args[2]}#issuecomment-{self.next_id}"
            self.comments.append({"body": input, "createdAt": f"2026-10-11T01:00:{self.next_id % 60:02d}Z", "url": url})
            return url + "\n"
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in sorted(self.labels)])
        return ""

    def issue(self, number):
        return mk(number, ["status:ready"], comments=list(self.comments))


@pytest.fixture
def fake(monkeypatch):
    f = FakeGitHub()
    monkeypatch.setattr(board, "gh", f.gh)
    monkeypatch.setattr(board, "get", f.issue)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board.time, "sleep", lambda s: None)
    board.repo_labels.cache_clear()
    yield f
    board.repo_labels.cache_clear()


def _claim(host, agent, cid, token=None, at="2026-10-11T01:00:00Z"):
    tok = f" id={token}" if token else ""
    return {
        "body": f"<!-- devkit:claim host={host} agent={agent} branch=1-x{tok} -->",
        "createdAt": at,
        "url": f"https://github.com/o/r/issues/1#issuecomment-{cid}",
    }


def _release(cid=None):
    tag = f" claim={cid}" if cid else ""
    return {"body": f"<!-- devkit:release{tag} -->\nwithdrawn", "createdAt": "2026-10-11T02:00:00Z"}


# --- S2-1: a release names the claim it withdraws -------------------------------------------------------------


def test_loser_withdrawal_keeps_winner_claim():
    a, b = _claim("h1", "a", 11, "ta"), _claim("h2", "b", 12, "tb")
    held = mk(1, [], comments=[a, b, _release(12)]).claim()
    assert held is not None and (held.agent, held.cid) == ("a", "11")


def test_release_naming_the_live_claim_clears_it_and_bare_release_clears_all():
    a, b = _claim("h1", "a", 11, "ta"), _claim("h2", "b", 12, "tb")
    assert mk(1, [], comments=[a, _release(11)]).claim() is None
    assert mk(1, [], comments=[a, b, _release()]).claim() is None
    assert mk(1, [], comments=[a, _release(11), b]).claim().agent == "b"


def test_race_loser_withdraws_only_itself_and_a_third_claimer_is_refused(fake, monkeypatch):
    """Two processes with the same agent name on one host race; the second's withdrawal leaves the first holding."""
    real_get = fake.issue
    snapshots = iter([mk(21, ["status:ready"]), mk(21, ["status:ready"])])  # both read before either posts

    def get(n):
        return next(snapshots, None) or real_get(n)

    monkeypatch.setattr(board, "get", get)
    results = {}

    def sleep_then_race(_s):
        monkeypatch.setattr(board.time, "sleep", lambda s: None)
        monkeypatch.setattr(board, "IDENTITY", "pid2-bbbb")
        with pytest.raises(board.BoardError, match="lost the claim race"):
            board.claim(21, "builder", "21-x")
        results["second"] = "lost"
        monkeypatch.setattr(board, "IDENTITY", "pid1-aaaa")

    monkeypatch.setattr(board, "IDENTITY", "pid1-aaaa")
    monkeypatch.setattr(board.time, "sleep", sleep_then_race)
    assert "claimed by builder" in board.claim(21, "builder", "21-x")
    assert results == {"second": "lost"}

    held = real_get(21).claim()
    assert held is not None and held.token == "pid1-aaaa"
    withdraw = [c["body"] for c in fake.comments if "Withdrawn" in c["body"]]
    assert len(withdraw) == 1 and "claim=" in withdraw[0]

    monkeypatch.setattr(board, "IDENTITY", "pid3-cccc")
    with pytest.raises(board.BoardError, match="is claimed by builder"):
        board.claim(21, "other", "21-y")


def test_identity_is_a_random_token_per_process():
    assert board.IDENTITY and " " not in board.IDENTITY
    other = board.new_identity()
    assert other != board.IDENTITY


def test_claim_comment_carries_identity(fake, monkeypatch):
    monkeypatch.setattr(board, "IDENTITY", "pid9-zzzz")
    board.claim(5, "a", "5-x")
    assert "id=pid9-zzzz" in fake.comments[0]["body"]
    assert board.get(5).claim().token == "pid9-zzzz"


def test_release_names_the_live_claim(fake):
    board.claim(5, "a", "5-x")
    board.release(5, "done for now")
    assert "claim=" in fake.comments[-1]["body"]
    assert board.get(5).claim() is None


# --- S2-2: blockers, Owns, limits, labels -----------------------------------------------------------------------


def test_unknown_blockers_count_as_open():
    i = mk(1, ["status:ready", "P1"], "## Blocked by\n#999")
    assert board.startable([i]) == []  # #999 is not a known closed issue: a typo, a PR, or outside the fetch
    assert [x.number for x in board.startable([i], done={999})] == [1]
    assert "waits #999" in board._line(i, set())
    assert "waits" not in board._line(i, {999})


def test_sync_blocks_on_unknown_and_unblocks_on_closed(monkeypatch):
    issues = [mk(1, ["status:ready"], "## Blocked by\n#999"), mk(2, ["status:blocked"], "## Blocked by\n#7")]
    moved = []
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    monkeypatch.setattr(board, "get", lambda n: issues[n - 1])
    monkeypatch.setattr(board, "closed_numbers", lambda: {7})
    monkeypatch.setattr(board, "set_status", lambda n, s: moved.append((n, s)))
    assert board.sync() == ["#1 -> blocked", "#2 -> ready"]
    assert moved == [(1, "blocked"), (2, "ready")]


def test_new_counts_unknown_blocker_as_open(fake, monkeypatch):
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    assert board.new("t", "build", "P2", "", ["x"], [], [12345], [], None).endswith("(blocked)")
    monkeypatch.setattr(board, "closed_numbers", lambda: {12345})
    assert board.new("t", "build", "P2", "", ["x"], [], [12345], [], None).endswith("(ready)")


@pytest.mark.parametrize(
    ("path", "owns", "expected"),
    [
        ("devkit/board.py", ["devkit/board.py"], True),
        ("devkit/board.py", ["devkit/"], True),
        ("devkit/board.py", ["devkit"], True),
        ("devkitx/board.py", ["devkit"], False),
        ("devkit/board.py", ["devkit/*.py"], True),
        ("docs/research/a/b.md", ["docs/research/**"], True),
        ("tests/test_board.py", ["tests/test_*.py"], True),
        ("tests/conftest.py", ["tests/test_*.py"], False),
        ("devkit/board.py", [], False),
    ],
)
def test_owns_match_globs_and_prefixes(path, owns, expected):
    assert board.owned(path, owns) is expected


@given(st.from_regex(r"[a-z]{1,8}(/[a-z]{1,8}){0,3}", fullmatch=True), st.from_regex(r"[a-z]{1,8}\.py", fullmatch=True))
def test_owns_prefix_covers_everything_below(prefix, name):
    assert board.owned(f"{prefix}/{name}", [prefix + "/"])
    assert board.owned(f"{prefix}/{name}", [prefix])
    assert board.owned(f"{prefix}/{name}", [f"{prefix}/*"])


def test_set_status_names_missing_labels_and_edits_nothing(fake):
    fake.labels -= {"status:review", "status:blocked"}
    with pytest.raises(board.BoardError, match=r"status:review.*board init|status:blocked.*board init"):
        board.set_status(3, "review")
    msg = ""
    try:
        board.set_status(3, "ready")
    except board.BoardError as exc:
        msg = str(exc)
    assert "status:review" in msg and "status:blocked" in msg and "uv run dev board init" in msg
    assert not [c for c in fake.calls if c[:2] == ("issue", "edit")]


def test_set_status_with_all_labels_edits(fake):
    board.set_status(3, "review")
    edit = next(c for c in fake.calls if c[:2] == ("issue", "edit"))
    assert "status:review" in edit and "status:ready" in edit


def test_fetch_and_closed_pass_explicit_limits(monkeypatch):
    seen = []
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "gh", lambda *a, input=None: seen.append(a) or "[]")
    board.fetch()
    board.closed_numbers()
    for call in seen:
        assert "--limit" in call and int(call[call.index("--limit") + 1]) >= 1000


def test_in_flight_paginates_pr_list_and_files(monkeypatch):
    calls = []
    big = [{"path": f"f{k}.py"} for k in range(100)]  # gh pr list returns at most 100 files per PR

    def gh(*args, input=None):
        calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": 7, "headRefName": "7-x", "files": big}])
        if args[0] == "api":
            assert "--paginate" in args and "repos/o/r/pulls/7/files" in args
            return "\n".join(f"f{k}.py" for k in range(250)) + "\n"
        raise AssertionError(args)

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "git", lambda *a, **k: "")
    files = board.in_flight_files()
    assert len(files) == 250 and files["f249.py"] == ["PR #7"]
    pr_list = next(c for c in calls if c[:2] == ("pr", "list"))
    assert "--limit" in pr_list


# --- S3: caps -------------------------------------------------------------------------------------------------


def test_brief_caps_each_section_at_40_lines(monkeypatch):
    lines = "\n".join(f"line {k}" for k in range(100))
    body = f"## Context\n{lines}\n## DONE WHEN\n- [ ] a\n## Owns\n- `devkit/`\n"
    monkeypatch.setattr(board, "get", lambda n: mk(n, ["status:ready"], body))
    monkeypatch.setattr(board, "in_flight_files", lambda: {f"devkit/f{k}.py": ["PR #1"] for k in range(90)})
    monkeypatch.setattr(board, "issue_prs", lambda n: [])
    out = board.brief(4)
    blocks = out.split("\n## ")
    for b in blocks[1:]:
        assert len(b.splitlines()) - 1 <= 40, b.splitlines()[0]
    assert "line 38" in out and "line 60" not in out
    assert out.count("more:") >= 2  # Context and the hotspot list


def test_orient_is_capped_at_70_lines(monkeypatch, tmp_path):
    issues = [mk(n, ["status:in-progress", "P1"]) for n in range(1, 81)] + [
        mk(n, ["status:ready", "P2"]) for n in range(81, 90)
    ]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    monkeypatch.setattr(workflow, "root", lambda: tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/HANDOFF.md").write_text("\n".join(f"h{k}" for k in range(50)))
    monkeypatch.setattr(workflow, "status", lambda: "\n".join(f"  wt{k}" for k in range(40)))
    out = workflow.orient()
    assert len(out.splitlines()) <= 70
    assert "more:" in out
    assert "## This machine" in out  # the cap trims sections, it does not drop the tail
    after = out.split("next startable:")[1].splitlines()
    assert sum(line.startswith("#8") for line in after) == 5  # 5 next-startable rows always kept
    assert "h0" in out  # the handoff gets the room that is left


def test_release_clears_every_live_claim(fake):
    fake.comments += [_claim("h1", "a", 11, "ta"), _claim("h2", "b", 12, "tb")]  # B never withdrew
    board.release(1, "back to ready")
    assert "claim=11,12" in fake.comments[-1]["body"]
    assert board.get(1).claim() is None


def test_force_takeover_over_two_leftover_claims_wins_first_try(fake, monkeypatch):
    fake.comments += [_claim("h1", "a", 11, "ta"), _claim("h2", "b", 12, "tb")]
    monkeypatch.setattr(board, "IDENTITY", "pid4-dddd")
    assert "claimed by c" in board.claim(1, "c", "1-z", force=True)
    assert not [c for c in fake.comments if "Withdrawn" in c["body"]]
    assert board.get(1).claim().token == "pid4-dddd"


def test_closed_as_not_planned_is_not_a_done_blocker(monkeypatch):
    rows = [{"number": 3, "stateReason": "COMPLETED"}, {"number": 4, "stateReason": "NOT_PLANNED"}]
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "gh", lambda *a, input=None: json.dumps(rows))
    assert board.closed_numbers() == {3}


def test_listing_json_names_claim_fields(monkeypatch):
    i = mk(1, ["status:in-progress"], comments=[_claim("h1", "a", 11, "ta")])
    monkeypatch.setattr(board, "fetch", lambda *a, **k: [i])
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    row = json.loads(board.listing(as_json=True))[0]
    assert row["claim"]["agent"] == "a" and row["claim"]["cid"] == "11"
