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
from devkit.gate import HELD_ENV, PRIORITY_ENV, free_gb, tree_key
from devkit.paths import default_branch, git, main_checkout, root, state_dir
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


SHA = re.compile(r"[0-9a-f]{40}")


# Where a worktree stopped mid-rebase or mid-bisect keeps the branch it will return to: `worktree list` shows such a
# worktree as detached, yet `rebase --continue` / `bisect reset` fail once that branch is gone.
IN_USE = ("rebase-merge/head-name", "rebase-apply/head-name", "BISECT_START")


def checked_out() -> dict[str, str]:
    """Branch -> path of the worktree (the main checkout included) that has it checked out, or is rebasing or
    bisecting it (`IN_USE`)."""
    rows = {}
    for block in git("worktree", "list", "--porcelain").split("\n\n"):
        path, branch = re.search(r"^worktree (.+)$", block, re.M), re.search(r"^branch refs/heads/(.+)$", block, re.M)
        if not path:
            continue
        if branch:
            rows[branch.group(1)] = path.group(1)
        gitdir = git("-C", path.group(1), "rev-parse", "--absolute-git-dir", check=False)
        for name in IN_USE if gitdir else ():
            with contextlib.suppress(OSError):
                held = (Path(gitdir) / name).read_text().strip().removeprefix("refs/heads/")
                if held:
                    rows.setdefault(held, path.group(1))
    return rows


def open_prs_on(branch: str) -> list[int] | None:
    """Open same-repository PRs whose head is `branch`; None when GitHub cannot say (callers keep the branch)."""
    try:
        found = board.gh(
            "pr", "list", "-R", board.repo(), "--head", branch, "--state", "open", "--json", "number,isCrossRepository"
        )
        return [p["number"] for p in json.loads(found or "[]") if not p.get("isCrossRepository")]
    except (board.BoardError, OSError, ValueError):
        return None


def drop_branch(branch: str, heads: list[str], dry_run: bool = False) -> str:
    """Delete the local `branch` when its tip is contained in `heads` (a merged PR's head, or an ancestor of it) and
    no worktree has it checked out; one line saying what happened and why. A squash merge never makes a branch
    'merged' for `git branch -d`, so agents would need `-D` (which guard_bash refuses) without this (#340). The ref is
    deleted only if it still points at the tip checked (`update-ref -d <ref> <old>`), so a concurrent push keeps it."""
    tip = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    if not tip:
        return f"no local branch {branch}"
    if path := checked_out().get(branch):
        return f"kept branch {branch}: checked out in {path}"
    if not (heads := [h for h in heads if SHA.fullmatch(h)]):
        return f"kept branch {branch}: no merged head in this clone to compare it with"
    # commits of the tip that no head reaches; a head this clone lacks is a git error: "" (fail closed)
    ahead = git("rev-list", "--count", tip, *(f"^{h}" for h in heads), check=False)
    if not ahead:
        return f"kept branch {branch}: this clone lacks the merged head, so nothing proves {tip[:10]} is in it"
    if ahead != "0":
        return f"kept branch {branch}: its tip {tip[:10]} has {ahead} commit(s) the merged head does not"
    if dry_run:
        return f"would delete branch {branch} (at {tip[:10]})"
    try:
        git("update-ref", "-d", f"refs/heads/{branch}", tip)
    except RuntimeError as exc:
        return f"kept branch {branch}: {exc}"
    git("config", "--remove-section", f"branch.{branch}", check=False)
    return f"deleted branch {branch} (was {tip[:10]})"


PRUNE_LIMIT = 1000  # PRs read per prune: plenty for one repository's local branches


def prune_branches(dry_run: bool = False) -> list[str]:
    """`dev worktree prune`: local branches whose PR is merged, or closed (superseded) with no open PR on the branch.
    Each is deleted when `drop_branch` finds its tip contained in the merged PR heads this clone has (its own, or for
    a superseded one another's); otherwise kept with the reason. One line per such branch."""
    fields, limit = "number,state,headRefName,headRefOid,isCrossRepository", str(PRUNE_LIMIT)
    prs = json.loads(board.gh("pr", "list", "-R", board.repo(), "--state", "all", "--limit", limit, "--json", fields))
    if len(prs) >= PRUNE_LIMIT:  # an older open PR on a local branch could be past the limit: delete nothing
        raise board.BoardError(
            f"worktree prune read {PRUNE_LIMIT} PRs (PRUNE_LIMIT), so an older open PR may be missing; nothing deleted"
        )
    prs = [p for p in prs if not p.get("isCrossRepository")]
    merged = [p["headRefOid"] for p in prs if p["state"] == "MERGED" and SHA.fullmatch(p["headRefOid"] or "")]
    check = subprocess.run(
        ["git", "cat-file", "--batch-check"], input="\n".join(merged), capture_output=True, text=True, check=False
    )
    heads = [line.split()[0] for line in check.stdout.splitlines() if not line.endswith(" missing")]
    local = git("for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads/").splitlines()
    lines = []
    for branch in local:
        if branch == default_branch():
            continue
        own = [p for p in prs if p["headRefName"] == branch]
        if not own or any(p["state"] == "OPEN" for p in own):
            continue
        which = ", ".join(f"#{p['number']} {p['state'].lower()}" for p in own)
        lines.append(f"{drop_branch(branch, heads, dry_run)} (PR {which})")
    return lines


def gate_holder() -> str:
    lock = state_dir() / "gate.lock"
    if not lock.exists():
        return "idle"
    out = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True, check=False)
    return "idle" if out.returncode == 0 else f"running ({lock.read_text().strip()})"


def open_prs() -> dict[str, dict] | None:
    """Branch -> its open same-repository PR (number, url); None when GitHub cannot say."""
    try:
        found = board.gh("pr", "list", "-R", board.repo(), "--state", "open", "--limit", str(PRUNE_LIMIT),
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
    for w in worktrees():
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


def main_conflicts() -> list[str]:
    """The paths HEAD conflicts in with origin/main, fetched now, as integrate would merge them (#349: 7 of 19
    conflicting hand-backs already conflicted at `dev pr`). Fails closed: no fetch, no PR."""
    try:
        git("fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
        return merge_conflicts("HEAD", git("rev-parse", "refs/remotes/origin/main"))
    except RuntimeError as exc:
        raise board.BoardError(f"cannot check this branch for conflicts with origin/main: {exc}") from exc


# Conventional Commits 1.0.0 PR titles (#100): the squash subject is the PR title, and release-please (#60) reads the
# subjects on main. The type comes from the issue's type label; the scope from its '<component>: ' title prefix.
COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
KIND_TYPE = {"build": "feat", "epic": "feat", "bug": "fix", "test": "test", "research": "docs", "infra": "ci"}
# Lowercase types only (the spec allows any case; commitizen's check does not). The scope keeps to ASCII word chars,
# '.', '/' and '-', which release-please's and conventional-changelog's parsers both read. The ` (#n)` suffix `dev pr`
# writes is optional: Dependabot's and release-please's PR titles carry none. docs/research/agent-harness.md §12.
CONVENTIONAL_HEAD = re.compile(rf"({'|'.join(COMMIT_TYPES)})(\([\w./-]+\))?!?: (?=\S)", re.ASCII)
CONVENTIONAL = re.compile(CONVENTIONAL_HEAD.pattern + r"(?!\(#\d+\)$)[^\n\r]+", re.ASCII)  # an outcome, not only (#n)
TITLE_FORM = "type(scope): outcome (#n)"
# A release item ('Release v0.1.15', 'release: v0.1.15 notes') is a chore, never a feature scoped by its version.
RELEASE_TITLE = re.compile(r"release:\s*|release\s+(?=v?\d+\.\d)", re.I)


def conventional_title(issue: board.Issue) -> str:
    """`type(scope): outcome (#n)` for `issue`: the type from its type label (KIND_TYPE), the scope from its
    '<component>: ' prefix (board.COMPONENT_TITLE) as a slug; a 'Research: ' / 'Epic: ' prefix is not a scope, and a
    component that is itself a commit type becomes the type. A title that is already Conventional is kept as it is,
    and a release item is `chore(release)`."""
    whole = " ".join(issue.title.split())  # one line, so the subject is one line too
    title, kind, scope = whole, KIND_TYPE.get(issue.kind, "feat"), ""
    if CONVENTIONAL_HEAD.match(whole):
        return f"{whole} (#{issue.number})"
    if m := RELEASE_TITLE.match(title):
        title, kind, scope = title[m.end() :].strip(), "chore", "release"
    elif m := board.TITLE_PREFIX.match(title):
        title = title[m.end() :].strip()
    elif board.COMPONENT_TITLE.match(title):
        component, title = (part.strip() for part in title.split(":", 1))
        if component.lower() in COMMIT_TYPES:
            kind = component.lower()
        else:
            scope = re.sub(r"[^\w.-]+", "-", component.lower(), flags=re.ASCII).strip("-.")
    return f"{kind}{f'({scope})' if scope else ''}: {title or whole} (#{issue.number})"


def title_problem(title: str | None) -> str | None:
    """Why `title` is not a Conventional Commits PR title, or None (always None with `conventional_titles = false`)."""
    if not config.get().conventional_titles or CONVENTIONAL.fullmatch((title or "").strip()):
        return None
    return (
        f"PR title {title!r} is not Conventional Commits: write '{TITLE_FORM}', type one of {', '.join(COMMIT_TYPES)}"
    )


def expected_title(info: dict) -> str:
    """The title `dev pr` gives the PR `info`, from the first issue it links (`board.linked`), quoted; TITLE_FORM when
    it links none or the board cannot be read."""
    closes, own = board.linked(info.get("body") or "", info.get("headRefName", ""))
    for n in closes + own:
        try:
            return repr(conventional_title(board.get(n)))
        except board.BoardError:
            break
    return repr(TITLE_FORM)


def check_title(title: str | None) -> int:
    """`dev pr-title`: CI passes the title in PR_TITLE (an env value, never `${{ }}` inside a script)."""
    if why := title_problem(title):
        print(f"FAIL {why}")
        return 1
    print(f"ok: {title.strip()}")
    return 0


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
    if info is not None and (why := title_problem(subject)):  # the title as re-read after the gate
        return f"{why} in the squash commit: edit the PR title, then rerun integrate"
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


def _merge_rules(main: str) -> list[str]:
    """The git options every merge of main into a PR runs with: main's `.gitattributes` (`--attr-source`; an old
    branch must not bring back a union rule that interleaves rewrites) and the generated-file driver. Raises
    RuntimeError when git is too old for `--attr-source`."""
    if (have := git_version()) < ATTR_SOURCE_GIT:
        need, got = ".".join(map(str, ATTR_SOURCE_GIT)), ".".join(map(str, have))
        raise RuntimeError(f"merging main needs git >= {need} (--attr-source); this is git {got}")
    return [f"--attr-source={main}", *(arg for key, value in GENERATED_MERGE for arg in ("-c", f"{key}={value}"))]


def merge_conflicts(head: str, main: str, cwd: Path | None = None) -> list[str]:
    """The paths that conflict when `main` is merged into `head` as `merge_main` merges it, without a worktree or a
    commit: `git merge-tree --write-tree` (3.7 ms a PR, docs/research/issue-pr-automation.md §3.2). [] when it
    merges cleanly. Raises RuntimeError when git is too old or merge-tree fails (unrelated histories, a bad sha)."""
    rules = _merge_rules(main)
    run = subprocess.run(
        ["git", *rules, "merge-tree", "--write-tree", "--name-only", "--no-messages", head, main],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = run.stdout.splitlines()
    # exit 1 is a conflict, but also "not something we can merge": only a tree id on the first line says it merged
    if run.returncode not in (0, 1) or not lines or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", lines[0]):
        raise RuntimeError(f"git merge-tree {head[:10]} {main[:10]} failed: {run.stderr.strip()[-300:]}")
    return sorted({p for p in lines[1:] if p}) if run.returncode == 1 else []


def conflicting(
    main: str, heads: dict[int, str], cwd: Path | None = None
) -> tuple[dict[int, list[str]], dict[int, str]]:
    """(PR number -> the paths its head (`heads`) conflicts in with `main`, for the PRs that conflict; PR number ->
    why git could not check it). One PR git cannot merge-tree does not stop the check of the others."""
    found, failed = {}, {}
    for n, head in heads.items():
        try:
            if paths := merge_conflicts(head, main, cwd):
                found[n] = paths
        except RuntimeError as exc:
            failed[n] = str(exc)
    return found, failed


def merge_main(tree: Path, main: str) -> list[str]:
    """Merge main into the PR head checked out at `tree`, as GitHub's "Update branch" would, with `_merge_rules`.
    Returns the paths that really conflict (the merge is then aborted), or [] when it merged or was current. Raises
    RuntimeError when git is too old or the merge failed without a conflict."""
    merge = subprocess.run(
        ["git", *_merge_rules(main), "merge", "--no-ff", "--no-edit", main],
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


FIX_LINES = 40  # failing lines a fix-loop comment carries: the local gate's, or the failed Actions jobs' log tail
NEXT_STEP = "fix each item above, run `uv run dev check`, push, and hand back"
# Credentials a gate or job log can print, cut from a fix-loop comment before it is posted: GitHub tokens (classic and
# fine-grained), API keys of the `sk-` form, AWS access key ids, vendor-prefixed tokens, PEM private keys, JWTs, the
# `user:password` of a URL, and HTTP bearer/token values of 20+ characters (so "Bearer of bad news" stays). Measured
# in docs/research/devkit-reuse-survey.md §2.3. The vendor, private-key and JWT patterns are ported from gitleaks'
# default rules (slack-bot-token, gcp-api-key, pypi-upload-token, npm-access-token, gitlab-pat,
# huggingface-access-token, stripe-access-token, private-key, jwt) in config/gitleaks.toml of
# https://github.com/gitleaks/gitleaks, Copyright (c) 2019 Zachary Rice, MIT License; gitleaks' trailing
# delimiter group is a lookahead here so only the token is cut.
_GITLEAKS_END = r"""(?=[\x60'"\s;]|\\[nr]|$)"""
SECRET = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{8,}|AKIA[A-Z0-9]{12,}"
    r"|xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*|pypi-AgEIcHlwaS5vcmc[\w-]{50,1000}|glpat-[\w-]{20}"
    r"|(?:AIza[\w-]{35}|hf_(?i:[a-z]{34})|(?i:npm_[a-z0-9]{36})|(?:sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99}"
    r"|ey[a-zA-Z0-9]{17,}\.ey[a-zA-Z0-9/\\_-]{17,}\.(?:[a-zA-Z0-9/\\_-]{10,}={0,2})?)" + _GITLEAKS_END + ")"
    r"|(?i:-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----[\s\S]{64,}?KEY(?: BLOCK)?-----)"
    r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|(?<=[Bb]earer )[A-Za-z0-9._~+/=-]{20,}|(?<=[Tt]oken )[A-Za-z0-9_]{20,}"
)
REDACTED = "[redacted]"
# A session link or trailer anywhere in a log line, looser than SESSION_LEAK (which judges a PR body): any scheme or
# none, a subdomain, URL-encoded slashes, and a trailer behind `gh run view --log-failed`'s job/step/time prefix.
SESSION_SCRUB = re.compile(r"claude\.ai(?:/|%2F)code(?:/|%2F)" + "session" + r"|Claude-Session[ \t]*:", re.I)
DURATION = re.compile(r", \d+(?:\.\d+)? s\)$")  # the gate's `RESULT: failed (N stage(s), 30 s)` timing


def scrubbed(text: str) -> str:
    """`text` with each SECRET cut, and each line carrying a session link or a Claude-Session trailer (SESSION_SCRUB)
    replaced, before integrate posts it: a log line is not the worker's to edit once it is on a PR. Session lines go
    whatever the repository's visibility (`repo_public` fails closed to public anyway)."""
    lines = [REDACTED if SESSION_SCRUB.search(ln) else ln for ln in text.split("\n")]
    return SECRET.sub(REDACTED, "\n".join(lines))


def failed_check_lines(info: dict) -> list[str]:
    """The last FIX_LINES lines of `gh run view --job <id> --log-failed` over the PR's failing Actions checks (a
    status context has no log to fetch). A log GitHub will not give (or no gh at all) is one line naming the check."""
    lines: list[str] = []
    for c in latest_checks(info.get("statusCheckRollup") or []):
        if c.get("__typename") == "StatusContext" or c.get("status") != "COMPLETED":
            continue
        if c.get("conclusion") in CHECK_PASSED or not (m := JOB_URL.search(c.get("detailsUrl") or "")):
            continue
        try:
            log = board.gh("run", "view", "-R", board.repo(), "--job", m[1], "--log-failed")
        except (board.BoardError, OSError) as exc:
            lines.append(f"check {_named(c)}: no failed log from GitHub ({exc})")
            continue
        lines += [ln.rstrip() for ln in log.splitlines() if ln.strip()]
    return lines[-FIX_LINES:]


def _fenced(lines: list[str]) -> str:
    """`lines` as a fenced block no backtick run inside can close."""
    fence = "`" * max([3, *(len(r) + 1 for ln in lines for r in re.findall(r"`+", ln))])
    return "\n".join([f"{fence}text", *lines, fence])


def fix_cycle(last: re.Match | None, head: str, counted: bool) -> int:
    """The fix cycle of a send-back on `head` after the latest marker `last`: a cycle is a head integrate failed on,
    so the same head keeps the number (a rerun, new feedback, a new gate duration). After a capped round (cycle
    FIX_CAP, issues blocked 'needs owner') the next head starts at 1: the owner who unblocks grants a fresh round.
    A send-back that is not `counted` (a conflict another merge caused) adds nothing."""
    if last is None:
        return int(counted)
    cycle = int(last[1])
    if last[2] == head:
        return cycle
    return (0 if cycle >= board.FIX_CAP else cycle) + counted


def fix_loop(
    pr_number: int,
    info: dict,
    outcome: str,
    reasons: list[str],
    failing: list[str],
    head: str,
    dry_run: bool = False,
    counted: bool = True,
    step: str = NEXT_STEP,
) -> dict:
    """Close the fix loop on a PR integrate did not merge (#110), with no lead relay: comment the `reasons`, the last
    FIX_LINES `failing` lines and the PR's unresolved feedback on the PR (`scrubbed`), under a board.FIX_MARK that
    `board brief` shows as 'Fix before merge'; then move each issue the PR links back to in-progress, its claim kept,
    so the worker is resumed or respawned on it. The cycle (`fix_cycle`) comes from the gh user's latest marker among
    all the PR's comments; at board.FIX_CAP the issues are blocked 'needs owner' instead. The same content on the same
    head as that marker posts nothing. Returns the result fields: `cycle`, `fix` (in-progress, needs owner, already
    posted, dry run), `resume` (the claims' agent@host) or, when GitHub refused a read or a write, `fix_error`: the
    outcome stands either way."""
    branch = info.get("headRefName", "")
    closes, own = board.linked(info.get("body") or "", branch)
    issues = closes + own
    try:
        fb = board.pr_feedback(pr_number)
        marked = board.fix_comments(pr_number)
        trusted = {board.gh_user()} - {""}
        workers = list(dict.fromkeys(f"{c.agent}@{c.host}" for n in issues if (c := board.get(n).claim())))
    except (board.BoardError, OSError, ValueError, KeyError) as exc:
        print(f"warning: no fix loop on PR #{pr_number}: cannot read it or its issues ({exc}); comment it by hand")
        return {"fix_error": f"cannot read the PR or its issues: {exc}"}
    feedback = board.pr_feedback_lines(fb | {"trusted": fb.get("trusted", set()) | trusted})
    failing = failing[-FIX_LINES:]
    _, last = board.fix_loop_state(marked, trusted)
    mark = board.FIX_MARK.search(last["body"]) if last else None
    timeless = [DURATION.sub(")", ln) if ln.startswith("RESULT:") else ln for ln in failing]  # a rerun's new timing
    key = hashlib.sha256(json.dumps([outcome, reasons, timeless, feedback]).encode()).hexdigest()[:12]
    cycle = fix_cycle(mark, head, counted)
    if mark and (mark[2], mark[3]) == (head, key):
        print(f"PR #{pr_number} already has this failure on {head[:10]} (fix cycle {cycle}): nothing posted")
        return {"cycle": cycle, "fix": "already posted", "resume": workers}
    capped = counted and cycle >= board.FIX_CAP
    who = ", ".join(workers) or "the worker that opened it"
    refs = ", ".join(f"#{n}" for n in issues) or "no linked issue"
    parts = [
        f"`dev integrate`: PR #{pr_number} was not merged: **{outcome}** on head {head[:10]} "
        f"(fix cycle {cycle} of {board.FIX_CAP}).",
        "\n".join(reasons),
    ]
    if failing:
        parts.append(f"Failing lines (last {len(failing)}):\n{_fenced(failing)}")
    if feedback:
        parts.append("Unresolved feedback:\n" + "\n".join(feedback))
    if capped:
        parts.append(
            f"This is fix cycle {cycle} of {board.FIX_CAP}: {refs} blocked 'needs owner'. The owner decides: fix it by "
            "hand, re-scope the issue, or close the PR; the next head after an unblock starts a fresh round."
        )
    else:
        parts.append(
            f"Resume {who} on branch `{branch}`: {step}. Then the lead reruns integrate. {refs} stay in progress, "
            "claims kept; `uv run dev board brief <n>` shows this comment as 'Fix before merge'."
        )
    marker = f"<!-- devkit:fix-loop cycle={cycle} head={head} key={key} outcome={outcome} -->"
    body = marker + "\n" + scrubbed("\n\n".join(p for p in parts if p))
    if dry_run:
        print(f"would comment on PR #{pr_number} and move {refs} to {'blocked' if capped else 'in-progress'}")
        return {"cycle": cycle, "fix": "dry run", "resume": workers}
    try:
        board.gh("pr", "comment", str(pr_number), "-R", board.repo(), "--body", body)
        for n in issues:
            if capped:
                board.block(n, f"needs owner: PR #{pr_number} failed integrate on {cycle} heads (last: {outcome})")
            else:
                board.set_status(n, "in-progress")
    except (board.BoardError, OSError) as exc:
        print(f"warning: the fix loop on PR #{pr_number} failed ({exc}): comment it and move {refs} by hand")
        return {"cycle": cycle, "fix_error": str(exc), "resume": workers}
    fix = "needs owner" if capped else "in-progress"
    print(f"sent back: commented on PR #{pr_number}, {refs} -> {fix} (fix cycle {cycle} of {board.FIX_CAP})")
    return {"cycle": cycle, "fix": fix, "resume": workers}


def send_back(
    pr_number: int, info: dict, main: str, paths: list[str], dry_run: bool = False, counted: bool = True
) -> dict:
    """A real conflict with main, through `fix_loop`: the paths go on the PR and its issues back to in-progress,
    naming the worker to resume. `counted`: integrate's own run on this PR (a conflict another merge caused, found by
    `send_back_conflicting`, is no failed cycle of this PR). Returns `fix_loop`'s fields."""
    files = [f"- `{p}`" for p in paths]
    step = "merge `origin/main`, resolve these files, run `uv run dev docs` and `uv run dev check`, push, and hand back"
    head = info.get("headRefOid") or ""
    reasons = [f"It conflicts with main ({main[:10]}) in:", *files]
    fields = fix_loop(pr_number, info, "conflict", reasons, [], head, dry_run, counted, step)
    if head and not dry_run and "fix_error" not in fields:
        _sent_back_file().write_text(json.dumps(_sent_back() | {str(pr_number): head}) + "\n")
    return fields


def _sent_back_file() -> Path:
    """PR number -> the head last sent back, so a PR its worker has not pushed since is not sent back again."""
    path = state_dir("integrate") / "sent-back.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sent_back() -> dict[str, str]:
    try:
        told = json.loads(_sent_back_file().read_text())
    except (OSError, ValueError):
        return {}
    return told if isinstance(told, dict) else {}  # a corrupt file must not crash integrate after its merge


OPEN_PR_LIMIT = 200  # `gh pr list` returns 30 by default


def send_back_conflicting(merged: int) -> list[int]:
    """After PR `merged` landed (#349): check the new main against every other open, non-draft PR into main with
    `merge_conflicts` and `send_back` each one that conflicts at once, instead of when its own integrate runs (a median
    30.5 min later, docs/research/issue-pr-automation.md §3.2). A PR already sent back on its current head is skipped.
    Prints the count and each PR skipped (git cannot check it, or its send-back failed); returns the PRs sent back.
    Raises BoardError or RuntimeError when listing the PRs or the fetch fails."""
    listed = board.gh(
        "pr", "list", "-R", board.repo(), "--base", "main", "--state", "open", "--limit", str(OPEN_PR_LIMIT),
        "--json", "number,headRefName,headRefOid,body,isDraft",
    )  # fmt: skip
    prs = {p["number"]: p for p in json.loads(listed or "[]") if p["number"] != merged and not p.get("isDraft")}
    if not prs:
        print("conflict check of the new main: no other open PR into main")
        return []
    refs = {n: f"refs/devkit/conflicts/pr-{n}" for n in prs}
    main_ref = "refs/devkit/conflicts/main"
    try:
        pulls = (f"+refs/pull/{n}/head:{r}" for n, r in refs.items())
        git("fetch", "--no-tags", "origin", f"+refs/heads/main:{main_ref}", *pulls)
        main = git("rev-parse", main_ref)
        heads = {n: git("rev-parse", r) for n, r in refs.items()}
        found, failed = conflicting(main, heads)
    finally:
        for ref in (main_ref, *refs.values()):
            git("update-ref", "-d", ref, check=False)
    told = _sent_back()
    again = [n for n in found if told.get(str(n)) == heads[n]]
    sent: list[int] = []
    for n in (n for n in found if n not in again):
        try:  # one PR GitHub refuses must not hide the PRs already commented
            fields = send_back(n, prs[n] | {"headRefOid": heads[n]}, main, found[n], counted=False)
        except (board.BoardError, RuntimeError, OSError) as exc:
            fields = {"fix_error": str(exc)}
        if "fix_error" in fields:
            failed[n] = f"send-back failed: {fields['fix_error']}"
        else:
            sent.append(n)
    kept = {k: v for k, v in _sent_back().items() if k.isdigit() and int(k) in prs}
    _sent_back_file().write_text(json.dumps(kept) + "\n")
    print(
        f"conflict check of the new main {main[:10]}: sent back {len(sent)} of {len(prs)} open PRs"
        + (f" ({', '.join(f'#{n}' for n in sent)})" if sent else "")
        + (f"; already sent back: {', '.join(f'#{n}' for n in again)}" if again else "")
    )
    for n, why in failed.items():
        print(f"  skipped #{n}: {why}")
    return sent


def _head_is(pr_number: int, tip: str) -> bool:
    """After a push, wait until GitHub reports the PR head at `tip` (it lags the push by a moment)."""
    for i in range(PUSH_TRIES):
        if _remote_shas(pr_number)[0] == tip:
            return True
        if i + 1 < PUSH_TRIES:
            time.sleep(PUSH_WAIT_S)
    return False


def result(pr_number: int, code: int, outcome: str, *, judged: str | None = None, **fields: object) -> int:
    """The last line integrate prints, for the lead to branch on: `RESULT-JSON: {"pr", "outcome", "exit", ...}`.
    Outcomes: merged, would-merge, conflict, gate-failed, harness-review, refused, stopped, not-open, waiting.
    The same fields, with the PR head this run `judged` and the time, become the PR's record for `--why` (#348)."""
    payload = {"pr": pr_number, "outcome": outcome, "exit": code, **fields}
    print("RESULT-JSON: " + json.dumps(payload))
    record = payload | {"judged": judged, "when": time.strftime("%Y-%m-%d %H:%M")}
    try:  # a record that cannot be written costs `--why`, never the outcome
        path = _outcome_file(pr_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=1) + "\n")
        tmp.replace(path)
    except OSError as exc:
        print(f"warning: integrate --why will not see this outcome: {exc}")
    return code


def _outcome_file(pr_number: int) -> Path:
    """The last integrate outcome of a PR on this machine (`result`), what `--why` prints."""
    return state_dir("integrate") / f"pr-{pr_number}.outcome.json"


WHY_LINES = 30  # gate lines a record keeps from the failed stages; the log keeps the rest
WHY_TAIL = 10  # lines kept from the end of a run that printed no failed stage (a stand-in command, a killed gate)
WHY_END = 5  # lines kept from the end of each failed stage once the stages run over WHY_LINES
KEY_LINE = re.compile(r"(?:FAILED|ERROR|E) |\d+ (?:failed|passed|errors?)\b")  # pytest's verdict and count lines


def failing_lines(text: str) -> list[str]:
    """What a gate run says failed: each `FAIL` stage line with the indented problem lines under it (blank ones too:
    pytest's output has them) and the `RESULT` line. Over WHY_LINES, each stage keeps its own line, its pytest
    verdict and count lines (KEY_LINE) and, while they fit, its last WHY_END lines, then a count of the rest. Without
    a failed stage, the run's last WHY_TAIL non-blank lines."""
    stages: list[list[str]] = []
    results, inside = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("FAIL "):
            stages.append([line])
            inside = True
        elif inside and (not line or raw[:1].isspace()):
            stages[-1].append(line)
        else:
            inside = False
            if line.startswith("RESULT: failed"):
                results.append(line)
    if not stages and not results:
        return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-WHY_TAIL:]
    full = [ln for s in stages for ln in s]
    if len(full) <= WHY_LINES:
        return full + results
    for end in (WHY_END, 0):  # the stages' ends go first when the verdict lines alone fill the record
        kept = [
            ln for s in stages for i, ln in enumerate(s) if not i or KEY_LINE.match(ln.strip()) or i >= len(s) - end
        ]
        if len(kept) <= WHY_LINES:
            break
    kept = kept[:WHY_LINES]
    return [*kept, f"... {len(full) - len(kept)} more lines in the log", *results]


def _shown(value: object, pad: str) -> str:
    """A recorded value on screen: continuation lines indented by `pad`, so no text in it can pose as a RESULT line."""
    return re.sub(r"\r\n?|\n", "\n" + pad, str(value))


def why(pr_number: int) -> int:
    """`dev integrate <pr> --why`: print the PR's last recorded integrate outcome and whether its head has moved since.
    Reads only: one `gh pr view`, no GitHub write, no gate, no merge. 1 when this machine has no readable record."""
    path = _outcome_file(pr_number)
    if not path.exists():
        print(
            f"no integrate outcome recorded for PR #{pr_number} on this machine ({path}); read the PR's comments "
            f"(`uv run dev board feedback {pr_number}`) instead of rerunning integrate to see a failure"
        )
        return 1
    try:
        rec = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        print(f"no integrate outcome for PR #{pr_number}: its record {path} is unreadable")
        return 1
    judged = str(rec.get("judged") or "")
    print(
        f"PR #{pr_number}: last integrate here at {_shown(rec.get('when', '?'), '  ')}: "
        f"{_shown(rec.get('outcome'), '  ')} (exit {_shown(rec.get('exit'), '  ')}), judged head "
        + _shown(judged[:10] or "unknown", "  ")
    )
    for key, value in rec.items():
        if key in ("pr", "outcome", "exit", "judged", "when"):
            continue
        if isinstance(value, list) and value:
            print(f"  {_shown(key, '    ')}:\n" + "\n".join(f"    {_shown(v, '      ')}" for v in value))
        else:
            print(f"  {_shown(key, '    ')}: {_shown(value, '    ')}")
    try:
        now = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid"))
        head = now["headRefOid"]
    except (board.BoardError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read the PR head from GitHub ({_shown(exc, '  ')}): whether it moved since is unknown")
        return 0
    if not judged:
        print(f"PR head now {head[:10]}; the record names no judged head, so whether it moved is unknown")
    elif head == judged:
        print(f"PR head unchanged since ({head[:10]}): a rerun judges the same commit")
    else:
        print(f"PR head moved since: {judged[:10]} -> {head[:10]}: a rerun judges the new head")
    return 0


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
    for w in worktrees():
        if w["branch"] == info["headRefName"] and w["dirty"] == 0:
            git("worktree", "remove", w["path"], check=False)
    if name := info.get("headRefName"):
        if (others := open_prs_on(name)) is None:
            print(f"kept branch {name}: cannot read its open PRs from GitHub")
        elif others := [n for n in others if n != pr_number]:  # the list can still show the PR just merged as open
            print(f"kept branch {name}: open PR {', '.join(f'#{n}' for n in others)} has it as head")
        else:
            print(drop_branch(name, [tip]))
    try:  # the merge stands whatever this finds: a failure only skips the check
        sent = send_back_conflicting(pr_number)
    except (board.BoardError, RuntimeError, OSError, ValueError) as exc:
        sent = []
        print(f"conflict check of the new main skipped ({exc}): each open PR meets it at its own integrate")
    if not git("-C", str(main_checkout()), "status", "--porcelain", "--untracked-files=no"):
        git("-C", str(main_checkout()), "pull", "--ff-only", check=False)
    print(f"RESULT: merged PR #{pr_number}" + (f"; {', '.join(moved)}" if moved else ""))
    return done(0, "merged", moved=moved, sent_back=sent, **update)


def heartbeat(number: int, message: str) -> None:
    board.comment(number, f"heartbeat ({time.strftime('%Y-%m-%d %H:%M')}): {message}")
