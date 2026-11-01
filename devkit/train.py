"""`dev integrate --train N <pr>...`: merge several ready PRs with one gate, and bisect on failure (#464).

A single integrate spends one full gate per PR. A train admits each member through integrate's own per-PR checks,
merges the admitted members onto main one by one in the order given (the lead passes board priority order) in one
throwaway worktree, regenerates generated files and runs one gate on that candidate. On a pass each member lands on
main as its own squash commit and main is pushed once. On a failure the train bisects the candidate's prefixes to
find the first member whose addition breaks the gate, sends back only that member (the #110 fix loop), and gates the
rest once more without it (docs/research/agent-harness.md §12b).

Every integrate function is called through the module that defines it now (`devkit.workflow`, `devkit.branches`),
so a later move that re-exports it there keeps the train working (#441).
"""

import hashlib
import json
import math
import os
import subprocess
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from devkit import board, branches, config, handoff, workflow


class TrainStop(Exception):
    """A reason the whole train stops with nothing landed (no traceback: it is an outcome, not a crash)."""


@dataclass
class Member:
    number: int
    info: dict = field(default_factory=dict)
    head: str = ""
    stood: list[str] = field(default_factory=list)  # --local-ci: required contexts Actions never ran on its head
    outcome: str = ""  # set once this member's fate is decided
    fields: dict = field(default_factory=dict)


@dataclass
class Built:
    """A candidate: main with `members` merged in order, `steps[i]` the commit after member i, `tip` after
    regeneration. `conflict` names the member that did not merge (then the build stopped there) and its paths."""

    members: list[Member]
    steps: list[str]
    tip: str = ""
    conflict: tuple[Member, list[str]] | None = None
    log: Path | None = None  # its gate's log, once gated
    failing: list[str] = field(default_factory=list)  # its gate's failing lines, when it failed


def bisect_bound(n: int) -> int:
    """The most gates the bisection may run after the full candidate failed: ceil(log2 n) to find the first breaking
    member among n, and one more, either to gate the members after it without it or, when the culprit is the last
    member, to confirm the full candidate's failure (a flaky gate)."""
    return math.ceil(math.log2(n)) + 1 if n > 1 else 0


def first_breaking(n: int, passes: Callable[[int], bool]) -> tuple[int, int]:
    """(k, gates): the smallest prefix length k in 1..n whose candidate fails, given that the full candidate (n)
    failed and main (0) passes, with `passes(k)` gating the prefix of length k. At most ceil(log2 n) gates."""
    lo, hi, gates = 0, n, 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        gates += 1
        if passes(mid):
            lo = mid
        else:
            hi = mid
    return hi, gates


def harness_cleared(number: int, harness_ok: set[int] | bool) -> bool:
    """Whether the lead read the harness diff of PR `number`: `--harness-ok` names the PRs in a train."""
    return harness_ok if isinstance(harness_ok, bool) else number in harness_ok


def _admit(m: Member, main: str, harness_ok: bool, dry_run: bool, contexts: list[str], enabled: bool) -> bool:
    """Integrate's per-PR checks, before the member joins the candidate: open and into main from this repository,
    no session link, a Conventional title (#100), a Closes/Refs line (#434), no unfinished feedback, the required
    reviews (#391), and `harness_ok` for harness files. A failure the worker fixes goes through the fix loop as a
    single integrate would send it; checks still running leave the member for a later train. With `contexts`
    (`--local-ci`) it first waits for Actions on the head as integrate's `_stand_in` does (`await_actions`), and
    stands in only for the contexts Actions never ran; one that ran and failed refuses the member."""
    info, n = m.info, m.number

    def stop(outcome: str, **fields: object) -> bool:
        m.outcome, m.fields = outcome, dict(fields)
        print(f"train: #{n} not admitted: {outcome} ({fields.get('reason') or ''})".rstrip(" ()"))
        return False

    def back(reasons: list[str], failing: list[str], **fields: object) -> bool:
        stop("refused", **fields)
        m.fields |= workflow.fix_loop(n, info, "refused", reasons, failing, info["headRefOid"], dry_run)
        return False

    if info["state"] != "OPEN" or info["isDraft"]:
        return stop("not-open")
    if reason := workflow.refusal(info):
        return stop("refused", reason=reason)
    if m.head != info["headRefOid"]:
        return stop("stopped", reason="the PR head moved while fetching")
    if (leaks := workflow.session_leaks(**{"PR title": info.get("title"), "PR body": info.get("body")})) and (
        workflow.repo_public()
    ):
        reason = f"{workflow.SESSION_RULE}: the {' and '.join(leaks)} carry one; edit the PR, then rerun integrate"
        return back([reason], [], reason=reason)
    if why := workflow.title_problem(info.get("title")):
        reason = f"{why}; expected {workflow.expected_title(info)}: edit the PR title, then rerun integrate"
        return back([reason], [], reason=reason)
    if why := workflow.closes_problem(info):
        reason = f"{why}, then rerun integrate"
        return back([reason], [], reason=reason)
    ignore: frozenset[str] = frozenset()
    if contexts and dry_run:
        print(f"would wait for Actions on {m.head[:10]} and stand in for what it never runs: {', '.join(contexts)}")
        now = workflow.standing(info.get("statusCheckRollup") or [], contexts, enabled, {})
        ignore = frozenset(c for c, s in now.items() if s != "failed")
        m.stood = sorted(c for c, s in now.items() if s == "never-ran")
    elif contexts:
        print(f"train: waiting for Actions on #{n} {m.head[:10]} ({', '.join(contexts)}) ...", flush=True)
        state, now = workflow.await_actions(n, m.head, contexts)
        if state == "moved":
            return stop("stopped", reason="the PR head moved while waiting for Actions")
        if state == "timeout":
            running = [c for c, s in now.items() if s in ("running", "queued")]
            return stop("waiting", reason="Actions still run on its head: it joins a later train", pending=running)
        m.stood = sorted(c for c, s in now.items() if s == "never-ran")
        ignore = frozenset(m.stood)
        info = m.info = json.loads(board.gh("pr", "view", str(n), "-R", board.repo(), "--json", workflow.PR_FIELDS))
        if info["headRefOid"] != m.head:
            return stop("stopped", reason="the PR head moved while waiting for Actions")
    feedback = board.pr_feedback(n)
    if todo := workflow.unfinished(info, feedback, ignore):
        if workflow.only_pending(info, feedback, ignore):
            return stop("waiting", reason="checks still run on its head: it joins a later train", pending=todo)
        return back([f"- {t}" for t in todo], workflow.failed_check_lines(info), reason="unfinished feedback",
                    feedback=todo)  # fmt: skip
    touched = workflow.changed_paths(main, m.head)
    if rules := config.get().required_reviews:
        trusted = {board.gh_user()} - {""}

        def ancestor(sha: str) -> bool:
            return workflow._is_ancestor(sha, m.head)

        if why := workflow.review_problem(touched, feedback["reviews"], m.head, trusted, rules, ancestor):
            reason = f"{why}, then rerun integrate"
            return back([reason], [], reason=reason)
    if (harness := [f for f in touched if workflow.is_harness(f)]) and not harness_ok:
        print(f"train: #{n} changes the harness: read `git diff {main[:10]}...{m.head[:10]}`, then --harness-ok {n}")
        return stop("harness-review", harness=harness)
    return True


def _build(tree: Path, main: str, members: list[Member], admit: bool = False) -> Built:
    """Reset `tree` to main and merge each member's head in order, by main's merge rules (its `.gitattributes`
    and the generated-file driver), as integrate merges main into one PR. A member that conflicts is aborted and,
    with `admit` (the first build), left out while the build goes on (outcome `conflict`); so is a member whose merge
    empties an Owns entry of an open issue other than its own (`deleted_owns` against the step before it, outcome
    `refused` with `owns`). Without `admit` a conflict stops the build there. Regenerates once at the end when
    nothing stopped it. Raises TrainStop when the board cannot be read for the Owns check (fail closed)."""
    rules = workflow._merge_rules(main)
    workflow.git("checkout", "-q", "--detach", "-f", main, cwd=tree)
    workflow.git("clean", "-qfd", cwd=tree)  # not -x: the gate's .venv survives a rebuild
    built = Built([], [])
    for m in members:
        prev = built.steps[-1] if built.steps else main
        run = subprocess.run(
            ["git", *rules, "merge", "--no-ff", "--no-edit", "-m", f"train: merge PR #{m.number}", m.head],
            cwd=tree, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if run.returncode == 0:
            if admit:
                try:
                    gone = workflow.deleted_owns(tree, prev, m.info)
                except (OSError, RuntimeError) as exc:  # a gh failure is a BoardError, a RuntimeError
                    raise TrainStop(f"cannot read the board to check deleted Owns: {exc}") from exc
                if gone:
                    workflow.git("reset", "-q", "--hard", prev, cwd=tree)
                    m.outcome, m.fields = "refused", {"reason": "deletes paths open issues own", "owns": gone}
                    continue
            built.members.append(m)
            built.steps.append(workflow.git("rev-parse", "HEAD", cwd=tree))
            continue
        paths = workflow.git("diff", "--name-only", "--diff-filter=U", cwd=tree, check=False).splitlines()
        workflow.git("merge", "--abort", cwd=tree, check=False)
        if not paths:
            raise RuntimeError(f"git merge of PR #{m.number} failed without a conflict: {run.stderr.strip()[-300:]}")
        if admit:
            m.outcome, m.fields = "conflict", {"conflicts": paths}
            continue
        built.conflict = (m, paths)
        return built
    workflow.regenerate(tree)
    built.tip = workflow.git("rev-parse", "HEAD", cwd=tree)
    return built


def _send_back_conflict(m: Member, main: str, earlier: list[Member], paths: list[str], dry_run: bool) -> None:
    """A member that does not merge onto the candidate: against main itself it is integrate's conflict (counted);
    against the members merged before it, it is no failed cycle of its own (as `send_back_conflicting`)."""
    if own := workflow.merge_conflicts(m.head, main):
        m.fields |= workflow.send_back(m.number, m.info, main, own, dry_run=dry_run)
        return
    ahead = ", ".join(f"#{e.number}" for e in earlier) or "none"
    files = [f"- `{p}`" for p in paths]
    reasons = [f"In a merge train it conflicts with the PRs merged onto main ({main[:10]}) before it ({ahead}) in:",
               *files]  # fmt: skip
    step = "merge `origin/main` once those land, resolve these files, run `uv run dev check`, push, and hand back"
    m.fields |= workflow.fix_loop(m.number, m.info, "conflict", reasons, [], m.head, dry_run, False, step)


def _send_back_owns(m: Member, dry_run: bool) -> None:
    """As integrate refuses a merged tree that empties another open issue's Owns entry (override or not)."""
    rows = [f"#{n}: {', '.join(paths)}" for n, paths in m.fields["owns"].items()]
    print(f"train: #{m.number} deletes what open issues own:\n  " + "\n  ".join(rows))
    why = "It deletes what open issues own: name each with `Refs #N` in the PR body, or edit its Owns:"
    m.fields |= workflow.fix_loop(m.number, m.info, "refused", [why, *(f"- {r}" for r in rows)], [], m.head, dry_run)


def _post_local_ci(m: Member, log: Path, names: str) -> None:
    """`--local-ci` with `--train`, after main was pushed: the gated candidate holding this member passed the gate
    and each stand-in, so post a success status for each required context Actions never ran on the member's head (the
    commit whose checks branch protection reads for the PR), with the log's hash, and say so on the PR."""
    if not m.stood:
        return
    digest = hashlib.sha256(log.read_bytes()).hexdigest()
    note = f"dev integrate --local-ci --train: passed in train {names}; log sha256 {digest[:16]}"
    for ctx in m.stood:
        board.gh(
            "api", "-X", "POST", f"repos/{board.repo()}/statuses/{m.head}",
            "-f", "state=success", "-f", f"context={ctx}", "-f", f"description={note}",
        )  # fmt: skip
    body = (
        f"CI ran locally (`dev integrate --local-ci --train`): GitHub Actions did not run {', '.join(m.stood)} on "
        f"{m.head}. The train {names} holding this PR passed `uv run dev check --no-cache` and each required check's "
        f"stand-in. Log sha256 `{digest}`, kept at `{log}` on {os.uname().nodename}. Posted {', '.join(m.stood)} as "
        f"commit statuses on {m.head[:10]}."
    )
    board.gh("pr", "comment", str(m.number), "-R", board.repo(), "--body", body)
    m.fields["local_ci"] = m.stood


def _recheck(members: list[Member], main: str, dry_run: bool) -> str | None:
    """After the gate, as `_merge_if_unchanged` does for one PR: main and every member head unchanged, no feedback
    arrived (a check Actions ran and failed included), and the squash commit's title and body still pass
    (Conventional title, Closes line, no session link). A member that fails is sent back or stopped; the reason
    returned stops the whole train (nothing ungated lands)."""
    if (now_main := workflow.git("ls-remote", "origin", "refs/heads/main").split()[0]) != main:
        return f"main moved during the gate ({main[:10]} -> {now_main[:10]}): rerun the train"
    for m in members:
        state, todo = workflow._pr_state(m.number, frozenset(m.stood))
        why = None
        if state["headRefOid"] != m.head:
            m.outcome, m.fields = "stopped", {"reason": "the PR head moved during the gate"}
            return f"PR #{m.number}'s head moved during the gate: rerun the train"
        subject, body = workflow.squash_message(m.number, state)
        if workflow.session_leaks(**{"PR title": subject, "PR body": body}) and workflow.repo_public():
            why = [f"{workflow.SESSION_RULE} in the squash commit: edit the PR title or body, then rerun integrate"]
        elif t := workflow.title_problem(subject):
            why = [f"{t} in the squash commit: edit the PR title, then rerun integrate"]
        elif c := workflow.closes_problem(state):
            why = [f"{c}, then rerun integrate"]
        elif todo:
            why = [f"- {t}" for t in todo]
        if why:
            m.outcome, m.fields = "refused", {"reason": "changed during the gate", "feedback": why}
            m.fields |= workflow.fix_loop(m.number, state, "refused", why, [], m.head, dry_run)
            return f"PR #{m.number} changed during the gate ({why[0]}): rerun the train without it"
        m.info = state
    return None


def _regenerated_steps(members: list[Member], built: Built, main: str, tree: Path, dry_run: bool) -> list[str]:
    """Per member, the commit its squash commit will carry: its step with generated files regenerated (the last one is
    the gated tip itself), so every commit on main is consistent. Each is checked against the one before it with
    `deleted_owns`, as single integrate checks its regenerated tree. Done before `_recheck`, so the push follows the
    recheck at once. A member whose regenerated step empties another open issue's Owns entry is sent back and stops
    the train (TrainStop); so does a regenerate that fails or a board that cannot be read (fail closed)."""
    commits, prev = [], main
    for i, m in enumerate(members):
        try:
            if i == len(members) - 1:
                workflow.git("checkout", "-q", "--detach", "-f", built.tip, cwd=tree)
            else:
                workflow.git("checkout", "-q", "--detach", "-f", built.steps[i], cwd=tree)
                workflow.regenerate(tree)
        except RuntimeError as exc:
            raise TrainStop(f"regenerating generated files on PR #{m.number}'s step failed: {exc}") from exc
        top = workflow.git("rev-parse", "HEAD", cwd=tree)
        try:
            gone = workflow.deleted_owns(tree, prev, m.info)
        except (OSError, RuntimeError) as exc:  # a gh failure is a BoardError, a RuntimeError
            raise TrainStop(f"cannot read the board to check deleted Owns: {exc}") from exc
        if gone:
            m.outcome, m.fields = "refused", {"reason": "deletes paths open issues own", "owns": gone}
            _send_back_owns(m, dry_run)
            raise TrainStop(f"PR #{m.number}'s regenerated tree deletes what open issues own: rerun the train")
        commits.append(top)
        prev = top
    return commits


def _squash_chain(members: list[Member], commits: list[str], built: Built, main: str) -> str:
    """Each member as its own squash commit on main, in order, its subject and body `squash_message` of the PR as
    `_recheck` read it; only local `commit-tree` calls, so nothing slow sits between the recheck and the push.
    Returns the last commit, whose tree is exactly the gated tree."""
    parent = main
    for m, top in zip(members, commits, strict=True):
        subject, body = workflow.squash_message(m.number, m.info)
        parent = workflow.git("commit-tree", f"{top}^{{tree}}", "-p", parent, "-m", subject, "-m", body)
        m.fields["commit"] = parent
    if workflow.git("rev-parse", f"{parent}^{{tree}}") != workflow.git("rev-parse", f"{built.tip}^{{tree}}"):
        raise RuntimeError("the squash commits do not end on the gated tree")
    return parent


def _close_merged(m: Member, names: str, tip: str) -> None:
    """The member's commit is on main. Re-read its head: one that moved since the gate (the worker pushed after the
    push of main) leaves the PR open, its branch kept and its issues where they are (`kept_open`). Otherwise
    retarget PRs stacked on it, close it with a comment naming the train, delete its branch only if it still points
    at the gated head (a lease), and move its issues as integrate moves them. A step GitHub refuses is recorded
    (`close_error`, `branch_error`, `board_error`) and the rest still run: the member stays merged. `gh pr close` has
    no `--match-head-commit`, so the re-read and the lease bound the window instead."""
    branch = m.info.get("headRefName", "")
    try:
        now = workflow._remote_shas(m.number)[0]
    except (board.BoardError, RuntimeError, OSError, ValueError, KeyError) as exc:
        now = f"unreadable: {exc}"
    if now != m.head:
        m.fields["kept_open"] = (
            f"the PR head moved after the gate ({m.head[:10]} -> {now[:10]}): its train commit is on main, the PR "
            "stays open and its branch kept; read the new commits, then close it or let them go to a new PR"
        )
        print(f"train: PR #{m.number} landed but {m.fields['kept_open']}")
        return
    note = f"Merged by `dev integrate --train` in train {names} as {m.fields['commit']} on main (main now {tip[:10]})."
    closed = False
    try:
        if branch:
            workflow.retarget_stacked(branch, m.head)
        board.gh("pr", "close", str(m.number), "-R", board.repo(), "--comment", note)
        closed = True
    except (board.BoardError, RuntimeError, OSError) as exc:
        m.fields["close_error"] = str(exc)
        print(f"train: PR #{m.number} landed but closing it failed ({exc}): close it by hand")
    if closed and branch:
        try:  # refused if the worker pushed since: the branch is kept
            workflow.git("push", f"--force-with-lease=refs/heads/{branch}:{m.head}", "origin", f":refs/heads/{branch}")
        except RuntimeError as exc:
            m.fields["branch_error"] = str(exc)
            print(f"train: kept remote branch {branch}: {exc}")
    try:
        m.fields["moved"] = board.merged_pr(m.number, m.info.get("body") or "", branch, m.info["baseRefName"])
    except (board.BoardError, RuntimeError, OSError) as exc:
        m.fields |= {"moved": [], "board_error": str(exc)}
        print(f"train: PR #{m.number} landed but the board update failed ({exc}): move its issue by hand")
    try:
        if branch and (others := branches.open_prs_on(branch)) is not None and not [o for o in others if o != m.number]:
            print(branches.drop_branch(branch, [m.head]))
    except (board.BoardError, RuntimeError, OSError) as exc:
        print(f"train: kept branch {branch}: {exc}")


def train(prs: list[int], harness_ok: set[int] | bool, dry_run: bool, local_ci: bool = False) -> int:
    """`_train`, but every exit ends with the train's RESULT-JSON line, a crash included. `harness_ok`: the PRs whose
    harness diff the lead read (`--harness-ok 12 15`), or a bool for all of them."""
    gates: list[dict] = []
    members = [Member(n) for n in dict.fromkeys(prs)]
    try:
        return _train(members, gates, harness_ok, dry_run, local_ci)
    except Exception as exc:  # the lead branches on the last line, whatever failed
        if not isinstance(exc, TrainStop):
            traceback.print_exc()
        reason = str(exc) if isinstance(exc, TrainStop) else f"train failed: {type(exc).__name__}: {exc}"
        for m in members:
            if not m.outcome:
                m.outcome, m.fields = "stopped", {"reason": reason}
        print(f"RESULT: stopped ({reason}); check main, the PRs and the board, then rerun")
        return _result(members, gates, 1, "stopped", reason=reason)


def _result(members: list[Member], gates: list[dict], code: int, outcome: str, **fields: object) -> int:
    """One `result` record per member (what `--why` prints), then the train's line, the last one printed:
    `RESULT-JSON: {"train", "outcome", "exit", "members": {pr: {"outcome", ...}}, "gates_run": [...]}`."""
    for m in members:
        workflow.result(m.number, 0 if m.outcome == "merged" else 1, m.outcome or "stopped", judged=m.head or None,
                        train=[x.number for x in members], **m.fields)  # fmt: skip
    payload = {
        "train": [m.number for m in members],
        "outcome": outcome,
        "exit": code,
        "members": {str(m.number): {"outcome": m.outcome, **m.fields} for m in members},
        "gates_run": gates,
        **fields,
    }
    print("RESULT-JSON: " + json.dumps(payload))
    return code


def _stop_rest(members: list[Member], why: str) -> None:
    for m in members:
        if not m.outcome:
            m.outcome, m.fields = "stopped", {"reason": why}


def _train(members: list[Member], gates: list[dict], harness_ok: set[int] | bool, dry_run: bool, local_ci: bool) -> int:
    contexts: list[str] = []
    enabled = True
    if local_ci:
        contexts, bound = workflow.required_contexts()
        if why := workflow.local_ci_refusal(contexts, bound):
            for m in members:
                m.outcome, m.fields = "refused", {"reason": f"--local-ci: {why}"}
            print(f"RESULT: refused: --local-ci: {why}")
            return _result(members, gates, 1, "refused", reason=f"--local-ci: {why}")
        enabled = workflow.actions_enabled()
    for m in members:
        m.info = json.loads(board.gh("pr", "view", str(m.number), "-R", board.repo(), "--json", workflow.PR_FIELDS))
    refs = {m.number: f"refs/devkit/train/pr-{m.number}" for m in members}
    main_ref = "refs/devkit/train/main"
    tree = workflow.state_dir("integrate") / "train"
    names = "(" + ", ".join(f"#{m.number}" for m in members) + ")"
    try:
        workflow.git("fetch", "--no-tags", "origin", f"+refs/heads/main:{main_ref}",
                     *(f"+refs/pull/{n}/head:{r}" for n, r in refs.items()))  # fmt: skip
        main = workflow.git("rev-parse", main_ref)
        for m in members:
            m.head = workflow.git("rev-parse", refs[m.number])
        admitted = [
            m for m in members if _admit(m, main, harness_cleared(m.number, harness_ok), dry_run, contexts, enabled)
        ]
        if not admitted:
            print("RESULT: nothing to gate: no member was admitted")
            return _result(members, gates, 1, "none-admitted")
        workflow._add_worktree(tree, main)

        def gate(built: Built, label: str) -> bool | str:
            nums = [m.number for m in built.members]
            log = workflow.state_dir("integrate") / f"train-{built.tip[:12]}-{len(gates)}.log"
            print(f"train: gating {label} {nums} as {built.tip[:10]} on main {main[:10]} ...", flush=True)
            passed = workflow.run_gate(tree, contexts, log)
            failing = workflow.failing_lines(log.read_text(errors="replace")) if passed is not True else []
            gates.append({"members": nums, "tip": built.tip, "passed": passed is True, "log": str(log)}
                         | ({"stopped": passed} if isinstance(passed, str) else {}))  # fmt: skip
            built.log, built.failing = log, failing
            return passed

        # apply in order; a member that conflicts with main or those before it, or deletes what another open issue
        # owns, is sent back before any gate
        full = _build(tree, main, admitted, admit=True)
        for m in admitted:
            if m.outcome == "conflict":
                earlier = [e for e in full.members if admitted.index(e) < admitted.index(m)]
                _send_back_conflict(m, main, earlier, m.fields["conflicts"], dry_run)
            elif m.outcome == "refused" and "owns" in m.fields:
                _send_back_owns(m, dry_run)
        if not full.members:
            print("RESULT: no admitted member merges onto main; nothing to gate")
            return _result(members, gates, 1, "none-admitted")
        passed = gate(full, "the train")
        landing, landed = full.members, full
        if isinstance(passed, str):
            return _stopped(members, gates, passed)
        if not passed:
            found, landed_or_why = _bisect(full, tree, main, gate, dry_run)
            if found is None or isinstance(landed_or_why, str):
                return _stopped(members, gates, str(landed_or_why))
            landing, landed = found, landed_or_why
        if not landing:
            print("RESULT: gate-failed: no member passes the gate")
            return _result(members, gates, 1, "gate-failed")
        commits = _regenerated_steps(landing, landed, main, tree, dry_run)
        if why := _recheck(landing, main, dry_run):
            _stop_rest(landing, why)
            print(f"RESULT: stopped ({why})")
            return _result(members, gates, 1, "stopped", reason=why)
        tip = _squash_chain(landing, commits, landed, main)
        if dry_run:
            print(f"would push {tip[:10]} ({len(landing)} squash commits) to main")
        else:
            try:  # a fast-forward: refused if main moved since the recheck
                workflow.git("push", "origin", f"{tip}:refs/heads/main")
            except RuntimeError as exc:
                for m in landing:
                    m.outcome, m.fields = "stopped", {"reason": f"cannot push main: {exc}"}
                print(f"RESULT: stopped (cannot push main; did it move? rerun the train): {exc}")
                return _result(members, gates, 1, "stopped", reason=f"cannot push main: {exc}")
    finally:
        workflow.git("worktree", "remove", "--force", str(tree), check=False)
        for ref in (main_ref, *refs.values()):
            workflow.git("update-ref", "-d", ref, check=False)
    sent: list[int] = []
    if dry_run:
        for m in landing:
            m.outcome = "would-merge"
    else:
        for m in landing:  # on main now, whatever a later step does
            m.outcome = "merged"
        for m in landing:
            if local_ci and landed.log is not None:
                try:
                    _post_local_ci(m, landed.log, names)
                except (board.BoardError, RuntimeError, OSError) as exc:
                    m.fields["local_ci_error"] = str(exc)
            _close_merged(m, names, tip)
        try:  # the merge stands whatever this finds
            sent = workflow.send_back_conflicting(landing[-1].number)
        except (board.BoardError, RuntimeError, OSError, ValueError) as exc:
            print(f"conflict check of the new main skipped ({exc}): each open PR meets it at its own integrate")
        checkout = workflow.main_checkout()
        if not workflow.git("-C", str(checkout), "status", "--porcelain", "--untracked-files=no"):
            workflow.git("-C", str(checkout), "pull", "--ff-only", check=False)
        print(handoff.after_integrate())
    every = all(m.outcome in ("merged", "would-merge") for m in members)
    outcome = ("would-merge" if dry_run else "merged") if every else "partial"
    print(f"RESULT: train {outcome}: landed {', '.join(f'#{m.number}' for m in landing)} with {len(gates)} gate(s)")
    return _result(members, gates, 0 if every else 1, outcome, main=tip, sent_back=sent)


def _stopped(members: list[Member], gates: list[dict], why: str) -> int:
    _stop_rest(members, why)
    print(f"RESULT: stopped ({why}) and was killed; `uv run dev status` names the gate holder, then rerun the train")
    return _result(members, gates, 1, "stopped", reason=why)


def _bisect(
    full: Built, tree: Path, main: str, gate: Callable[[Built, str], bool | str], dry_run: bool
) -> tuple[list[Member] | None, Built | str]:
    """The full candidate failed: find the first member whose addition breaks the gate (prefix bisection, at most
    ceil(log2 n) gates). When that is the last member, every shorter prefix passed, so the full candidate is gated
    once more before blaming it (a flaky gate lands them all). Otherwise the culprit alone goes back through the fix
    loop with its failing lines, and the members after it are gated once on the passing prefix. Returns (members to
    land, their gated build), or (None, reason) when a gate was stopped. Members after the culprit that fail
    together, or no longer merge without it, are deferred: left open for the next train, never sent back on unproven
    blame."""
    members, n = full.members, len(full.members)
    builds: dict[int, Built] = {n: full}
    stopped: list[str] = []

    def passes(k: int) -> bool:
        if stopped:
            return False
        builds[k] = _build(tree, main, members[:k])
        result = gate(builds[k], f"prefix {k} of {n}")
        if isinstance(result, str):
            stopped.append(result)
            return False
        return result

    k, _ = first_breaking(n, passes)
    if stopped:
        return None, stopped[0]
    before = ", ".join(f"#{m.number}" for m in members[: k - 1])
    if k == n and n > 1:  # only the full candidate failed: confirm it before blaming its last member
        again = _build(tree, main, members)
        result = gate(again, "the train again (only the full candidate failed)")
        if isinstance(result, str):
            return None, result
        if result:
            print("train: the full candidate passed when gated again (a flaky gate): landing every member")
            for m in members:
                m.fields["flaky_gate"] = True
            return members, again
        builds[k] = again
    culprit, broke = members[k - 1], builds[k]
    measured = (
        f"main {main[:10]} with {before} merged passed the gate and adding this PR failed it"
        + (" (gated twice)" if k == n and n > 1 else "")
        if before
        else f"main {main[:10]} with this PR merged first failed the gate (main itself was not gated)"
    )
    why = f"In a merge train {names_of(members)}: {measured}; log {broke.log}"
    culprit.outcome, culprit.fields = "gate-failed", {"reason": "breaks the train's gate", "gate": broke.failing}
    culprit.fields |= workflow.fix_loop(
        culprit.number, culprit.info, "gate-failed", [why], broke.failing, culprit.head, dry_run
    )
    print(f"train: #{culprit.number} breaks the gate; sent back")
    good, rest = members[: k - 1], members[k:]
    if not rest:
        return good, builds.get(k - 1) or Built([], [], main)
    again = _build(tree, main, good + rest)
    if again.conflict is None:
        result = gate(again, "the train without the culprit")
        if isinstance(result, str):
            return None, result
        if result:
            return good + rest, again
    for m in rest:
        m.outcome, m.fields = (
            "deferred",
            {"reason": f"not proven without #{culprit.number}: rerun it in the next train"},
        )
    return good, builds.get(k - 1) or Built([], [], main)


def names_of(members: list[Member]) -> str:
    return "(" + ", ".join(f"#{m.number}" for m in members) + ")"
