"""#531: local-first CI. `ci = "local"` stands in for every required check at once, without asking Actions; with
`ci = "actions"`, `--local-ci` sees within seconds that Actions cannot run a head (a billing block, an offline
self-hosted runner, no workflow run at all) instead of waiting `local_ci_wait_s`. The GitHub payloads are recorded
from real repositories (tests/fixtures/actions_blocked, scrubbed; docs/research/agent-harness.md)."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from devkit import ci_status, config, train, workflow
from tests.test_gate import (  # noqa: F401 - actions, clock and fake_integrate are fixtures
    HEAD,
    _job,
    _last_json,
    _merged,
    _run,
    actions,
    clock,
    fake_integrate,
)
from tests.test_train import last_json, three, world  # noqa: F401 - world is a fixture

RECORDED = Path(__file__).parent / "fixtures" / "actions_blocked"


def recorded(name: str):
    return json.loads((RECORDED / name).read_text())


QUEUED_AT = datetime.fromisoformat(recorded("queued_jobs.json")["jobs"][0]["created_at"])


@pytest.fixture(autouse=True)
def aged(monkeypatch):
    """GitHub's clock as `stranded` reads it: the recorded queued job is 60 s old unless a test calls aged(seconds)."""
    now = {"t": QUEUED_AT + timedelta(seconds=60)}
    monkeypatch.setattr(ci_status, "WALL_CLOCK", lambda: now["t"])
    return lambda seconds: now.update(t=QUEUED_AT + timedelta(seconds=seconds))


def _actions_calls(events) -> list[tuple]:
    return [e for e in events if e[:2] == ("gh", "api") and any("/actions/" in a for a in e)]


def _billing(name: str, job: int) -> tuple[dict, dict]:
    """A rollup CheckRun and its job as GitHub showed a billing-blocked job (the recorded payloads, renamed)."""
    (check,) = recorded("billing_rollup.json")["statusCheckRollup"]
    (payload,) = recorded("billing_jobs.json")["jobs"]
    url = check["detailsUrl"].rsplit("/", 1)[0] + f"/{job}"
    return check | {"name": name, "detailsUrl": url}, payload | {"id": job}


# --- the recorded payloads, read as ci_status reads them ------------------------------------------------------------


def test_the_recorded_billing_block_reads_as_never_ran_with_a_billing_annotation(actions):  # noqa: F811
    check, job = _billing("check", 11)
    actions["jobs"]["11"] = job
    actions["notes"]["11"] = recorded("billing_annotations.json")
    probe = ci_status.Probe()
    assert job["steps"] == [] and job["runner_id"] == 0  # what the record shows: no step ran, no runner took it
    assert ci_status.never_ran(check, probe) and ci_status.billing_blocked(check, probe)
    assert ci_status.standing([check], ["check"], True, probe) == {"check": "never-ran"}


def test_a_billing_block_on_any_check_of_the_head_covers_a_required_check_not_registered_yet(actions):  # noqa: F811
    other, job = _billing("pr-title", 12)
    actions["jobs"]["12"] = job
    actions["notes"]["12"] = recorded("billing_annotations.json")
    assert ci_status.standing([other], ["check"], True, ci_status.Probe()) == {"check": "never-ran"}
    actions["notes"]["12"] = [{"message": "Process completed with exit code 1."}]  # no step, but not billing
    assert ci_status.standing([other], ["check"], True, ci_status.Probe()) == {"check": "queued"}


def _queued(knobs, **job_fields) -> dict:
    """The recorded rollup CheckRun of a job queued for a self-hosted runner; its job payload with `job_fields`."""
    (check,) = [c for c in recorded("queued_rollup.json")["statusCheckRollup"] if c["name"] == "check"]
    (job,) = recorded("queued_jobs.json")["jobs"]
    knobs["jobs"][str(job["id"])] = job | job_fields
    return check


def _reads(check) -> str:
    return ci_status.standing([check], ["check"], True, ci_status.Probe())["check"]


def test_a_job_queued_for_an_offline_self_hosted_runner_is_stranded(actions, aged):  # noqa: F811
    check = _queued(actions)
    actions["runners"] = recorded("queued_runners.json")  # all three offline
    assert _reads(check) == "never-ran"
    online = recorded("queued_runners.json")
    online["runners"][1]["status"] = "online"
    actions["runners"] = online
    assert _reads(check) == "queued"
    actions["jobs"][next(iter(actions["jobs"]))]["labels"] = ["ubuntu-latest"]  # a GitHub-hosted job just waits
    actions["runners"] = recorded("queued_runners.json")
    assert _reads(check) == "queued"


def test_a_queued_job_is_never_stranded_before_actions_start_s(actions, aged):  # noqa: F811
    """Round 1 (#573): a runner list alone is never proof; the job must also have waited `actions_start_s`."""
    check = _queued(actions)
    actions["runners"] = recorded("queued_runners.json")
    aged(5)
    assert _reads(check) == "queued"
    aged(20)
    assert _reads(check) == "never-ran"
    actions["jobs"][next(iter(actions["jobs"]))] |= {"runner_id": 21, "runner_name": "runner-0"}  # a runner took it
    assert _reads(check) == "queued"


def test_runner_labels_match_case_insensitively(actions, aged):  # noqa: F811
    """Reviewer probe (a): `runs-on: [self-hosted, linux]` next to an online runner labelled `Linux` can run."""
    check = _queued(actions, labels=["self-hosted", "linux"])
    online = recorded("queued_runners.json")
    online["runners"][0]["status"] = "online"  # labels self-hosted, Linux, X64
    actions["runners"] = online
    assert _reads(check) == "queued"


@pytest.mark.parametrize(
    "case",
    ["empty list", "no runner with its labels", "unreadable", "incomplete", "organisation"],
)
def test_without_the_whole_runner_list_only_the_time_rule_decides(actions, aged, case):  # noqa: F811
    """Reviewer probe (b): repository runners [] (scale-from-zero, or an organisation's runner group the list does not
    show), none with the job's labels, an unreadable or a partial list: the list settles nothing, so the job is
    stranded only once queued `actions_stranded_s` (300 s; lead, 2026-11-19), not `actions_start_s`."""
    check = _queued(actions, labels=["self-hosted", "linux"])
    online = recorded("queued_runners.json")
    online["runners"][0]["status"] = "online"
    gpu = recorded("queued_runners.json")
    for r in gpu["runners"]:
        r["labels"] = [{"name": "self-hosted"}, {"name": "gpu"}]
    actions["runners"] = {
        "empty list": {"total_count": 0, "runners": []},
        "no runner with its labels": gpu,
        "unreadable": None,
        "incomplete": online | {"total_count": 120},  # an online match on page 1 but the list is partial
        "organisation": online,
    }[case]
    if case == "organisation":
        actions["owner"] = "Organization"
    for seconds, reads in ((5, "queued"), (60, "queued"), (299, "queued"), (300, "never-ran")):
        aged(seconds)
        assert _reads(check) == reads, seconds


def test_a_listed_matching_runner_offline_settles_it_after_actions_start_s(actions, aged):  # noqa: F811
    """The runner list settles it (whole, user-owned, a runner with the job's labels, every such one offline): the
    short `actions_start_s` applies, not `actions_stranded_s`."""
    check = _queued(actions, labels=["self-hosted", "LINUX"])
    actions["runners"] = recorded("queued_runners.json")
    for seconds, reads in ((19, "queued"), (20, "never-ran")):
        aged(seconds)
        assert _reads(check) == reads, seconds


# --- ci = "actions": standing in within seconds ----------------------------------------------------------------------


def test_a_billing_blocked_head_is_stood_in_for_within_30_s_not_local_ci_wait_s(fake_integrate, actions, clock):  # noqa: F811
    """DONE WHEN (#531): `check` never registers on the pushed head; the head's other required job was
    billing-blocked, so Actions runs nothing for the account and both are stood in for, not waited 600 s on."""
    other, job = _billing("template", 12)
    actions["jobs"]["12"] = job
    actions["notes"]["12"] = recorded("billing_annotations.json")
    actions["protection"] = {"contexts": ["check", "template"], "checks": []}
    actions["commands"]["template"] = []
    fake_integrate["checks"] = [other]
    fake_integrate["tip"] = "t" * 40
    fake_integrate["after_push"] = [[other]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert sum(clock["sleeps"]) < 30 and _merged(fake_integrate["events"])
    assert sorted((s["sha"], s["context"]) for s in actions["statuses"]) == [
        ("t" * 40, "check"),
        ("t" * 40, "template"),
    ]


def test_without_the_billing_annotation_a_check_that_never_registers_still_waits(fake_integrate, actions, clock):  # noqa: F811
    other, job = _billing("template", 12)
    actions["jobs"]["12"] = job
    actions["protection"] = {"contexts": ["check", "template"], "checks": []}
    actions["commands"]["template"] = []
    fake_integrate["checks"] = [other]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert clock["now"] >= 600


def test_a_head_actions_made_no_run_for_is_stood_in_for_after_actions_start_s(fake_integrate, actions, clock):  # noqa: F811
    fake_integrate["checks"] = []
    actions["runs"] = 0
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert 20 <= clock["now"] < 30
    assert [s["context"] for s in actions["statuses"]] == ["check"]


def test_an_offline_self_hosted_runner_is_stood_in_for_at_once(fake_integrate, actions, clock):  # noqa: F811
    (check,) = [c for c in recorded("queued_rollup.json")["statusCheckRollup"] if c["name"] == "check"]
    (job,) = recorded("queued_jobs.json")["jobs"]
    actions["jobs"][str(job["id"])] = job
    actions["runners"] = recorded("queued_runners.json")
    fake_integrate["checks"] = [check]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert clock["sleeps"] == [] and [s["context"] for s in actions["statuses"]] == ["check"]


def test_a_head_actions_runs_on_still_waits_for_its_checks(fake_integrate, actions, clock):  # noqa: F811
    """No false stand-in: a registered run on the head means the checks get their usual wait (and pass)."""
    fake_integrate["tip"] = "t" * 40
    fake_integrate["checks"] = [_run("check")]
    fake_integrate["after_push"] = [[], [], [], [], [_run("check", status="IN_PROGRESS")], [_run("check")]]
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert actions["statuses"] == [] and clock["now"] >= 20


# --- ci = "local": no wait, no Actions query -------------------------------------------------------------------------


def _polls(events) -> int:
    return sum(1 for e in events if e[:2] == ("gh", "pr") and "headRefOid,statusCheckRollup" in e)


def test_ci_local_posts_the_required_statuses_and_merges_without_waiting_on_actions(fake_integrate, actions, clock):  # noqa: F811
    """DONE WHEN (#531): no --local-ci flag. The head's checks are read once, nothing is waited for, and no workflow
    run is looked up: a job Actions never ran, one still queued and one not registered are all stood in for."""
    actions["ci"] = "local"
    actions["protection"] = {"contexts": ["check", "template", "plugin"], "checks": []}
    actions["commands"] |= {"template": [], "plugin": []}
    fake_integrate["checks"] = [_job("check", 11), _run("template", status="QUEUED")]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    assert sorted(s["context"] for s in actions["statuses"]) == ["check", "plugin", "template"]
    assert _merged(ev) and clock["sleeps"] == [] and _polls(ev) == 1
    assert not any("/actions/runs" in a for e in _actions_calls(ev) for a in e)


def test_ci_local_refuses_a_required_check_actions_ran_and_failed(fake_integrate, actions, clock, capsys):  # noqa: F811
    """Owner (2026-11-19): with ci = "local", a required check that completed with a real failure on the head
    refuses; nothing is posted over it and nothing is waited for."""
    actions["ci"] = "local"
    actions["steps"] = {"11": 5}  # the job ran its steps: a real failure
    fake_integrate["checks"] = [_job("check", 11)]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert actions["statuses"] == [] and not _merged(fake_integrate["events"]) and clock["sleeps"] == []
    assert _last_json(capsys.readouterr().out)["outcome"] == "refused"


# --- ci = "actions" without --local-ci: the automatic stand-in (owner, 2026-11-19) ----------------------------------


def test_a_billing_blocked_head_is_stood_in_for_without_the_flag(fake_integrate, actions, clock, capsys):  # noqa: F811
    check, job = _billing("check", 11)
    actions["jobs"]["11"] = job
    actions["notes"]["11"] = recorded("billing_annotations.json")
    fake_integrate["checks"] = [check]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert [s["context"] for s in actions["statuses"]] == ["check"] and _merged(fake_integrate["events"])
    assert sum(clock["sleeps"]) < 30 and "Actions cannot run on this head" in capsys.readouterr().out


def test_an_offline_runner_head_is_stood_in_for_without_the_flag(fake_integrate, actions, clock):  # noqa: F811
    (check,) = [c for c in recorded("queued_rollup.json")["statusCheckRollup"] if c["name"] == "check"]
    (job,) = recorded("queued_jobs.json")["jobs"]
    actions["jobs"][str(job["id"])] = job
    actions["runners"] = recorded("queued_runners.json")
    fake_integrate["checks"] = [check]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert [s["context"] for s in actions["statuses"]] == ["check"] and clock["sleeps"] == []


def test_a_head_with_no_workflow_run_is_stood_in_for_without_the_flag(fake_integrate, actions, clock):  # noqa: F811
    fake_integrate["checks"] = []
    actions["runs"] = 0
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert [s["context"] for s in actions["statuses"]] == ["check"] and sum(clock["sleeps"]) < 30


def test_without_the_flag_a_required_check_actions_ran_and_failed_still_refuses(fake_integrate, actions, capsys):  # noqa: F811
    """The head is billing-blocked (template), so the stand-in starts; but `check` ran its steps and failed."""
    other, job = _billing("template", 12)
    actions["jobs"]["12"] = job
    actions["notes"]["12"] = recorded("billing_annotations.json")
    actions["steps"] = {"11": 5}
    actions["protection"] = {"contexts": ["check", "template"], "checks": []}
    actions["commands"]["template"] = []
    fake_integrate["checks"] = [_job("check", 11), other]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert actions["statuses"] == [] and not _merged(fake_integrate["events"])
    assert _last_json(capsys.readouterr().out)["outcome"] == "refused"


def test_a_head_actions_runs_normally_takes_no_stand_in_and_no_extra_call(fake_integrate, actions):  # noqa: F811
    fake_integrate["checks"] = [_run("check")]
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    assert actions["statuses"] == [] and _actions_calls(ev) == [] and _merged(ev)


def test_a_train_with_a_billing_blocked_member_stands_in_without_the_flag(world, capsys, monkeypatch):  # noqa: F811
    three(world)
    monkeypatch.setattr(ci_status, "required_contexts", lambda: (["check"], []))
    monkeypatch.setattr(ci_status, "cannot_run", lambda rollup, head, probe: True)
    monkeypatch.setattr(ci_status, "actions_enabled", lambda: False)
    monkeypatch.setattr(config, "get", lambda: config.Config(local_ci={"check": []}))
    assert train.train([1, 2], harness_ok=True, dry_run=False) == 0
    posts = [e for e in world["events"] if e[:4] == ("gh", "api", "-X", "POST")]
    assert len(posts) == 2 and all("context=check" in e for e in posts)
    assert "running CI locally (Actions cannot run on this head)" in capsys.readouterr().out


def test_ci_local_still_refuses_what_a_status_cannot_satisfy(fake_integrate, actions, capsys):  # noqa: F811
    actions["ci"] = "local"
    actions["protection"] = {"contexts": ["check", "plugin"], "checks": []}
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert "plugin" in _last_json(capsys.readouterr().out)["reason"] and actions["statuses"] == []


def test_ci_local_a_failing_gate_posts_nothing(fake_integrate, actions, capsys):  # noqa: F811
    actions["ci"] = "local"
    fake_integrate["gate_code"] = 1
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert actions["statuses"] == [] and not _merged(fake_integrate["events"])


def test_ci_local_trains_post_on_each_member_head_without_querying_actions(world, capsys, monkeypatch):  # noqa: F811
    three(world)
    monkeypatch.setattr(ci_status, "required_contexts", lambda: (["check"], []))
    monkeypatch.setattr(config, "get", lambda: config.Config(local_ci={"check": []}, ci="local"))
    assert train.train([1, 2], harness_ok=True, dry_run=False) == 0
    posts = [e for e in world["events"] if e[:4] == ("gh", "api", "-X", "POST")]
    assert len(posts) == 2 and all("context=check" in e for e in posts)
    assert _actions_calls(world["events"]) == []


@pytest.mark.parametrize("value", ["Local", "none", ""])
def test_ci_takes_only_actions_or_local(value):
    with pytest.raises(config.ConfigError, match="ci"):
        config.parse({"ci": value})
    assert config.parse({"ci": "local"}).ci == "local" and config.parse({}).ci == "actions"


def test_the_recorded_payloads_name_no_private_repository():
    text = "".join(p.read_text() for p in RECORDED.glob("*.json"))
    assert "repos/o/r/" in text and HEAD not in text


class _Widest:
    """A poll-jitter source that always draws the top of its window."""

    def uniform(self, lo: float, hi: float) -> float:
        return hi


@pytest.mark.timeout(30)
@pytest.mark.parametrize("start", [8186.968838927999, 2097132.3524050734, 0.0])  # the first two trapped the old cut
def test_a_wait_cut_to_a_deadline_never_spins_on_a_float_remainder(fake_integrate, actions, clock, monkeypatch, start):  # noqa: F811
    """Regression (#531, a gate hang): cutting a poll's sleep to `actions_start_s - waited` left a remainder of a few
    ulps that no longer moved the clock, so the poll spun forever. A deadline within DEADLINE_EPS_S now counts as
    reached, and no deadline is overshot."""
    monkeypatch.setattr(ci_status, "CHECKS_RNG", _Widest())
    clock["now"] = start
    fake_integrate["checks"] = []  # `check` never registers; Actions has runs on the head, so the 600 s bound decides
    assert ci_status.await_actions(7, HEAD, ["check"]) == ("ready", {"check": "never-ran"})
    assert (
        len(clock["sleeps"]) <= 600 / ci_status.CHECKS_FLOOR_S + 3 and min(clock["sleeps"]) > ci_status.DEADLINE_EPS_S
    )
    clock["sleeps"].clear()
    assert ci_status.await_checks(7, HEAD, ["ci/check"], 37.3)[0] == "timeout"
    assert min(clock["sleeps"]) > ci_status.DEADLINE_EPS_S and sum(clock["sleeps"]) == pytest.approx(37.3)


class _Draws:
    """A poll-jitter source that draws `values` in turn (each clamped to its window), then the window's top."""

    def __init__(self, *values: float):
        self.values = list(values)

    def uniform(self, lo: float, hi: float) -> float:
        return min(hi, self.values.pop(0)) if self.values else hi


def _short_of(deadline: float) -> _Draws:
    """Draws whose second poll ends DEADLINE_EPS_S / 2 short of `deadline` (the first ends at floor + 2.3 s)."""
    return _Draws(2.3, deadline - 2.3 - 2 * ci_status.CHECKS_FLOOR_S - ci_status.DEADLINE_EPS_S / 2)


def test_a_pause_landing_just_short_of_actions_start_s_is_stretched_to_it(fake_integrate, actions, clock, monkeypatch):  # noqa: F811
    """Regression (#666, a CI flake): a jitter draw ending a poll within DEADLINE_EPS_S short of `actions_start_s`
    escaped the cut to that deadline, so the head was stood in for at 19.9992 s of a 20 s window. Real time never
    entered: the fake clock moves only on sleeps, and the jitter was unseeded."""
    monkeypatch.setattr(ci_status, "CHECKS_RNG", _short_of(20))
    fake_integrate["checks"] = []
    actions["runs"] = 0
    assert ci_status.await_actions(7, HEAD, ["check"]) == ("ready", {"check": "never-ran"})
    assert clock["now"] >= 20 and len(clock["sleeps"]) == 2


def test_a_pause_landing_just_short_of_the_checks_deadline_is_stretched_to_it(fake_integrate, clock, monkeypatch):  # noqa: F811
    """#666, the same class in await_checks: a draw ending within DEADLINE_EPS_S of `timeout_s` timed out early."""
    monkeypatch.setattr(ci_status, "CHECKS_RNG", _short_of(19.3))
    fake_integrate["checks"] = []
    assert ci_status.await_checks(7, HEAD, ["ci/check"], 19.3)[0] == "timeout"
    assert clock["now"] >= 19.3 and len(clock["sleeps"]) == 2
