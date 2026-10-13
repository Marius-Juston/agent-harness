"""Session and hand-off workflow: orient, status, pr (worker), integrate (lead).

Roles (docs/specs/multi-agent.md): a *lead* session per machine plans, dispatches and integrates; *workers* each
own one issue in their own worktree and finish with a PR. Only the lead merges, through `integrate`, which re-runs the
gate on the merged tree in a throwaway worktree, never on the worker's say-so.
"""

import hashlib
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path, PurePosixPath

from devkit import backoff, board, checks, config, consolidation
from devkit.gate import free_gb, tree_key
from devkit.paths import git, main_checkout, root, state_dir

# Every path is harness (it can change what the gate or an agent does, and the gate that judges a branch is the
# branch's own code) except plain content under these directories. A PR touching harness needs the lead to read it.
# The content directories are `[tool.devkit] content`. Eval scenarios are harness: they hold the decisions agents are
# graded against.
CONTENT_SUFFIXES = (".md", ".yaml", ".yml", ".txt", ".csv", ".bib", ".png", ".jpg", ".jpeg", ".svg", ".pdf")
AGENT_FILES = {"claude.md", "claude.local.md", "agents.md", "gemini.md", "skill.md"}  # loaded from any directory


def is_harness(path: str) -> bool:
    parts = PurePosixPath(path).parts
    content = (
        path.startswith(tuple(config.get().content))
        and path.lower().endswith(CONTENT_SUFFIXES)
        and not any(p == ".." or p.startswith(".") or p.lower() in AGENT_FILES for p in parts)
    )
    return not content


def changed_paths(base: str, head: str) -> list[str]:
    """Every path the PR changes, from git on the pinned SHAs: no file cap, and a rename lists both of its paths."""
    merge_base = git("merge-base", base, head)
    return git("diff", "--name-only", "--no-renames", merge_base, head).splitlines()


POLLER = "setsid nohup uv run --quiet dev usage-poll >/dev/null 2>&1 &"
FRESH_S = 1800


def quota_reading() -> tuple[int | None, int]:
    """(weekly all-models percent or None when no reading under 30 min old, cap percent from [tool.devkit])."""
    f = state_dir() / "usage.json"
    cap = config.get().quota_cap
    if not f.exists():
        return None, cap
    data = json.loads(f.read_text())
    return (data["pct"] if time.time() - data["t"] < FRESH_S else None), cap


def quota() -> str:
    f = state_dir() / "usage.json"
    cap = quota_reading()[1]
    if not f.exists():
        return f"quota: no reading (start the poller: `{POLLER}`), cap {cap}%"
    data = json.loads(f.read_text())
    age = (time.time() - data["t"]) / 60
    return f"quota: {data['pct']}% of weekly all-models used ({age:.0f} min ago), cap {cap}%"


USAGE_LINE = re.compile(r"all models.*?(\d+)%", re.I)


def usage_percent(text: str) -> int | None:
    """The weekly all-models percent from `/usage` output, or None."""
    for line in text.splitlines():
        if m := USAGE_LINE.search(line):
            return int(m.group(1))
    return None


def write_usage(pct: int, now: float | None = None) -> Path:
    """Atomically record a reading, with the cap, for the spawn guard (a bash hook that cannot read pyproject.toml)."""
    f = state_dir() / "usage.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pct": pct, "t": int(now or time.time()), "cap": config.get().quota_cap}) + "\n")
    tmp.replace(f)
    return f


def usage_poll(interval_s: int = 600, once: bool = False) -> int:
    """Every interval, one cheap `/usage` read (no model call), recorded for `dev status` and the spawn guard.
    One poller per clone: a second one exits at once."""
    import fcntl

    with open(state_dir() / "usage_poll.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("usage-poll already running")
            return 0
        while True:
            try:
                out = subprocess.run(
                    ["claude", "-p", "--no-session-persistence", "/usage"],
                    cwd=tempfile.gettempdir(),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                ).stdout
            except (OSError, subprocess.TimeoutExpired):
                out = ""
            if (pct := usage_percent(out)) is not None:
                write_usage(pct)
            if once:
                return 0 if pct is not None else 1
            time.sleep(interval_s)


def worktrees() -> list[dict]:
    rows = []
    for block in git("worktree", "list", "--porcelain").split("\n\n"):
        path = re.search(r"^worktree (.+)$", block, re.M)
        branch = re.search(r"^branch refs/heads/(.+)$", block, re.M)
        if not path:
            continue
        p = Path(path.group(1))
        b = branch.group(1) if branch else "(detached)"
        ahead = git("rev-list", "--count", f"main..{b}", check=False) if branch and b != "main" else "0"
        dirty = len(git("-C", str(p), "status", "--porcelain", check=False).splitlines()) if p.exists() else 0
        last = git("-C", str(p), "log", "-1", "--format=%cr", check=False) if p.exists() else "?"
        rows.append({"path": str(p), "branch": b, "ahead": int(ahead or 0), "dirty": dirty, "last": last})
    return rows


def gate_holder() -> str:
    lock = state_dir() / "gate.lock"
    if not lock.exists():
        return "idle"
    out = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True, check=False)
    return "idle" if out.returncode == 0 else f"running ({lock.read_text().strip()})"


def status(as_json: bool = False) -> str:
    wts = worktrees()
    pct, cap = quota_reading()
    data = {
        "gate": gate_holder(),
        "mem_gb": round(free_gb(), 1),
        "worktrees": wts,
        "quota": quota(),
        "quota_pct": pct,
        "quota_cap": cap,
    }
    if as_json:
        return json.dumps(data, indent=1)
    out = [f"gate {data['gate']} . {data['mem_gb']} GB free . {data['quota']}"]
    for w in wts:
        out.append(
            f"  {w['branch']:<40} +{w['ahead']} {'*' + str(w['dirty']) if w['dirty'] else ''}  {w['last']}  {w['path']}"
        )
    return "\n".join(out)


def orient() -> str:
    """Session start (also after /clear and compaction): where things are, in at most board.ORIENT_LINES lines."""
    # Board and machine first, so their rows (and the 5 next startable) are always kept; the handoff gets the rest.
    try:  # blockers re-synced here and in integrate, so no scheduled Actions job is needed (#76)
        synced = board.sync()
    except board.BoardError as exc:
        synced = [f"failed ({exc})"]
    try:
        issues, done = board.fetch(with_comments=True, links=board.ALL_LINK_FIELDS), board.closed_numbers()
        active = [i for i in issues if i.status in ("in-progress", "review", "blocked") and i.kind != "epic"]
        rows = [board._line(i, done) for i in active] or ["nothing in progress, review or blocked"]
        nxt = [board._line(i, done) for i in board.startable(issues, done=done)[:5]] or ["  (none)"]
        bad = [f for f in board.lint(issues, done, {}) if not f.warn]
        board_part = [
            "",
            "## Board (GitHub issues; `uv run dev board list` for all)",
            *([f"board sync: {'; '.join(synced)}"[:200]] if synced else []),
            *board.cap(rows, 12, "`uv run dev board list`"),
            "next startable:",
            *nxt,
        ]
        if bad:
            n = len({f.number for f in bad})
            board_part.append(f"board lint: {len(bad)} problem(s) on {n} item(s); `uv run dev board lint`")
    except board.BoardError as exc:
        board_part = ["", f"## Board unavailable: {exc}"]
    try:  # a rare architecture review (#113): one line, only when due
        review = ["", line] if (line := consolidation.orient_line(root())) else []
    except Exception as exc:  # orientation is a convenience: report, never fail
        review = ["", f"consolidation trigger failed: {exc}"[:200]]
    tail = [*review, "", "## This machine", *board.cap(status().splitlines(), 10, "`uv run dev status`")]
    head = []
    name = config.get().handoff
    handoff = root() / name
    room = min(36, board.ORIENT_LINES - len(board_part) - len(tail) - 1)
    if handoff.exists() and room > 0:
        head = [f"## Handoff ({name})", *board.cap(handoff.read_text().splitlines(), room, name)]
    return "\n".join([*head, *board_part, *tail])


def _gate_passed() -> bool:
    key = tree_key(checks.merge_base())
    return key is not None and (state_dir("gate") / f"{key}.json").exists()


def _summary_body(number: int, summary: str | None) -> str:
    """The PR body: a file, or stdin for `-` (subagents may not write report files, so they pipe a heredoc)."""
    if summary is None:
        return f"Refs #{number}\n\n(summary missing)"
    if summary != "-":
        return Path(summary).read_text()
    body = sys.stdin.read()
    if not body.strip():
        raise board.BoardError("--summary - read nothing from stdin: pipe the summary in, e.g. a <<'EOF' heredoc")
    return body


def pr_areas() -> list[str]:
    """Area labels (the area map, [tool.devkit] area_map) for the files this branch changes since it left main."""
    base = checks.merge_base()
    files = git("diff", "--name-only", "--no-renames", base, "HEAD").splitlines() if base else []
    return board.path_areas(files, board.area_globs())


def pr(number: int, summary: str | None, draft: bool, partial: bool = False) -> str:
    """Push the gated branch and open its PR. A body without a `Closes #N` / `Refs #N` first line gets `Closes #N`
    (a complete hand-back: the merge closes the issue), or `Refs #N` with `partial` (the issue returns to ready)."""
    branch = git("branch", "--show-current")
    if branch in ("main", ""):
        raise board.BoardError("open PRs from an issue branch in a worktree, not from main")
    if dirty := git("status", "--porcelain"):
        raise board.BoardError(f"uncommitted files (commit them, or move scratch files to .scratch/):\n{dirty}")
    if not _gate_passed():
        raise board.BoardError("run `uv run dev check` on this tip first: the PR must be gated")
    issue = board.get(number)
    body = _summary_body(number, summary)
    if partial and re.match(r"Closes #\d+", body):
        raise board.BoardError("--partial, but the summary starts with 'Closes #N': write 'Refs #N' or drop --partial")
    if not re.match(r"(Closes|Refs) #\d+", body):
        body = f"{'Refs' if partial else 'Closes'} #{number}\n\n{body}"
    git("push", "-u", "origin", branch)
    args = [
        "pr",
        "create",
        "-R",
        board.repo(),
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        f"{issue.title} (#{number})",
        "--body-file",
        "-",
    ]
    if draft:
        args.append("--draft")
    if area := pr_areas():  # area labels from the changed paths, by the map `board new` reads too (no CI minutes)
        board.missing_labels_created(area)
        args += ["--label", ",".join(area)]
    url = board.gh(*args, input=body).strip()
    board.comment(number, f"PR ready for review: {url}\n\n{body[:1500]}")
    board.set_status(number, "review")
    return url


PR_FIELDS = (
    "headRefName,headRefOid,baseRefName,isCrossRepository,title,body,state,isDraft,"
    "statusCheckRollup,latestReviews,reviewDecision"
)
CHECK_PASSED = {
    "SUCCESS",
    "NEUTRAL",
    "SKIPPED",
}  # CheckRun conclusions that do not block; a StatusContext needs SUCCESS


def refusal(info: dict) -> str | None:
    """Why integrate must not merge this PR at all, or None."""
    if info["baseRefName"] != "main":
        return f"PR targets {info['baseRefName']!r}, not main: integrate only merges into main"
    if info["isCrossRepository"]:
        return "PR comes from a fork: integrate only merges branches of this repository"
    return None


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


def pending_checks(info: dict, expected: list[str]) -> list[str]:
    """Checks on the PR head that are not done: still queued or running, or one of `expected` (`check_key`s the PR
    had before integrate pushed) not registered on it yet. A failed check is done: `unfinished` judges it."""
    runs = latest_checks(info.get("statusCheckRollup") or [])
    out = [
        check_key(c)
        for c in runs
        if (
            (c.get("state") or "PENDING") in {"PENDING", "EXPECTED"}
            if c.get("__typename") == "StatusContext"
            else c.get("status") != "COMPLETED"
        )
    ]
    seen = {check_key(c) for c in runs}
    return out + [f"{k} (not started)" for k in expected if k not in seen]


# Waiting for the checks on a head integrate pushed (#72): polls back off with full jitter (devkit.backoff) from a
# floor, so a lead's integrate does not hammer the API and parallel leads do not poll in step.
CHECKS_FLOOR_S = 5.0  # every poll waits at least this
CHECKS_BASE_S = 10.0  # the first poll's jitter window
CHECKS_CAP_S = 60.0  # no jitter window grows past this
CHECKS_RNG = random.Random()  # noqa: S311 - poll jitter, not a secret
CHECKS_CLOCK = time.monotonic


def await_checks(pr_number: int, tip: str, expected: list[str], timeout_s: float) -> tuple[str, list[str]]:
    """Poll the PR until every check on `tip` is complete, at most `timeout_s`. Returns ("ready", []), ("moved",
    [new head]) when the head is no longer `tip`, or ("timeout", the checks still pending)."""
    deadline = CHECKS_CLOCK() + timeout_s
    for attempt in itertools.count():
        info = json.loads(
            board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid,statusCheckRollup")
        )
        if info["headRefOid"] != tip:
            return "moved", [info["headRefOid"]]
        if not (pending := pending_checks(info, expected)):
            return "ready", []
        if (left := deadline - CHECKS_CLOCK()) <= 0:
            return "timeout", pending
        time.sleep(min(left, backoff.wait(attempt, 0, CHECKS_RNG, CHECKS_BASE_S, CHECKS_CAP_S, CHECKS_FLOOR_S)))
    raise AssertionError("unreachable")


def _waiting_file(pr_number: int) -> Path:
    """Where a timed-out wait records the tip it pushed and the checks expected on it, so a rerun resumes there."""
    return state_dir("integrate") / f"pr-{pr_number}.waiting.json"


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


def actions_enabled() -> bool:
    try:
        return bool(json.loads(board.gh("api", f"repos/{board.repo()}/actions/permissions")).get("enabled", True))
    except board.BoardError:
        return True  # unreadable: the wait for a check that never queues decides


def never_ran(c: dict, jobs: dict[str, bool]) -> bool:
    """A CheckRun that ended unsuccessfully without running one step: Actions did not start its job (a billing block
    or spending limit; the message wording varies, the empty step list does not). `jobs` caches the answer per job."""
    if c.get("__typename") == "StatusContext" or c.get("status") != "COMPLETED" or c.get("conclusion") in CHECK_PASSED:
        return False
    if not (m := JOB_URL.search(c.get("detailsUrl") or "")):
        return False
    if m[1] not in jobs:
        try:
            jobs[m[1]] = not json.loads(board.gh("api", f"repos/{board.repo()}/actions/jobs/{m[1]}")).get("steps")
        except board.BoardError:
            jobs[m[1]] = False  # unknown: a real failure
    return jobs[m[1]]


STANDING = ("failed", "running", "queued", "never-ran", "passed")  # the first one a context's checks show wins


def standing(rollup: list[dict], contexts: list[str], enabled: bool, jobs: dict[str, bool]) -> dict[str, str]:
    """Each required context on a head: failed (it ran and failed), running, queued (not registered yet, or waiting
    for a runner), never-ran (Actions did not start it, or Actions are disabled) or passed."""
    out = {}
    for ctx in contexts:
        seen = []
        for c in latest_checks(rollup):
            if (c.get("name") or c.get("context")) != ctx:
                continue
            if c.get("__typename") == "StatusContext":
                state = c.get("state") or "PENDING"
                seen.append(
                    "passed" if state == "SUCCESS" else "queued" if state in {"PENDING", "EXPECTED"} else "failed"
                )
            elif c.get("status") != "COMPLETED":
                seen.append("running" if c.get("status") == "IN_PROGRESS" else "queued")
            else:
                seen.append(
                    "passed" if c.get("conclusion") in CHECK_PASSED else "never-ran" if never_ran(c, jobs) else "failed"
                )
        seen = ["never-ran" if s == "queued" and not enabled else s for s in seen or ["queued"]]
        out[ctx] = min(seen, key=STANDING.index)
    return out


def await_actions(pr_number: int, tip: str, contexts: list[str]) -> tuple[str, dict[str, str]]:
    """Poll the required contexts on `tip` until none runs, and none is queued unless Actions are disabled or
    `local_ci_wait_s` passed (a check that never queues: Actions cannot run). ("ready", standing) with what is still
    queued read as never-ran, ("moved", {}) when the head is no longer `tip`, ("timeout", standing) when a check still
    runs after `checks_wait_s`."""
    cfg = config.get()
    enabled, jobs, start = actions_enabled(), {}, CHECKS_CLOCK()
    for attempt in itertools.count():
        info = json.loads(
            board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid,statusCheckRollup")
        )
        if info["headRefOid"] != tip:
            return "moved", {}
        now = standing(info.get("statusCheckRollup") or [], contexts, enabled, jobs)
        waited = CHECKS_CLOCK() - start
        if "running" not in now.values() and ("queued" not in now.values() or waited >= cfg.local_ci_wait_s):
            return "ready", {k: "never-ran" if v == "queued" else v for k, v in now.items()}
        if waited >= cfg.checks_wait_s:
            return "timeout", now
        time.sleep(backoff.wait(attempt, 0, CHECKS_RNG, CHECKS_BASE_S, CHECKS_CAP_S, CHECKS_FLOOR_S))
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


def local_ci_run(tree: Path, contexts: list[str], log: Path) -> bool:
    """The gate, then each required context's own stand-in commands, in the merged tree; all output goes to `log`
    (printed after). True when every one passed."""
    commands = config.get().local_ci
    with log.open("w") as out:
        out.write(f"## gate: uv run dev check --no-cache in {tree}\n")
        out.flush()
        gate = ["uv", "run", "--quiet", "dev", "check", "--no-cache"]
        ok = subprocess.run(gate, cwd=tree, stdout=out, stderr=subprocess.STDOUT, check=False).returncode == 0
        for argv in [a for c in contexts for a in commands.get(c, [])] if ok else []:
            with tempfile.TemporaryDirectory(prefix="local-ci-") as tmpdir:
                cmd = [a.replace("{tmpdir}", tmpdir) for a in argv]
                out.write(f"## {' '.join(cmd)}\n")
                out.flush()
                if subprocess.run(cmd, cwd=tree, stdout=out, stderr=subprocess.STDOUT, check=False).returncode:
                    ok = False
                    break
    print(log.read_text(errors="replace"), end="", flush=True)
    return ok


def _stand_in(pr_number: int, tip: str, contexts: list[str], log: Path, todo: list[str], update: dict) -> int | None:
    """`--local-ci`, after the local run passed on the merged tree whose commit is `tip`: wait for Actions on `tip`,
    then post a success status for each required context Actions did not run, with the log's hash, and say so on the
    PR. None to go on to the merge, else the exit code after the result line."""
    print(f"waiting for Actions on {tip[:10]} ({', '.join(contexts)}) ...", flush=True)
    state, now = await_actions(pr_number, tip, contexts)
    if state == "moved":
        print(f"RESULT: stopped (the PR head moved away from {tip[:10]} while waiting for Actions); rerun integrate")
        return result(pr_number, 1, "stopped", reason="the PR head moved while waiting for Actions", **update)
    if state == "timeout":
        running = [c for c, s in now.items() if s in ("running", "queued")]
        print(f"RESULT: waiting (Actions still run {', '.join(running)} on {tip[:10]}); rerun integrate")
        return result(pr_number, 1, "waiting", pending=running, **({"head": tip} | update))
    if stood := [c for c, s in now.items() if s == "never-ran"]:
        digest = hashlib.sha256(log.read_bytes()).hexdigest()
        note = f"dev integrate --local-ci: passed on this commit; log sha256 {digest[:16]}"
        for ctx in stood:
            board.gh(
                "api", "-X", "POST", f"repos/{board.repo()}/statuses/{tip}",
                "-f", "state=success", "-f", f"context={ctx}", "-f", f"description={note}",
            )  # fmt: skip
        ran = "; ".join(
            f"`{c}`: " + (" && ".join(" ".join(a) for a in config.get().local_ci[c]) or "the gate") for c in stood
        )
        body = (
            f"CI ran locally (`dev integrate --local-ci`): GitHub Actions did not run {', '.join(stood)} on {tip} "
            "(disabled, billing-blocked, or not queued in time). On that exact merged tree `uv run dev check "
            f"--no-cache` passed, then each check's own stand-in ({ran}). Log sha256 `{digest}`, kept at `{log}` on "
            f"{os.uname().nodename}. Posted {', '.join(stood)} as commit statuses on {tip[:10]}."
        )
        board.gh("pr", "comment", str(pr_number), "-R", board.repo(), "--body", body)
        print(f"posted local CI statuses {', '.join(stood)} on {tip[:10]} (log sha256 {digest[:16]})")
    if arrived := [t for t in _pr_state(pr_number, frozenset(stood))[1] if t not in todo]:
        print(f"RESULT: refused: unfinished feedback on {tip[:10]}:\n  " + "\n  ".join(arrived))
        print(f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, then rerun integrate.")
        return result(pr_number, 1, "refused", reason="feedback on the gated head", feedback=arrived, **update)
    return None


def _add_worktree(path: Path, commit: str, cwd: Path | None = None) -> None:
    """A fresh detached worktree at commit. Prunes first: a registered worktree whose folder is gone blocks `add`."""
    if path.exists():
        git("worktree", "remove", "--force", str(path), cwd=cwd, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", cwd=cwd)
    git("worktree", "add", "--detach", str(path), commit, cwd=cwd)


def _remote_shas(pr_number: int) -> tuple[str, str]:
    """(PR head, main) as GitHub has them now."""
    head = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid"))
    main = git("ls-remote", "origin", "refs/heads/main").split()[0]
    return head["headRefOid"], main


def _merge_if_unchanged(
    pr_number: int, head: str, main: str, dry_run: bool = False, review: int | None = None
) -> str | None:
    """Merge only what was gated: the head is pinned by --match-head-commit, main is re-read just before. Returns the
    reason for not merging, or None. `review`: the merge commit carries `Consolidation-Review: #<review>`, the
    first-parent trailer that resets the consolidation trigger (only the lead writes it, through integrate)."""
    now_head, now_main = _remote_shas(pr_number)
    if now_head != head:
        return f"the PR head moved during the gate ({head[:10]} -> {now_head[:10]}): rerun integrate"
    if now_main != main:
        return f"main moved during the gate ({main[:10]} -> {now_main[:10]}): rerun integrate"
    if not dry_run:
        trailer = ["--body", f"{consolidation.REVIEW_TRAILER} #{review}"] if review else []
        board.gh(
            "pr", "merge", str(pr_number), "-R", board.repo(), "--merge", "--match-head-commit", head, "--delete-branch",
            *trailer,
        )  # fmt: skip
    return None


def _pr_state(pr_number: int, ignore: frozenset[str] = frozenset()) -> tuple[dict, list[str]]:
    """The PR as GitHub has it now, and its unfinished feedback."""
    info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
    return info, unfinished(info, board.pr_feedback(pr_number), ignore)


# Generated files never conflict: a merge keeps our side (`.gitattributes` names this driver for them, `dev setup`
# and integrate define it) and `dev docs` regenerates them in the merged tree before the gate.
GENERATED_DRIVER = "devkit-generated"
GENERATED_MERGE = (
    (f"merge.{GENERATED_DRIVER}.name", "generated file: keep one side, `uv run dev docs` regenerates it"),
    (f"merge.{GENERATED_DRIVER}.driver", "true"),
)
REGENERATED = "Regenerate generated files after merging main (dev integrate)"
PUSH_WAIT_S = 1.0  # between reads of the PR head after integrate pushed an update
PUSH_TRIES = 15


ATTR_SOURCE_GIT = (2, 40)  # `git --attr-source`


def git_version() -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)", git("--version"))
    return tuple(int(x) for x in m.groups()) if m else (0, 0)


def merge_main(tree: Path, main: str) -> list[str]:
    """Merge main into the PR head checked out at `tree`, as GitHub's "Update branch" would, with the generated-file
    driver. Merge rules come from main's `.gitattributes` (`--attr-source`): an old branch must not bring back a
    union rule that interleaves rewrites. Returns the paths that really conflict (the merge is then aborted), or []
    when it merged or was current. Raises RuntimeError when git is too old or the merge failed without a conflict."""
    if (have := git_version()) < ATTR_SOURCE_GIT:
        need, got = ".".join(map(str, ATTR_SOURCE_GIT)), ".".join(map(str, have))
        raise RuntimeError(f"integrate needs git >= {need} (--attr-source); this is git {got}")
    config = [arg for key, value in GENERATED_MERGE for arg in ("-c", f"{key}={value}")]
    merge = subprocess.run(
        ["git", f"--attr-source={main}", *config, "merge", "--no-ff", "--no-edit", main],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    if merge.returncode == 0:
        return []
    paths = git("diff", "--name-only", "--diff-filter=U", cwd=tree, check=False).splitlines()
    git("merge", "--abort", cwd=tree, check=False)
    if not paths:
        raise RuntimeError(
            f"git merge of main failed without a conflict: {(merge.stdout + merge.stderr).strip()[-300:]}"
        )
    return paths


def regenerate(tree: Path) -> bool:
    """`dev docs` in the merged tree, committing what it changed. True when it committed."""
    docs = subprocess.run(
        ["uv", "run", "--quiet", "dev", "docs"], cwd=tree, capture_output=True, text=True, check=False
    )
    if docs.returncode != 0:
        raise RuntimeError(f"`dev docs` failed in the merged tree: {(docs.stdout + docs.stderr).strip()[-500:]}")
    if not git("status", "--porcelain", cwd=tree):
        return False
    git("add", "-A", cwd=tree)
    git("commit", "--no-verify", "-q", "-m", REGENERATED, cwd=tree)  # the gate runs on this tree next
    return True


def send_back(pr_number: int, info: dict, main: str, paths: list[str], dry_run: bool = False) -> list[str]:
    """A real conflict with main: comment the paths on the PR and block each issue it links ('rebase on main'),
    naming the worker to resume. Returns the workers (agent@host of each live claim)."""
    branch = info.get("headRefName", "")
    closes, own = board.linked(info.get("body") or "", branch)
    issues = closes + own
    workers = list(dict.fromkeys(f"{c.agent}@{c.host}" for n in issues if (c := board.get(n).claim())))
    who = ", ".join(workers) or "the worker that opened it"
    files = "\n".join(f"- `{p}`" for p in paths)
    note = (
        f"`dev integrate`: PR #{pr_number} conflicts with main ({main[:10]}) in:\n{files}\n\n"
        f"Rebase on main: resume {who} on branch `{branch}` to merge `origin/main`, resolve these files, run "
        "`uv run dev docs` and `uv run dev check`, push, and hand back. Then the lead reruns integrate."
    )
    reason = f"rebase on main: PR #{pr_number} conflicts with main in {', '.join(paths)}; resume {who} on `{branch}`"
    if dry_run:
        print(f"would comment on PR #{pr_number} and block {', '.join(f'#{n}' for n in issues) or 'no issue'}")
        return workers
    board.gh("pr", "comment", str(pr_number), "-R", board.repo(), "--body", note)
    for n in issues:
        board.block(n, reason)
    return workers


def _head_is(pr_number: int, tip: str) -> bool:
    """After a push, wait until GitHub reports the PR head at `tip` (it lags the push by a moment)."""
    for i in range(PUSH_TRIES):
        if _remote_shas(pr_number)[0] == tip:
            return True
        if i + 1 < PUSH_TRIES:
            time.sleep(PUSH_WAIT_S)
    return False


def result(pr_number: int, code: int, outcome: str, **fields: object) -> int:
    """The last line integrate prints, for the lead to branch on: `RESULT-JSON: {"pr", "outcome", "exit", ...}`.
    Outcomes: merged, would-merge, conflict, gate-failed, harness-review, refused, stopped, not-open, waiting."""
    print("RESULT-JSON: " + json.dumps({"pr": pr_number, "outcome": outcome, "exit": code, **fields}))
    return code


def _wait_on_tip(pr_number: int, tip: str, expected: list[str], update: dict) -> int | None:
    """`await_checks` on a head integrate pushed. None when every check on it is complete; else the exit code, after
    the result line: `stopped` when the head moved, `waiting` (recorded for a rerun) when the time ran out."""
    timeout_s = config.get().checks_wait_s
    print(f"waiting up to {timeout_s} s for the checks on {tip[:10]} ...", flush=True)
    state, detail = await_checks(pr_number, tip, expected, timeout_s)
    if state == "moved":
        _waiting_file(pr_number).unlink(missing_ok=True)
        print(f"RESULT: stopped (the PR head moved to {detail[0][:10]} while waiting for checks); rerun integrate")
        return result(pr_number, 1, "stopped", reason="the PR head moved while waiting for checks", **update)
    if state == "timeout":
        _waiting_file(pr_number).write_text(json.dumps({"tip": tip, "expected": expected}) + "\n")
        print(f"RESULT: waiting (checks on {tip[:10]} still pending after {timeout_s} s: {', '.join(detail)}); rerun")
        return result(pr_number, 1, "waiting", pending=detail, **({"head": tip} | update))
    return None


def integrate(
    pr_number: int,
    harness_ok: bool,
    dry_run: bool,
    override: str | None = None,
    local_ci: bool = False,
    review: int | None = None,
) -> int:
    """`_integrate`, but every exit ends with a `result` line: a crash (even after a push) is `stopped` with its reason."""
    try:
        return _integrate(pr_number, harness_ok, dry_run, override, local_ci, review)
    except Exception as exc:  # the lead branches on the last line, whatever failed
        traceback.print_exc()
        print(f"RESULT: stopped (integrate failed: {exc}); check the PR branch and the board, then rerun")
        return result(pr_number, 1, "stopped", reason=f"integrate failed: {type(exc).__name__}: {exc}")


def _integrate(
    pr_number: int,
    harness_ok: bool,
    dry_run: bool,
    override: str | None = None,
    local_ci: bool = False,
    review: int | None = None,
) -> int:
    """Lead only: merge a PR after re-gating the merged tree in a throwaway worktree: main merged into pull/N/head,
    generated files regenerated. When that tree differs from the head (the PR is behind main, or its generated files
    are stale) it is pushed to the PR branch before the merge; a real conflict is sent back (`send_back`).
    Unfinished feedback (checks, requested changes, open threads) refuses it unless `override` gives a reason,
    which is logged on the PR just before the merge. Feedback is read again after the gate: what arrived during it
    refuses the merge, override or not. After a push it waits (bounded, `checks_wait_s`) for every check on the new
    head, whatever the branch protection, and reads feedback once more; a timeout is `waiting`, and a rerun on that
    head resumes the wait instead of refusing its pending checks. With `local_ci` (no Actions minutes), required checks
    Actions does not run are left to the local run on the merged tree (`_stand_in`), which posts them as commit statuses
    only after it passed. Issues close only after the merge; the last line printed is `result`."""
    info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
    if info["state"] != "OPEN" or info["isDraft"]:
        print(f"PR #{pr_number} is {info['state']}{' (draft)' if info['isDraft'] else ''}: nothing to integrate")
        return result(pr_number, 1, "not-open")
    if reason := refusal(info):
        print(f"RESULT: refused: {reason}")
        return result(pr_number, 1, "refused", reason=reason)
    if override is not None and not override.strip():
        print("RESULT: refused: --override needs a reason")
        return result(pr_number, 1, "refused", reason="--override needs a reason")
    contexts, ignore = [], frozenset()
    if local_ci:
        contexts, bound = required_contexts()
        if why := local_ci_refusal(contexts, bound):
            print(f"RESULT: refused: --local-ci: {why}")
            return result(pr_number, 1, "refused", reason=f"--local-ci: {why}")
        now = standing(info.get("statusCheckRollup") or [], contexts, actions_enabled(), {})
        ignore = frozenset(c for c, s in now.items() if s != "failed")  # judged on the gated head, after the run
    if (waiting := _waiting_file(pr_number)).exists():
        mark = json.loads(waiting.read_text())
        if (
            mark.get("tip") == info["headRefOid"] and not local_ci
        ):  # a rerun after `waiting`: resume on the head integrate pushed
            if (code := _wait_on_tip(pr_number, mark["tip"], mark.get("expected") or [], {})) is not None:
                return code
            info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
        waiting.unlink(missing_ok=True)
    if todo := unfinished(info, board.pr_feedback(pr_number), ignore):
        if override is None:
            print(f"RESULT: refused: PR #{pr_number} has unfinished feedback:\n  " + "\n  ".join(todo))
            print(
                f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, resolve the threads and "
                "wait for the checks; or rerun with --override '<reason>' (logged on the PR)."
            )
            return result(pr_number, 1, "refused", reason="unfinished feedback", feedback=todo)
        print(f"overriding unfinished feedback ({override}):\n  " + "\n  ".join(todo))
    head_ref, main_ref = f"refs/devkit/integrate/pr-{pr_number}", f"refs/devkit/integrate/main-{pr_number}"
    try:
        git("fetch", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{head_ref}", f"+refs/heads/main:{main_ref}")
        head, main = git("rev-parse", head_ref), git("rev-parse", main_ref)
        touched = changed_paths(main, head)
    except RuntimeError as exc:
        print(f"RESULT: stopped (cannot fetch or diff PR #{pr_number} against main): {exc}")
        return result(pr_number, 1, "stopped", reason=f"cannot fetch or diff: {exc}")
    if head != info["headRefOid"]:
        print(f"RESULT: stopped (the PR head moved while fetching: {info['headRefOid'][:10]} -> {head[:10]}); rerun")
        return result(pr_number, 1, "stopped", reason="the PR head moved while fetching")
    harness = [f for f in touched if is_harness(f)]
    if harness and not harness_ok:
        print("PR changes the harness (rules agents are judged by):\n  " + "\n  ".join(harness))
        print(f"Read that diff yourself (`git diff {main[:10]}...{head[:10]}`), then rerun with --harness-ok.")
        return result(pr_number, 2, "harness-review", harness=harness)
    tmp = state_dir("integrate") / f"pr-{pr_number}"
    _add_worktree(tmp, head)
    try:
        try:
            if conflicts := merge_main(tmp, main):
                workers = send_back(pr_number, info, main, conflicts, dry_run=dry_run)
                who = ", ".join(workers) or "its worker"
                print(f"RESULT: conflict with main in {', '.join(conflicts)}; sent back, resume {who}")
                return result(pr_number, 1, "conflict", conflicts=conflicts, resume=workers, branch=info["headRefName"])
            regenerated = regenerate(tmp)
        except RuntimeError as exc:
            print(f"RESULT: stopped ({exc})")
            return result(pr_number, 1, "stopped", reason=str(exc))
        tip = git("rev-parse", "HEAD", cwd=tmp)
        print(
            f"gating PR #{pr_number} ({head[:10]}) merged with main {main[:10]} as {tip[:10]} in {tmp} ...", flush=True
        )
        run_log = state_dir("integrate") / f"pr-{pr_number}-{tip[:12]}.log"
        if local_ci:
            passed = local_ci_run(tmp, contexts, run_log)
        else:
            gate = ["uv", "run", "--quiet", "dev", "check", "--no-cache"]
            passed = subprocess.run(gate, cwd=tmp, check=False).returncode == 0
        if not passed:
            print(f"RESULT: stopped (merged tree fails the gate); comment on PR #{pr_number} and send it back")
            return result(pr_number, 1, "gate-failed")
    finally:
        git("worktree", "remove", "--force", str(tmp), check=False)
        git("update-ref", "-d", head_ref, check=False)
        git("update-ref", "-d", main_ref, check=False)
    state, now = _pr_state(pr_number, ignore)
    if arrived := [t for t in now if t not in todo]:
        print(f"RESULT: refused: feedback arrived on PR #{pr_number} during the gate:\n  " + "\n  ".join(arrived))
        print(f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, then rerun integrate.")
        return result(pr_number, 1, "refused", reason="feedback arrived during the gate", feedback=arrived)
    if todo:
        log = f"`dev integrate --override`: {override}\n\nOverriding:\n" + "\n".join(f"- {t}" for t in todo)
        if dry_run:
            print(f"would log on PR #{pr_number}:\n{log}")
        else:
            board.gh("pr", "comment", str(pr_number), "-R", board.repo(), "--body", log)
    update = {"updated": tip != head, "regenerated": regenerated, "head": tip}
    if tip != head:  # behind main, or stale generated files: the gated tree goes onto the PR branch first
        if dry_run:
            print(f"would push {tip[:10]} (main merged in, generated files regenerated) to {info['headRefName']}")
        else:
            try:  # a fast-forward of the head: refused if the worker pushed meanwhile
                git("push", "origin", f"{tip}:refs/heads/{info['headRefName']}")
            except RuntimeError as exc:
                print(f"RESULT: stopped (cannot update the PR branch; did the worker push? rerun): {exc}")
                return result(pr_number, 1, "stopped", reason=f"cannot update the PR branch: {exc}", **update)
            if not _head_is(pr_number, tip):
                print(f"RESULT: stopped (GitHub does not show the pushed head {tip[:10]} yet); rerun integrate")
                return result(pr_number, 1, "stopped", reason="pushed head not visible on GitHub", **update)
            expected = [check_key(c) for c in latest_checks(state.get("statusCheckRollup") or [])]
            if not local_ci and (code := _wait_on_tip(pr_number, tip, expected, update)) is not None:
                return code
            if not local_ci and (arrived := [t for t in _pr_state(pr_number)[1] if t not in todo]):
                print(f"RESULT: refused: unfinished feedback on the pushed head {tip[:10]}:\n  " + "\n  ".join(arrived))
                print(
                    f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, then rerun integrate."
                )
                return result(pr_number, 1, "refused", reason="feedback on the pushed head", feedback=arrived, **update)
    if local_ci and dry_run:
        print(f"would wait for Actions on {tip[:10]} and post what it does not run as statuses: {', '.join(contexts)}")
    elif local_ci and (code := _stand_in(pr_number, tip, contexts, run_log, todo, update)) is not None:
        return code
    try:
        reason = _merge_if_unchanged(pr_number, head if dry_run else tip, main, dry_run=dry_run, review=review)
    except board.BoardError as exc:  # e.g. branch protection waiting for checks on the update just pushed
        print(f"RESULT: stopped (GitHub refused the merge: {exc}); rerun integrate once its checks pass")
        return result(pr_number, 1, "stopped", reason=f"GitHub refused the merge: {exc}", **update)
    if reason:
        print(f"RESULT: stopped ({reason})")
        return result(pr_number, 1, "stopped", reason=reason, **update)
    if dry_run:
        print("RESULT: would merge (dry run)")
        return result(pr_number, 0, "would-merge", **update)
    try:  # issues close only now, after the merge
        moved = board.merged_pr(pr_number, info["body"] or "", info.get("headRefName", ""), info["baseRefName"])
    except board.BoardError as exc:
        print(f"RESULT: merged PR #{pr_number}, but the board update failed ({exc}): move its issue by hand")
        return result(pr_number, 0, "merged", moved=[], board_error=str(exc), **update)
    for w in worktrees():
        if w["branch"] == info["headRefName"] and w["dirty"] == 0:
            git("worktree", "remove", w["path"], check=False)
    if not git("-C", str(main_checkout()), "status", "--porcelain", "--untracked-files=no"):
        git("-C", str(main_checkout()), "pull", "--ff-only", check=False)
    print(f"RESULT: merged PR #{pr_number}" + (f"; {', '.join(moved)}" if moved else ""))
    return result(pr_number, 0, "merged", moved=moved, **update)


def heartbeat(number: int, message: str) -> None:
    board.comment(number, f"heartbeat ({time.strftime('%Y-%m-%d %H:%M')}): {message}")
