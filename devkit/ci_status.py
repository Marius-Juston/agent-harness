"""PR check state on GitHub: which checks a head has, which still run, what each required context stands at, and
waiting on them (#72, #76, #322). Integrate is the caller; devkit.workflow re-exports these names (devkit/train.py
reads them through it).
"""

import itertools
import json
import random
import re
import time
from dataclasses import dataclass, field

from devkit import backoff, board, config

CHECK_PASSED = {
    "SUCCESS",
    "NEUTRAL",
    "SKIPPED",
}  # CheckRun conclusions that do not block; a StatusContext needs SUCCESS


def check_key(c: dict) -> str:
    """`workflow/name` of a CheckRun, `/context` of a StatusContext."""
    return f"{c.get('workflowName') or ''}/{c.get('name') or c.get('context') or '?'}"


def latest_checks(rollup: list[dict]) -> list[dict]:
    """One check per (workflow, name or context), the latest by start then completion: a rollup keeps every run, so a
    rerun that passed sits next to the failure it replaced."""
    latest: dict[str, dict] = {}
    for c in rollup:
        key = check_key(c)
        when = (c.get("startedAt") or "", c.get("completedAt") or "")
        if key not in latest or when >= (latest[key].get("startedAt") or "", latest[key].get("completedAt") or ""):
            latest[key] = c
    return list(latest.values())


def unfinished(info: dict, feedback: dict, ignore: frozenset[str] = frozenset()) -> list[str]:
    """Feedback a merge would bury: failing or pending checks, a standing CHANGES_REQUESTED review, unresolved
    review threads (`feedback` is board.pr_feedback). Every check counts, required or not: a PR once merged over a
    failing optional job. `ignore`: required contexts Actions has not run, which the local gate stands in for
    (`--local-ci`)."""
    out = []
    for c in latest_checks(info.get("statusCheckRollup") or []):
        name = c.get("name") or c.get("context") or "?"
        if name in ignore:
            continue
        if c.get("__typename") == "StatusContext":
            state = c.get("state") or "PENDING"
            if state in {"PENDING", "EXPECTED"}:
                out.append(f"check {name}: pending")
            elif state != "SUCCESS":
                out.append(f"check {name}: failing ({state})")
        elif c.get("status") != "COMPLETED":
            out.append(f"check {name}: pending ({c.get('status')})")
        elif c.get("conclusion") not in CHECK_PASSED:
            out.append(f"check {name}: failing ({c.get('conclusion')})")
    asked = [board.login(r) for r in info.get("latestReviews") or [] if r.get("state") == "CHANGES_REQUESTED"]
    out += [f"changes requested by {who}" for who in asked]
    if not asked and info.get("reviewDecision") == "CHANGES_REQUESTED":
        out.append("changes requested (review decision)")
    out += [f"unresolved review thread {t['path']}:{t.get('line') or '?'}" for t in feedback["threads"]]
    if feedback.get("threads_truncated"):
        out.append(f"more than {board.THREADS_PAGE} review threads: the rest are unread, check them on GitHub")
    return out


def running(c: dict) -> bool:
    """A check still queued or running (a status context still pending)."""
    if c.get("__typename") == "StatusContext":
        return (c.get("state") or "PENDING") in {"PENDING", "EXPECTED"}
    return c.get("status") != "COMPLETED"


def _named(c: dict) -> str:
    return c.get("name") or c.get("context") or "?"


def only_pending(info: dict, feedback: dict, ignore: frozenset[str] = frozenset()) -> bool:
    """True when the PR's only unfinished feedback is checks still running (#322): nothing failed, no change is
    requested, no thread is open. Then integrate waits for the checks instead of refusing. `ignore` (the required
    contexts `--local-ci` stands in for) neither counts as running nor as unfinished."""
    runs = [c for c in latest_checks(info.get("statusCheckRollup") or []) if _named(c) not in ignore]
    settled = info | {"statusCheckRollup": [c for c in runs if not running(c)]}
    return any(running(c) for c in runs) and not unfinished(settled, feedback)


def pending_checks(info: dict, expected: list[str], ignore: frozenset[str] = frozenset()) -> list[str]:
    """Checks on the PR head that are not done: still queued or running, or one of `expected` (`check_key`s the PR
    had before integrate pushed) not registered on it yet. A failed check is done: `unfinished` judges it. Checks
    named in `ignore` (left to `--local-ci`) are not waited for."""
    runs = [c for c in latest_checks(info.get("statusCheckRollup") or []) if _named(c) not in ignore]
    out = [check_key(c) for c in runs if running(c)]
    seen = {check_key(c) for c in runs}
    return out + [f"{k} (not started)" for k in expected if k not in seen]


# Waiting for the checks on a head integrate pushed (#72): polls back off with full jitter (devkit.backoff) from a
# floor, so a lead's integrate does not hammer the API and parallel leads do not poll in step.
CHECKS_FLOOR_S = 5.0  # every poll waits at least this
CHECKS_BASE_S = 10.0  # the first poll's jitter window
CHECKS_CAP_S = 60.0  # no jitter window grows past this
CHECKS_RNG = random.Random()  # noqa: S311 - poll jitter, not a secret
CHECKS_CLOCK = time.monotonic


def await_checks(
    pr_number: int, tip: str, expected: list[str], timeout_s: float, ignore: frozenset[str] = frozenset()
) -> tuple[str, list[str]]:
    """Poll the PR until every check on `tip` is complete, at most `timeout_s`. Returns ("ready", []), ("moved",
    [new head]) when the head is no longer `tip`, or ("timeout", the checks still pending)."""
    deadline = CHECKS_CLOCK() + timeout_s
    for attempt in itertools.count():
        info = json.loads(
            board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid,statusCheckRollup")
        )
        if info["headRefOid"] != tip:
            return "moved", [info["headRefOid"]]
        if not (pending := pending_checks(info, expected, ignore)):
            return "ready", []
        if (left := deadline - CHECKS_CLOCK()) <= 0:
            return "timeout", pending
        time.sleep(min(left, backoff.wait(attempt, 0, CHECKS_RNG, CHECKS_BASE_S, CHECKS_CAP_S, CHECKS_FLOOR_S)))
    raise AssertionError("unreachable")


# `dev integrate --local-ci` (#76): merging must work with no Actions minutes. When Actions cannot run a required
# check (disabled, a billing block, or it never queues), the gate integrate already runs on the exact merged tree
# stands in, and only then is its pass posted as that check's commit status, with the hash of the run's log.
JOB_URL = re.compile(r"/actions/runs/\d+/job/(\d+)")


def required_contexts() -> tuple[list[str], list[str]]:
    """(main's required check contexts, those bound to one app): branch protection's, else the configured `local_ci`
    contexts when protection is absent or unreadable. A bound context (app_id set, e.g. GitHub Actions) is satisfied
    by that app alone, never by a status integrate posts."""
    try:
        prot = json.loads(board.gh("api", f"repos/{board.repo()}/branches/main/protection/required_status_checks"))
    except board.BoardError:
        return list(config.get().local_ci), []
    rules = prot.get("checks") or [{"context": c} for c in prot.get("contexts") or []]
    return [r["context"] for r in rules], [r["context"] for r in rules if r.get("app_id") not in (None, -1)]


def local() -> bool:
    """`ci = "local"` under [tool.devkit] (#531): Actions is never waited for. Each head's checks are read once; a
    required check Actions ran and failed refuses, every other required context is stood in for at once."""
    return config.get().ci == "local"


def _api(path: str) -> object | None:
    """A GitHub REST read, None when it fails or is not JSON: what it would have told is then unknown."""
    try:
        return json.loads(board.gh("api", f"repos/{board.repo()}/{path}"))
    except (board.BoardError, ValueError):
        return None


def actions_enabled() -> bool:
    """Whether Actions may run on this repository (`ci = "local"`: no, without asking)."""
    if local():
        return False
    got = _api("actions/permissions")
    return bool(got.get("enabled", True)) if isinstance(got, dict) else True  # unreadable: the wait decides


# #531: how a head shows that Actions cannot run, recorded in tests/fixtures/actions_blocked (research note
# agent-harness.md, "Actions that cannot run"). A billing block or spending limit fails each job within seconds with
# no step, no runner and one annotation naming billing; a job for a self-hosted runner stays queued while no runner
# with its labels is online; a head with no workflow run at all never gets its checks.
BILLING = re.compile(r"spending limit|payments have failed|billing", re.I)


@dataclass
class Probe:
    """What one look at a head read from the Actions API, each item at most once: job id -> job (None unreadable),
    check run id -> its annotations' messages, and the repository's self-hosted runners (None unreadable)."""

    jobs: dict[str, dict | None] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    runners: list[dict] | None = None
    runners_read: bool = False


def _job(c: dict, probe: Probe) -> tuple[str, dict | None]:
    if not (m := JOB_URL.search(c.get("detailsUrl") or "")):
        return "", None
    if m[1] not in probe.jobs:
        got = _api(f"actions/jobs/{m[1]}")
        probe.jobs[m[1]] = got if isinstance(got, dict) else None
    return m[1], probe.jobs[m[1]]


def never_ran(c: dict, probe: Probe) -> bool:
    """A CheckRun that ended unsuccessfully without running one step: Actions did not start its job (a billing block
    or spending limit; the message wording varies, the empty step list does not). An unreadable job is a real
    failure."""
    if c.get("__typename") == "StatusContext" or c.get("status") != "COMPLETED" or c.get("conclusion") in CHECK_PASSED:
        return False
    job = _job(c, probe)[1]
    return job is not None and not job.get("steps")


def billing_blocked(c: dict, probe: Probe) -> bool:
    """A CheckRun Actions never ran whose annotation names billing: the account cannot run any job (the job id is
    its check run's id)."""
    if not never_ran(c, probe):
        return False
    job_id = _job(c, probe)[0]
    if job_id not in probe.notes:
        got = _api(f"check-runs/{job_id}/annotations")
        probe.notes[job_id] = [a.get("message") or "" for a in got] if isinstance(got, list) else []
    return any(BILLING.search(n) for n in probe.notes[job_id])


def stranded(c: dict, probe: Probe) -> bool:
    """A queued CheckRun whose job waits for a self-hosted runner while no runner with all its labels is online: it
    never starts. A GitHub-hosted job that is queued, or an unreadable job or runner list, is not stranded."""
    if c.get("__typename") == "StatusContext" or c.get("status") in ("COMPLETED", "IN_PROGRESS"):
        return False
    job = _job(c, probe)[1]
    if not job or job.get("status") != "queued" or "self-hosted" not in (labels := set(job.get("labels") or [])):
        return False
    if not probe.runners_read:
        probe.runners_read = True
        got = _api("actions/runners")  # needs admin access: unreadable is unknown, so the wait decides
        probe.runners = got.get("runners") if isinstance(got, dict) else None
    if probe.runners is None:
        return False
    return not any(
        r.get("status") == "online" and labels <= {lab.get("name") for lab in r.get("labels") or []}
        for r in probe.runners
    )


STANDING = ("failed", "running", "queued", "never-ran", "passed")  # the first one a context's checks show wins


def standing(rollup: list[dict], contexts: list[str], enabled: bool, probe: Probe) -> dict[str, str]:
    """Each required context on a head: failed (it ran and failed), running, queued (not registered yet, or waiting
    for a runner), never-ran (Actions did not start it, Actions are disabled or billing-blocked, its self-hosted
    runner is offline) or passed. A billing block on any check of the head counts for every context still queued:
    the account runs no job. With `ci = "local"` nothing is waited for: running and queued read as never-ran."""
    checks = latest_checks(rollup)
    blocked = not enabled or any(billing_blocked(c, probe) for c in checks)
    out = {}
    for ctx in contexts:
        seen = []
        for c in checks:
            if (c.get("name") or c.get("context")) != ctx:
                continue
            if c.get("__typename") == "StatusContext":
                state = c.get("state") or "PENDING"
                seen.append(
                    "passed" if state == "SUCCESS" else "queued" if state in {"PENDING", "EXPECTED"} else "failed"
                )
            elif c.get("status") == "IN_PROGRESS":
                seen.append("running")
            elif c.get("status") != "COMPLETED":
                seen.append("never-ran" if stranded(c, probe) else "queued")
            else:
                seen.append(
                    "passed"
                    if c.get("conclusion") in CHECK_PASSED
                    else "never-ran"
                    if never_ran(c, probe)
                    else "failed"
                )
        seen = ["never-ran" if s == "queued" and blocked else s for s in seen or ["queued"]]
        out[ctx] = min(seen, key=STANDING.index)
    if local():
        return {k: "never-ran" if v in ("running", "queued") else v for k, v in out.items()}
    return out


def cannot_run(rollup: list[dict], head: str, probe: Probe) -> bool:
    """Whether a PR head shows, within seconds, that Actions cannot run (#531): a check billing-blocked or stranded
    on an offline self-hosted runner, or no check at all while Actions is disabled or made no run for `head`. Only
    what the rollup hints at is read from the API, so a head whose checks ran costs no call."""
    if local():
        return True
    checks = [c for c in latest_checks(rollup) if c.get("__typename") != "StatusContext"]
    if any(billing_blocked(c, probe) or stranded(c, probe) for c in checks):
        return True
    return not checks and (not actions_enabled() or no_runs(head))


def stand_in_mode(flag: bool, rollup: list[dict], head: str) -> str:
    """Why integrate or the train runs CI locally for a head (#531), or "" when it waits for Actions as usual:
    `--local-ci`, `ci = "local"`, or (with `ci = "actions"`) Actions shows it cannot run the head (`cannot_run`)."""
    if flag:
        return "--local-ci"
    if local():
        return 'ci = "local"'
    return "Actions cannot run on this head" if cannot_run(rollup, head, Probe()) else ""


def no_runs(tip: str) -> bool:
    """True when Actions created no workflow run for `tip` (an unreadable answer is not)."""
    got = _api(f"actions/runs?head_sha={tip}&per_page=1")
    return isinstance(got, dict) and got.get("total_count") == 0


def await_actions(pr_number: int, tip: str, contexts: list[str]) -> tuple[str, dict[str, str]]:
    """Poll the required contexts on `tip` until none runs, and none is queued unless Actions cannot run it
    (`standing`), Actions created no workflow run for `tip` after `actions_start_s`, or `local_ci_wait_s` passed.
    ("ready", standing) with what is still queued read as never-ran, ("moved", {}) when the head is no longer `tip`,
    ("timeout", standing) when a check still runs after `checks_wait_s`. With `ci = "local"` it reads `tip` once and
    is ready at once (`standing`)."""
    cfg = config.get()
    enabled, probe, start, ran = actions_enabled(), Probe(), CHECKS_CLOCK(), False
    for attempt in itertools.count():
        info = json.loads(
            board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid,statusCheckRollup")
        )
        if info["headRefOid"] != tip:
            return "moved", {}
        probe.jobs = {k: v for k, v in probe.jobs.items() if v and v.get("status") == "completed"}  # re-read the rest
        now = standing(info.get("statusCheckRollup") or [], contexts, enabled, probe)
        waited = CHECKS_CLOCK() - start
        queued, gone = "queued" in now.values(), False
        if queued and not ran and waited >= cfg.actions_start_s:
            gone = no_runs(tip)
            ran = not gone  # once Actions has a run on the head (or it is unreadable), the usual bound decides
        if "running" not in now.values() and (not queued or gone or waited >= cfg.local_ci_wait_s):
            return "ready", {k: "never-ran" if v == "queued" else v for k, v in now.items()}
        if waited >= cfg.checks_wait_s:
            return "timeout", now
        pause = backoff.wait(attempt, 0, CHECKS_RNG, CHECKS_BASE_S, CHECKS_CAP_S, CHECKS_FLOOR_S)
        if queued and not ran and waited < cfg.actions_start_s:
            pause = min(pause, cfg.actions_start_s - waited)  # look for the head's runs as soon as that is due
        time.sleep(pause)
    raise AssertionError("unreachable")


def local_ci_refusal(contexts: list[str], bound: list[str]) -> str | None:
    """Why `--local-ci` cannot stand in for main's required checks, or None."""
    if bound:
        rules = json.dumps({"checks": [{"context": c, "app_id": -1} for c in contexts]})
        return (
            f"required checks {', '.join(bound)} accept only one app (GitHub Actions), so a status integrate posts "
            "cannot satisfy them; the owner can allow any source: gh api -X PATCH "
            f"repos/{board.repo()}/branches/main/protection/required_status_checks --input - <<< '{rules}'"
        )
    if missing := [c for c in contexts if c not in config.get().local_ci]:
        return (
            f"no local stand-in for required checks {', '.join(missing)}: add each to `local_ci` under [tool.devkit] "
            "with the commands that verify what its CI job does"
        )
    return None
