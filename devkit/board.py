"""The work board: GitHub Issues of this repository, driven through `gh`.

Why issues and not files in git: several machines and many agents claim work at once. An issue is one atomic,
shared record with no merge conflicts; a ticket file in git needs merge drivers and an integrator to fold it.
Status is a label (one source); the issue body declares DONE WHEN, owned paths and blockers in fixed sections, so
tools parse them instead of agents re-reading prose.

Status labels:  status:ready  status:in-progress  status:review  status:blocked   (closed = done)
Priority:       P0 critical path . P1 correctness . P2 improvement . P3 nice to have
Type:           type:research  type:build  type:test  type:infra  type:bug  type:epic

Claim protocol (safe across machines): post a claim comment with host, agent, branch and this process's random
IDENTITY, then re-read the thread; the earliest live claim wins and a loser withdraws. A release names the claim it
withdraws (its comment id), so a loser's withdrawal never clears the winner's claim; a bare release clears all.
A claim without a heartbeat comment for STALE_H hours shows as STALE and may be taken over with --force.
Blockers count as done only when they are known closed issues: a typo, a PR number or an issue outside the fetch
keeps the item waiting, where a person sees it, instead of letting it start early.
"""

import functools
import json
import os
import re
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import yaml

from devkit import config
from devkit.paths import default_branch, git, root

STATUSES = ("ready", "in-progress", "review", "blocked")
TYPES = ("research", "build", "test", "infra", "bug", "epic")
PRIORITIES = ("P0", "P1", "P2", "P3")
STALE_H = 6
LABELS = {
    **{f"status:{s}": c for s, c in zip(STATUSES, ("0e8a16", "fbca04", "1d76db", "b60205"), strict=True)},
    **{p: c for p, c in zip(PRIORITIES, ("b60205", "d93f0b", "fbca04", "c5def5"), strict=True)},
    **{f"type:{t}": "5319e7" for t in TYPES},
    "stale": "ededed",
}
AREA_COLOR = "c2e0c6"
STALE_MARK = "<!-- devkit:stale -->"  # the sweep's own comment; it never counts as activity
BLOCK_MARK = "<!-- devkit:block -->"  # `board block`: a manual block that sync never lifts
CLAIM = re.compile(r"<!-- devkit:claim host=(\S+) agent=(\S+) branch=(\S*)(?: id=(\S+))? -->")
RELEASE = re.compile(r"<!-- devkit:release(?: claim=(\S+))? -->")
SECTIONS = ("Context", "DONE WHEN", "Owns", "Blocked by", "Refs", "Parent")
ISSUE_LIMIT = 2000  # explicit: gh paginates up to this many; beyond it, blockers stay waiting (never wrongly done)
PR_LIMIT = 200
PR_FILES_SHOWN = 100  # gh pr list returns at most this many files per PR; more means fetch them page by page
SECTION_LINES = 40
ORIENT_LINES = 70


def new_identity() -> str:
    return f"{os.getpid()}-{secrets.token_hex(4)}"


IDENTITY = new_identity()  # with the host, unique per claiming process even when two share an agent name


class Claim(NamedTuple):
    host: str
    agent: str
    branch: str
    at: str
    token: str | None
    cid: str  # the claim comment's id, which a release names


def comment_id(c: dict) -> str:
    m = re.search(r"issuecomment-(\d+)", c.get("url", ""))
    return m.group(1) if m else str(c.get("id", ""))


def cap(lines: list[str], n: int, more: str) -> list[str]:
    """At most n lines; the last says how many were cut and where to see them."""
    if len(lines) <= n:
        return lines
    return [*lines[: n - 1], f"... {len(lines) - n + 1} more: {more}"]


def owned(path: str, owns: list[str]) -> bool:
    """An Owns entry is an fnmatch glob (`devkit/*.py`, `docs/**`) or a path prefix (`devkit/`, `devkit`)."""
    for o in owns:
        prefix = o.rstrip("/")
        if fnmatchcase(path, o) or path == prefix or (prefix and path.startswith(prefix + "/")):
            return True
    return False


class BoardError(RuntimeError):
    pass


def gh(*args: str, input: str | None = None) -> str:
    out = subprocess.run(["gh", *args], cwd=root(), capture_output=True, text=True, input=input, check=False)
    if out.returncode != 0:
        raise BoardError(f"gh {' '.join(args[:3])}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def repo() -> str:
    url = git("remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    if not m:
        raise BoardError("no GitHub origin remote: run `uv run dev setup` (or `gh repo create`) first")
    return m.group(1)


def sections(body: str) -> dict[str, str]:
    found, current = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"##\s+(.+?)\s*$", line)
        if m and m.group(1) in SECTIONS:
            current = m.group(1)
            found[current] = ""
        elif current:
            found[current] += line + "\n"
    return {k: v.strip() for k, v in found.items()}


def issue_numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"#(\d+)", text or "")]


@dataclass
class Issue:
    number: int
    title: str
    labels: list[str]
    body: str
    state: str
    updated: str
    comments: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.state.upper() == "CLOSED":
            return "done"
        return next((label.split(":", 1)[1] for label in self.labels if label.startswith("status:")), "ready")

    @property
    def priority(self) -> str:
        return next((p for p in PRIORITIES if p in self.labels), "P2")

    @property
    def kind(self) -> str:
        return next((label.split(":", 1)[1] for label in self.labels if label.startswith("type:")), "build")

    @property
    def parts(self) -> dict[str, str]:
        return sections(self.body)

    @property
    def blockers(self) -> list[int]:
        return issue_numbers(self.parts.get("Blocked by", ""))

    def live_claims(self) -> list[Claim]:
        """Claims no release has withdrawn, oldest first. A release names claim ids (comma-separated) or none (all)."""
        live: list[Claim] = []
        for c in self.comments:
            body = c.get("body", "")
            if r := RELEASE.search(body):
                named = set(r.group(1).split(",")) if r.group(1) else None
                live = [x for x in live if not ({x.cid, x.token} & named)] if named else []
            elif m := CLAIM.search(body):
                live.append(
                    Claim(m.group(1), m.group(2), m.group(3), c.get("createdAt", ""), m.group(4), comment_id(c))
                )
        return live

    @property
    def manually_blocked(self) -> bool:
        """The latest claim or release is a `board block` (or a pre-marker release whose reason starts `Blocked:`)."""
        for c in reversed(self.comments):
            body = c.get("body", "")
            if RELEASE.search(body):
                return BLOCK_MARK in body or RELEASE.sub("", body).strip().startswith("Blocked:")
            if CLAIM.search(body):
                return False
        return False

    def claim(self) -> Claim | None:
        """The earliest live claim, or None."""
        return next(iter(self.live_claims()), None)

    def hours_idle(self, now: datetime | None = None) -> float:
        """Hours since the last comment that is not the stale sweep's (the issue's update time if none).

        With comments present `updatedAt` is ignored: label edits move it, and the sweep's own label and comment
        must not make a stale claim look fresh.
        """
        stamps = [c.get("createdAt", "") for c in self.comments if STALE_MARK not in c.get("body", "")]
        latest = max((s for s in stamps if s), default=self.updated)
        then = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        return ((now or datetime.now(UTC)) - then).total_seconds() / 3600


FIELDS = "number,title,labels,body,state,updatedAt"


def _issue(d: dict) -> Issue:
    return Issue(
        d["number"],
        d["title"],
        [x["name"] for x in d.get("labels", [])],
        d.get("body") or "",
        d.get("state", "OPEN"),
        d.get("updatedAt", ""),
        d.get("comments", []),
    )


def fetch(state: str = "open", with_comments: bool = False) -> list[Issue]:
    fields = FIELDS + (",comments" if with_comments else "")
    data = json.loads(
        gh("issue", "list", "-R", repo(), "--state", state, "--limit", str(ISSUE_LIMIT), "--json", fields)
    )
    return [_issue(d) for d in data]


def closed_numbers() -> set[int]:
    """Issues closed as done: the only blockers that count as resolved (closed as not planned does not)."""
    data = json.loads(
        gh(
            "issue",
            "list",
            "-R",
            repo(),
            "--state",
            "closed",
            "--limit",
            str(ISSUE_LIMIT),
            "--json",
            "number,stateReason",
        )
    )
    return {d["number"] for d in data if d.get("stateReason") != "NOT_PLANNED"}


def get(number: int) -> Issue:
    return _issue(json.loads(gh("issue", "view", str(number), "-R", repo(), "--json", FIELDS + ",comments")))


@functools.cache
def repo_labels() -> frozenset[str]:
    return frozenset(
        d["name"] for d in json.loads(gh("label", "list", "-R", repo(), "--limit", "500", "--json", "name"))
    )


def set_status(number: int, status: str) -> None:
    missing = [f"status:{s}" for s in STATUSES if f"status:{s}" not in repo_labels()]
    if missing:  # gh would fail after a partial edit; refuse before touching the issue
        raise BoardError(f"labels missing from the repo: {', '.join(missing)}; run `uv run dev board init`")
    args = ["issue", "edit", str(number), "-R", repo(), "--add-label", f"status:{status}"]
    for s in STATUSES:
        if s != status:
            args += ["--remove-label", f"status:{s}"]
    gh(*args)


def comment(number: int, body: str) -> str:
    """Post a comment; returns its URL (which carries the comment id)."""
    return gh("issue", "comment", str(number), "-R", repo(), "--body-file", "-", input=body)


def ensure_labels() -> list[str]:
    existing = repo_labels()
    made = []
    for name, color in {**LABELS, **dict.fromkeys(area_globs(), AREA_COLOR)}.items():
        if name not in existing:
            gh("label", "create", name, "-R", repo(), "--color", color, "--force")
            made.append(name)
    repo_labels.cache_clear()
    return made


def area_globs(path: Path | None = None) -> dict[str, list[str]]:
    """Area label -> globs from the map (actions/labeler format). Only its simplest shape is accepted,
    `label: [changed-files: [any-glob-to-any-file: globs]]` without `!` negation; anything else is an error here."""
    area_map = config.get().area_map
    path = path or root() / area_map
    if not path.exists():
        return {}
    out: dict[str, list[str]] = {}
    for label, rules in (yaml.safe_load(path.read_text()) or {}).items():
        if not isinstance(rules, list):  # a top-level option such as changed-files-labels-limit
            continue
        for rule in rules:
            matches = rule.get("changed-files") if isinstance(rule, dict) and list(rule) == ["changed-files"] else None
            for m in matches if isinstance(matches, list) else [None]:
                if not isinstance(m, dict) or list(m) != ["any-glob-to-any-file"]:
                    raise BoardError(
                        f"{area_map}: `{label}` uses a rule dev board cannot read (any-glob-to-any-file only)"
                    )
                globs = m["any-glob-to-any-file"]
                globs = [globs] if isinstance(globs, str) else list(globs)
                if any(g.startswith("!") for g in globs):
                    raise BoardError(f"{area_map}: `{label}` negates a glob, which dev board cannot read")
                out.setdefault(label, []).extend(globs)
    return out


def tracked_files() -> list[str]:
    return git("ls-files").splitlines()


def areas(owns: list[str], globs: dict[str, list[str]], files: list[str]) -> list[str]:
    """Area labels for an item: a label applies when one of its globs matches a tracked file under an Owns entry,
    or a stand-in path for the entry itself (so a path nothing occupies yet still gets its area)."""
    stand_ins = {re.sub(r"\*+|\?", "x", o.rstrip("/")) for o in owns}
    return path_areas(stand_ins | {f"{s}/x" for s in stand_ins} | {f for f in files if owned(f, owns)}, globs)


def path_areas(paths: set[str] | list[str], globs: dict[str, list[str]]) -> list[str]:
    """Area labels whose globs match any of these paths (a PR's changed files, in `dev pr`)."""
    return [label for label, gs in globs.items() if any(PurePosixPath(p).full_match(g) for p in paths for g in gs)]


def missing_labels_created(labels: list[str]) -> None:
    """`gh ... --label` fails on a label the repo lacks: create the board's labels first when any is missing."""
    if any(x not in repo_labels() for x in labels):
        ensure_labels()


def body_from(
    context: str, done: list[str], owns: list[str], blocked_by: list[int], refs: list[str], parent: int | None
) -> str:
    out = [f"## Context\n{context.strip() or '-'}", "## DONE WHEN\n" + "\n".join(f"- [ ] {d}" for d in done)]
    if owns:
        out.append("## Owns\n" + "\n".join(f"- `{o}`" for o in owns))
    if blocked_by:
        out.append("## Blocked by\n" + ", ".join(f"#{n}" for n in blocked_by))
    if refs:
        out.append("## Refs\n" + "\n".join(f"- {r}" for r in refs))
    if parent:
        out.append(f"## Parent\n#{parent}")
    return "\n\n".join(out) + "\n"


def new(
    title: str,
    kind: str,
    priority: str,
    context: str,
    done: list[str],
    owns: list[str],
    blocked_by: list[int],
    refs: list[str],
    parent: int | None,
) -> str:
    if kind != "epic" and not done:
        raise BoardError("a work item needs at least one --done (a measurable DONE WHEN condition)")
    closed = closed_numbers() if blocked_by else set()  # not `done`: that is the DONE WHEN list
    status = "blocked" if any(n not in closed for n in blocked_by) else "ready"
    area = areas(owns, area_globs(), tracked_files()) if owns else []
    labels = [f"type:{kind}", priority] + ([] if kind == "epic" else [f"status:{status}"]) + area
    missing_labels_created(area)
    url = gh(
        "issue",
        "create",
        "-R",
        repo(),
        "--title",
        title,
        "--body-file",
        "-",
        "--label",
        ",".join(labels),
        input=body_from(context, done, owns, blocked_by, refs, parent),
    ).strip()
    return f"{url} ({status})"


def _line(i: Issue, done: set[int]) -> str:
    who = ""
    if c := i.claim():
        who = f" @{c[0]}/{c[1]}"
    stale = " STALE" if i.status == "in-progress" and i.comments and i.hours_idle() > STALE_H else ""
    waits = [n for n in i.blockers if n not in done]
    wait = f" waits {','.join(f'#{n}' for n in waits)}" if waits else ""
    return f"#{i.number:<4} {i.priority} {i.kind:<8} {i.status:<11}{who}{stale}{wait}  {i.title if len(i.title) <= 90 else i.title[:89] + '…'}"


def listing(status: str | None = None, kind: str | None = None, as_json: bool = False) -> str:
    issues = fetch(with_comments=True)
    done = closed_numbers()

    def wanted(i: Issue) -> bool:
        if kind:
            return i.kind == kind and (not status or i.status == status)
        return i.kind != "epic" and (not status or i.status == status)

    rows = [i for i in issues if wanted(i)]
    rows.sort(key=lambda i: (STATUSES.index(i.status) if i.status in STATUSES else 9, i.priority, i.number))
    if as_json:
        return json.dumps(
            [
                {
                    "number": i.number,
                    "title": i.title,
                    "status": i.status,
                    "priority": i.priority,
                    "type": i.kind,
                    "claim": c._asdict() if (c := i.claim()) else None,
                    "blockers": i.blockers,
                }
                for i in rows
            ],
            indent=1,
        )
    return "\n".join(_line(i, done) for i in rows) or "no matching issues"


def startable(issues: list[Issue], kind: str | None = None, done: set[int] = frozenset()) -> list[Issue]:
    """Ready items whose blockers are all known closed (`done`); an unknown blocker counts as open."""
    ready = [
        i
        for i in issues
        if i.status == "ready"
        and i.kind != "epic"
        and (not kind or i.kind == kind)
        and all(n in done for n in i.blockers)
    ]
    unblocks = {i.number: sum(i.number in j.blockers for j in issues) for i in ready}
    return sorted(ready, key=lambda i: (i.priority, -unblocks[i.number], i.number))


def next_items(kind: str | None, count: int) -> str:
    issues, done = fetch(), closed_numbers()
    rows = startable(issues, kind, done)[:count]
    return "\n".join(_line(i, done) for i in rows) or "nothing startable (check `uv run dev board list`)"


def claim(number: int, agent: str, branch: str, force: bool = False) -> str:
    issue = get(number)
    if issue.state.upper() == "CLOSED":
        raise BoardError(f"#{number} is closed")
    held = issue.claim()
    if held and not force:
        raise BoardError(
            f"#{number} is claimed by {held[1]} on {held[0]} (branch {held[2]}, {held[3]}); "
            f"idle {issue.hours_idle():.1f} h. Take over only with --force after reading its last comment."
        )
    if held and force:  # clear every live claim, not only the oldest, so the new claim is the earliest
        comment(number, f"{release_tag(issue)}\nTaken over from {held.agent}@{held.host} by {agent}.")
    host, token = socket.gethostname(), IDENTITY
    url = comment(
        number,
        f"<!-- devkit:claim host={host} agent={agent} branch={branch} id={token} -->\n"
        f"Claimed by **{agent}** on `{host}`, branch `{branch}`.",
    )
    mine = comment_id({"url": url}) or token
    time.sleep(2)  # let a racing claim land, then the earliest live claim wins
    winner = get(number).claim()
    if not winner or (winner.host, winner.token) != (host, token):
        comment(number, f"<!-- devkit:release claim={mine} -->\nWithdrawn: {agent}@{host} lost a simultaneous claim.")
        raise BoardError(f"#{number}: lost the claim race to {winner.agent if winner else '?'}; pick another item")
    set_status(number, "in-progress")
    gh("issue", "edit", str(number), "-R", repo(), "--add-assignee", "@me")
    return f"#{number} claimed by {agent}@{host} on {branch}"


def release_tag(issue: Issue) -> str:
    """A release marker naming every live claim (ids without a comment id fall back to their token)."""
    ids = [x.cid or x.token or "" for x in issue.live_claims()]
    return f"<!-- devkit:release claim={','.join(ids)} -->" if ids and all(ids) else "<!-- devkit:release -->"


def release(number: int, reason: str, status: str = "ready") -> None:
    comment(number, f"{release_tag(get(number))}\n{reason}")
    set_status(number, status)


def block(number: int, reason: str) -> None:
    """Withdraw any claim and block with a reason; sync keeps it blocked until a release or claim."""
    comment(number, f"{release_tag(get(number))}\n{BLOCK_MARK}\nBlocked: {reason}")
    set_status(number, "blocked")


def sync() -> list[str]:
    """Blocked <-> ready from the declared blockers. Manual blocks (`board block`) stay until a release or claim."""
    issues, done = fetch(), closed_numbers()
    moved = []
    for i in issues:
        waiting = any(n not in done for n in i.blockers)
        if i.status == "ready" and waiting:
            set_status(i.number, "blocked")
            moved.append(f"#{i.number} -> blocked")
        elif i.status == "blocked" and i.blockers and not waiting and not get(i.number).manually_blocked:
            set_status(i.number, "ready")
            moved.append(f"#{i.number} -> ready")
    return moved


CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)


def linked(body: str, branch: str) -> tuple[list[int], list[int]]:
    """(issues a PR closes by keyword, its own issue from the `<n>-slug` branch when it does not close it).
    `Refs #N` links nothing: a mention must never move another agent's item."""
    closes = list(dict.fromkeys(int(n) for n in CLOSES.findall(body or "")))
    m = re.match(r"(\d+)-", branch or "")
    own = [int(m.group(1))] if m and int(m.group(1)) not in closes else []
    return closes, own


def merged_pr(number: int, body: str, head: str, base: str) -> list[str]:
    """After `dev integrate` merges a PR into the default branch: each issue it closes is closed unless GitHub got
    there first, with one "Merged in PR" comment either way; its own issue, merged without being closed, goes back
    to ready; then blockers are re-synced so dependents start. A merge into another branch closes nothing."""
    if base != default_branch():
        return []
    closes, own = linked(body, head)
    moved = []
    for n in closes:
        note = f"Merged in PR #{number}."
        if get(n).state.upper() == "CLOSED":
            comment(n, note)
        else:
            gh("issue", "close", str(n), "-R", repo(), "--reason", "completed", "--comment", note)
            moved.append(f"#{n} -> done")
    for n in own:
        if (i := get(n)).state.upper() != "CLOSED" and i.status == "review":
            release(n, f"PR #{number} merged without closing #{n}; back to ready.")
            moved.append(f"#{n} -> ready")
    return moved + sync()


def sweep(hours: float = STALE_H, now: datetime | None = None, only: list[int] | None = None) -> list[str]:
    """Label and comment once on claimed in-progress items idle over `hours`; unlabel them once they move again.
    `only` limits the sweep to those issue numbers (a manual sweep with a low threshold must not touch the rest)."""
    ensure_labels()
    moved = []
    for i in (x for x in fetch(with_comments=True) if not only or x.number in only):
        stale = i.status == "in-progress" and i.claim() is not None and i.hours_idle(now) > hours
        if stale and "stale" not in i.labels:
            c = i.claim()
            gh("issue", "edit", str(i.number), "-R", repo(), "--add-label", "stale")
            comment(
                i.number,
                f"{STALE_MARK}\nStale: no comment for {i.hours_idle(now):.1f} h on the claim by {c.agent}@{c.host} "
                f'(branch `{c.branch}`). Claimant: post `uv run dev board beat {i.number} "<progress>"`. Others: '
                f"read the thread, then take over with `uv run dev board claim {i.number} --agent <you> --force`.",
            )
            moved.append(f"#{i.number} -> stale")
        elif not stale and "stale" in i.labels:
            gh("issue", "edit", str(i.number), "-R", repo(), "--remove-label", "stale")
            moved.append(f"#{i.number} -> fresh")
    return moved


def event(name: str, payload: dict, now: datetime | None = None) -> list[str]:
    """The scheduled board job (name + the JSON at GITHUB_EVENT_PATH): re-sync blockers, then sweep stale claims."""
    if name in ("schedule", "workflow_dispatch"):
        hours = (payload.get("inputs") or {}).get("hours") or STALE_H
        return sync() + sweep(float(hours), now)
    raise BoardError(f"no board automation for the {name!r} event")


def pr_files(number: int) -> list[str]:
    """Every file a PR changes, page by page (gh pr list stops at 100 per PR)."""
    out = gh("api", "--paginate", f"repos/{repo()}/pulls/{number}/files", "--jq", ".[].filename")
    return [line for line in out.splitlines() if line]


def in_flight_files() -> dict[str, list[str]]:
    """Files other work is changing now: open PRs (any machine) and local agent worktrees."""
    files: dict[str, list[str]] = {}
    try:
        prs = gh(
            "pr",
            "list",
            "-R",
            repo(),
            "--state",
            "open",
            "--limit",
            str(PR_LIMIT),
            "--json",
            "number,headRefName,files",
        )
        for pr in json.loads(prs):
            paths = [f["path"] for f in pr.get("files", [])]
            if len(paths) >= PR_FILES_SHOWN:
                paths = pr_files(pr["number"])
            for f in paths:
                files.setdefault(f, []).append(f"PR #{pr['number']}")
    except BoardError:
        pass
    for line in git("worktree", "list", "--porcelain").split("\n\n"):
        m = re.search(r"^worktree (.+)$", line, re.M)
        b = re.search(r"^branch refs/heads/(.+)$", line, re.M)
        if not m or not b or b.group(1) == "main":
            continue
        changed = git("diff", "--name-only", f"main...{b.group(1)}", check=False).splitlines()
        changed += git("-C", m.group(1), "diff", "--name-only", "HEAD", check=False).splitlines()
        for f in set(changed):
            files.setdefault(f, []).append(f"worktree {b.group(1)}")
    return files


def brief(number: int) -> str:
    """Everything an agent needs to start one item, so its spawn prompt can be one line."""
    i = get(number)
    p = i.parts
    out = [f"# #{i.number} {i.title}", f"{i.priority} type:{i.kind} status:{i.status}"]
    if c := i.claim():
        out.append(f"claimed by {c.agent}@{c.host} on branch {c.branch}")
    more = f"`gh issue view {i.number}`"
    for name in ("Context", "DONE WHEN", "Owns", "Blocked by", "Refs", "Parent"):
        if p.get(name):
            out += [f"\n## {name}", *cap(p[name].splitlines(), SECTION_LINES, more)]
    if i.comments:
        last = [
            f"- {c.get('author', {}).get('login', '?')} {c.get('createdAt', '')[:16]}: "
            f"{RELEASE.sub('', CLAIM.sub('', c.get('body', ''))).strip()[:300]}"
            for c in i.comments[-3:]
        ]
        out += ["\n## Last comments", *cap("\n".join(last).splitlines(), SECTION_LINES, more)]
    owns = re.findall(r"`([^`]+)`", p.get("Owns", ""))
    busy = in_flight_files()
    hot = sorted(f"{f} ({', '.join(w)})" for f, w in busy.items() if owned(f, owns))
    if busy:
        out.append("\n## Files other work is changing (hotspots: coordinate, do not edit blindly)")
        rows = [f"- {h}" for h in hot] or [f"- {len(busy)} files in flight elsewhere, none under this item's Owns"]
        out += cap(rows, SECTION_LINES, "`uv run dev status` and `gh pr list`")
    slug = re.sub(r"[^a-z0-9]+", "-", i.title.lower()).strip("-")[:40]
    out.append(f"""
## Commands
- branch: `{i.number}-{slug}`; claim: `uv run dev board claim {i.number} --agent <you> --branch {i.number}-{slug}`
- navigate: `uv run dev code outline|show|refs|find ...`; tests while iterating: `uv run dev test --affected`
- gate once at the end: `uv run dev check`; then push and open the PR: `uv run dev pr {i.number}`
- heartbeat every few hours: `uv run dev board beat {i.number} "<numbers>"`""")
    return "\n".join(out)
