"""The per-PR admission single integrate and the merge train share (#473), and the train's push preflight (#472)."""

import ast
import inspect
import textwrap
from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st

from devkit import config, integrate, train, workflow

# The checks the review of #476 found listed twice: each must sit in `integrate.admit` and in neither caller.
CHECKS = {"refusal", "session_leaks", "title_problem", "closes_problem", "unfinished", "only_pending",
          "review_problem", "is_harness"}  # fmt: skip
# What `admit` calls through `workflow` only to word or prove a refusal, not to decide one.
WORDING = {"expected_title", "failed_check_lines", "_is_ancestor"}


def called(fn: Callable[..., object], module: str | None = None) -> set[str]:
    """The names `fn` calls (nested helpers included); with `module`, only `<module>.<name>(...)` calls."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if module is None:
                out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            elif isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == module:
                out.add(f.attr)
    return out


def test_integrate_and_the_train_admit_through_one_function_that_lists_every_check_once():
    checks = called(integrate.admit, "workflow") - WORDING  # derived from admit: a check added there is covered
    assert checks >= CHECKS
    for caller in (workflow._integrate, train._admit):
        calls = called(caller)
        assert "admit" in calls, caller.__name__
        assert not checks & calls, (caller.__name__, checks & calls)


def info(**kw: object) -> dict:
    return {"state": "OPEN", "isDraft": False, "baseRefName": "main", "isCrossRepository": False,
            "headRefOid": "a" * 40, "headRefName": "5-x", "title": "feat: t (#5)", "body": "Closes #5",
            "statusCheckRollup": [], "latestReviews": [], "reviewDecision": ""} | kw  # fmt: skip


def test_without_at_only_the_checks_on_the_pr_itself_run():
    assert integrate.admit(5, info()).admitted
    assert integrate.admit(5, info(state="MERGED")).outcome == "not-open"
    adm = integrate.admit(5, info(isCrossRepository=True))
    assert adm.outcome == "refused" and adm.send is None and "fork" in adm.reason
    adm = integrate.admit(5, info(), override="  ")
    assert adm.reason == "--override needs a reason" and adm.send is None


def test_a_head_that_moved_stops_and_pending_checks_wait_only_when_held(monkeypatch):
    monkeypatch.setattr(config, "get", config.Config)  # no required review: this repository's own requires one (#501)
    feedback = {"since": "", "comments": [], "reviews": [], "threads": []}
    at = integrate.At("b" * 40, "c" * 40, feedback, ["devkit/x.py"])
    assert integrate.admit(5, info(), at=at, harness_ok=True).outcome == "stopped"
    running = [{"__typename": "CheckRun", "name": "check", "workflowName": "ci", "status": "IN_PROGRESS"}]
    at.head = "a" * 40
    assert integrate.admit(5, info(statusCheckRollup=running), at=at, hold_pending=True).outcome == "waiting"
    adm = integrate.admit(5, info(statusCheckRollup=running), at=at)
    assert adm.outcome == "refused" and adm.send and adm.fields["feedback"]
    adm = integrate.admit(
        5, info(statusCheckRollup=running), at=at, override="CI is down", hold_pending=True, harness_ok=True
    )
    assert adm.admitted and adm.overridden == ["check check: pending (IN_PROGRESS)"]


PROTECTION = st.fixed_dictionaries(
    {},
    optional={
        "required_pull_request_reviews": st.just({"required_approving_review_count": 0}),
        "required_status_checks": st.just({"contexts": ["check"]}),
        "required_signatures": st.just({"enabled": True}),
        "restrictions": st.just({"users": []}),
    },
)


@given(PROTECTION, st.booleans(), st.booleans())
def test_classic_protection_blocks_unless_an_admin_is_not_enforced(protection, admin, enforced):
    protection = protection | {"enforce_admins": {"enabled": enforced}}
    blockers = train.push_blockers(protection, [], {}, admin, "lead")
    rules = set(protection) - {"enforce_admins"}
    assert bool(blockers) == bool(rules and not (admin and not enforced))


def test_an_unprotected_main_and_push_neutral_rulesets_block_nothing():
    assert train.push_blockers(None, [{"type": "deletion", "ruleset_id": 1}], {}, False, "lead") == []
    assert train.push_blockers(None, [{"type": "update", "ruleset_id": 1}], {1: "always"}, False, "lead") == []
    assert train.push_blockers(None, [{"type": "update", "ruleset_id": 1}], {}, True, "lead") == [
        "ruleset 1 requires no updates"
    ]
    assert train.push_blockers({"lock_branch": {"enabled": True}}, [], {}, True, "lead") == [
        "main is locked (branch protection)"
    ]
    listed = {"restrictions": {"users": [{"login": "lead"}]}}
    assert train.push_blockers(listed, [], {}, False, "lead") == []
