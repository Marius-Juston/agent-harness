"""Board robustness: claim races, blockers, Owns matching, pagination, label checks, output caps, native links.

No test calls GitHub: `board.gh`, `board.get` and `board.repo` are replaced by an in-memory fake.
"""

import graphlib
import json
import subprocess
from itertools import pairwise

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
        if args[:2] == ("issue", "create"):
            return "https://github.com/o/r/issues/50\n"
        if args[:2] == ("api", "graphql"):  # every issue exists, open, with no native links
            return json.dumps({"data": {"repository": {"issue": {"id": "I_1", "state": "OPEN", "blockedBy": None}}}})
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


# --- #64: native relationships (blocked-by, sub-issues) -----------------------------------------------------------


def url(key):
    return f"https://github.com/{key[0]}/issues/{key[1]}"


class NativeGitHub:
    """Issues in several repositories with GitHub's native links, behind the `gh` argv the board sends (issue
    list/view/edit/create, label list, and `gh api graphql` for lookups, addBlockedBy and addSubIssue)."""

    HERE = "o/r"

    def __init__(self):
        self.issues: dict[tuple[str, int], dict] = {}
        self.calls: list[tuple] = []
        self.mutations: list[tuple] = []
        self.old_gh = False
        self.refuse: set[str] = set()  # mutation names GitHub refuses (no access)

    def add(
        self, n, labels=("status:ready",), body="", state="OPEN", reason=None, where=HERE, blocked_by=(), parent=None
    ):
        self.issues[(where, n)] = {
            "number": n,
            "labels": list(labels),
            "body": body,
            "state": state,
            "reason": reason or ("COMPLETED" if state == "CLOSED" else None),
            "blocked_by": [self.key(b) for b in blocked_by],
            "parent": self.key(parent) if parent else None,
        }

    def key(self, ref):
        if isinstance(ref, int):
            return (self.HERE, ref)
        where, _, n = ref.rpartition("#")
        return (where or self.HERE, int(n))

    def nodes(self, keys):
        return {"nodes": [{"url": url(k)} for k in keys]}

    def json(self, key):
        d = self.issues[key]
        return {
            "number": key[1],
            "title": f"t{key[1]}",
            "labels": [{"name": x} for x in d["labels"]],
            "body": d["body"],
            "state": d["state"],
            "stateReason": d["reason"],
            "updatedAt": "2026-10-11T00:00:00Z",
            "comments": [],
            "blockedBy": self.nodes(d["blocked_by"]),
            "parent": {"url": url(d["parent"])} if d["parent"] else None,
            "blocking": self.nodes([k for k, x in self.issues.items() if key in x["blocked_by"]]),
            "subIssues": self.nodes([k for k, x in self.issues.items() if x["parent"] == key]),
        }

    def gh(self, *args, input=None):
        self.calls.append(args)
        flags = dict(pairwise(args))
        if self.old_gh and "blockedBy" in flags.get("--json", ""):
            raise board.BoardError(f'gh {args[0]} {args[1]}: Unknown JSON field: "blockedBy"')
        if args[:2] == ("issue", "list"):
            state = flags["--state"].upper()
            mine = [k for k, d in self.issues.items() if k[0] == self.HERE and d["state"] == state]
            rows = [self.json(k) for k in sorted(mine)]
            if self.old_gh:
                rows = [{k: v for k, v in r.items() if k not in ("blockedBy", "parent")} for r in rows]
            return json.dumps(rows)
        if args[:2] == ("issue", "view"):
            row = self.json((self.HERE, int(args[2])))
            return json.dumps({k: v for k, v in row.items() if k != "blockedBy"} if self.old_gh else row)
        if args[:2] == ("issue", "edit"):
            d = self.issues[(self.HERE, int(args[2]))]
            if "--body-file" in flags:
                d["body"] = input
            for flag, value in pairwise(args):
                if flag == "--add-label" and value not in d["labels"]:
                    d["labels"].append(value)
                if flag == "--remove-label" and value in d["labels"]:
                    d["labels"].remove(value)
            return ""
        if args[:2] == ("issue", "create"):
            n = max(k[1] for k in self.issues if k[0] == self.HERE) + 1
            self.add(n, flags["--label"].split(","), input)
            return url((self.HERE, n)) + "\n"
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in sorted(board.LABELS)])
        if args[:2] == ("api", "graphql"):
            return json.dumps({"data": self.graphql(args)})
        raise AssertionError(f"unexpected gh call {args}")

    def graphql(self, args):
        query = args[3].removeprefix("query=")
        v = dict(a.split("=", 1) for a in args[4:] if a not in ("-f", "-F"))
        for name in ("addBlockedBy", "addSubIssue"):
            if name in query:
                if name in self.refuse:
                    raise board.BoardError(f"gh api graphql: {name}: Resource not accessible by integration")
                self.mutations.append((name, *v.values()))
                if name == "addBlockedBy":
                    self.issues[self.key(v["issue"])]["blocked_by"].append(self.key(v["blocker"]))
                else:
                    sub = self.issues[self.key(v["sub"])]
                    if sub["parent"] and v["replace"] != "true":
                        raise board.BoardError("gh api graphql: issue already has a parent")
                    sub["parent"] = self.key(v["parent"])
                return {name: {"issue": {"number": 0}}}
        key = (f"{v['owner']}/{v['name']}", int(v["number"]))
        d = self.issues.get(key)
        if d is None:
            return {"repository": {"issue": None}}
        node = {"id": f"{key[0]}#{key[1]}", "state": d["state"], "stateReason": d["reason"]}
        return {"repository": {"issue": node | {"blockedBy": self.nodes(d["blocked_by"])}}}

    def blockers(self, n, where=HERE):
        return [k[1] if k[0] == self.HERE else f"{k[0]}#{k[1]}" for k in self.issues[(where, n)]["blocked_by"]]


@pytest.fixture
def native(monkeypatch):
    f = NativeGitHub()
    monkeypatch.setattr(board, "gh", f.gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "area_globs", lambda: {})
    board.repo_labels.cache_clear()
    yield f
    board.repo_labels.cache_clear()


def test_new_links_blockers_and_parent_natively_across_repos(native):
    native.add(1)
    native.add(2, ["type:epic"])
    native.add(5, where="o/other", state="CLOSED")
    out = board.new("t", "build", "P2", "", ["x"], [], ["1", "o/other#5"], [], "#2")
    assert out.endswith("(blocked)")  # #1 is open; o/other#5 is closed as done
    assert native.blockers(3) == [1, "o/other#5"] and native.issues[("o/r", 3)]["parent"] == ("o/r", 2)
    assert [m[0] for m in native.mutations] == ["addBlockedBy", "addBlockedBy", "addSubIssue"]
    i = board.get(3)
    assert i.rendered and i.parts["Blocked by"] == "#1, o/other#5" and i.parts["Parent"] == "#2"
    assert i.blockers == [1, "o/other#5"] and i.parent == 2


def test_new_with_only_closed_blockers_is_ready_and_not_planned_is_not_done(native):
    native.add(1, state="CLOSED")
    native.add(5, where="o/other", state="CLOSED", reason="NOT_PLANNED")
    assert board.new("t", "build", "P2", "", ["x"], [], [1], [], None).endswith("(ready)")
    assert board.new("t", "build", "P2", "", ["x"], [], ["o/other#5"], [], None).endswith("(blocked)")


def test_new_refuses_an_unknown_reference_before_creating_anything(native):
    native.add(1)
    for bad in (["o/r#999"], ["nope"]):
        with pytest.raises(board.BoardError, match=r"no such issue|not one issue reference"):
            board.new("t", "build", "P2", "", ["x"], [], bad, [], None)
    assert not [c for c in native.calls if c[:2] == ("issue", "create")]


def test_new_whose_linking_fails_leaves_an_unmarked_body_that_sync_backfills(native):
    native.add(1)
    native.add(2, ["type:epic"])
    native.refuse = {"addSubIssue"}
    with pytest.raises(board.BoardError, match="board sync"):
        board.new("t", "build", "P2", "", ["x"], [], [1], [], 2)
    assert not board.get(3).rendered and board.get(3).declared_parent == 2
    native.refuse = set()
    assert "#3 sub-issue of #2: linked" in board.sync()
    assert native.issues[("o/r", 3)]["parent"] == ("o/r", 2) and board.get(3).rendered


def test_link_refuses_a_cycle_with_its_path_and_changes_nothing(native):
    native.add(1, blocked_by=[2])
    native.add(2, blocked_by=[3])
    native.add(3)
    with pytest.raises(board.BoardError, match=r"#3 blocked by #1 closes the cycle #3 -> #1 -> #2 -> #3"):
        board.link(3, ["#1"])
    with pytest.raises(board.BoardError, match=r"cycle #3 -> #3"):
        board.link(3, [3])
    assert native.mutations == []
    assert board.link(1, [3]) == ["#1 blocked by #3: linked"]  # a second path, not a cycle
    assert native.blockers(1) == [2, 3] and board.get(1).parts["Blocked by"] == "#2, #3"


def test_link_follows_the_cycle_through_closed_and_other_repositories(native):
    native.add(1, blocked_by=["o/x#7"])
    native.add(7, where="o/x", blocked_by=["o/r#4"])
    native.add(4, state="CLOSED", blocked_by=[2])
    native.add(2)
    with pytest.raises(board.BoardError, match=r"cycle #2 -> #1 -> o/x#7 -> #4 -> #2"):
        board.link(2, [1])


def test_cycles_walk_a_1500_issue_chain_without_recursion():
    chain = {n: [n + 1] for n in range(1500)}

    def deps(r):
        return chain.get(r, [])

    assert board.cycles([0], deps) == []
    path = board.closing_cycle(1500, 0, deps)
    assert path is not None and len(path) == 1502 and path[0] == path[-1] == 1500


GRAPHS = st.dictionaries(st.integers(0, 12), st.lists(st.integers(0, 12), max_size=4), max_size=13)


@given(GRAPHS)
def test_cycles_agree_with_graphlib(edges):
    found = board.cycles(sorted(edges), lambda r: edges.get(r, []))
    for c in found:  # each is a real closed path
        assert c[0] == c[-1] and all(b in edges.get(a, []) for a, b in pairwise(c))
    try:
        list(graphlib.TopologicalSorter(edges).static_order())
        acyclic = True
    except graphlib.CycleError:
        acyclic = False
    assert acyclic == (found == [])


@given(GRAPHS, st.integers(0, 12), st.integers(0, 12))
def test_closing_cycle_is_exactly_a_path_back_through_the_new_edge(edges, a, b):
    def deps(r):
        return edges.get(r, [])

    path = board.closing_cycle(a, b, deps)
    reach, todo = set(), [b]  # does b reach a?
    while todo:
        r = todo.pop()
        if r not in reach:
            reach.add(r)
            todo += deps(r)
    assert (path is not None) == (a in reach)
    if path:
        assert path[0] == path[-1] == a and path[1] == b
        assert all(y in deps(x) for x, y in pairwise(path[1:]))


def test_sync_backfills_declared_links_renders_the_body_and_is_then_idempotent(native):
    native.add(1, body="## Context\nc\n\n## Blocked by\n#2\n\n## Parent\n#3\n\n## Notes\nkeep me")
    native.add(2)
    native.add(3, ["type:epic"])
    moved = board.sync()
    assert "#1 blocked by #2: linked" in moved and "#1 sub-issue of #3: linked" in moved
    assert "#1 -> blocked" in moved
    assert native.blockers(1) == [2] and native.issues[("o/r", 1)]["parent"] == ("o/r", 3)
    body = native.issues[("o/r", 1)]["body"]
    assert board.LINKS_MARK in body and "## Notes\nkeep me" in body and body.count("## Blocked by") == 1
    calls, mutations = len(native.calls), len(native.mutations)
    assert board.sync() == []
    assert len(native.mutations) == mutations
    assert not [c for c in native.calls[calls:] if c[:2] == ("issue", "edit")]


def test_sync_renders_the_body_from_native_links_never_the_other_way(native):
    native.add(1, body=board.render_links("## Context\nc\n", [2], None), blocked_by=[4])  # #2 removed in the UI
    native.add(2)
    native.add(4)
    board.sync()
    assert native.blockers(1) == [4] and native.mutations == []
    assert board.get(1).parts["Blocked by"] == "#4"


def test_sync_refuses_contradicting_declarations_and_reports_native_cycles(native):
    native.add(1, body="## Blocked by\n#2")
    native.add(2, body="## Blocked by\n#1")
    native.add(3, blocked_by=[4])
    native.add(4, blocked_by=[3])  # made by hand in the GitHub UI
    moved = board.sync()
    assert "#1 blocked by #2: linked" in moved
    assert "#2: refused blocked by #1: it closes the cycle #2 -> #1 -> #2" in moved
    assert "cycle: #3 -> #4 -> #3" in moved
    assert native.blockers(2) == []


def test_sync_keeps_the_body_unmarked_when_a_link_is_refused_for_access(native):
    native.add(1, body="## Blocked by\no/private#9")
    native.add(9, where="o/private")
    native.refuse = {"addBlockedBy"}
    moved = board.sync()
    assert any("o/private#9 not linked" in m for m in moved)
    assert not board.get(1).rendered and board.get(1).blockers == ["o/private#9"]  # still waits on it


def test_native_blockers_drive_startable_next_brief_and_status(native, monkeypatch):
    monkeypatch.setattr(board, "in_flight_files", lambda: {})
    monkeypatch.setattr(board, "issue_prs", lambda n: [])
    native.add(1, ["status:ready", "P0"], blocked_by=[2, "o/x#7"])
    native.add(2, ["status:ready", "P1"], parent=5)
    native.add(5, ["type:epic"])
    native.add(7, where="o/x", state="CLOSED")
    nxt = board.next_items(None, 5)
    assert "#1 " not in nxt and "#2 " in nxt
    assert "waits #2  " in board.listing()  # o/x#7 is closed as done: not waited on
    assert board.sync() == ["#1 -> blocked"]
    brief = board.brief(2)
    assert "## Blocking\n#1" in brief and "## Parent\n#5" in brief
    assert "## Sub-issues\n#2" in board.brief(5)
    native.issues[("o/r", 2)].update(state="CLOSED", reason="COMPLETED")
    assert board.sync() == ["#1 -> ready"]
    native.issues[("o/x", 7)].update(reason="NOT_PLANNED")
    assert board.sync() == ["#1 -> blocked"]  # closed as not planned elsewhere is not done either
    assert "waits o/x#7" in board.listing()


def test_an_old_gh_without_link_fields_reads_the_body(native):
    native.add(1, body="## Blocked by\n#2, o/x#7")
    native.add(2)
    native.old_gh = True
    i = board.fetch()[0]
    assert i.links is None and i.blockers == [2, "o/x#7"]
    assert board.get(1).blockers == [2, "o/x#7"]


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("12", 12),
        ("#12", 12),
        ("o/r#12", 12),
        ("O/R#12", 12),
        ("a/b#3", "a/b#3"),
        ("https://github.com/a/b/issues/3", "a/b#3"),
    ],
)
def test_parse_ref(monkeypatch, text, ref):
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    assert board.parse_ref(text) == ref


# --- deleted Owns (#230; docs/research/decision-records-and-drift.md check 2) ------------------------------------


def owns(*paths):
    return "## DONE WHEN\n- [ ] a test passes\n## Owns\n" + "".join(f"- `{p}`\n" for p in paths)


def test_gone_owns_names_entries_the_deletion_empties_not_those_still_matched():
    deleted, after = {"old/a.py", "lib/b.py"}, {"lib/c.py", "new/a.py"}
    entries = ["old/a.py", "old/", "lib/", "lib/*.py", "never.py", "new/a.py"]
    assert board.gone_owns(entries, deleted, after) == ["old/a.py", "old/"]


PATHS = st.sets(st.from_regex(r"[ab]{1,2}(/[ab]{1,2}){0,2}", fullmatch=True), max_size=8)
ENTRIES = st.lists(st.from_regex(r"[ab]{1,2}(/[ab*]{1,2}){0,2}/?", fullmatch=True), max_size=4)


@given(PATHS, PATHS, ENTRIES)
def test_gone_owns_over_the_deleted_paths_is_matched_before_and_none_after(before, after, entries):
    """Looking only at the deleted paths is the rule itself: an entry some path matched before and none matches after."""
    hit = lambda o, ps: any(board.owned(p, [o]) for p in ps)  # noqa: E731
    naive = [o for o in entries if hit(o, before) and not hit(o, after)]
    assert board.gone_owns(entries, before - after, after) == naive


def test_deleting_owns_skips_the_issues_the_pr_closes_refs_or_is_branched_from():
    issues = [mk(n, ["type:build"], owns("old/x.py")) for n in (12, 13, 14, 15)]
    issues.append(mk(16, ["type:build"], owns("lib/")))  # lib/b.py is still there
    issues.append(mk(17, ["type:build"], owns("new/y.py")))  # never existed
    found = board.deleting_owns(issues, {"old/x.py", "lib/a.py"}, {"lib/b.py"}, "Closes #13\nRefs #14", "12-move")
    assert found == {15: ["old/x.py"]}


def test_lint_reports_an_owns_path_that_existed_and_is_gone_as_an_error():
    issues = [
        mk(1, ["type:build", "P2", "status:ready"], owns("old/x.py"), title="devkit: one"),
        mk(2, ["type:build", "P2", "status:ready"], owns("planned/new.py"), title="devkit: two"),
        mk(3, ["type:build", "P2", "status:ready"], owns("devkit/"), title="devkit: three"),
    ]
    ever, now = {"old/x.py", "devkit/a.py", "devkit/b.py"}, {"devkit/b.py"}
    gone = [f for f in board.lint(issues, set(), {}, (ever, now)) if "gone" in f.problem]
    assert [(f.number, f.warn) for f in gone] == [(1, False)]
    assert "`old/x.py`" in gone[0].problem
    assert not [f for f in board.lint(issues, set(), {}) if "gone" in f.problem]  # no history read: no claim


def test_path_history_lists_every_path_ever_committed_and_the_tracked_ones(tmp_path, monkeypatch):
    def sh(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    sh("init", "-q", "-b", "main")
    sh("config", "user.email", "t@t")
    sh("config", "user.name", "t")
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    sh("add", "-A")
    sh("commit", "-qm", "one")
    sh("rm", "-q", "a.py")
    sh("commit", "-qm", "two")
    monkeypatch.chdir(tmp_path)
    assert board.path_history() == ({"a.py", "b.py"}, {"b.py"})
    monkeypatch.chdir(tmp_path.parent)  # outside a checkout: nothing to compare
    assert board.path_history() is None
