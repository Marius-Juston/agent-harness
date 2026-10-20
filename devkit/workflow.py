"""Session and hand-off workflow: orient, status, pr (worker), integrate (lead).

Roles (docs/specs/multi-agent.md): a *lead* session per machine plans, dispatches and integrates; *workers* each
own one issue in their own worktree and finish with a PR. Only the lead merges, through `integrate`, which re-runs the
gate on the merged tree in a throwaway worktree, never on the worker's say-so.
"""

import contextlib
import hashlib
import itertools
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import traceback
from pathlib import Path, PurePosixPath

from devkit import backoff, board, checks, config, consolidation
from devkit.gate import free_gb, tree_key
from devkit.paths import git, main_checkout, root, state_dir
from devkit.suite_audit import orient_lines

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


CAP_FILE = "quota_cap"  # <git-common-dir>/devkit/quota_cap: the owner's machine-local override (`dev quota-cap`)
CAP_SOURCES = {
    "local": "local: `uv run dev quota-cap --clear` drops it",
    "pyproject": "pyproject: [tool.devkit] quota_cap",
}


def quota_cap() -> tuple[int, str]:
    """(cap percent, "local" | "pyproject"): the override from `dev quota-cap` when it is valid, else [tool.devkit].
    A corrupt or unreadable override falls back with a warning, never an error: the guard must not lose its cap."""
    f = state_dir() / CAP_FILE
    try:
        return config.parse_cap(f.read_text(errors="replace")), "local"
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"warning: ignoring {f} ({exc}); using [tool.devkit] quota_cap", file=sys.stderr)
    return config.get().quota_cap, "pyproject"


def set_quota_cap(pct: int | None) -> Path:
    """Owner only (the Bash and Edit guards deny agents): write the override, or drop it when pct is None. A usage
    reading is rewritten with the new cap so the spawn guard and `dev status` agree before the next poll."""
    f = state_dir() / CAP_FILE
    if pct is None:
        f.unlink(missing_ok=True)
    else:
        tmp = f.with_suffix(".tmp")
        tmp.write_text(f"{config.parse_cap(str(pct))}\n")
        tmp.replace(f)
    try:
        data = json.loads((state_dir() / "usage.json").read_text())
        write_usage(data["pct"], now=data["t"])
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no reading yet, or a corrupt one: the next poll writes it
    return f


def quota_reading() -> tuple[int | None, int]:
    """(weekly all-models percent or None when no reading under 30 min old, effective cap percent)."""
    f = state_dir() / "usage.json"
    cap = quota_cap()[0]
    if not f.exists():
        return None, cap
    data = json.loads(f.read_text())
    return (data["pct"] if time.time() - data["t"] < FRESH_S else None), cap


def quota() -> str:
    f = state_dir() / "usage.json"
    cap, source = quota_cap()
    if not f.exists():
        return f"quota: no reading (start the poller: `{POLLER}`), cap {cap}% ({source})"
    data = json.loads(f.read_text())
    age = (time.time() - data["t"]) / 60
    return f"quota: {data['pct']}% of weekly all-models used ({age:.0f} min ago), cap {cap}% ({source})"


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
    tmp.write_text(json.dumps({"pct": pct, "t": int(now or time.time()), "cap": quota_cap()[0]}) + "\n")
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


REUSE_FORMS = """\
A branch that adds a dependency to pyproject.toml or a new non-test .py module needs one `Reuse:` line in its PR
summary, in one of three forms (#173; why: docs/research/software-discovery.md §9):
  Reuse: httpx PASS (chosen), requests HOLD dormant   <- candidates named from memory, each with its result as
                                                         `uv run dev reuse check <name>...` prints it (PASS, HOLD,
                                                         FAIL, FLAG, NOT FOUND or UNVERIFIED)
  Reuse: docs/research/<note>.md                      <- the research note that already weighed them (must exist)
  Reuse: none: <reason>                               <- why no library applies (`none` then :, -, – or —),
                                                         e.g. `Reuse: none: harness adoption` on a copier update
Empty or whitespace-only new .py files (package markers) do not count. Each new dependency also needs a fit clause
in a `Reuse:` line, answered from memory after `reuse check` (#196; why: docs/research/software-discovery.md §11):
  fit: <package> does <need> via <API>   e.g. `fit: httpx does HTTP/2 requests via httpx.Client(http2=True)`
  fit: unsure                            <- when you cannot name the API; `fit: <package> unsure` for one of several
  Reuse: httpx PASS (chosen); fit: httpx does HTTP/2 requests via httpx.Client(http2=True)
`Reuse: none: harness adoption` needs no fit clause: the template, not the agent, picked those dependencies."""
REUSE_LINE = re.compile(r"^[\s>*_-]*Reuse[*_]*:[*_]*[ \t]*(.*)$", re.M | re.I)
GATE_RESULT = re.compile(r"\b[A-Za-z0-9][\w.\[\]-]*[\s:(=]{1,3}(PASS|HOLD|FAIL|FLAG|NOT FOUND|UNVERIFIED)\b")
NONE_REASON = re.compile(r"none\s*[:\-–—]\s*\S", re.I)
NOTE_PATH = re.compile(r"docs/research/[\w./-]+\.md")
HARNESS_ADOPTION = re.compile(r"none\s*[:\-–—]\s*harness adoption\b", re.I)
FIT_CLAUSE = re.compile(r"\bfit:\s*(.*?)\s*(?=[;,]?\s*\bfit:|$)", re.I)
FIT_NAMED = re.compile(r"([A-Za-z0-9][\w.-]*)(?:\[[\w,.\s-]*\])?\s+(?:unsure\b|does\s+\S.*?\s+via\s+\S)", re.I)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dependency_names(pyproject: str) -> set[str]:
    """Normalized names in [project] dependencies and optional-dependencies, [dependency-groups] (PEP 735) and
    [tool.uv] dev-dependencies, less the project's own name (a self-reference such as `p[extra]`)."""
    try:
        data = tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project", {})
    specs = [*project.get("dependencies", []), *data.get("tool", {}).get("uv", {}).get("dev-dependencies", [])]
    for group in (*project.get("optional-dependencies", {}).values(), *data.get("dependency-groups", {}).values()):
        specs += group
    names = (re.match(r"\s*([A-Za-z0-9][\w.-]*)", s) for s in specs if isinstance(s, str))
    return {_normalized(m.group(1)) for m in names if m} - {_normalized(str(project.get("name", "")))}


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    in_tests = bool({"tests", "test"} & set(p.parts[:-1]))
    return in_tests or p.name == "conftest.py" or p.name.startswith("test_") or p.name.endswith("_test.py")


def reuse_needs(base: str) -> list[str]:
    """What on this branch since `base` calls for a `Reuse:` line: added dependencies and new non-test modules (a
    renamed module is not new)."""
    head_deps = _dependency_names(git("show", "HEAD:pyproject.toml", check=False))
    deps = head_deps - _dependency_names(git("show", f"{base}:pyproject.toml", check=False))
    added = git("diff", "--name-only", "-M", "--diff-filter=A", base, "HEAD").splitlines()
    modules = [
        p
        for p in added
        if p.endswith(".py") and not _is_test(p) and git("show", f"HEAD:{p}", check=False).strip()  # not a marker
    ]
    return [*(f"dependency {d}" for d in sorted(deps)), *(f"module {m}" for m in modules)]


def reuse_line_problem(body: str) -> str | None:
    """Why `body` has no `Reuse:` line in one of REUSE_FORMS, or None."""
    lines = [m.group(1).strip() for m in REUSE_LINE.finditer(body)]
    for line in lines:
        if GATE_RESULT.search(line) or NONE_REASON.match(line):
            return None
        if (note := NOTE_PATH.search(line)) and (root() / note.group(0)).is_file():
            return None
    return f"its `Reuse:` line ({lines[0][:80]!r}) is in none of the forms" if lines else "has no `Reuse:` line"


def fit_missing(body: str, dependencies: list[str]) -> list[str]:
    """The new `dependencies` (normalized names) that no fit clause in a `Reuse:` line of `body` covers (#196 F0).
    `fit: <package> does <need> via <API>` or `fit: <package> unsure` covers that package; `fit: unsure` covers all."""
    named: set[str] = set()
    for line in REUSE_LINE.finditer(body):
        if HARNESS_ADOPTION.match(line.group(1).strip()):
            return []  # template-added dependencies are not the agent's pick
        for clause in FIT_CLAUSE.finditer(line.group(1)):
            if re.match(r"unsure\b", clause.group(1), re.I):
                return []
            if m := FIT_NAMED.match(clause.group(1)):
                named.add(_normalized(m.group(1)))
    return [d for d in dependencies if d not in named]


def reuse_problem(body: str) -> str | None:
    """One line on why this branch's PR summary needs a `Reuse:` line it lacks (#173), or a fit clause for a new
    dependency (#196), or None."""
    base = checks.merge_base()
    if base is None or not (needs := reuse_needs(base)):
        return None
    if (why := reuse_line_problem(body)) is not None:
        shown = ", ".join(needs[:3]) + (f" and {len(needs) - 3} more" if len(needs) > 3 else "")
        return f"the branch adds {shown}, but the PR summary {why}"
    if missing := fit_missing(body, [n.removeprefix("dependency ") for n in needs if n.startswith("dependency ")]):
        return (
            f"the branch adds dependency {', '.join(missing)}, but no `Reuse:` line has a fit clause for it"
            " (`fit: <package> does <need> via <API>` or `fit: unsure`)"
        )
    return None


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
    issue = board.get(number)
    body = _summary_body(number, summary)
    if partial and re.match(r"Closes #\d+", body):
        raise board.BoardError("--partial, but the summary starts with 'Closes #N': write 'Refs #N' or drop --partial")
    if not re.match(r"(Closes|Refs) #\d+", body):
        body = f"{'Refs' if partial else 'Closes'} #{number}\n\n{body}"
    title = f"{issue.title} (#{number})"
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
    ref = re.match(r"(Closes|Refs) #\d+", body)
    return f"{url}\n#{number} status:review, {ref.group(0) if ref else ''}"


# Owner rule (2026-10-05): a public repository never gets a session link or trailer in a commit, PR or comment.
# Assembled from pieces: the scrub test bans the literal link from this public repository.
SESSION_URL = "claude.ai/code/" + "session"
SESSION_LEAK = re.compile(rf"https?://{re.escape(SESSION_URL)}_\w|^[ \t]*Claude-Session[ \t]*:", re.I | re.M)
SESSION_RULE = f"this repository is public: no {SESSION_URL} URL or Claude-Session trailer"
# A worker's PR body may not carry the consolidation trailer onto main: only `integrate --consolidation-review` does.
REVIEW_LINE = re.compile(rf"^[ \t]*{re.escape(consolidation.REVIEW_TRAILER.rstrip(':'))}[ \t]*:.*\n?", re.I | re.M)


def session_leaks(**texts: str | None) -> list[str]:
    """The names of `texts` that carry a session link (https:// + SESSION_URL + `_<id>`) or a Claude-Session
    trailer; a mention of the path alone, as in a rule's text, is not one."""
    return [name for name, text in texts.items() if SESSION_LEAK.search(text or "")]


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


GATE = ["uv", "run", "--quiet", "dev", "check", "--no-cache"]  # what integrate runs in the merged tree


def bounded_run(cmd: list[str], timeout_s: float, **kwargs: object) -> int | None:
    """Run `cmd` in a session of its own and return its exit code, or None when it outran `timeout_s`: then the whole
    process group (uv, the gate, its pytest workers) is killed, since killing only `uv` would leave the gate under it
    holding the machine's gate lock. An interrupt, SIGTERM or SIGHUP to this process kills the group too, since none
    of them reaches a separate session (in the main thread only, where Python can handle signals)."""
    main = threading.current_thread() is threading.main_thread()
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)} if main else {}
    for s, handler in old.items():
        if handler != signal.SIG_IGN:  # nohup's ignored SIGHUP stays ignored
            signal.signal(s, _exit_on_signal)
    try:
        with subprocess.Popen(cmd, start_new_session=True, **kwargs) as proc:  # type: ignore[call-overload]
            try:
                return proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                return None
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


def local_ci_run(tree: Path, contexts: list[str], log: Path) -> bool | None:
    """The gate, then each required context's own stand-in commands, in the merged tree; all output goes to `log`
    (printed after). True when every one passed, None when the gate ran past `gate_deadline_s`."""
    commands = config.get().local_ci
    with log.open("w") as out:
        out.write(f"## gate: uv run dev check --no-cache in {tree}\n")
        out.flush()
        code = bounded_run(GATE, config.get().gate_deadline_s, cwd=tree, stdout=out, stderr=subprocess.STDOUT)
        ok = None if code is None else code == 0
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
    if branch := (info or {}).get("headRefName"):
        retarget_stacked(branch, head, dry_run)
    if not dry_run:
        board.gh(
            "pr", "merge", str(pr_number), "-R", board.repo(), "--squash", "--subject", subject, "--body", body,
            "--match-head-commit", head, "--delete-branch",
        )  # fmt: skip
    return None


def retarget_stacked(branch: str, tip: str, dry_run: bool = False) -> list[int]:
    """Stacked PRs: point every open PR based on `branch` (about to be squash-merged and deleted, which would close
    them) at main, and say how each stacked PR drops its parent's commits, which the squash commit already carries on
    main under another sha. Returns the retargeted PR numbers. The route it prints carries the stacked PR's own commits
    to a new branch from main: auto mode refuses a rebase plus a force push as destructive (#305)."""
    found = board.gh(
        "pr", "list", "-R", board.repo(), "--base", branch, "--state", "open", "--json", "number,headRefName"
    )
    stacked = json.loads(found or "[]")
    for pr in stacked:
        n, name = pr["number"], pr["headRefName"]
        if dry_run:
            print(f"would retarget stacked PR #{n} ({name}) from {branch} to main")
            continue
        board.gh("pr", "edit", str(n), "-R", board.repo(), "--base", "main")
        print(f"retargeted stacked PR #{n} ({name}) from {branch} to main. {stacked_route(n, name, tip)}")
    return [c["number"] for c in stacked]


def stacked_route(pr_number: int, name: str, tip: str) -> str:
    """How a stacked PR leaves its squash-merged parent's commits behind without a rebase or a force push: its own
    commits (those after the parent's `tip`) cherry-picked onto a new branch from main, a new PR, the old one closed.
    The commits are listed by sha when GitHub can compare them, else by the command that lists them."""
    try:
        shas = board.gh("api", f"repos/{board.repo()}/compare/{tip}...{name}", "--jq", ".commits[].sha").split()
    except board.BoardError:
        shas = []
    own = (
        f"`git cherry-pick {' '.join(s[:12] for s in shas)}`"
        if shas
        else f"`git cherry-pick` each commit `git log --reverse --format=%h {tip[:12]}..origin/{name}` lists"
    )
    issue = m[1] if (m := re.match(r"(\d+)-", name)) else "<issue>"
    return (
        f"Before its integrate, carry its own commits to a new branch (no history rewrite: auto mode refuses that): "
        f"`git fetch origin && git switch -c {name}-v2 origin/main`, then {own}, gate it (`uv run dev check`), open "
        f"the new PR with `uv run dev pr {issue}`, and close the old one: `gh pr close {pr_number}`"
    )


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


def deleted_owns(tree: Path, main: str, info: dict) -> dict[int, list[str]]:
    """Open issue -> its Owns entries that `main` has and the merged tree at `tree` empties, other than the PR's own
    issues (docs/research/decision-records-and-drift.md check 2). The board is read only when the merge deletes."""
    before = set(git("ls-tree", "-r", "--name-only", main).splitlines())
    after = set(git("ls-tree", "-r", "--name-only", "HEAD", cwd=tree).splitlines())
    if not (deleted := before - after):
        return {}
    return board.deleting_owns(board.fetch(), deleted, after, info["body"] or "", info.get("headRefName", ""))


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
        return result(pr_number, 1, "stopped", reason="the PR head moved while waiting for checks", **update)
    if state == "timeout":
        _waiting_file(pr_number).write_text(json.dumps({"tip": tip, "expected": expected}) + "\n")
        pending = f"checks on {tip[:10]} still pending after {timeout_s} s: {', '.join(detail)}"
        if not again:
            print(f"RESULT: waiting ({pending}); rerun")
            return result(pr_number, 1, "waiting", pending=detail, **({"head": tip} | update))
        print(
            f"RESULT: waiting ({pending}, again on this head); another call waits the same: a required check Actions "
            "cannot run (e.g. billing-blocked) wants --local-ci, anything else the owner"
        )
        return result(pr_number, 1, "waiting", pending=detail, again=True, **({"head": tip} | update))
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
    head resumes the wait instead of refusing its pending checks. The same bounded wait runs before the gate when the
    only unfinished feedback is checks still running on the PR's own head (no override, not a dry run; with
    `local_ci`, apart from the contexts it stands in for); what they conclude is then judged as above. With
    `local_ci` (no Actions minutes), required checks Actions does not run are left to the local run on the merged
    tree (`_stand_in`), which posts them as commit statuses only after it passed. A merged tree that empties an Owns
    entry of an open issue other than the PR's own (`deleted_owns`) is refused before the gate, and `override` does
    not lift that: name the issue with `Refs #N` in the PR body, or edit that issue's Owns. A deleting merge whose
    board cannot be read stops (fail closed). Issues close only after the merge; the last line printed is `result`."""
    info = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", PR_FIELDS))
    if info["state"] != "OPEN" or info["isDraft"]:
        print(f"PR #{pr_number} is {info['state']}{' (draft)' if info['isDraft'] else ''}: nothing to integrate")
        return result(pr_number, 1, "not-open")
    if reason := refusal(info):
        print(f"RESULT: refused: {reason}")
        return result(pr_number, 1, "refused", reason=reason)
    if (leaks := session_leaks(**{"PR title": info.get("title"), "PR body": info.get("body")})) and repo_public():
        reason = f"{SESSION_RULE}: the {' and '.join(leaks)} carry one; edit the PR, then rerun integrate"
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
            gone = deleted_owns(tmp, main, info)
        except OSError as exc:  # no gh at all; a gh failure is a BoardError, a RuntimeError
            print(f"RESULT: stopped (cannot read the board to check deleted Owns: {exc})")
            return result(pr_number, 1, "stopped", reason=f"cannot read the board: {exc}")
        except RuntimeError as exc:
            print(f"RESULT: stopped ({exc})")
            return result(pr_number, 1, "stopped", reason=str(exc))
        if gone:
            rows = [f"#{n}: {', '.join(paths)}" for n, paths in gone.items()]
            print(f"RESULT: refused: PR #{pr_number} deletes what open issues own:\n  " + "\n  ".join(rows))
            print("--override does not lift this: name each issue with `Refs #N` in the PR body, or edit its Owns.")
            return result(pr_number, 1, "refused", reason="deletes paths open issues own", owns=gone)
        tip = git("rev-parse", "HEAD", cwd=tmp)
        print(
            f"gating PR #{pr_number} ({head[:10]}) merged with main {main[:10]} as {tip[:10]} in {tmp} ...", flush=True
        )
        run_log = state_dir("integrate") / f"pr-{pr_number}-{tip[:12]}.log"
        deadline = config.get().gate_deadline_s
        if local_ci:
            passed = local_ci_run(tmp, contexts, run_log)
        else:
            code = bounded_run(GATE, deadline, cwd=tmp)
            passed = None if code is None else code == 0
        if passed is None:
            # the deadline also counts any wait for this machine's gate lock, so the output, not this line, says why
            reason = f"the gate, counting any wait for the machine's gate lock, ran past {deadline} s (gate_deadline_s)"
            print(
                f"RESULT: stopped ({reason}) and was killed; its output shows a hung stage or a wait on another gate "
                "(`uv run dev status` names the holder), then rerun integrate"
            )
            return result(pr_number, 1, "stopped", reason=reason)
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
        reason = _merge_if_unchanged(
            pr_number, head if dry_run else tip, main, dry_run=dry_run, review=review, info=state
        )
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
