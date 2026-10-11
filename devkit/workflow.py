"""Session and hand-off workflow: orient, status, pr (worker), integrate (lead).

Roles (docs/specs/multi-agent.md): a *lead* session per machine plans, dispatches and integrates; *workers* each
own one issue in their own worktree and finish with a PR. Only the lead merges, through `integrate`, which re-runs the
gate on the merged tree in a throwaway worktree, never on the worker's say-so.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

from devkit import board, checks, config
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
    try:
        issues, done = board.fetch(with_comments=True), board.closed_numbers()
        active = [i for i in issues if i.status in ("in-progress", "review", "blocked") and i.kind != "epic"]
        rows = [board._line(i, done) for i in active] or ["nothing in progress, review or blocked"]
        nxt = [board._line(i, done) for i in board.startable(issues, done=done)[:5]] or ["  (none)"]
        board_part = [
            "",
            "## Board (GitHub issues; `uv run dev board list` for all)",
            *board.cap(rows, 12, "`uv run dev board list`"),
            "next startable:",
            *nxt,
        ]
    except board.BoardError as exc:
        board_part = ["", f"## Board unavailable: {exc}"]
    tail = ["", "## This machine", *board.cap(status().splitlines(), 10, "`uv run dev status`")]
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


def pr(number: int, summary: str | None, draft: bool) -> str:
    branch = git("branch", "--show-current")
    if branch in ("main", ""):
        raise board.BoardError("open PRs from an issue branch in a worktree, not from main")
    if dirty := git("status", "--porcelain"):
        raise board.BoardError(f"uncommitted files (commit them, or move scratch files to .scratch/):\n{dirty}")
    if not _gate_passed():
        raise board.BoardError("run `uv run dev check` on this tip first: the PR must be gated")
    issue = board.get(number)
    body = _summary_body(number, summary)
    if not re.match(r"(Closes|Refs) #\d+", body):
        body = f"Refs #{number}\n\n{body}"
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


PR_FIELDS = "headRefName,headRefOid,baseRefName,isCrossRepository,title,body,state,isDraft"


def refusal(info: dict) -> str | None:
    """Why integrate must not merge this PR at all, or None."""
    if info["baseRefName"] != "main":
        return f"PR targets {info['baseRefName']!r}, not main: integrate only merges into main"
    if info["isCrossRepository"]:
        return "PR comes from a fork: integrate only merges branches of this repository"
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


def _merge_if_unchanged(pr_number: int, head: str, main: str, dry_run: bool = False) -> str | None:
    """Merge only what was gated: the head is pinned by --match-head-commit, main is re-read just before. Returns the
    reason for not merging, or None."""
    now_head, now_main = _remote_shas(pr_number)
    if now_head != head:
        return f"the PR head moved during the gate ({head[:10]} -> {now_head[:10]}): rerun integrate"
    if now_main != main:
        return f"main moved during the gate ({main[:10]} -> {now_main[:10]}): rerun integrate"
    if not dry_run:
        board.gh(
            "pr", "merge", str(pr_number), "-R", board.repo(), "--merge", "--match-head-commit", head, "--delete-branch"
        )
    return None


def integrate(pr_number: int, harness_ok: bool, dry_run: bool) -> int:
    """Lead only: merge a PR after re-gating the merged tree (main + pull/N/head) in a throwaway worktree."""
    info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
    if info["state"] != "OPEN" or info["isDraft"]:
        print(f"PR #{pr_number} is {info['state']}{' (draft)' if info['isDraft'] else ''}: nothing to integrate")
        return 1
    if reason := refusal(info):
        print(f"RESULT: refused: {reason}")
        return 1
    head_ref, main_ref = f"refs/devkit/integrate/pr-{pr_number}", f"refs/devkit/integrate/main-{pr_number}"
    try:
        git("fetch", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{head_ref}", f"+refs/heads/main:{main_ref}")
        head, main = git("rev-parse", head_ref), git("rev-parse", main_ref)
        touched = changed_paths(main, head)
    except RuntimeError as exc:
        print(f"RESULT: stopped (cannot fetch or diff PR #{pr_number} against main): {exc}")
        return 1
    if head != info["headRefOid"]:
        print(f"RESULT: stopped (the PR head moved while fetching: {info['headRefOid'][:10]} -> {head[:10]}); rerun")
        return 1
    harness = [f for f in touched if is_harness(f)]
    if harness and not harness_ok:
        print("PR changes the harness (rules agents are judged by):\n  " + "\n  ".join(harness))
        print(f"Read that diff yourself (`git diff {main[:10]}...{head[:10]}`), then rerun with --harness-ok.")
        return 2
    tmp = state_dir("integrate") / f"pr-{pr_number}"
    _add_worktree(tmp, main)
    try:
        merge = subprocess.run(
            ["git", "merge", "--no-ff", "--no-edit", head], cwd=tmp, capture_output=True, text=True, check=False
        )
        if merge.returncode != 0:
            print(f"merge conflicts with main: send the branch back to rebase.\n{merge.stdout[-1500:]}")
            return 1
        print(f"gating the merged tree of PR #{pr_number} ({head[:10]} into main {main[:10]}) in {tmp} ...", flush=True)
        gate = subprocess.run(["uv", "run", "--quiet", "dev", "check", "--no-cache"], cwd=tmp, check=False)
        if gate.returncode != 0:
            print(f"RESULT: stopped (merged tree fails the gate); comment on PR #{pr_number} and send it back")
            return 1
    finally:
        git("worktree", "remove", "--force", str(tmp), check=False)
        git("update-ref", "-d", head_ref, check=False)
        git("update-ref", "-d", main_ref, check=False)
    if reason := _merge_if_unchanged(pr_number, head, main, dry_run=dry_run):
        print(f"RESULT: stopped ({reason})")
        return 1
    if dry_run:
        print("RESULT: would merge (dry run)")
        return 0
    moved = board.merged_pr(pr_number, info["body"] or "", info.get("headRefName", ""), info["baseRefName"])
    for w in worktrees():
        if w["branch"] == info["headRefName"] and w["dirty"] == 0:
            git("worktree", "remove", w["path"], check=False)
    if not git("-C", str(main_checkout()), "status", "--porcelain", "--untracked-files=no"):
        git("-C", str(main_checkout()), "pull", "--ff-only", check=False)
    print(f"RESULT: merged PR #{pr_number}" + (f"; {', '.join(moved)}" if moved else ""))
    return 0


def heartbeat(number: int, message: str) -> None:
    board.comment(number, f"heartbeat ({time.strftime('%Y-%m-%d %H:%M')}): {message}")
