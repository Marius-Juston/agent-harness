"""Board lint: the conventions every open issue must meet, and `--fix` for the mechanical ones.

No test calls GitHub: lint is a pure function of fetched issues; the command's `gh` is a fake.
"""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, gate, workflow

T0 = "2026-10-11T00:00:00Z"
DONE = "## DONE WHEN\n- [ ] a test passes\n"
OWNS = "## Owns\n- `devkit/x.py`\n"


def mk(n, labels, body=DONE + OWNS, title=None, links=None, comments=None):
    return board.Issue(n, title or f"devkit: item {n}", labels, body, "OPEN", T0, comments or [], links)


def ok(n, *extra, **kw):
    return mk(n, ["type:build", "P2", "status:ready", *extra], **kw)


def problems(issues, done=frozenset(), merged=None, number=None):
    found = board.lint(issues, set(done), merged or {})
    return [f.problem for f in found if not f.warn and (number is None or f.number == number)]


def fixes(issues, done=frozenset()):
    """Issue -> (labels --fix adds, labels it removes), over all its findings."""
    out = {}
    for f in board.lint(issues, set(done), {}):
        if f.add or f.remove:
            add, remove = out.setdefault(f.number, (set(), set()))
            add.update(f.add)
            remove.update(f.remove)
    return out


def test_a_conforming_board_has_no_findings():
    epic = mk(9, ["type:epic", "P1"], "## Context\nround 2\n", "Epic: round 2", board.Links([], None, [], [1]))
    research = mk(3, ["type:research", "P2", "status:ready"], DONE, "Research: which ranker")
    assert board.lint([ok(1), research, epic], set(), {}) == []


@pytest.mark.parametrize(
    ("labels", "fragment"),
    [
        (["P2", "status:ready"], "type:"),
        (["type:build", "type:bug", "P2", "status:ready"], "type:"),
        (["type:build", "status:ready"], "priority"),
        (["type:build", "P1", "P2", "status:ready"], "priority"),
        (["type:build", "P2"], "status:"),
        (["type:build", "P2", "status:ready", "status:review"], "status:"),
        (["type:build", "P2", "status:done"], "status:"),
    ],
)
def test_not_exactly_one_type_priority_and_status_label_fails(labels, fragment):
    assert any(fragment in p for p in problems([mk(1, labels)]))


def test_status_is_optional_on_epics_but_never_doubled():
    links = board.Links([], None, [], [2])
    assert problems([mk(1, ["type:epic", "P1"], DONE, "Epic: x", links)]) == []
    doubled = mk(1, ["type:epic", "P1", "status:ready", "status:blocked"], DONE, "Epic: x", links)
    assert any("status:" in p for p in problems([doubled]))


@pytest.mark.parametrize(
    ("title", "labels", "bad"),
    [
        ("devkit: rank by unblock count", ["type:build"], False),
        ("Gate and integrate: per-test timeout", ["type:infra"], False),
        ("guard_bash: forced rm rule", ["type:bug"], False),
        ("Release v0.1.5 (round-2 hardening)", ["type:infra"], True),  # no '<component>: '
        ("devkit: rank by unblock count.", ["type:build"], True),  # trailing period
        ("devkit: " + "x" * 93, ["type:build"], True),  # 101 chars
        ("devkit: " + "x" * 92, ["type:build"], False),  # 100 chars
        ("Research: which ranker", ["type:research"], False),
        ("Experiment: does the ranker help", ["type:research"], False),
        ("agent-stats: estimate output", ["type:research"], True),  # research needs Research:/Experiment:
        ("Epic: round 2", ["type:epic"], False),
        ("round 2 hardening", ["type:epic"], True),
        ("Research: which ranker", ["type:build"], True),  # a prefix that disagrees with the type
        ("Epic: round 2", ["type:build"], True),
    ],
)
def test_title_convention(title, labels, bad):
    links = board.Links([], None, [], [2])
    status = [] if "type:epic" in labels else ["status:ready"]
    issue = mk(1, [*labels, "P2", *status], DONE + OWNS, title, links)
    assert any("title" in p for p in problems([issue])) is bad


@pytest.mark.parametrize(
    "body",
    ["", "## Context\nx\n", "## DONE WHEN\n\n## Owns\n- `a`\n", "## DONE WHEN\n- [ ]\n- \n", "## DONE WHEN\n- [ ] 3\n"],
)
def test_empty_or_bare_number_done_when_fails(body):
    assert any("DONE WHEN" in p for p in problems([ok(1, body=body + OWNS)]))


def test_build_and_infra_need_owns_others_do_not():
    for kind in ("build", "infra"):
        assert any("Owns" in p for p in problems([mk(1, [f"type:{kind}", "P2", "status:ready"], DONE)]))
    for kind in ("bug", "test"):
        assert problems([mk(1, [f"type:{kind}", "P2", "status:ready"], DONE)]) == []
    # regression: an item with no type label is not taken for a build (the board's display default) and nagged twice
    assert not any("Owns" in p for p in problems([mk(1, ["P2", "status:ready"], DONE)]))


def test_blocking_cycle_fails_and_names_its_path():
    a = mk(1, ["type:build", "P2", "status:blocked"], links=board.Links([2], None))
    b = mk(2, ["type:build", "P2", "status:blocked"], links=board.Links([3], None))
    c = mk(3, ["type:build", "P2", "status:blocked"], links=board.Links([1], None))
    found = [p for p in problems([a, b, c]) if "cycle" in p]
    assert len(found) == 1
    assert "#1 -> #2 -> #3 -> #1" in found[0]


def test_open_issue_closed_by_a_merged_pr_fails():
    assert any("merged PR #40" in p for p in problems([ok(1)], merged={1: [40]}))


def test_ready_with_an_open_blocker_fails_and_is_fixed_to_blocked():
    i = ok(1, links=board.Links([7], None))
    assert any("open blocker" in p for p in problems([i]))
    assert problems([i], done={7}) == []
    assert fixes([i])[1] == ({"status:blocked"}, {"status:ready"})


def test_epic_with_no_sub_issues_fails():
    lonely = mk(1, ["type:epic", "P1"], DONE, "Epic: x", board.Links([], None, [], []))
    assert any("sub-issue" in p for p in problems([lonely]))
    # without fetched links, an open sub-issue naming it as parent is enough
    epic = mk(1, ["type:epic", "P1"], DONE, "Epic: x")
    sub = ok(2, body=DONE + OWNS + "\n## Parent\n#1\n")
    assert problems([epic, sub], number=1) == []


def test_fix_labels_an_epic_titled_issue_type_epic():
    """The owner's case: an epic filed as type:build."""
    i = mk(1, ["type:build", "P1", "status:ready"], DONE, "Epic: round 2", board.Links([], None, [], [2]))
    assert fixes([i])[1] == ({"type:epic"}, {"type:build"})


def test_fix_status_and_priority():
    f = fixes(
        [
            mk(1, ["type:build"]),  # no priority, no status: the board's defaults (P2, ready)
            mk(2, ["type:build", "P1", "P3", "status:ready", "status:in-progress"]),  # keep the highest and furthest
            mk(3, ["type:build", "P2", "status:done"], links=board.Links([9], None)),  # unknown status; waits on #9
        ]
    )
    assert f[1] == ({"P2", "status:ready"}, set())
    assert f[2] == (set(), {"P3", "status:ready"})
    assert f[3] == ({"status:blocked"}, {"status:done"})


def test_unfixable_findings_carry_no_label_edit():
    assert fixes([mk(1, ["P2", "status:ready"], title="no component here")]) == {}


def test_in_progress_without_a_live_claim_is_only_a_warning():
    found = board.lint([mk(1, ["type:build", "P2", "status:in-progress"])], set(), {})
    assert [f.warn for f in found] == [True]


@given(st.lists(st.sampled_from([*board.LABELS, "status:done", "type:epic"]), max_size=8))
def test_fix_reaches_a_fixed_point_on_labels(labels):
    """Applying the fixes once leaves no fixable label finding: --fix is idempotent."""
    i = mk(1, labels, title="Epic: x" if "type:epic" in labels else "devkit: x", links=board.Links([], None, [], [2]))
    add, remove = fixes([i]).get(1, (set(), set()))
    after = mk(1, sorted((set(labels) - remove) | add), title=i.title, links=i.links)
    assert fixes([after]) == {}


@pytest.fixture
def gh_board(monkeypatch):
    issues = [
        {"number": 1, "title": "Epic: x", "labels": [{"name": "type:build"}, {"name": "P1"}], "body": DONE},
        {"number": 2, "title": "devkit: y", "labels": [{"name": "type:build"}, {"name": "P2"}], "body": DONE + OWNS},
    ]
    for d in issues:
        d |= {"state": "OPEN", "updatedAt": T0, "comments": [], "blockedBy": {"nodes": []}, "parent": None}
        d |= {"blocking": {"nodes": []}, "subIssues": {"nodes": [{"url": "https://github.com/o/r/issues/2"}]}}
    calls = []

    def gh(*args, input=None):
        calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps(issues if "open" in args else [])
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": 40, "body": "Closes #2", "headRefName": "2-y", "baseRefName": "main"}])
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in board.LABELS])
        return ""

    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "default_branch", lambda: "main")
    board.repo_labels.cache_clear()
    yield calls
    board.repo_labels.cache_clear()


def test_lint_command_reports_and_exits_nonzero(gh_board):
    out, code = board.lint_board()
    assert code == 1
    assert "#1" in out and "type:epic" in out and "merged PR #40" in out
    assert not [c for c in gh_board if c[:2] == ("issue", "edit")]


def test_lint_command_fix_edits_labels_once_per_issue(gh_board):
    out, _ = board.lint_board(fix=True)
    edits = [c for c in gh_board if c[:2] == ("issue", "edit")]
    assert [c[2] for c in edits] == ["1", "2"]
    first = edits[0]
    assert "type:epic" in first[first.index("--add-label") + 1]
    assert "fixed" in out


def test_gate_stage_warns_and_never_fails_on_board_state(gh_board):
    ran = board.check_board()
    assert ran.ok and ran.checked == 2 and ran.warnings


def test_gate_stage_is_offline_tolerant(monkeypatch):
    def offline(*a, **k):
        raise board.BoardError("gh issue list: error connecting to api.github.com")

    monkeypatch.setattr(board, "fetch", offline)
    ran = board.check_board()
    assert ran.ok and ran.checked == 0 and "not read" in ran.warnings[0]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("gh")))
    assert board.check_board().ok


def test_gate_runs_the_board_stage():
    assert board.check_board in gate.stages(False, {}, None)


def test_orient_warns_with_the_lint_count(monkeypatch, tmp_path):
    issues = [mk(1, ["P2", "status:ready"], title="no component")]
    monkeypatch.setattr(board, "fetch", lambda *a, **k: issues)
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    monkeypatch.setattr(workflow, "status", lambda: "")
    monkeypatch.setattr(workflow, "root", lambda: tmp_path)
    out = workflow.orient()
    assert "board lint:" in out and "`uv run dev board lint`" in out
