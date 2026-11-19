"""board.gh retries transient network errors (#538) without ever repeating a write that may have landed.

GitHub is faked at subprocess level (`FakeCli`), so the real gh() wrapper, its retry loops and its rechecks run.
"""

import json
import random
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, workflow
from tests.test_gate import _last_json, fake_integrate  # noqa: F401 - fake_integrate is a fixture

REAL_GH = board.gh  # fake_integrate replaces board.gh; the end-to-end test puts the real one back
TLS = "Post https://api.github.com/graphql: net/http: TLS handshake timeout"
T0 = 1_800_000_000.0  # the injected clock: 2027-01-15T08:00:00Z
STAMP = "2027-01-15T08:00:05Z"


class FakeCli:
    """The `gh` CLI over a small GitHub: issues with comments, PRs and labels. `drop[i]` = (kind, stderr) fails the
    i-th call (0-based) as a dropped connection: kind "before" fails before GitHub acts, "after" once it has."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.drop: dict[int, tuple[str, str]] = {}
        self.comments: dict[int, list[dict]] = {}
        self.issues: list[dict] = []  # created issues, newest last
        self.prs: list[dict] = []
        self.state: dict[int, str] = {}  # issue or PR number -> OPEN / CLOSED / MERGED
        self.labels: dict[int, set[str]] = {}
        self.next_id = 100

    def run(self, argv, input=None, **_):
        assert argv[0] == "gh"
        args = list(argv[1:])
        i = len(self.calls)
        self.calls.append(args)
        how, err = self.drop.get(i, ("", ""))
        if how == "before":
            return subprocess.CompletedProcess(argv, 1, "", err)
        out = self.answer(args, input)
        if how == "after":
            return subprocess.CompletedProcess(argv, 1, "", err)
        return subprocess.CompletedProcess(argv, 0, out, "")

    def answer(self, a: list[str], input: str | None) -> str:
        flag = lambda *names: next((a[i + 1] for i, x in enumerate(a) if x in names), None)  # noqa: E731
        body = input if flag("--body-file", "-F") == "-" else flag("--body", "-b")
        if a[:2] in (["issue", "comment"], ["pr", "comment"]):
            return self.comment(int(a[2]), body) + "\n"
        if a[:2] == ["issue", "create"]:
            self.next_id += 1
            url = f"https://github.com/o/r/issues/{self.next_id}"
            self.issues.append({"title": flag("--title"), "body": body, "html_url": url})
            return url + "\n"
        if a[:2] == ["pr", "create"]:
            self.next_id += 1
            url = f"https://github.com/o/r/pull/{self.next_id}"
            self.prs.append({"url": url, "createdAt": STAMP, "head": flag("--head")})
            return url + "\n"
        if a[:2] == ["pr", "list"]:
            return json.dumps([p for p in self.prs if p["head"] == flag("--head")])
        if a[:2] in (["issue", "close"], ["pr", "close"]):
            if (note := flag("--comment", "-c")) is not None:
                self.comment(int(a[2]), note)
            self.state[int(a[2])] = "CLOSED"
            return ""
        if a[:2] == ["pr", "merge"]:
            self.state[int(a[2])] = "MERGED"
            return ""
        if a[:2] in (["issue", "view"], ["pr", "view"]):
            n = int(a[2])
            d = {"number": n, "title": f"t{n}", "body": "", "state": self.state.get(n, "OPEN"), "updatedAt": STAMP}
            d |= {
                "labels": [{"name": x} for x in sorted(self.labels.get(n, set()))],
                "comments": self.comments.get(n, []),
            }
            return json.dumps(d | {"blockedBy": None, "parent": None, "blocking": None, "subIssues": None})
        if a[:2] == ["issue", "edit"]:
            got = self.labels.setdefault(int(a[2]), set())
            for i, x in enumerate(a):
                if x == "--add-label":
                    got |= set(a[i + 1].split(","))
                if x == "--remove-label":
                    got -= set(a[i + 1].split(","))
            return ""
        if a[:2] == ["label", "list"]:
            return json.dumps([{"name": x} for x in board.LABELS])
        if a[0] == "api" and "/comments?" in a[1]:
            n = int(a[1].split("/issues/")[1].split("/")[0])
            return json.dumps([{"body": c["body"], "html_url": c["url"]} for c in self.comments.get(n, [])])
        if a[0] == "api" and "/issues?" in a[1]:
            return json.dumps(list(reversed(self.issues)))
        return ""

    def comment(self, n: int, body: str | None) -> str:
        self.next_id += 1
        url = f"https://github.com/o/r/issues/{n}#issuecomment-{self.next_id}"
        self.comments.setdefault(n, []).append({"body": body, "createdAt": STAMP, "url": url})
        return url

    def sent(self, *head: str) -> int:
        return sum(c[: len(head)] == list(head) for c in self.calls)


@pytest.fixture
def cli(monkeypatch):
    f = FakeCli()
    slept: list[float] = []
    monkeypatch.setattr(board.subprocess, "run", f.run)
    monkeypatch.setattr(board, "root", lambda: Path("."))
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "GH_SLEEP", slept.append)
    monkeypatch.setattr(board, "GH_RNG", random.Random(7))
    monkeypatch.setattr(board, "GH_CLOCK", lambda: T0)
    f.slept = slept
    return f


ERRORS = [
    TLS,
    "read tcp 10.0.0.2:5555->140.82.112.6:443: read: connection reset by peer",
    "Get https://api.github.com/repos/o/r: dial tcp 140.82.112.6:443: i/o timeout",
    "Post https://api.github.com/graphql: unexpected EOF",
    "HTTP 502: Bad Gateway (https://api.github.com/graphql)",
    "HTTP 503: Service Unavailable (https://api.github.com/repos/o/r/issues)",
    "HTTP 504: Gateway Timeout (https://api.github.com/graphql)",
]


@pytest.mark.parametrize("err", ERRORS)
def test_a_read_is_retried_after_a_transient_error_then_succeeds(cli, err, capsys):
    cli.drop = {0: ("before", err)}
    assert json.loads(board.gh("issue", "view", "3", "-R", "o/r", "--json", "state"))["state"] == "OPEN"
    assert len(cli.calls) == 2 and len(cli.slept) == 1
    assert board.GH_NET_FLOOR <= cli.slept[0] <= board.GH_NET_FLOOR + board.GH_NET_BASE
    assert "network error" in capsys.readouterr().err


def test_transient_retries_are_bounded(cli):
    cli.drop = {i: ("before", TLS) for i in range(50)}
    with pytest.raises(board.BoardError, match="TLS handshake timeout"):
        board.gh("pr", "list", "-R", "o/r")
    assert len(cli.calls) == board.GH_NET_TRIES
    assert len(cli.slept) == board.GH_NET_TRIES - 1


@pytest.mark.parametrize("err", ["HTTP 404: Not Found", "HTTP 500: Internal Server Error", "GraphQL: Bad credentials"])
def test_other_errors_are_not_retried(cli, err):
    cli.drop = {0: ("before", err)}
    with pytest.raises(board.BoardError):
        board.gh("issue", "view", "3", "-R", "o/r")
    assert len(cli.calls) == 1 and cli.slept == []


def test_an_idempotent_write_is_retried_as_is(cli):
    cli.drop = {0: ("after", "HTTP 502: Bad Gateway")}
    board.gh("issue", "edit", "4", "-R", "o/r", "--add-label", "x")
    assert cli.sent("issue", "edit") == 2 and cli.labels[4] == {"x"}


@pytest.mark.parametrize(
    "args",
    [
        ("api", "graphql", "-f", "query=mutation($a: ID!) { addBlockedBy(input: {}) { clientMutationId } }"),
        ("api", "-X", "POST", "repos/o/r/issues/1/labels", "-f", "labels[]=x"),
        ("issue", "comment", "4", "--body", "hi"),  # no -R: no recheck can find it
    ],
)
def test_a_write_without_a_recheck_is_not_retried_and_says_its_outcome_is_unknown(cli, args):
    cli.drop = {0: ("after", TLS)}
    with pytest.raises(board.BoardError, match="outcome is unknown"):
        board.gh(*args)
    assert len(cli.calls) == 1


@pytest.mark.parametrize("how", ["before", "after"])
def test_a_comment_is_posted_once_whether_or_not_the_dropped_try_landed(cli, how):
    cli.drop = {0: (how, "read: connection reset by peer")}
    url = board.gh("issue", "comment", "4", "-R", "o/r", "--body-file", "-", input="hello\n")
    assert [c["body"] for c in cli.comments[4]] == ["hello\n"]
    assert url == cli.comments[4][0]["url"] + "\n"
    assert cli.sent("issue", "comment") == (1 if how == "after" else 2)


def test_a_pr_comment_by_body_flag_is_posted_once(cli):
    cli.drop = {0: ("after", "unexpected EOF")}
    board.gh("pr", "comment", "9", "-R", "o/r", "--body", "CI ran locally")
    assert len(cli.comments[9]) == 1 and cli.sent("pr", "comment") == 1


def test_an_identical_older_comment_is_not_taken_for_the_new_one(cli, monkeypatch):
    """The recheck asks only for comments since the call began: an old twin outside that window is not returned."""
    cli.comment(4, "Merged in PR #5.")
    real = cli.answer

    def windowed(a, input):
        if a[0] == "api" and "/comments?" in a[1]:
            assert f"since={board._iso(T0 - board.GH_SKEW)}" in a[1]
            return "[]"  # GitHub filters the old twin out by `since`
        return real(a, input)

    monkeypatch.setattr(cli, "answer", windowed)
    cli.drop = {0: ("before", TLS)}
    board.gh("issue", "comment", "4", "-R", "o/r", "--body", "Merged in PR #5.")
    assert len(cli.comments[4]) == 2 and cli.sent("issue", "comment") == 2


@pytest.mark.parametrize("how", ["before", "after"])
def test_an_issue_is_created_once(cli, how):
    cli.drop = {0: (how, TLS)}
    url = board.gh("issue", "create", "-R", "o/r", "--title", "T", "--body-file", "-", "--label", "P1", input="B")
    assert len(cli.issues) == 1 and url == cli.issues[0]["html_url"] + "\n"


def test_an_issue_with_the_same_title_but_another_body_is_not_taken_for_it(cli):
    cli.issues.append({"title": "T", "body": "older", "html_url": "https://github.com/o/r/issues/1"})
    cli.drop = {0: ("before", TLS)}
    url = board.gh("issue", "create", "-R", "o/r", "--title", "T", "--body-file", "-", input="new")
    assert len(cli.issues) == 2 and url.endswith("/issues/101\n")


@pytest.mark.parametrize("how", ["before", "after"])
def test_a_pr_is_opened_once(cli, how):
    cli.drop = {0: (how, "HTTP 502: Bad Gateway")}
    args = ("pr", "create", "-R", "o/r", "--base", "main", "--head", "5-x", "--title", "t", "--body-file", "-")
    url = board.gh(*args, input="Closes #5")
    assert len(cli.prs) == 1 and url == cli.prs[0]["url"] + "\n"


@pytest.mark.parametrize("how", ["before", "after"])
def test_a_merge_is_sent_again_only_when_the_pr_is_not_merged(cli, how):
    cli.drop = {0: (how, TLS)}
    board.gh("pr", "merge", "7", "-R", "o/r", "--squash", "--match-head-commit", "abc")
    assert cli.state[7] == "MERGED"
    assert cli.sent("pr", "merge") == (1 if how == "after" else 2)


def test_a_close_whose_comment_landed_is_sent_again_without_the_comment(cli, monkeypatch):
    real = cli.answer

    def comment_then_drop(a, input):
        if a[:2] == ["issue", "close"] and "--comment" in a:
            cli.comment(int(a[2]), a[a.index("--comment") + 1])
            raise ConnectionError
        return real(a, input)

    def run(argv, input=None, **kw):
        try:
            return FakeCli.run(cli, argv, input=input, **kw)
        except ConnectionError:
            return subprocess.CompletedProcess(argv, 1, "", "read: connection reset by peer")

    monkeypatch.setattr(cli, "answer", comment_then_drop)
    monkeypatch.setattr(board.subprocess, "run", run)
    board.gh("issue", "close", "6", "-R", "o/r", "--reason", "completed", "--comment", "Merged in PR #8.")
    assert [c["body"] for c in cli.comments[6]] == ["Merged in PR #8."] and cli.state[6] == "CLOSED"
    assert cli.calls[-1] == ["issue", "close", "6", "-R", "o/r", "--reason", "completed"]


def test_a_failed_recheck_stops_with_the_outcome_unknown(cli):
    cli.drop = {i: ("after" if i == 0 else "before", TLS) for i in range(50)}
    with pytest.raises(board.BoardError, match="outcome is unknown"):
        board.gh("pr", "merge", "7", "-R", "o/r", "--squash")
    assert cli.sent("pr", "merge") == 1


def test_a_rate_limit_after_a_dropped_write_still_does_not_duplicate(cli):
    cli.drop = {0: ("before", TLS), 2: ("before", "HTTP 403: You have exceeded a secondary rate limit.")}
    board.gh("issue", "comment", "4", "-R", "o/r", "--body", "x")
    assert len(cli.comments[4]) == 1


# ---- the classification every call site relies on ----

KINDS = [
    (("issue", "list", "-R", "o/r", "--json", "number"), "read"),
    (("pr", "view", "7", "-R", "o/r", "--json", "headRefOid"), "read"),
    (("run", "download", "1", "-R", "o/r", "-n", "x", "-D", "dir"), "read"),
    (("api", "repos/o/r/compare/a...b", "--jq", ".commits[].sha"), "read"),
    (("api", "--paginate", "repos/o/r/pulls/3/files", "--jq", ".[].filename"), "read"),
    (("api", "graphql", "-f", "owner=o", "-F", "n=3", "-f", "query=query($n: Int!) { x }"), "read"),
    (("api", "graphql", "-f", "query=\n  mutation($a: ID!) { addSubIssue }"), "write"),
    (("api", "-X", "POST", "repos/o/r/statuses/abc123", "-f", "state=success"), "idempotent"),
    (("api", "repos/o/r/issues/1/labels", "-f", "labels[]=x"), "write"),  # fields make gh POST
    (("api", "-X", "DELETE", "repos/o/r/git/refs/heads/x"), "idempotent"),
    (("issue", "edit", "3", "-R", "o/r", "--body-file", "-"), "idempotent"),
    (("pr", "edit", "3", "-R", "o/r", "--base", "main"), "idempotent"),
    (("label", "create", "x", "-R", "o/r", "--color", "fff", "--force"), "idempotent"),
    (("label", "create", "x", "-R", "o/r"), "write"),
    (("issue", "close", "3", "-R", "o/r", "--reason", "not planned"), "idempotent"),
    (("issue", "close", "3", "-R", "o/r", "--comment", "x"), "write"),
    (("pr", "close", "3", "-R", "o/r", "--comment=x"), "write"),
    (("issue", "create", "-R", "o/r"), "write"),
    (("issue", "comment", "3", "-R", "o/r"), "write"),
    (("pr", "merge", "3", "-R", "o/r"), "write"),
    (("pr", "create", "-R", "o/r"), "write"),
]


@pytest.mark.parametrize(("args", "kind"), KINDS)
def test_each_call_shape_has_its_kind(args, kind):
    assert board.gh_kind(args) == kind


KNOWN = {*board.GH_READS, *board.GH_IDEMPOTENT}


@given(st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=8), min_size=1, max_size=6))
def test_an_unknown_command_counts_as_a_write(args):
    """Fail safe: only commands known to read, or to set state, may be repeated blind."""
    head = tuple(args[:2])
    if head in KNOWN or args[0] in ("api", "search", "label", "issue", "pr"):
        return
    assert board.gh_kind(tuple(args)) == "write"


# ---- end to end: one injected TLS timeout ----


def test_board_claim_survives_a_tls_timeout_on_a_read_and_on_its_claim_comment(cli, monkeypatch):
    monkeypatch.setattr(board, "get", board.get)  # the real one, through the fake CLI
    monkeypatch.setattr(board.time, "sleep", lambda s: None)
    board.repo_labels.cache_clear()
    cli.drop = {0: ("before", TLS), 2: ("after", TLS)}  # the first issue read; the claim comment (it landed)
    try:
        assert board.claim(12, "builder", "12-x").startswith("#12 claimed by builder")
    finally:
        board.repo_labels.cache_clear()
    claims = [c for c in cli.comments[12] if "devkit:claim" in c["body"]]
    assert len(claims) == 1 and cli.sent("issue", "comment") == 1
    assert "status:in-progress" in cli.labels[12]


def test_integrate_survives_a_tls_timeout_on_a_read_and_on_its_merge(fake_integrate, monkeypatch, capsys):  # noqa: F811
    world_gh, other_run = board.gh, workflow.subprocess.run
    merged = {"done": False, "merges": 0, "views": 0}

    def run(cmd, input=None, **kw):
        if cmd[0] != "gh":
            return other_run(cmd, input=input, **kw)
        args = tuple(cmd[1:])
        if args[:2] == ("pr", "view") and args[-1] == "state":
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps({"state": "MERGED" if merged["done"] else "OPEN"}), ""
            )
        if args[:2] == ("pr", "view"):
            merged["views"] += 1
            if merged["views"] == 1:
                return subprocess.CompletedProcess(cmd, 1, "", TLS)  # the first PR read never reached GitHub
        out = world_gh(*args, input=input)
        if args[:2] == ("pr", "merge"):
            merged["merges"] += 1
            merged["done"] = True
            return subprocess.CompletedProcess(cmd, 1, "", TLS)  # merged, but the answer was lost
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(board, "gh", REAL_GH)
    monkeypatch.setattr(board, "root", lambda: Path("."))
    monkeypatch.setattr(board, "GH_SLEEP", lambda s: None)
    monkeypatch.setattr(workflow.subprocess, "run", run)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert merged["merges"] == 1
    assert _last_json(capsys.readouterr().out)["outcome"] == "merged"
