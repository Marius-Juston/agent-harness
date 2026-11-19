"""#531: local-first CI. `ci = "local"` stands in for every required check at once, without asking Actions; with
`ci = "actions"`, `--local-ci` sees within seconds that Actions cannot run a head (a billing block, an offline
self-hosted runner, no workflow run at all) instead of waiting `local_ci_wait_s`. The GitHub payloads are recorded
from real repositories (tests/fixtures/actions_blocked, scrubbed; docs/research/agent-harness.md)."""

import json
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


def test_a_job_queued_for_an_offline_self_hosted_runner_is_stranded(actions):  # noqa: F811
    (check,) = [c for c in recorded("queued_rollup.json")["statusCheckRollup"] if c["name"] == "check"]
    (job,) = recorded("queued_jobs.json")["jobs"]
    actions["jobs"][str(job["id"])] = job
    actions["runners"] = recorded("queued_runners.json")
    assert ci_status.standing([check], ["check"], True, ci_status.Probe()) == {"check": "never-ran"}
    online = recorded("queued_runners.json")
    online["runners"][1]["status"] = "online"
    actions["runners"] = online
    assert ci_status.standing([check], ["check"], True, ci_status.Probe()) == {"check": "queued"}
    actions["runners"] = None  # the runner list needs admin access: unknown, so it waits
    assert ci_status.standing([check], ["check"], True, ci_status.Probe()) == {"check": "queued"}
    actions["runners"] = recorded("queued_runners.json")
    actions["jobs"][str(job["id"])] = job | {"labels": ["ubuntu-latest"]}  # a GitHub-hosted job just waits
    assert ci_status.standing([check], ["check"], True, ci_status.Probe()) == {"check": "queued"}


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
    assert sum(clock["sleeps"]) >= 600


def test_a_head_actions_made_no_run_for_is_stood_in_for_after_actions_start_s(fake_integrate, actions, clock):  # noqa: F811
    fake_integrate["checks"] = []
    actions["runs"] = 0
    assert workflow.integrate(7, harness_ok=True, dry_run=False, local_ci=True) == 0
    assert 20 <= sum(clock["sleeps"]) < 30
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
    assert actions["statuses"] == [] and sum(clock["sleeps"]) >= 20


# --- ci = "local": no wait, no Actions query -------------------------------------------------------------------------


def test_ci_local_posts_the_required_statuses_and_merges_without_querying_actions(fake_integrate, actions, clock):  # noqa: F811
    """DONE WHEN (#531): no --local-ci flag; nothing under /actions/ is read, nothing waited for."""
    actions["ci"] = "local"
    actions["protection"] = {"contexts": ["check", "template"], "checks": []}
    actions["commands"]["template"] = []
    fake_integrate["checks"] = [_job("check", 11)]  # whatever Actions shows is not consulted
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    ev = fake_integrate["events"]
    assert sorted(s["context"] for s in actions["statuses"]) == ["check", "template"]
    assert _merged(ev) and _actions_calls(ev) == [] and clock["sleeps"] == []


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
    monkeypatch.setattr(train.config, "get", lambda: train.config.Config(local_ci={"check": []}, ci="local"))
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
