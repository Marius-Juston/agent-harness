"""`uv run dev autopilot`: one deterministic lead iteration (#499), so a lead on /loop reads a few lines per tick.

It stops at the quota cap; syncs the board; reads every open PR into main; integrates those integrate would take on
its own (checks complete and green, no open feedback, required reviews in place, no harness file unless
`autopilot_trust_review` and a merge verdict on the current head clear it) through `workflow.integrate`, or
`train.train` for two or more, never with `--override`; then prints only
what needs a model or the owner: an integrate that did not merge (and stops there), harness PRs waiting for a
reviewer or the lead's read, PRs to resume, and the next disjoint wave to dispatch. Integrate stays the judge: the
filter here only decides which PRs are worth calling it on, so nothing it would refuse is merged. Its output goes to
a log under the state dir; at most LINES lines reach the lead.
"""

import contextlib
import io
import json
import re

from devkit import board, ci_status, config, quota, train, workflow

LINES = 15
WAVE = 3  # startable items offered per tick
MERGED = ("merged", "would-merge")


class Pr:
    """An open PR into main, as `gh pr list` has it, with its feedback and (once fetched) its changed paths."""

    def __init__(self, info: dict, feedback: dict) -> None:
        self.info, self.feedback = info, feedback
        self.number: int = info["number"]
        self.head: str = info["headRefOid"]
        self.todo = ci_status.unfinished(info, feedback)
        self.pending = ci_status.only_pending(info, feedback)
        self.touched: list[str] = []


LIST_FIELDS = "number,headRefName,headRefOid,baseRefName,isCrossRepository,isDraft,statusCheckRollup,latestReviews,"
LIST_FIELDS += "reviewDecision"


def open_prs() -> list[dict]:
    """Open same-repository PRs into main, oldest first."""
    found = json.loads(
        board.gh("pr", "list", "-R", board.repo(), "--state", "open", "--base", "main", "--limit", "100",
                 "--json", LIST_FIELDS) or "[]"
    )  # fmt: skip
    return sorted((p for p in found if not p.get("isCrossRepository")), key=lambda p: p["number"])


def fetch_paths(prs: list[Pr]) -> None:
    """Each PR's changed paths against main, from git on one fetch (no file cap, as integrate reads them)."""
    if not prs:
        return
    refs = {p.number: f"refs/devkit/autopilot/pr-{p.number}" for p in prs}
    main = "refs/devkit/autopilot/main"
    try:
        workflow.git("fetch", "--no-tags", "origin", f"+refs/heads/main:{main}",
                     *(f"+refs/pull/{n}/head:{r}" for n, r in refs.items()))  # fmt: skip
        for p in prs:
            p.touched = workflow.changed_paths(main, refs[p.number])
    finally:
        for ref in (main, *refs.values()):
            workflow.git("update-ref", "-d", ref, check=False)


def verdict(p: Pr, trusted: set[str]) -> tuple[str, str] | None:
    """(word, sha) of the latest Verdict review by a `trusted` login, or None."""
    found = [
        (r.get("submittedAt") or "", v[1], v[3])
        for r in p.feedback.get("reviews") or []
        if board.login(r) in trusted and (v := board.verdict_of(r.get("body") or ""))
    ]
    return max(found, key=lambda f: f[0])[1:] if found else None


def trusted_merge(p: Pr, trusted: set[str]) -> bool:
    """The latest trusted verdict says merge on the PR's current head (an earlier head's verdict does not count)."""
    v = verdict(p, trusted)
    return v is not None and v[0] == "merge" and p.head.lower().startswith(v[1].lower())


def review_line(p: Pr, trusted: set[str]) -> str:
    """What a harness PR waits for: a reviewer, a re-review of a newer head, the worker, or the lead's read."""
    v = verdict(p, trusted)
    if v is None:
        return f"review #{p.number} (harness): no verdict; spawn harness-ops:reviewer"
    word, sha = v
    if not p.head.lower().startswith(sha.lower()):
        return f"review #{p.number} (harness): verdict {word} on {sha[:7]}, head is {p.head[:7]}; re-review"
    if word != "merge":
        return f"resume #{p.number} (harness): verdict {word}; uv run dev board feedback {p.number}"
    return f"lead #{p.number} (harness): verdict merge; read its diff, then dev integrate {p.number} --harness-ok"


def outcomes(text: str, numbers: list[int]) -> tuple[dict[int, dict], list[int]]:
    """(PR -> its result fields, PRs sent back for conflicting with the new main), from the last RESULT-JSON line
    integrate or the train printed. A PR the line does not name is `stopped`."""
    lines = [ln for ln in text.splitlines() if ln.startswith("RESULT-JSON: ")]
    data = json.loads(lines[-1].removeprefix("RESULT-JSON: ")) if lines else {}
    found = {int(n): f for n, f in data["members"].items()} if "members" in data else {}
    if "pr" in data:
        found[data["pr"]] = data
    missing = {"outcome": "stopped", "reason": "integrate printed no result for it"}
    return {n: found.get(n) or missing for n in numbers}, list(data.get("sent_back") or [])


def land(numbers: list[int], cleared: dict[int, str], dry_run: bool) -> tuple[int, dict[int, dict], list[int]]:
    """Integrate one PR, or train several, as `dev integrate` does, never with --override; `cleared` (PR -> head) is
    --harness-ok for that head only, so a push after the verdict is not cleared. Their output goes to a log;
    returns the exit code and `outcomes`."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        if len(numbers) == 1:
            code = workflow.integrate(numbers[0], cleared.get(numbers[0], False), dry_run)
        else:
            code = train.train(numbers, cleared, dry_run)
    log = workflow.state_dir("autopilot") / "last.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(buf.getvalue())
    return code, *outcomes(buf.getvalue(), numbers)


def landed_line(n: int, f: dict) -> str:
    moved = "; ".join(f.get("moved") or [])
    return f"{f['outcome']} #{n}" + (f": {moved}" if moved else "")


def failed_line(n: int, f: dict) -> str:
    why = re.sub(r"\s+", " ", str(f.get("reason") or f.get("fix") or ""))[:100]
    return f"STOP integrate #{n} {f.get('outcome')}" + (f": {why}" if why else "") + f"; dev integrate {n} --why"


def dispatch_lines(issues: list[board.Issue]) -> list[str]:
    done = board.resolved(issues)
    return [f"dispatch #{i.number} {i.priority} {i.kind}: {i.title[:80]}" for i in board.wave(issues, WAVE, done)]


def run(dry_run: bool = False) -> int:
    """One iteration; prints at most LINES lines, the last `AUTOPILOT: idle|continue|stop (...)`. 0 unless it
    stopped: the quota cap, an integrate that did not merge, an owner decision, or GitHub or git failing."""
    try:
        return _run(dry_run)
    except (board.BoardError, RuntimeError, OSError, ValueError) as exc:  # gh, git or a reply it cannot parse
        print(f"AUTOPILOT: stop (cannot read GitHub or the board: {' '.join(str(exc).split())[:150]})")
        return 1


def _run(dry_run: bool) -> int:
    pct, cap = quota.quota_reading()
    if pct is not None and pct >= cap:
        print(f"AUTOPILOT: stop (quota {pct}% at or over the cap {cap}%): start nothing; commit, update HANDOFF")
        return 1
    synced = [] if dry_run else board.sync()
    raw = open_prs()
    drafts = [p for p in raw if p.get("isDraft")]
    prs = [Pr(p, board.pr_feedback(p["number"])) for p in raw if not p.get("isDraft")]
    pending = [p for p in prs if p.pending]
    settled = [p for p in prs if not p.todo]
    fetch_paths(settled)
    rules = config.get().required_reviews
    trusted = {board.gh_user()} - {""}
    trust = config.get().autopilot_trust_review
    ready: list[int] = []
    cleared: dict[int, str] = {}
    body: list[str] = []
    stop = ""
    for p in settled:
        harness = [f for f in p.touched if workflow.is_harness(f)]
        if harness and not (trust and trusted_merge(p, trusted)):
            body.append(review_line(p, trusted))
        elif rules and (why := workflow.review_problem(p.touched, p.feedback["reviews"], p.head, trusted, rules,
                                                       lambda sha, h=p.head: workflow._is_ancestor(sha, h))):  # fmt: skip
            body.append(f"review #{p.number}: {why[:100]}")
        else:
            ready.append(p.number)
            if harness:
                cleared[p.number] = p.head
    sent: list[int] = []
    if ready:
        code, got, sent = land(ready, cleared, dry_run)
        landed = [landed_line(n, f) for n, f in got.items() if f["outcome"] in MERGED]
        failed = [failed_line(n, f) for n, f in got.items() if f["outcome"] not in MERGED]
        body = [*failed, *landed, *body]
        if any(f.get("fix") == "needs owner" for f in got.values()):
            stop = "owner decision: a fix loop reached its cap"
        elif code or failed:
            stop = "an integrate did not merge"
    body += [f"resume #{p.number}: {p.todo[0]}; dev board feedback {p.number}" for p in prs if p.todo and not p.pending]
    body += [f"resume #{n}: conflicts with the new main" for n in sent]
    if not stop:
        body += dispatch_lines(board.fetch())
    head = (
        f"autopilot{' (dry run)' if dry_run else ''}: quota {pct if pct is not None else '?'}%/{cap}% . "
        f"{len(raw)} open PRs ({len(drafts)} draft, {len(pending)} checks running)"
        + (f" . board sync: {len(synced)} moved" if synced else "")
    )
    state = f"stop ({stop})" if stop else ("continue" if body else "idle")
    print("\n".join([head, *board.cap(body, LINES - 2, "rerun after acting on these"), f"AUTOPILOT: {state}"]))
    return 1 if stop else 0
