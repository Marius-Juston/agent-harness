"""Board automation: PR and issue events, the stale sweep, area labels from the shared path map.

No test calls GitHub: `board.gh` is an in-memory fake of the issues it is asked about, so the real parsing in
`board.get`, `board.fetch` and `board.closed_numbers` runs against it.
"""

import json
import re
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, workflow

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "template/.github/workflows"  # the board workflow ships to projects
TEMPLATE_MAP = ROOT / "template/.github/labeler.yml"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def at(hours_ago: float) -> str:
    return datetime.fromtimestamp(NOW.timestamp() - hours_ago * 3600, UTC).isoformat().replace("+00:00", "Z")


def claim_comment(hours_ago: float, cid: int = 1) -> dict:
    return {
        "body": "<!-- devkit:claim host=h agent=builder-9 branch=9-x id=t -->\nClaimed",
        "createdAt": at(hours_ago),
        "url": f"https://github.com/o/r/issues/9#issuecomment-{cid}",
    }


class FakeBoard:
    """Issues by number behind the `gh` argv the board sends: list, view, edit labels, comment, close, labels."""

    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.labels = set(board.LABELS) | set(board.area_globs())
        self.calls: list[tuple] = []

    def add(self, n, labels, body="", comments=None, state="OPEN"):
        self.issues[n] = {
            "number": n,
            "title": f"t{n}",
            "labels": [{"name": x} for x in labels],
            "body": body,
            "state": state,
            "stateReason": "COMPLETED" if state == "CLOSED" else None,
            "updatedAt": at(0),
            "comments": list(comments or []),
        }

    def names(self, n):
        return [x["name"] for x in self.issues[n]["labels"]]

    def gh(self, *args, input=None):
        self.calls.append(args)
        flags = dict(pairwise(args))
        if args[:2] == ("issue", "list"):
            state = flags["--state"].upper()
            return json.dumps([d for d in self.issues.values() if d["state"] == state])
        if args[:2] == ("issue", "view"):
            return json.dumps(self.issues[int(args[2])])
        if args[:2] == ("issue", "edit"):
            d = self.issues[int(args[2])]
            for flag, value in pairwise(args):
                if flag == "--add-label" and value not in self.names(d["number"]):
                    d["labels"].append({"name": value})
                if flag == "--remove-label":
                    d["labels"] = [x for x in d["labels"] if x["name"] != value]
            return ""
        if args[:2] == ("issue", "comment"):
            d = self.issues[int(args[2])]
            cid = 100 + len(d["comments"])
            d["comments"].append({"body": input, "createdAt": at(0), "url": f"x#issuecomment-{cid}"})
            return f"x#issuecomment-{cid}\n"
        if args[:2] == ("issue", "close"):
            self.issues[int(args[2])].update(state="CLOSED", stateReason="COMPLETED")
            return ""
        if args[:2] == ("issue", "create"):
            self.created = {"labels": flags["--label"].split(","), "body": input}
            return "https://github.com/o/r/issues/77\n"
        if args[:2] == ("pr", "create"):
            self.pr_args = args
            return "https://github.com/o/r/pull/1\n"
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in sorted(self.labels)])
        if args[:2] == ("label", "create"):
            self.labels.add(args[2])
            return ""
        raise AssertionError(f"unexpected gh call {args}")


@pytest.fixture
def fake(monkeypatch):
    f = FakeBoard()
    monkeypatch.setattr(board, "gh", f.gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    board.repo_labels.cache_clear()
    yield f
    board.repo_labels.cache_clear()


def merged_notes(fake, n):
    """Every "Merged in PR" note on issue n: comments plus the comment a `gh issue close` carries."""
    comments = [c["body"] for c in fake.issues[n]["comments"] if "Merged in PR" in c["body"]]
    closes = [c for c in fake.calls if c[:3] == ("issue", "close", str(n)) and "--comment" in c]
    return len(comments) + len(closes)


# --- what a merge does (`dev integrate` -> board.merged_pr) ------------------------------------------------


def test_linked_reads_closing_keywords_and_the_branch_number_never_refs():
    assert board.linked("Closes #1, fixes #2 and Resolves #3. Refs #4", "5-slug") == ([1, 2, 3], [5])
    assert board.linked("Refs #7", "7-x") == ([], [7])
    assert board.linked("closes #8", "8-x") == ([8], [])
    assert board.linked("Refs #4, part of #6", "main") == ([], [])
    assert board.linked("see PR#3 and issue #9", "x") == ([], [])


def test_merged_pr_closes_its_issue_and_unblocks_dependents(fake):
    """a merged PR moves blocked dependents to ready without any agent action."""
    fake.add(1, ["status:review"])
    fake.add(2, ["status:blocked"], "## Blocked by\n#1")
    fake.add(3, ["status:blocked"], "## Blocked by\n#1, #99")  # #99 unknown: stays blocked
    assert board.merged_pr(50, "Closes #1", "1-x", "main") == ["#1 -> done", "#2 -> ready"]
    assert fake.issues[1]["state"] == "CLOSED"
    assert "status:ready" in fake.names(2)
    assert "status:blocked" in fake.names(3)
    assert merged_notes(fake, 1) == 1


def test_merged_pr_after_github_closed_the_issue_comments_once_and_syncs(fake):
    fake.add(1, ["status:review"], state="CLOSED")
    fake.add(2, ["status:blocked"], "## Blocked by\n#1")
    assert board.merged_pr(50, "Closes #1", "1-x", "main") == ["#2 -> ready"]
    assert not any(c[:2] == ("issue", "close") for c in fake.calls)
    assert merged_notes(fake, 1) == 1


def test_a_merge_into_another_branch_closes_nothing(fake):
    fake.add(1, ["status:review"])
    fake.add(2, ["status:blocked"], "## Blocked by\n#1")
    assert board.merged_pr(50, "Closes #1", "1-x", "test-base") == []
    assert fake.issues[1]["state"] == "OPEN" and "status:blocked" in fake.names(2)
    assert fake.calls == []


def test_refs_never_moves_another_agents_issue(fake):
    fake.add(4, ["status:review"], comments=[claim_comment(1)])
    fake.add(5, ["status:review"])
    assert board.merged_pr(50, "Refs #4", "5-x", "main") == ["#5 -> ready"]  # its own branch's issue only
    assert "status:review" in fake.names(4) and board.get(4).claim() is not None
    assert fake.issues[4]["state"] == "OPEN" and fake.issues[5]["state"] == "OPEN"


def test_schedule_syncs_blockers_then_sweeps(fake):
    fake.add(1, ["status:ready"], state="CLOSED")
    fake.add(2, ["status:blocked"], "## Blocked by\n#1")
    fake.add(9, ["status:in-progress"], comments=[claim_comment(7)])
    assert board.event("schedule", {}, NOW) == ["#2 -> ready", "#9 -> stale"]


def test_unknown_event_is_an_error(fake):
    with pytest.raises(board.BoardError, match="no board automation"):
        board.event("pull_request", {})


# --- the stale sweep -------------------------------------------------------------------------------------


def test_sweep_labels_and_comments_a_stale_claim_once(fake):
    """the stale sweep labels and comments a stale in-progress item."""
    fake.add(9, ["status:in-progress"], comments=[claim_comment(7)])
    fake.add(10, ["status:in-progress"], comments=[claim_comment(2)])  # fresh
    fake.add(11, ["status:ready"], comments=[claim_comment(30)])  # not in progress
    fake.add(12, ["status:in-progress"])  # no claim: nobody to nudge
    assert board.sweep(now=NOW) == ["#9 -> stale"]
    assert "stale" in fake.names(9)
    note = fake.issues[9]["comments"][-1]["body"]
    assert board.STALE_MARK in note and "builder-9@h" in note and "board beat 9" in note
    assert board.sweep(now=NOW) == []  # labelled already; its own comment did not make it fresh
    assert len(fake.issues[9]["comments"]) == 2


def test_a_heartbeat_clears_the_stale_label(fake):
    fake.add(9, ["status:in-progress", "stale"], comments=[claim_comment(9), {"body": "heartbeat", "createdAt": at(1)}])
    assert board.sweep(now=NOW) == ["#9 -> fresh"]
    assert "stale" not in fake.names(9)


def test_sweep_threshold_comes_from_workflow_dispatch(fake):
    fake.add(9, ["status:in-progress"], comments=[claim_comment(0.5)])
    assert board.event("schedule", {}, NOW) == []
    assert board.event("workflow_dispatch", {"inputs": {"hours": "0.25"}}, NOW) == ["#9 -> stale"]


def test_sweep_creates_the_stale_label_when_missing(fake):
    fake.labels.discard("stale")
    board.sweep(now=NOW)
    assert "stale" in fake.labels


@given(st.floats(0, 500), st.floats(0, 500))
def test_hours_idle_ignores_the_sweeps_own_comments_and_label_edits(claimed, swept):
    comments = [claim_comment(claimed), {"body": f"{board.STALE_MARK}\nStale", "createdAt": at(swept)}]
    i = board.Issue(9, "t", [], "", "OPEN", at(0), comments)
    assert abs(i.hours_idle(NOW) - claimed) < 1e-6


def test_hours_idle_without_comments_uses_the_update_time():
    assert board.Issue(1, "t", [], "", "OPEN", at(3), []).hours_idle(NOW) == pytest.approx(3)


# --- area labels from the one path map --------------------------------------------------------------------------


def test_the_template_area_map_has_the_five_areas_in_the_shared_shape():
    globs = board.area_globs(TEMPLATE_MAP)
    assert set(globs) == {"area:harness", "area:research", "area:docs", "area:tests", "area:product"}
    assert ".devkit/**" in globs["area:harness"] and ".claude/**" in globs["area:harness"]


def test_this_repository_reads_its_own_area_map():
    assert {"area:devkit", "area:plugin", "area:template"} <= set(board.area_globs())


@pytest.mark.parametrize(
    ("owns", "expected"),
    [
        ([".devkit/ratchets.toml"], ["area:harness"]),
        ([".claude/"], ["area:harness"]),
        ([".github/workflows/board.yml", "tests/test_x.py"], ["area:harness", "area:tests"]),
        (["docs/research/new-note.md"], ["area:research"]),
        (["docs/**"], ["area:research", "area:docs"]),
        (["src/app/exam.py"], ["area:product"]),
        (["README.md"], ["area:docs"]),
        (["nowhere/x.txt"], []),
    ],
)
def test_areas_from_owns(owns, expected):
    files = ["docs/research/a.md", "docs/specs/s.md", "README.md"]  # a project tree
    assert board.areas(owns, board.area_globs(TEMPLATE_MAP), files) == expected


@given(st.from_regex(r"[a-z]{1,8}(/[a-z]{1,8}){0,3}\.py", fullmatch=True))
def test_anything_under_claude_is_harness_and_under_tests_is_tests(rest):
    globs = board.area_globs(TEMPLATE_MAP)
    assert "area:harness" in board.areas([f".claude/{rest}"], globs, [])
    assert board.areas([f"tests/{rest}"], globs, []) == ["area:tests"]


def test_new_applies_area_labels_from_owns(fake, monkeypatch):
    """dev board new applies area labels derived from --owns using the same map file."""
    fake.labels -= {"area:devkit", "area:tests"}  # a repo before `board init`: the labels get created
    out = board.new("t", "infra", "P2", "c", ["d1", "d2"], ["devkit/board.py", "tests/"], [], [], None)
    assert out.endswith("(ready)")
    assert {"area:devkit", "area:tests"} <= set(fake.created["labels"])
    assert {"area:devkit", "area:tests"} <= fake.labels


def test_area_map_rejects_shapes_board_cannot_read(tmp_path):
    bad = {
        "negation": {"a": [{"changed-files": [{"any-glob-to-any-file": ["!x/**"]}]}]},
        "all": {"a": [{"all": [{"changed-files": [{"any-glob-to-any-file": ["x/**"]}]}]}]},
        "branch": {"a": [{"head-branch": ["^x"]}]},
        "other-match": {"a": [{"changed-files": [{"all-globs-to-all-files": ["x/**"]}]}]},
    }
    for name, data in bad.items():
        p = tmp_path / f"{name}.yml"
        p.write_text(yaml.safe_dump(data))
        with pytest.raises(board.BoardError, match="cannot read"):
            board.area_globs(p)
    ok = tmp_path / "ok.yml"
    ok.write_text("changed-files-labels-limit: 3\na:\n  - changed-files:\n      - any-glob-to-any-file: 'x/**'\n")
    assert board.area_globs(ok) == {"a": ["x/**"]}


def test_ensure_labels_creates_area_labels(fake):
    fake.labels -= set(board.area_globs())
    assert set(board.area_globs()) <= set(board.ensure_labels())


# --- the workflow file and `dev pr` labels ----------------------------------------------------------------------


def test_board_workflow_is_one_scheduled_job_on_mains_code():
    text = (WORKFLOWS / "board.yml").read_text()
    w = yaml.safe_load(text)
    assert set(w[True]) == {"schedule", "workflow_dispatch"}  # YAML 1.1 reads the key `on` as true
    assert list(w["jobs"]) == ["board"] and w["permissions"] == {}
    assert w["jobs"]["board"]["permissions"] == {"contents": "read", "issues": "write"}
    steps = w["jobs"]["board"]["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert steps[-1]["run"] == "uv run dev board event"
    for ref, comment in re.findall(r"uses:\s*(\S+)(.*)", text):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.fullmatch(r"\s*# v\d+\.\d+\.\d+", comment), (ref, comment)


def test_no_labeler_workflow_spends_minutes():
    assert not (WORKFLOWS / "labeler.yml").exists() and not (ROOT / ".github/workflows/labeler.yml").exists()
    assert TEMPLATE_MAP.exists() and (ROOT / ".github/labeler.yml").exists()


def test_dev_pr_labels_the_pr_from_its_changed_paths(fake, monkeypatch, tmp_path):
    """A PR touching devkit/ gets its area label from `dev pr` with the shared map."""
    monkeypatch.setattr(workflow.checks, "merge_base", lambda: "b" * 40)
    git = {"diff": "devkit/board.py\ntests/test_x.py", "branch": "33-x"}
    monkeypatch.setattr(workflow, "git", lambda *a, **k: git.get(a[0], ""))
    monkeypatch.setattr(workflow, "_gate_passed", lambda: True)
    fake.add(33, ["status:in-progress"])
    fake.labels.discard("area:devkit")  # a repo before `board init`: the label gets created first
    summary = tmp_path / "s.md"
    summary.write_text("Closes #33\n")
    workflow.pr(33, str(summary), draft=False)
    labels = fake.pr_args[fake.pr_args.index("--label") + 1]
    assert labels == "area:devkit,area:tests"
    assert "area:devkit" in fake.labels


def test_a_manual_sweep_can_be_limited_to_named_items(fake):
    fake.add(9, ["status:in-progress"], comments=[claim_comment(1)])
    fake.add(10, ["status:in-progress"], comments=[claim_comment(1)])
    assert board.sweep(0, NOW, only=[10]) == ["#10 -> stale"]
    assert "stale" not in fake.names(9)


def test_new_with_blocked_by_writes_the_done_list_not_blocker_numbers(fake):
    """Regression: new(..., done=["x"], blocked_by=[1]) wrote "- [ ] 1" (closed numbers) as DONE WHEN."""
    fake.add(1, [], state="CLOSED")
    board.new("t", "build", "P2", "", ["x"], [], [1], [], None)
    assert board.sections(fake.created["body"])["DONE WHEN"] == "- [ ] x"
