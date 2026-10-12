"""PR feedback reaches agents: new comments and reviews since the last commit, and every unresolved review thread,
show in `board feedback <pr>` and in the brief of an issue that has an open PR. No test calls GitHub."""

import json

from devkit import board
from tests.test_board import mk
from tests.test_gate import _graphql, _thread

COMMIT, T1 = "2026-10-11T12:00:00Z", "2026-10-11T13:00:00Z"


def _comment(body, at, who="ann"):
    return {"author": {"login": who}, "body": body, "createdAt": at, "url": f"https://x/{at}"}


def _review(state, body, at, who="rev"):
    return {"author": {"login": who}, "body": body, "state": state, "submittedAt": at, "url": "https://x/r"}


def _fake(monkeypatch, data, prs=()):
    calls = []

    def gh(*args, input=None):
        calls.append(args)
        if args[:2] == ("api", "graphql"):
            return json.dumps(data)
        if args[:2] == ("pr", "list"):
            return json.dumps(list(prs))
        return ""

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    return calls


def test_feedback_shows_only_what_is_new_since_the_last_commit_plus_unresolved_threads(monkeypatch):
    data = _graphql(
        committed=COMMIT,
        comments=[_comment("old, already handled", "2026-10-11T11:00:00Z"), _comment("new:\nplease rename", T1)],
        reviews=[
            _review("CHANGES_REQUESTED", "stale review", "2026-10-11T10:00:00Z"),
            _review("COMMENTED", "", T1),  # the empty container of inline comments: the threads carry them
            _review("CHANGES_REQUESTED", "tests missing", T1),
        ],
        threads=[_thread("devkit/a.py", 4, who="coderabbit"), _thread("devkit/b.py", 1, resolved=True)],
    )
    calls = _fake(monkeypatch, data)
    lines = board.pr_feedback_lines(board.pr_feedback(7))
    text = "\n".join(lines)
    assert "new: please rename" in text and "old, already handled" not in text
    assert "tests missing" in text and "stale review" not in text
    assert sum("review" in ln and "COMMENTED" in ln for ln in lines) == 0
    assert "devkit/a.py:4" in text and "coderabbit" in text and "devkit/b.py" not in text
    q = next(c for c in calls if c[:2] == ("api", "graphql"))
    assert "n=7" in q and "owner=o" in q and "name=r" in q


def test_feedback_tolerates_deleted_authors_and_no_feedback(monkeypatch):
    c = _comment("ghost says hi", T1)
    c["author"] = None
    _fake(monkeypatch, _graphql(committed=COMMIT, comments=[c]))
    assert "ghost says hi" in "\n".join(board.pr_feedback_lines(board.pr_feedback(7)))
    _fake(monkeypatch, _graphql(committed=COMMIT))
    assert board.pr_feedback_lines(board.pr_feedback(7)) == []


def test_issue_prs_finds_open_prs_by_branch_or_closing_keyword(monkeypatch):
    prs = [
        {"number": 50, "headRefName": "39-pr-feedback", "body": "Refs #39"},
        {"number": 51, "headRefName": "other", "body": "Closes #39"},
        {"number": 52, "headRefName": "390-x", "body": "Refs #39"},  # a mention links nothing
    ]
    _fake(monkeypatch, {}, prs)
    assert board.issue_prs(39) == [50, 51]


def test_a_resumed_worker_gets_its_pr_feedback_in_the_brief(monkeypatch):
    monkeypatch.setattr(board, "get", lambda n: mk(n, ["status:in-progress"], "## DONE WHEN\n- [ ] a\n"))
    monkeypatch.setattr(board, "in_flight_files", lambda: {})
    _fake(
        monkeypatch, _graphql(committed=COMMIT, threads=[_thread("x.py", 2)]), [{"number": 50, "headRefName": "39-a"}]
    )
    out = board.brief(39)
    assert "## PR #50 feedback" in out and "x.py:2" in out


def test_brief_without_an_open_pr_or_with_github_down_has_no_feedback_section(monkeypatch):
    monkeypatch.setattr(board, "get", lambda n: mk(n, ["status:ready"], "## DONE WHEN\n- [ ] a\n"))
    monkeypatch.setattr(board, "in_flight_files", lambda: {})
    _fake(monkeypatch, {}, [])
    assert "feedback" not in board.brief(39)

    def down(*a, **k):
        raise board.BoardError("offline")

    monkeypatch.setattr(board, "gh", down)
    assert "feedback" not in board.brief(39)


def test_feedback_past_the_first_page_of_threads_is_flagged_not_dropped(monkeypatch):
    calls = _fake(monkeypatch, _graphql(committed=COMMIT, threads=[_thread("x.py", 2)], more_threads=True))
    fb = board.pr_feedback(7)
    assert fb["threads_truncated"]
    assert "more than 100 review threads" in "\n".join(board.pr_feedback_lines(fb))
    assert "hasNextPage" in next(c for c in calls if c[:2] == ("api", "graphql"))[-1]
    _fake(monkeypatch, _graphql(committed=COMMIT))
    assert not board.pr_feedback(7)["threads_truncated"]
