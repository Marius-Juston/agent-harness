"""Integrate's per-PR admission (#473): the one list of checks a PR passes before its merged tree is gated.

Single `dev integrate` (`workflow._integrate`) and every member of a merge train (`train._admit`) call `admit`, so a
check added for one reaches the other: two reviews of #476 found checks the train had missed while they were listed
twice. What differs between the two callers is mechanics, not checks: how long each waits for running checks, how
it reports, and what it does with a member that must wait.

Each check is read through the module that defines it now (`devkit.workflow`), as `devkit/train.py` does (#441).
"""

from dataclasses import dataclass, field

from devkit import board, config, workflow


@dataclass
class At:
    """What the later checks judge: the fetched PR head and main, the PR's feedback (`board.pr_feedback`) and the
    paths it changes against main."""

    head: str
    main: str
    feedback: dict
    touched: list[str]


@dataclass
class Admission:
    """`outcome` is `admitted`, or why not: `not-open`, `refused`, `stopped`, `waiting` or `harness-review`. `send`
    holds the reasons the fix loop posts to the worker (None: nothing goes back); `say` the lines for the lead;
    `overridden` the unfinished feedback `--override` lifts, logged on the PR before the merge."""

    outcome: str = "admitted"
    reason: str = ""
    code: int = 1
    say: list[str] = field(default_factory=list)
    send: list[str] | None = None
    failing: list[str] = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    overridden: list[str] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return self.outcome == "admitted"


def _refused(reason: str, send: bool = True, **fields: object) -> Admission:
    return Admission("refused", reason, say=[f"RESULT: refused: {reason}"], send=[reason] if send else None,
                     fields=dict(fields))  # fmt: skip


def admit(
    pr: int,
    info: dict,
    *,
    override: str | None = None,
    harness_ok: bool = False,
    at: At | None = None,
    ignore: frozenset[str] = frozenset(),
    hold_pending: bool = False,
) -> Admission:
    """Every per-PR check, in order; the first that fails decides. Without `at` only the checks on the PR itself run
    (open, into main from this repository, no session link in a public repository, a Conventional title (#100), a
    Closes/Refs line (#434), a reason with `override`): callers run them before they fetch or wait. With `at` they run
    again (on what may be a fresh `info`), then: the head GitHub shows is the one fetched, no unfinished feedback
    (`ignore`: the contexts `--local-ci` stands in for), the reviews `required_reviews` asks for (#391), and
    `harness_ok` when it touches harness files. `override` lifts unfinished feedback only (then `overridden` lists
    it); nothing else. Feedback that is only checks still running is `waiting` with `hold_pending` (the caller waits
    or defers), else refused like any other."""
    if info["state"] != "OPEN" or info["isDraft"]:
        said = f"PR #{pr} is {info['state']}{' (draft)' if info['isDraft'] else ''}: nothing to integrate"
        return Admission("not-open", say=[said])
    if reason := workflow.refusal(info):
        return _refused(reason, send=False)
    if (leaks := workflow.session_leaks(**{"PR title": info.get("title"), "PR body": info.get("body")})) and (
        workflow.repo_public()
    ):
        return _refused(
            f"{workflow.SESSION_RULE}: the {' and '.join(leaks)} carry one; edit the PR, then rerun integrate"
        )
    if why := workflow.title_problem(info.get("title")):  # works with no Actions; override does not lift it
        return _refused(f"{why}; expected {workflow.expected_title(info)}: edit the PR title, then rerun integrate")
    if why := workflow.closes_problem(info):  # #434: override does not lift it either
        return _refused(f"{why}, then rerun integrate")
    if override is not None and not override.strip():
        return _refused("--override needs a reason", send=False)
    if at is None:
        return Admission()
    if at.head != info["headRefOid"]:
        said = f"RESULT: stopped (the PR head moved while fetching: {info['headRefOid'][:10]} -> {at.head[:10]}); rerun"
        return Admission("stopped", "the PR head moved while fetching", say=[said])
    overridden: list[str] = []
    if todo := workflow.unfinished(info, at.feedback, ignore):
        if override is not None:
            overridden = todo
        elif hold_pending and workflow.only_pending(info, at.feedback, ignore):
            return Admission("waiting", "checks still run on its head", fields={"pending": todo})
        else:
            said = [
                f"RESULT: refused: PR #{pr} has unfinished feedback:\n  " + "\n  ".join(todo),
                f"Read it (`uv run dev board feedback {pr}`), answer or fix each item, resolve the threads and wait for "
                "the checks; or rerun with --override '<reason>' (logged on the PR).",
            ]
            return Admission("refused", "unfinished feedback", say=said, send=[f"- {t}" for t in todo],
                             failing=workflow.failed_check_lines(info), fields={"feedback": todo})  # fmt: skip
    if rules := config.get().required_reviews:  # #391: override does not lift it
        trusted = {board.gh_user()} - {""}  # asked only when a review can be required

        def ancestor(sha: str) -> bool:
            return workflow._is_ancestor(sha, at.head)

        if why := workflow.review_problem(at.touched, at.feedback["reviews"], at.head, trusted, rules, ancestor):
            return _refused(f"{why}, then rerun integrate")
    if (harness := [f for f in at.touched if workflow.is_harness(f)]) and not harness_ok:
        said = [
            "PR changes the harness (rules agents are judged by):\n  " + "\n  ".join(harness),
            f"Read that diff yourself (`git diff {at.main[:10]}...{at.head[:10]}`), then rerun with --harness-ok.",
        ]
        return Admission("harness-review", code=2, say=said, fields={"harness": harness})
    if overridden:
        print(f"overriding unfinished feedback ({override}):\n  " + "\n  ".join(overridden))
    return Admission(overridden=overridden)


def override_log(override: str, overridden: list[str]) -> str:
    """The comment `--override` leaves on a PR just before it merges: the reason and what it lifted."""
    lifted = "\n".join(f"- {t}" for t in overridden) or "- nothing: no unfinished feedback at admission"
    return f"`dev integrate --override`: {override}\n\nOverriding:\n{lifted}"
