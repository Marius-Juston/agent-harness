"""Session and hand-off workflow: orient, status, pr (worker), integrate (lead).

Roles (docs/specs/multi-agent.md): a *lead* session per machine plans, dispatches and integrates; *workers* each
own one issue in their own worktree and finish with a PR. Only the lead merges, through `integrate`, which re-runs the
gate on the merged tree in a throwaway worktree, never on the worker's say-so.
"""

import contextlib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from devkit import board, branches, checks, config, consolidation, handoff, quota
from devkit.ci_status import (  # re-exported: devkit/train.py reads these through devkit.workflow
    actions_enabled,
    await_actions,
    await_checks,
    check_key,
    latest_checks,
    local_ci_refusal,
    only_pending,
    required_contexts,
    standing,
    unfinished,
)
from devkit.fixloop import (  # re-exported: devkit/train.py reads these through devkit.workflow
    failed_check_lines,
    failing_lines,
    fix_loop,
    result,
    send_back,
    send_back_conflicting,
)
from devkit.gate import HELD_ENV, PRIORITY_ENV, free_gb, holders, tree_key
from devkit.merging import (  # re-exported: devkit/train.py reads these through devkit.workflow
    GENERATED_MERGE,  # noqa: F401 - tests read it here
    _add_worktree,
    _merge_rules,  # noqa: F401 - devkit/train.py reads it by name
    _remote_shas,
    deleted_owns,
    main_conflicts,
    merge_conflicts,  # noqa: F401 - devkit/train.py reads it by name
    merge_main,
    regenerate,
    retarget_stacked,
)
from devkit.paths import git, main_checkout, root, state_dir
from devkit.prtext import (  # re-exported: plugin scripts and devkit/train.py read these through devkit.workflow
    REUSE_FORMS,  # noqa: F401 - completion_gate.py imports it by name
    SESSION_RULE,
    conventional_title,
    expected_title,
    reuse_problem,  # noqa: F401 - completion_gate.py imports it by name
    session_leaks,
    title_problem,
)
from devkit.suite_audit import orient_lines

# Every path is harness (it can change what the gate or an agent does, and the gate that judges a branch is the
# branch's own code) except plain content under these directories. A PR touching harness needs the lead to read it.
# The content directories are `[tool.devkit] content`. Eval scenarios are harness: they hold the decisions agents are
# graded against.
CONTENT_SUFFIXES = (".md", ".yaml", ".yml", ".txt", ".csv", ".bib", ".png", ".jpg", ".jpeg", ".svg", ".pdf")
AGENT_FILES = {"claude.md", "claude.local.md", "agents.md", "gemini.md", "skill.md"}  # loaded from any directory


def is_harness(path: str) -> bool:
    if path == config.get().install_allowlist:  # an entry there lets a branch past the gate's install check (#392)
        return True
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


def gate_holder() -> str:
    """'idle', or 'running (<pid> <root>; ...)' for every gate holding a slot (#461)."""
    found = holders(state_dir() / "gate.lock")
    return f"running ({'; '.join(found)})" if found else "idle"


def open_prs() -> dict[str, dict] | None:
    """Branch -> its open same-repository PR (number, url); None when GitHub cannot say."""
    try:
        found = board.gh("pr", "list", "-R", board.repo(), "--state", "open", "--limit", str(branches.PRUNE_LIMIT),
                         "--json", "number,headRefName,url,isCrossRepository")  # fmt: skip
        return {p["headRefName"]: p for p in json.loads(found or "[]") if not p.get("isCrossRepository")}
    except (board.BoardError, OSError, ValueError):
        return None


def review_url(pr: int) -> str | None:
    """The newest comment URL of the PR's first unresolved review thread, or None (none, or GitHub cannot say)."""
    try:
        threads = board.pr_feedback(pr)["threads"]
    except (board.BoardError, OSError, ValueError, KeyError):
        return None
    return ((threads[0]["comments"]["nodes"] or [{}])[-1].get("url") or None) if threads else None


def resume_rows() -> list[dict]:
    """#388: every agent worktree with commits ahead of main or edits, with what a lead needs to re-dispatch it after a
    session restart: issue, branch, PR, last commit, unresolved review URL and a one-line respawn prompt."""
    home, prs = main_checkout().resolve(), open_prs()
    rows = []
    for w in branches.worktrees():
        if Path(w["path"]).resolve() == home or not (w["ahead"] or w["dirty"]):
            continue
        m = re.match(r"(\d+)-", w["branch"])
        pr = (prs or {}).get(w["branch"])
        target = f"#{m[1]} in {w['path']}" if m else w["path"]
        rows.append(
            w
            | {
                "issue": int(m[1]) if m else None,
                "pr": pr["number"] if pr else None,
                "pr_known": prs is not None,
                "last": git("-C", w["path"], "log", "-1", "--format=%h %s", check=False),
                "review": review_url(pr["number"]) if pr else None,
                "prompt": f"Work on {target}; finish, gate, dev pr",
            }
        )
    return rows


def resume_text(rows: list[dict]) -> str:
    out = []
    for r in rows:
        pr = f"PR #{r['pr']}" if r["pr"] else ("no PR" if r["pr_known"] else "PR ?")
        edits = f" *{r['dirty']}" if r["dirty"] else ""
        review = f" . review {r['review']}" if r["review"] else ""
        out += [
            f"#{r['issue'] or '?'} {r['branch']} . {pr} . +{r['ahead']}{edits} . {r['last']}{review}",
            f"  {r['prompt']}",
        ]
    return "\n".join(out) or "no worktree with commits or edits to resume"


def status(as_json: bool = False, resume: bool = False) -> str:
    if resume:
        rows = resume_rows()
        return json.dumps(rows, indent=1) if as_json else resume_text(rows)
    wts = branches.worktrees()
    pct, cap = quota.quota_reading()
    data = {
        "gate": gate_holder(),
        "mem_gb": round(free_gb(), 1),
        "worktrees": wts,
        "quota": quota.quota(),
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
        board_part += orient_lines(root())  # the suite audit (#114): due, red CI on main, a stale floor; never raises
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


def closes_problem(info: dict) -> str | None:
    """Why the body of the PR `info` does not link the issue claimed on its `<n>-slug` branch, or None (#434: a body
    rewritten with `gh pr edit` lost the `Closes #N` line `dev pr` wrote, so the merge left the issue open). Either
    `Closes #N` (or another closing keyword) or `Refs #N` will do. A branch with no issue number, such as
    Dependabot's or release-please's, claims none and is never refused."""
    if not (m := re.match(r"(\d+)-", info.get("headRefName") or "")):
        return None
    n, body = int(m.group(1)), info.get("body") or ""
    if n in board.keyword_numbers(board.CLOSES, body) + board.keyword_numbers(board.REFS, body):
        return None
    return (
        f"the PR body has no 'Closes #{n}' or 'Refs #{n}' line for the issue claimed on branch "
        f"{info['headRefName']!r}: add 'Closes #{n}' as its first line (or 'Refs #{n}' for a partial hand-back)"
    )


def _is_ancestor(sha: str, head: str) -> bool:
    """Whether commit `sha` (maybe abbreviated) is an ancestor of `head` in this clone; False when it is unknown."""
    try:
        git("merge-base", "--is-ancestor", sha, head)
    except RuntimeError:
        return False
    return True


def review_problem(
    touched: list[str],
    reviews: list[dict],
    head: str,
    trusted: set[str],
    rules: dict[str, str],
    is_ancestor: Callable[[str], bool],
) -> str | None:
    """Why the PR at `head` lacks a review its `touched` paths require (`rules`, [tool.devkit] required_reviews), or
    None (#391: a hand-back said a PR was audited, but no review existed). Each required reviewer needs a VERDICT
    review (board.pr_feedback's `reviews`) by a `trusted` login whose body names it and whose SHA is the head or an
    ancestor of it (`is_ancestor`); the latest such review must say `merge`. Only the gh user is trusted: anyone can
    review a public repository's PR."""
    needs: dict[str, list[str]] = {}
    for path in touched:
        for match, who in rules.items():
            if board.owned(path, [match]):
                needs.setdefault(who, []).append(path)
    missing = []
    for who, paths in needs.items():
        judged = [
            (r.get("submittedAt") or "", v[1])
            for r in reviews
            if board.login(r) in trusted
            and (v := board.verdict_of(r.get("body") or ""))
            and who in (r.get("body") or "")
            and (head.lower().startswith(v[3].lower()) or is_ancestor(v[3]))
        ]
        last = sorted(judged, key=lambda j: j[0])[-1] if judged else None  # stable: of equal times, the later listed
        if last is None or last[1] != "merge":
            seen = f" (its latest says {last[1]!r})" if last else ""
            missing.append(f"{', '.join(paths[:3])}{' ...' if len(paths) > 3 else ''} needs a review by {who}{seen}")
    if not missing:
        return None
    return (
        f"{'; '.join(missing)}: post a 'Verdict: merge, round N, {head[:7]}' review naming the reviewer as "
        "the gh user on the PR head or an ancestor of it"
    )  # the trusted logins stay out of the message: the gh user's own login is all an agent can post as


def pr(number: int, summary: str | None, draft: bool, partial: bool = False) -> str:
    """Push the gated branch and open its PR. A body without a `Closes #N` / `Refs #N` first line gets `Closes #N`
    (a complete hand-back: the merge closes the issue), or `Refs #N` with `partial` (the issue returns to ready).
    Prints two lines, the PR URL and a status with the ref line the body carries; keep it at two so nobody pipes it
    (worktree isolation refuses a piped `dev pr`, #261)."""
    branch = git("branch", "--show-current")
    if branch in ("main", ""):
        raise board.BoardError("open PRs from an issue branch in a worktree, not from main")
    if dirty := git("status", "--porcelain"):
        raise board.BoardError(f"uncommitted files (commit them, or move scratch files to .scratch/):\n{dirty}")
    if not _gate_passed():
        raise board.BoardError("run `uv run dev check` on this tip first: the PR must be gated")
    if conflicts := main_conflicts():
        raise board.BoardError(
            "this branch conflicts with origin/main in:\n  " + "\n  ".join(conflicts) + "\nmerge main, re-gate: "
            "`git merge origin/main`, resolve these files, `uv run dev docs`, commit, `uv run dev check`, then rerun"
        )
    issue = board.get(number)
    body = _summary_body(number, summary)
    if partial and re.match(r"Closes #\d+", body):
        raise board.BoardError("--partial, but the summary starts with 'Closes #N': write 'Refs #N' or drop --partial")
    if not re.match(r"(Closes|Refs) #\d+", body):
        body = f"{'Refs' if partial else 'Closes'} #{number}\n\n{body}"
    title = conventional_title(issue)
    commits = git("log", "--format=%B", f"{base}..HEAD") if (base := checks.merge_base()) else ""
    texts = {"PR title": title, "PR body": body, "branch commit messages": commits}
    if (leaks := session_leaks(**texts)) and repo_public():
        raise board.BoardError(f"{SESSION_RULE}: the {' and '.join(leaks)} carry one; remove it and rerun")
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
        title,
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
    moved = [number, *_also_closed(number, body, branch)]
    for n in moved[1:]:
        board.comment(n, f"PR ready for review: {url} (closes this issue too)")
        board.set_status(n, "review")
    ref = re.match(r"(Closes|Refs) #\d+", body)
    return f"{url}\n{' '.join(f'#{n}' for n in moved)} status:review, {ref.group(0) if ref else ''}"


def _also_closed(number: int, body: str, branch: str) -> list[int]:
    """Other open issues the PR body closes by keyword that are claimed on this branch (#384: one PR for two issues
    left the second in-progress). An issue claimed on another branch is never moved: it is another worker's item."""
    out = []
    for n in board.keyword_numbers(board.CLOSES, body):
        if n == number:
            continue
        issue = board.get(n)
        if issue.state.upper() == "OPEN" and (c := issue.claim()) and c.branch == branch:
            out.append(n)
    return out


# A worker's PR body may not carry the consolidation trailer onto main: only `integrate --consolidation-review` does.
REVIEW_LINE = re.compile(rf"^[ \t]*{re.escape(consolidation.REVIEW_TRAILER.rstrip(':'))}[ \t]*:.*\n?", re.I | re.M)


def repo_public() -> bool:
    """Whether GitHub may show this repository to anyone. Fails closed: only a visibility read as PRIVATE or
    INTERNAL is not public."""
    try:
        seen = json.loads(board.gh("repo", "view", board.repo(), "--json", "visibility")).get("visibility")
    except (board.BoardError, ValueError, AttributeError):
        return True
    return seen not in ("PRIVATE", "INTERNAL")


def squash_message(pr_number: int, info: dict, review: int | None = None) -> tuple[str, str]:
    """(subject, body) of the squash commit: the PR title and body, never the branch's commit messages (GitHub's
    COMMIT_MESSAGES default would copy them and their trailers onto main). `review` appends the
    `Consolidation-Review: #<review>` trailer as the body's last paragraph, where `consolidation.last_review` reads
    it; one a worker wrote into the PR body is dropped (only the lead resets the trigger). Neither part is ever empty:
    an empty `--body` would let GitHub's default message back in."""
    title, tag = (info.get("title") or "").strip(), f"(#{pr_number})"
    subject = title if title.endswith(tag) else f"{title} {tag}".strip()
    body = REVIEW_LINE.sub("", info.get("body") or "").strip()
    if review:
        body = f"{body}\n\n{consolidation.REVIEW_TRAILER} #{review}".strip()
    return subject, body or f"PR {tag} has no description."


PR_FIELDS = (
    "headRefName,headRefOid,baseRefName,isCrossRepository,title,body,state,isDraft,"
    "statusCheckRollup,latestReviews,reviewDecision"
)


def refusal(info: dict) -> str | None:
    """Why integrate must not merge this PR at all, or None."""
    if info["baseRefName"] != "main":
        return f"PR targets {info['baseRefName']!r}, not main: integrate only merges into main"
    if info["isCrossRepository"]:
        return "PR comes from a fork: integrate only merges branches of this repository"
    return None


def _waiting_file(pr_number: int) -> Path:
    """Where a timed-out wait records the tip it pushed and the checks expected on it, so a rerun resumes there."""
    return state_dir("integrate") / f"pr-{pr_number}.waiting.json"


GATE = ["uv", "run", "--quiet", "dev", "check", "--no-cache"]  # what integrate runs in the merged tree


HELD_POLL_S = 1.0  # how often bounded_run looks for its `held` file while the command waits for the gate lock


def bounded_run(
    cmd: list[str], timeout_s: float, held: Path | None = None, wait_s: float = 0, **kwargs: object
) -> int | None:
    """Run `cmd` in a session of its own and return its exit code, or None when it outran `timeout_s`: then the whole
    process group (uv, the gate, its pytest workers) is killed, since killing only `uv` would leave the gate under it
    holding the machine's gate lock. With `held`, `timeout_s` counts from when that file appears (the gate holds the
    lock, #385) and the time before it is bounded by `wait_s`. An interrupt, SIGTERM or SIGHUP to this process kills
    the group too, since none of them reaches a separate session (in the main thread only, where Python can handle
    signals)."""
    main = threading.current_thread() is threading.main_thread()
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)} if main else {}
    for s, handler in old.items():
        if handler != signal.SIG_IGN:  # nohup's ignored SIGHUP stays ignored
            signal.signal(s, _exit_on_signal)
    try:
        with subprocess.Popen(cmd, start_new_session=True, **kwargs) as proc:  # type: ignore[call-overload]
            try:
                start, running = time.monotonic(), held is None
                while True:
                    if not running and held is not None and held.exists():
                        start, running = time.monotonic(), True
                    left = start + (timeout_s if running else wait_s) - time.monotonic()
                    if left <= 0:
                        _kill_group(proc)
                        return None
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        return proc.wait(timeout=left if running else min(left, HELD_POLL_S))
            except BaseException:
                _kill_group(proc)
                raise
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


def _exit_on_signal(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)  # the shell's exit status for a process ended by that signal


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def run_gate(tree: Path, contexts: list[str], log: Path) -> bool | str:
    """The gate, then (`--local-ci`) each required context's own stand-in commands, in the merged tree; all output
    goes to `log` (kept for `--why`) and, as it comes, to stdout (`_follow`). True when every one passed, the reason
    when the gate was stopped: it waited past `gate_lock_wait_s` for the machine's gate lock, which it takes ahead of
    worker gates, or ran past `gate_deadline_s` once it held it (#385)."""
    cfg = config.get()
    stop = threading.Event()
    with log.open("w") as out, tempfile.TemporaryDirectory(prefix="gate-held-") as marks:
        follower = threading.Thread(target=_follow, args=(log, stop), daemon=True)
        follower.start()
        try:
            out.write(f"## gate: uv run dev check --no-cache in {tree}\n")
            out.flush()
            held = Path(marks) / "held"
            env = os.environ | {PRIORITY_ENV: "1", HELD_ENV: str(held)}
            kw = {"cwd": tree, "stdout": out, "stderr": subprocess.STDOUT, "env": env}
            code = bounded_run(GATE, cfg.gate_deadline_s, held=held, wait_s=cfg.gate_lock_wait_s, **kw)
            ok: bool | str = code == 0
            if code is None:
                ok = (
                    f"the gate ran past {cfg.gate_deadline_s} s (gate_deadline_s) once it held the gate lock"
                    if held.exists()
                    else f"the gate waited past {cfg.gate_lock_wait_s} s (gate_lock_wait_s) for the machine's gate lock"
                )
            for argv in [a for c in contexts for a in cfg.local_ci.get(c, [])] if ok is True else []:
                with tempfile.TemporaryDirectory(prefix="local-ci-") as tmpdir:
                    cmd = [a.replace("{tmpdir}", tmpdir) for a in argv]
                    out.write(f"## {' '.join(cmd)}\n")
                    out.flush()
                    if subprocess.run(cmd, cwd=tree, stdout=out, stderr=subprocess.STDOUT, check=False).returncode:
                        ok = False
                        break
        finally:
            out.flush()
            stop.set()
            follower.join()
    return ok


FOLLOW_S = 0.2  # how often the gate's log is copied to stdout while it runs


def _follow(log: Path, stop: threading.Event) -> None:
    """Copy what is appended to `log` to stdout until `stop` is set, then the rest: the lead sees the gate's stages
    and any wait for the machine's gate lock while it runs."""
    with log.open(errors="replace") as f:
        while True:
            last = stop.is_set()
            if chunk := f.read():
                sys.stdout.write(chunk)
                sys.stdout.flush()
            if last:
                return
            stop.wait(FOLLOW_S)


def _stand_in(pr_number: int, tip: str, contexts: list[str], log: Path, todo: list[str], update: dict) -> int | None:
    """`--local-ci`, after the local run passed on the merged tree whose commit is `tip`: wait for Actions on `tip`,
    then post a success status for each required context Actions did not run, with the log's hash, and say so on the
    PR. None to go on to the merge, else the exit code after the result line."""
    print(f"waiting for Actions on {tip[:10]} ({', '.join(contexts)}) ...", flush=True)
    state, now = await_actions(pr_number, tip, contexts)
    if state == "moved":
        print(f"RESULT: stopped (the PR head moved away from {tip[:10]} while waiting for Actions); rerun integrate")
        return result(
            pr_number, 1, "stopped", judged=tip, reason="the PR head moved while waiting for Actions", **update
        )
    if state == "timeout":
        running = [c for c, s in now.items() if s in ("running", "queued")]
        print(f"RESULT: waiting (Actions still run {', '.join(running)} on {tip[:10]}); rerun integrate")
        return result(pr_number, 1, "waiting", judged=tip, pending=running, **({"head": tip} | update))
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
        return result(
            pr_number, 1, "refused", judged=tip, reason="feedback on the gated head", feedback=arrived, **update
        )
    return None


def _merge_if_unchanged(
    pr_number: int, head: str, main: str, dry_run: bool = False, review: int | None = None, info: dict | None = None
) -> str | None:
    """Squash-merge only what was gated: the head is pinned by --match-head-commit, main is re-read just before.
    Returns the reason for not merging, or None. The commit is `squash_message` of `info` (the PR as last read);
    `review` adds `Consolidation-Review: #<review>`, the first-parent trailer that resets the consolidation trigger
    (only the lead writes it, through integrate)."""
    now_head, now_main = _remote_shas(pr_number)
    if now_head != head:
        return f"the PR head moved during the gate ({head[:10]} -> {now_head[:10]}): rerun integrate"
    if now_main != main:
        return f"main moved during the gate ({main[:10]} -> {now_main[:10]}): rerun integrate"
    subject, body = squash_message(pr_number, info or {}, review)
    if session_leaks(**{"PR title": subject, "PR body": body}) and repo_public():
        return f"{SESSION_RULE} in the squash commit: edit the PR title or body, then rerun integrate"
    if info is not None and (why := title_problem(subject)):  # the title as re-read after the gate
        return f"{why} in the squash commit: edit the PR title, then rerun integrate"
    if info is not None and (why := closes_problem(info)):  # the body as re-read after the gate
        return f"{why}, then rerun integrate"
    if branch := (info or {}).get("headRefName"):
        retarget_stacked(branch, head, dry_run)
    if not dry_run:
        board.gh(
            "pr", "merge", str(pr_number), "-R", board.repo(), "--squash", "--subject", subject, "--body", body,
            "--match-head-commit", head, "--delete-branch",
        )  # fmt: skip
    return None


def _pr_state(pr_number: int, ignore: frozenset[str] = frozenset()) -> tuple[dict, list[str]]:
    """The PR as GitHub has it now, and its unfinished feedback."""
    info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
    return info, unfinished(info, board.pr_feedback(pr_number), ignore)


PUSH_WAIT_S = 1.0  # between reads of the PR head after integrate pushed an update
PUSH_TRIES = 15


def _head_is(pr_number: int, tip: str) -> bool:
    """After a push, wait until GitHub reports the PR head at `tip` (it lags the push by a moment)."""
    for i in range(PUSH_TRIES):
        if _remote_shas(pr_number)[0] == tip:
            return True
        if i + 1 < PUSH_TRIES:
            time.sleep(PUSH_WAIT_S)
    return False


def _wait_on_tip(
    pr_number: int,
    tip: str,
    expected: list[str],
    update: dict,
    ignore: frozenset[str] = frozenset(),
    again: bool = False,
) -> int | None:
    """`await_checks` on a head integrate pushed. None when every check on it is complete; else the exit code, after
    the result line: `stopped` when the head moved, `waiting` (recorded for a rerun) when the time ran out. `again`
    (the last run already timed out on this head): the result says a rerun waits the same and carries `again`."""
    timeout_s = config.get().checks_wait_s
    print(f"waiting up to {timeout_s} s for the checks on {tip[:10]} ...", flush=True)
    state, detail = await_checks(pr_number, tip, expected, timeout_s, ignore)
    if state == "moved":
        _waiting_file(pr_number).unlink(missing_ok=True)
        print(f"RESULT: stopped (the PR head moved to {detail[0][:10]} while waiting for checks); rerun integrate")
        return result(
            pr_number, 1, "stopped", judged=tip, reason="the PR head moved while waiting for checks", **update
        )
    if state == "timeout":
        _waiting_file(pr_number).write_text(json.dumps({"tip": tip, "expected": expected}) + "\n")
        pending = f"checks on {tip[:10]} still pending after {timeout_s} s: {', '.join(detail)}"
        if not again:
            print(f"RESULT: waiting ({pending}); rerun")
            return result(pr_number, 1, "waiting", judged=tip, pending=detail, **({"head": tip} | update))
        print(
            f"RESULT: waiting ({pending}, again on this head); another call waits the same: a required check Actions "
            "cannot run (e.g. billing-blocked) wants --local-ci, anything else the owner"
        )
        return result(pr_number, 1, "waiting", judged=tip, pending=detail, again=True, **({"head": tip} | update))
    return None


def integrate(
    pr_number: int,
    harness_ok: bool,
    dry_run: bool,
    override: str | None = None,
    local_ci: bool = False,
    review: int | None = None,
) -> int:
    """`_integrate`, but every exit ends with a `result` line: a crash (even after a push) is `stopped` with its reason.
    An interrupt (Ctrl-C, or a signal `bounded_run` turned into SystemExit) is recorded as `stopped`, then raised on,
    so `--why` never shows an older outcome as this PR's last."""
    try:
        return _integrate(pr_number, harness_ok, dry_run, override, local_ci, review)
    except Exception as exc:  # the lead branches on the last line, whatever failed
        traceback.print_exc()
        print(f"RESULT: stopped (integrate failed: {exc}); check the PR branch and the board, then rerun")
        return result(pr_number, 1, "stopped", reason=f"integrate failed: {type(exc).__name__}: {exc}")
    except BaseException as exc:
        result(pr_number, 1, "stopped", reason=f"integrate interrupted: {type(exc).__name__} {exc}".rstrip())
        raise


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
    are stale) it is pushed to the PR branch before the merge; a real conflict is sent back (`send_back`), and so is
    a failed gate or a refusal the worker can fix (`fix_loop`, #110: `cycle` in the result).
    Unfinished feedback (checks, requested changes, open threads) refuses it unless `override` gives a reason,
    which is logged on the PR just before the merge. Feedback is read again after the gate: what arrived during it
    refuses the merge, override or not. After a push it waits (bounded, `checks_wait_s`) for every check on the new
    head, whatever the branch protection, and reads feedback once more; a timeout is `waiting`, and a rerun on that
    head resumes the wait instead of refusing its pending checks. The same bounded wait runs before the gate when the
    only unfinished feedback is checks still running on the PR's own head (no override, not a dry run; with
    `local_ci`, apart from the contexts it stands in for); what they conclude is then judged as above. With
    `local_ci` (no Actions minutes), required checks Actions does not run are left to the local run on the merged
    tree (`_stand_in`), which posts them as commit statuses only after it passed. A merged tree that empties an Owns
    entry of an open issue other than the PR's own (`deleted_owns`) is refused before the gate, and `override` does
    not lift that: name the issue with `Refs #N` in the PR body, or edit that issue's Owns. A deleting merge whose
    board cannot be read stops (fail closed). Issues close only after the merge; then every other open PR into main
    that the new main conflicts with is sent back (`send_back_conflicting`, `sent_back` in the result). The last line
    printed is `result`, which also records the outcome for `--why`."""
    info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
    pushed = None  # the merged tree, once pushed onto the PR branch: from then on the head this run judges

    def done(code: int, outcome: str, **fields: object) -> int:
        return result(pr_number, code, outcome, judged=pushed or info["headRefOid"], **fields)

    def back(outcome: str, reasons: list[str], failing: list[str], **fields: object) -> int:
        """A failure the worker fixes: `fix_loop` sends it back, and the result carries its cycle."""
        head = pushed or info["headRefOid"]
        return done(1, outcome, **fields, **fix_loop(pr_number, info, outcome, reasons, failing, head, dry_run))

    if info["state"] != "OPEN" or info["isDraft"]:
        print(f"PR #{pr_number} is {info['state']}{' (draft)' if info['isDraft'] else ''}: nothing to integrate")
        return done(1, "not-open")
    if reason := refusal(info):
        print(f"RESULT: refused: {reason}")
        return done(1, "refused", reason=reason)
    if (leaks := session_leaks(**{"PR title": info.get("title"), "PR body": info.get("body")})) and repo_public():
        reason = f"{SESSION_RULE}: the {' and '.join(leaks)} carry one; edit the PR, then rerun integrate"
        print(f"RESULT: refused: {reason}")
        return back("refused", [reason], [], reason=reason)
    if why := title_problem(info.get("title")):  # works with no Actions; override does not lift it
        reason = f"{why}; expected {expected_title(info)}: edit the PR title, then rerun integrate"
        print(f"RESULT: refused: {reason}")
        return back("refused", [reason], [], reason=reason)
    if why := closes_problem(info):  # #434: override does not lift it either
        reason = f"{why}, then rerun integrate"
        print(f"RESULT: refused: {reason}")
        return back("refused", [reason], [], reason=reason)
    if override is not None and not override.strip():
        print("RESULT: refused: --override needs a reason")
        return done(1, "refused", reason="--override needs a reason")
    contexts, ignore = [], frozenset()
    if local_ci:
        contexts, bound = required_contexts()
        if why := local_ci_refusal(contexts, bound):
            print(f"RESULT: refused: --local-ci: {why}")
            return done(1, "refused", reason=f"--local-ci: {why}")
        now = standing(info.get("statusCheckRollup") or [], contexts, actions_enabled(), {})
        ignore = frozenset(c for c, s in now.items() if s != "failed")  # judged on the gated head, after the run
    again = False  # the last run timed out waiting on this same head (#305)
    if (waiting := _waiting_file(pr_number)).exists():
        mark = json.loads(waiting.read_text())
        again = mark.get("tip") == info["headRefOid"]
        if again and not local_ci:  # a rerun after `waiting`: resume on the head integrate pushed
            if (code := _wait_on_tip(pr_number, mark["tip"], mark.get("expected") or [], {}, again=True)) is not None:
                return code
            info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
        waiting.unlink(missing_ok=True)
    feedback = board.pr_feedback(pr_number)
    if override is None and not dry_run and only_pending(info, feedback, ignore):
        # checks still running on the PR's own head: wait for them as for a pushed head, then judge them (#322).
        # Under --local-ci the required contexts it stands in for are neither waited for nor judged here.
        if (code := _wait_on_tip(pr_number, info["headRefOid"], [], {}, ignore, again)) is not None:
            return code
        info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
        feedback = board.pr_feedback(pr_number)
    if todo := unfinished(info, feedback, ignore):
        if override is None:
            print(f"RESULT: refused: PR #{pr_number} has unfinished feedback:\n  " + "\n  ".join(todo))
            print(
                f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, resolve the threads and "
                "wait for the checks; or rerun with --override '<reason>' (logged on the PR)."
            )
            return back("refused", [f"- {t}" for t in todo], failed_check_lines(info), reason="unfinished feedback",
                        feedback=todo)  # fmt: skip
        print(f"overriding unfinished feedback ({override}):\n  " + "\n  ".join(todo))
    head_ref, main_ref = f"refs/devkit/integrate/pr-{pr_number}", f"refs/devkit/integrate/main-{pr_number}"
    try:
        git("fetch", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{head_ref}", f"+refs/heads/main:{main_ref}")
        head, main = git("rev-parse", head_ref), git("rev-parse", main_ref)
        touched = changed_paths(main, head)
    except RuntimeError as exc:
        print(f"RESULT: stopped (cannot fetch or diff PR #{pr_number} against main): {exc}")
        return done(1, "stopped", reason=f"cannot fetch or diff: {exc}")
    if head != info["headRefOid"]:
        print(f"RESULT: stopped (the PR head moved while fetching: {info['headRefOid'][:10]} -> {head[:10]}); rerun")
        return done(1, "stopped", reason="the PR head moved while fetching")
    rules = config.get().required_reviews
    trusted = {board.gh_user()} - {""} if rules else set()  # asked only when a review can be required

    def ancestor(sha: str) -> bool:
        return _is_ancestor(sha, head)

    if rules and (why := review_problem(touched, feedback["reviews"], head, trusted, rules, ancestor)):
        reason = f"{why}, then rerun integrate"  # #391: override does not lift it
        print(f"RESULT: refused: {reason}")
        return back("refused", [reason], [], reason=reason)
    harness = [f for f in touched if is_harness(f)]
    if harness and not harness_ok:
        print("PR changes the harness (rules agents are judged by):\n  " + "\n  ".join(harness))
        print(f"Read that diff yourself (`git diff {main[:10]}...{head[:10]}`), then rerun with --harness-ok.")
        return done(2, "harness-review", harness=harness)
    tmp = state_dir("integrate") / f"pr-{pr_number}"
    _add_worktree(tmp, head)
    try:
        try:
            if conflicts := merge_main(tmp, main):
                returned = send_back(pr_number, info, main, conflicts, dry_run=dry_run)
                who = ", ".join(returned.get("resume", [])) or "its worker"
                print(f"RESULT: conflict with main in {', '.join(conflicts)}; sent back, resume {who}")
                return done(1, "conflict", conflicts=conflicts, branch=info["headRefName"], **returned)
            regenerated = regenerate(tmp)
            gone = deleted_owns(tmp, main, info)
        except OSError as exc:  # no gh at all; a gh failure is a BoardError, a RuntimeError
            print(f"RESULT: stopped (cannot read the board to check deleted Owns: {exc})")
            return done(1, "stopped", reason=f"cannot read the board: {exc}")
        except RuntimeError as exc:
            print(f"RESULT: stopped ({exc})")
            return done(1, "stopped", reason=str(exc))
        if gone:
            rows = [f"#{n}: {', '.join(paths)}" for n, paths in gone.items()]
            print(f"RESULT: refused: PR #{pr_number} deletes what open issues own:\n  " + "\n  ".join(rows))
            print("--override does not lift this: name each issue with `Refs #N` in the PR body, or edit its Owns.")
            why = "It deletes what open issues own: name each with `Refs #N` in the PR body, or edit its Owns:"
            return back("refused", [why, *(f"- {r}" for r in rows)], [], reason="deletes paths open issues own",
                        owns=gone)  # fmt: skip
        tip = git("rev-parse", "HEAD", cwd=tmp)
        print(
            f"gating PR #{pr_number} ({head[:10]}) merged with main {main[:10]} as {tip[:10]} in {tmp} ...", flush=True
        )
        run_log = state_dir("integrate") / f"pr-{pr_number}-{tip[:12]}.log"
        print(f"gate output (also kept in {run_log}):", flush=True)
        passed = run_gate(tmp, contexts if local_ci else [], run_log)
        said = {"gate": failing_lines(run_log.read_text(errors="replace")), "log": str(run_log)}
        if isinstance(passed, str):
            print(
                f"RESULT: stopped ({passed}) and was killed; its output shows a hung stage or a wait on another gate "
                "(`uv run dev status` names the holder), then rerun integrate"
            )
            return done(1, "stopped", reason=passed, **said)
        if not passed:
            print(f"RESULT: gate-failed (the merged tree fails the gate); PR #{pr_number} is sent back")
            why = f"The merged tree (main {main[:10]} merged in) fails the gate; its full log: {run_log}"
            return back("gate-failed", [why], said["gate"], reason="the merged tree fails the gate", **said)
    finally:
        git("worktree", "remove", "--force", str(tmp), check=False)
        git("update-ref", "-d", head_ref, check=False)
        git("update-ref", "-d", main_ref, check=False)
    state, now = _pr_state(pr_number, ignore)
    if arrived := [t for t in now if t not in todo]:
        print(f"RESULT: refused: feedback arrived on PR #{pr_number} during the gate:\n  " + "\n  ".join(arrived))
        print(f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, then rerun integrate.")
        return back("refused", [f"- {t}" for t in arrived], failed_check_lines(state),
                    reason="feedback arrived during the gate", feedback=arrived)  # fmt: skip
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
                return done(1, "stopped", reason=f"cannot update the PR branch: {exc}", **update)
            pushed = tip
            if not _head_is(pr_number, tip):
                print(f"RESULT: stopped (GitHub does not show the pushed head {tip[:10]} yet); rerun integrate")
                return done(1, "stopped", reason="pushed head not visible on GitHub", **update)
            expected = [check_key(c) for c in latest_checks(state.get("statusCheckRollup") or [])]
            if not local_ci and (code := _wait_on_tip(pr_number, tip, expected, update)) is not None:
                return code
            fresh, later = (state, []) if local_ci else _pr_state(pr_number)
            if arrived := [t for t in later if t not in todo]:
                print(f"RESULT: refused: unfinished feedback on the pushed head {tip[:10]}:\n  " + "\n  ".join(arrived))
                print(
                    f"Read it (`uv run dev board feedback {pr_number}`), answer or fix each item, then rerun integrate."
                )
                return back("refused", [f"- {t}" for t in arrived], failed_check_lines(fresh),
                            reason="feedback on the pushed head", feedback=arrived, **update)  # fmt: skip
    if local_ci and dry_run:
        print(f"would wait for Actions on {tip[:10]} and post what it does not run as statuses: {', '.join(contexts)}")
    elif local_ci and (code := _stand_in(pr_number, tip, contexts, run_log, todo, update)) is not None:
        return code
    try:
        reason = _merge_if_unchanged(
            pr_number, head if dry_run else tip, main, dry_run=dry_run, review=review, info=state
        )
    except board.BoardError as exc:  # e.g. branch protection waiting for checks on the update just pushed
        print(f"RESULT: stopped (GitHub refused the merge: {exc}); rerun integrate once its checks pass")
        return done(1, "stopped", reason=f"GitHub refused the merge: {exc}", **update)
    if reason:
        print(f"RESULT: stopped ({reason})")
        return done(1, "stopped", reason=reason, **update)
    if dry_run:
        print("RESULT: would merge (dry run)")
        return done(0, "would-merge", **update)
    try:  # issues close only now, after the merge
        moved = board.merged_pr(pr_number, info["body"] or "", info.get("headRefName", ""), info["baseRefName"])
    except board.BoardError as exc:
        print(f"RESULT: merged PR #{pr_number}, but the board update failed ({exc}): move its issue by hand")
        return done(0, "merged", moved=[], board_error=str(exc), **update)
    for w in branches.worktrees():
        if w["branch"] == info["headRefName"] and w["dirty"] == 0:
            git("worktree", "remove", w["path"], check=False)
    if name := info.get("headRefName"):
        if (others := branches.open_prs_on(name)) is None:
            print(f"kept branch {name}: cannot read its open PRs from GitHub")
        elif others := [n for n in others if n != pr_number]:  # the list can still show the PR just merged as open
            print(f"kept branch {name}: open PR {', '.join(f'#{n}' for n in others)} has it as head")
        else:
            print(branches.drop_branch(name, [tip]))
    try:  # the merge stands whatever this finds: a failure only skips the check
        sent = send_back_conflicting(pr_number)
    except (board.BoardError, RuntimeError, OSError, ValueError) as exc:
        sent = []
        print(f"conflict check of the new main skipped ({exc}): each open PR meets it at its own integrate")
    if not git("-C", str(main_checkout()), "status", "--porcelain", "--untracked-files=no"):
        git("-C", str(main_checkout()), "pull", "--ff-only", check=False)
    print(handoff.after_integrate())  # the board-derived HANDOFF sections (#394); never fails the merge
    print(f"RESULT: merged PR #{pr_number}" + (f"; {', '.join(moved)}" if moved else ""))
    return done(0, "merged", moved=moved, sent_back=sent, **update)


def heartbeat(number: int, message: str) -> None:
    board.comment(number, f"heartbeat ({time.strftime('%Y-%m-%d %H:%M')}): {message}")
