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

Relationships are GitHub's native issue links: blocked-by (and so blocking) and the sub-issue parent, set through
GraphQL (addBlockedBy, addSubIssue), across repositories too. They are the one source of truth: the body's
`Blocked by` and `Parent` sections are rendered from them under LINKS_MARK. A body without the mark predates native
links; its sections still count, and `board sync` backfills them as native links, then renders the body. A blocked-by
link that would close a cycle is refused with the cycle's path. `Refs` stays plain mentions (relates-to).
"""

import functools
import json
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import yaml

from devkit import backoff, config
from devkit.paths import default_branch, git, root
from devkit.ran import Ran

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
LINKS_MARK = "<!-- devkit:links native -->"  # Blocked by / Parent below are rendered from native links
# An issue reference: a URL, `owner/repo#n`, or `#n` (this repository).
REF = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)|(?<![\w/.-])(?:([\w.-]+/[\w.-]+))?#(\d+)")
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


# GitHub's secondary rate limits answer 403 or 429; its guidance is to wait at least a minute, then exponentially
# longer (docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api). gh prints no headers, so
# the wait is GH_FLOOR plus full jitter (devkit.backoff). A refused request did nothing, so a retry is safe.
SECONDARY_LIMIT = re.compile(r"secondary rate limit|abuse detection|HTTP 429", re.I)
GH_TRIES = 3
GH_FLOOR, GH_BASE, GH_CAP = 60.0, 30.0, 240.0  # seconds
GH_SLEEP = time.sleep
GH_RNG = random.Random()  # noqa: S311 - retry jitter, not a secret


def gh(*args: str, input: str | None = None) -> str:
    def hint(out: subprocess.CompletedProcess) -> float | None:
        return 0.0 if out.returncode != 0 and SECONDARY_LIMIT.search(out.stderr + out.stdout) else None

    out = backoff.retry(
        lambda: subprocess.run(["gh", *args], cwd=root(), capture_output=True, text=True, input=input, check=False),
        hint,
        tries=GH_TRIES,
        sleep=GH_SLEEP,
        rng=GH_RNG,
        base=GH_BASE,
        cap=GH_CAP,
        floor=GH_FLOOR,
        on_wait=lambda w: print(
            f"gh {' '.join(args[:2])}: GitHub secondary rate limit; retrying in {w:.0f}s", file=sys.stderr
        ),
    )
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


type Ref = int | str  # an issue of this repository by number, or another's as `owner/repo#n`


def ref_of(where: str | None, number: int | str) -> Ref:
    """`number` in repository `where` (None: this one), as a Ref: an int when it is this repository."""
    n = int(number)
    return n if not where or where.lower() == repo().lower() else f"{where}#{n}"


def refs_in(text: str) -> list[Ref]:
    """Issue references in text (URLs, `owner/repo#n`, `#n`), deduplicated in order."""
    found = [ref_of(m.group(1) or m.group(3), m.group(2) or m.group(4)) for m in REF.finditer(text or "")]
    return list(dict.fromkeys(found))


def parse_ref(text: int | str) -> Ref:
    """A command-line reference: `12`, `#12`, `owner/repo#12` or an issue URL."""
    if isinstance(text, int) or text.isdigit():
        return int(text)
    found = refs_in(text)
    if len(found) != 1:
        raise BoardError(f"not one issue reference: {text!r} (use 12, #12, owner/repo#12 or an issue URL)")
    return found[0]


def show(ref: Ref) -> str:
    return f"#{ref}" if isinstance(ref, int) else ref


def split(ref: Ref) -> tuple[str, str, int]:
    """(owner, name, number) of a Ref."""
    where, _, n = (f"{repo()}#{ref}" if isinstance(ref, int) else ref).rpartition("#")
    owner, _, name = where.partition("/")
    return owner, name, int(n)


def ref_from_url(url: str) -> Ref:
    m = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", url or "")
    if not m:
        raise BoardError(f"not an issue URL: {url}")
    return ref_of(m.group(1), m.group(2))


def graphql(query: str, **variables: str | int | bool) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        typed = isinstance(v, int)  # -F sends ints and booleans typed, -f sends a string
        args += ["-F" if typed else "-f", f"{k}={str(v).lower() if isinstance(v, bool) else v}"]
    return json.loads(gh(*args))["data"]


ISSUE_QUERY = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { issue(number: $number) {
    id state stateReason blockedBy(first: 100) { nodes { url } } } } }"""
ADD_BLOCKED_BY = """mutation($issue: ID!, $blocker: ID!) {
  addBlockedBy(input: {issueId: $issue, blockingIssueId: $blocker}) { issue { number } } }"""
ADD_SUB_ISSUE = """mutation($parent: ID!, $sub: ID!, $replace: Boolean!) {
  addSubIssue(input: {issueId: $parent, subIssueId: $sub, replaceParent: $replace}) { issue { number } } }"""


def remote(ref: Ref) -> dict:
    """An issue's node id, state, stateReason and native blockers (as Refs), by GraphQL; any repository."""
    owner, name, n = split(ref)
    issue = (graphql(ISSUE_QUERY, owner=owner, name=name, number=n).get("repository") or {}).get("issue")
    if not issue:
        raise BoardError(f"{show(ref)}: no such issue, or no access to it")
    issue["blockers"] = [ref_from_url(x["url"]) for x in (issue.get("blockedBy") or {}).get("nodes") or []]
    return issue


def cycles(starts: list[Ref], deps: Callable[[Ref], list[Ref]]) -> list[list[Ref]]:
    """Each blocked-by cycle reachable from `starts` once, as the path that closes it. Iterative (a port of
    a sibling project's board `_cycles`), so a chain of a thousand issues meets no recursion limit."""
    state: dict[Ref, int] = {}  # 1: on the current path, 2: finished
    found: list[list[Ref]] = []
    for start in starts:
        if start in state:
            continue
        state[start] = 1
        path = [start]
        stack = [iter(deps(start))]
        while stack:
            dep = next(stack[-1], None)
            if dep is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(dep) == 1:
                found.append([*path[path.index(dep) :], dep])
            elif dep not in state:
                state[dep] = 1
                path.append(dep)
                stack.append(iter(deps(dep)))
    return found


def closing_cycle(issue: Ref, blocker: Ref, deps: Callable[[Ref], list[Ref]]) -> list[Ref] | None:
    """The cycle that `issue` blocked by `blocker` would close (issue -> blocker -> ... -> issue), or None."""
    if issue == blocker:
        return [issue, issue]

    def only_the_new_edge(r: Ref) -> list[Ref]:  # from `issue`, walk the new edge alone: does blocker reach issue?
        return [blocker] if r == issue else deps(r)

    return next((c for c in cycles([issue], only_the_new_edge) if c[0] == issue), None)


class Graph:
    """Blocked-by edges as `deps(ref)`: fetched issues' native links (their body's when gh gave none) from memory,
    any other issue (closed, or in another repository) by GraphQL, once each. Body-only declarations are not edges
    until linked, so of two that contradict each other the first linked wins and the second is refused. An
    unreadable issue raises: a cycle check that skips part of the graph proves nothing."""

    def __init__(self, issues: list["Issue"]):
        self.known: dict[Ref, list[Ref]] = {
            i.number: list(i.links.blocked_by) if i.links else i.blockers for i in issues
        }

    def __call__(self, ref: Ref) -> list[Ref]:
        if ref not in self.known:
            self.known[ref] = remote(ref)["blockers"]
        return self.known[ref]

    def add(self, issue: Ref, blocker: Ref) -> None:
        self(issue).append(blocker)


class Links(NamedTuple):
    """An issue's native relationships, as GitHub returns them."""

    blocked_by: list[Ref]
    parent: Ref | None
    blocking: list[Ref] = []  # noqa: RUF012 - a NamedTuple default, never mutated
    subs: list[Ref] = []  # noqa: RUF012


@dataclass
class Issue:
    number: int
    title: str
    labels: list[str]
    body: str
    state: str
    updated: str
    comments: list[dict] = field(default_factory=list)
    links: Links | None = None  # None: not fetched (an old gh, or a test); the body's sections are all there is

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
    def rendered(self) -> bool:
        """The body's Blocked by / Parent were rendered from native links, so they declare nothing of their own."""
        return LINKS_MARK in self.body

    @property
    def declared_blockers(self) -> list[Ref]:
        """Blockers only the body declares (a body from before native links); `board sync` backfills them."""
        return [] if self.rendered else refs_in(self.parts.get("Blocked by", ""))

    @property
    def declared_parent(self) -> Ref | None:
        return None if self.rendered else next(iter(refs_in(self.parts.get("Parent", ""))), None)

    @property
    def blockers(self) -> list[Ref]:
        """Native blockers, plus any the body still declares: an unsynced declaration keeps the item waiting."""
        native = self.links.blocked_by if self.links else []
        return list(dict.fromkeys([*native, *self.declared_blockers]))

    @property
    def parent(self) -> Ref | None:
        return (self.links.parent if self.links else None) or self.declared_parent

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
LINK_FIELDS = "blockedBy,parent"  # gh 2.80+; an older gh answers "Unknown JSON field" and the body is read instead
ALL_LINK_FIELDS = LINK_FIELDS + ",blocking,subIssues"


def _links(d: dict) -> Links | None:
    if "blockedBy" not in d:
        return None

    def refs(key: str) -> list[Ref]:
        return [ref_from_url(x["url"]) for x in ((d.get(key) or {}).get("nodes") or [])]

    parent = d.get("parent")
    return Links(
        refs("blockedBy"), ref_from_url(parent["url"]) if parent else None, refs("blocking"), refs("subIssues")
    )


def _issue(d: dict) -> Issue:
    return Issue(
        d["number"],
        d["title"],
        [x["name"] for x in d.get("labels", [])],
        d.get("body") or "",
        d.get("state", "OPEN"),
        d.get("updatedAt", ""),
        d.get("comments", []),
        _links(d),
    )


def gh_json(*args: str, fields: str, links: str) -> object:
    """`gh ... --json fields,links`; without the link fields when this gh does not know them."""
    try:
        return json.loads(gh(*args, "--json", f"{fields},{links}"))
    except BoardError as e:
        if "Unknown JSON field" not in str(e):
            raise
        return json.loads(gh(*args, "--json", fields))


def fetch(state: str = "open", with_comments: bool = False, links: str = LINK_FIELDS) -> list[Issue]:
    fields = FIELDS + (",comments" if with_comments else "")
    args = ("issue", "list", "-R", repo(), "--state", state, "--limit", str(ISSUE_LIMIT))
    return [_issue(d) for d in gh_json(*args, fields=fields, links=links)]


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
    args = ("issue", "view", str(number), "-R", repo())
    return _issue(gh_json(*args, fields=FIELDS + ",comments", links=ALL_LINK_FIELDS))


def resolved(issues: list[Issue]) -> set[Ref]:
    """Blockers known done: issues of this repository closed as done, and other repositories' blockers closed as
    done (asked by GraphQL; one that cannot be read stays waiting)."""
    done: set[Ref] = set(closed_numbers())
    for ref in {b for i in issues for b in i.blockers if isinstance(b, str)}:
        try:
            r = remote(ref)
        except BoardError:
            continue
        if r.get("state") == "CLOSED" and r.get("stateReason") != "NOT_PLANNED":
            done.add(ref)
    return done


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


def path_history() -> tuple[set[str], set[str]] | None:
    """(every path ever committed in HEAD's history, the paths tracked now), or None outside a git checkout."""
    try:
        ever = git("log", "--format=", "--name-only", "--no-renames", "HEAD")
        return set(ever.splitlines()) - {""}, set(tracked_files())
    except RuntimeError:
        return None


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
    context: str,
    done: list[str],
    owns: list[str],
    blocked_by: list[Ref],
    refs: list[str],
    parent: Ref | None,
    rendered: bool = False,
) -> str:
    out = [f"## Context\n{context.strip() or '-'}", "## DONE WHEN\n" + "\n".join(f"- [ ] {d}" for d in done)]
    if owns:
        out.append("## Owns\n" + "\n".join(f"- `{o}`" for o in owns))
    if refs:
        out.append("## Refs\n" + "\n".join(f"- {r}" for r in refs))
    return render_links("\n\n".join(out) + "\n", blocked_by, parent, rendered)


LINK_SECTIONS = ("Blocked by", "Parent")


def render_links(body: str, blocked_by: list[Ref], parent: Ref | None, rendered: bool = True) -> str:
    """The body with its Blocked by / Parent sections replaced by these links, and LINKS_MARK first when `rendered`
    (before any section, so no section reads it). A link section ends at the next `## ` heading; everything else
    is kept as written."""
    kept, skipping = [], False
    for line in (body or "").splitlines():
        if m := re.match(r"##\s+(.+?)\s*$", line):
            skipping = m.group(1) in LINK_SECTIONS
        if not skipping and line.strip() != LINKS_MARK:
            kept.append(line)
    out = [LINKS_MARK if rendered else "", "\n".join(kept).strip()]
    if blocked_by:
        out.append("## Blocked by\n" + ", ".join(show(b) for b in blocked_by))
    if parent:
        out.append(f"## Parent\n{show(parent)}")
    return "\n\n".join(x for x in out if x) + "\n"


def is_done(ref: Ref, closed: set[int], info: dict | None = None) -> bool:
    """A blocker is done when closed as done: this repository's from `closed`, another's from its GraphQL `info`."""
    if isinstance(ref, int):
        return ref in closed
    return bool(info) and info.get("state") == "CLOSED" and info.get("stateReason") != "NOT_PLANNED"


def new(
    title: str,
    kind: str,
    priority: str,
    context: str,
    done: list[str],
    owns: list[str],
    blocked_by: list[int | str],
    refs: list[str],
    parent: int | str | None,
) -> str:
    """Create an item, then link it natively (blocked-by, sub-issue of `parent`); the body renders those links.
    Every reference is looked up before anything is created, so a typo or an unreadable issue creates nothing."""
    if kind != "epic" and not done:
        raise BoardError("a work item needs at least one --done (a measurable DONE WHEN condition)")
    blockers = list(dict.fromkeys(parse_ref(b) for b in blocked_by))
    up = parse_ref(parent) if parent else None
    info = {r: remote(r) for r in [*blockers, *([up] if up else [])]}
    closed = closed_numbers() if blockers else set()  # not `done`: that is the DONE WHEN list
    status = "blocked" if any(not is_done(b, closed, info[b]) for b in blockers) else "ready"
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
        input=body_from(context, done, owns, blockers, refs, up, rendered=bool(info)),
    ).strip()
    if info:
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        try:
            me = remote(number)["id"]
            for b in blockers:
                graphql(ADD_BLOCKED_BY, issue=me, blocker=info[b]["id"])
            if up:
                graphql(ADD_SUB_ISSUE, parent=info[up]["id"], sub=me, replace=False)
        except BoardError as e:  # unmark the body, so `board sync` backfills the links still missing
            plain = body_from(context, done, owns, blockers, refs, up)
            gh("issue", "edit", str(number), "-R", repo(), "--body-file", "-", input=plain)
            raise BoardError(f"{url} created, but linking failed ({e}); `uv run dev board sync` retries") from e
    return f"{url} ({status})"


def cycle_text(path: list[Ref]) -> str:
    return " -> ".join(map(show, path))


def settle(i: Issue, deps: Graph, blockers: list[Ref] = (), parent: Ref | None = None) -> list[str]:
    """Native links for `blockers` and `parent` plus what i's body still declares, then the body rendered from
    them. A link that would close a cycle is refused; a body whose links failed for another reason stays unmarked
    (so a later sync retries). An explicit `parent` replaces a native one; a declared one never does."""
    if i.links is None:
        return []
    native, up, msgs, failed, me = list(i.links.blocked_by), i.links.parent, [], False, None
    for b in dict.fromkeys([*blockers, *i.declared_blockers]):
        if b in native:
            continue
        if path := closing_cycle(i.number, b, deps):
            msgs.append(f"#{i.number}: refused blocked by {show(b)}: it closes the cycle {cycle_text(path)}")
            continue
        try:
            me = me or remote(i.number)["id"]
            graphql(ADD_BLOCKED_BY, issue=me, blocker=remote(b)["id"])
        except BoardError as e:
            failed = True
            msgs.append(f"#{i.number}: blocked by {show(b)} not linked: {e}")
            continue
        native.append(b)
        deps.add(i.number, b)
        msgs.append(f"#{i.number} blocked by {show(b)}: linked")
    want = parent or i.declared_parent
    if want and want != up:
        if up and not parent:
            msgs.append(f"#{i.number}: kept native parent {show(up)}; the body named {show(want)}")
        else:
            try:
                me = me or remote(i.number)["id"]
                graphql(ADD_SUB_ISSUE, parent=remote(want)["id"], sub=me, replace=bool(up))
                up = want
                msgs.append(f"#{i.number} sub-issue of {show(want)}: linked")
            except BoardError as e:
                failed = True
                msgs.append(f"#{i.number}: sub-issue of {show(want)} not linked: {e}")
    stated = (set(refs_in(i.parts.get("Blocked by", ""))), next(iter(refs_in(i.parts.get("Parent", ""))), None))
    if not failed and (stated != (set(native), up) or (not i.rendered and (stated[0] or stated[1]))):
        i.body = render_links(i.body, native, up)
        gh("issue", "edit", str(i.number), "-R", repo(), "--body-file", "-", input=i.body)
    i.links = i.links._replace(blocked_by=native, parent=up)
    return msgs


def link(number: int, blocked_by: list[int | str], parent: int | str | None = None) -> list[str]:
    """Add native links to an existing item: blocked by each of `blocked_by`, sub-issue of `parent`. A blocked-by
    link that would close a cycle refuses the whole call, before anything changes, and names the cycle."""
    blockers = list(dict.fromkeys(parse_ref(b) for b in blocked_by))
    deps = Graph(fetch())
    for b in blockers:
        if path := closing_cycle(number, b, deps):
            raise BoardError(f"refused: #{number} blocked by {show(b)} closes the cycle {cycle_text(path)}")
    i = get(number)
    if i.links is None:
        raise BoardError("this gh cannot read native issue links (blockedBy, parent); upgrade gh")
    return settle(i, deps, blockers, parse_ref(parent) if parent else None)


def _line(i: Issue, done: set[Ref]) -> str:
    who = ""
    if c := i.claim():
        who = f" @{c[0]}/{c[1]}"
    stale = " STALE" if i.status == "in-progress" and i.comments and i.hours_idle() > STALE_H else ""
    waits = [n for n in i.blockers if n not in done]
    wait = f" waits {','.join(map(show, waits))}" if waits else ""
    return f"#{i.number:<4} {i.priority} {i.kind:<8} {i.status:<11}{who}{stale}{wait}  {i.title if len(i.title) <= 90 else i.title[:89] + '…'}"


def listing(status: str | None = None, kind: str | None = None, as_json: bool = False) -> str:
    issues = fetch(with_comments=True)
    done = resolved(issues)

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


class Rank(NamedTuple):
    """What starting an item opens up: its effective priority (the highest of its own and of everything it
    transitively blocks; `inherited` names the item it came from) and the open non-epic items downstream of it."""

    priority: str
    inherited: Ref | None
    downstream: list[Ref]


def ref_key(r: Ref) -> tuple[bool, int | str]:
    """Sorts this repository's issues by number before other repositories' references."""
    return (isinstance(r, str), r)


def ranks(issues: list[Issue]) -> dict[Ref, Rank]:
    """Each fetched item's Rank over the blocked-by graph of the fetched (open) items. Priority inheritance lets a
    P3 that blocks a P0 start like a P0; a cycle or a closed blocker adds nothing."""
    by = {i.number: i for i in issues}
    dependents: dict[Ref, list[Ref]] = {}
    for i in issues:
        for b in i.blockers:
            dependents.setdefault(b, []).append(i.number)
    out: dict[Ref, Rank] = {}
    for i in issues:
        seen: set[Ref] = set()
        todo: list[Ref] = [i.number]
        while todo:
            for d in dependents.get(todo.pop(), []):
                if d not in seen and d != i.number:
                    seen.add(d)
                    todo.append(d)
        prio, source = i.priority, None
        for d in sorted(seen, key=ref_key):
            if by[d].priority < prio:
                prio, source = by[d].priority, d
        out[i.number] = Rank(prio, source, sorted((d for d in seen if by[d].kind != "epic"), key=ref_key))
    return out


def reason(r: Rank) -> str:
    prio = f"{r.priority} from {show(r.inherited)}" if r.inherited is not None else r.priority
    shown = ",".join(map(show, r.downstream[:5])) + (",…" if len(r.downstream) > 5 else "")
    return f"[{prio}, unblocks {len(r.downstream)}{': ' + shown if r.downstream else ''}]"


def startable(issues: list[Issue], kind: str | None = None, done: set[Ref] = frozenset()) -> list[Issue]:
    """Ready items whose blockers are all known closed (`done`); an unknown blocker counts as open. Ranked by
    effective priority, then by how many items they transitively unblock, then by number (oldest first)."""
    ready = [
        i
        for i in issues
        if i.status == "ready"
        and i.kind != "epic"
        and (not kind or i.kind == kind)
        and all(n in done for n in i.blockers)
    ]
    rank = ranks(issues)
    return sorted(ready, key=lambda i: (rank[i.number].priority, -len(rank[i.number].downstream), i.number))


def _ranked_lines(rows: list[Issue], issues: list[Issue], done: set[Ref]) -> str:
    rank = ranks(issues)
    return "\n".join(f"{_line(i, done)}  {reason(rank[i.number])}" for i in rows)


def next_items(kind: str | None, count: int) -> str:
    issues = fetch()
    done = resolved(issues)
    rows = startable(issues, kind, done)[:count]
    return _ranked_lines(rows, issues, done) or "nothing startable (check `uv run dev board list`)"


def owns_of(i: Issue) -> list[str]:
    """The item's Owns entries: backticked paths or globs, a comma-separated list inside one backtick included."""
    found = re.findall(r"`([^`]+)`", i.parts.get("Owns", ""))
    return [p.strip() for entry in found for p in entry.split(",") if p.strip()]


def overlap(a: list[str], b: list[str]) -> bool:
    """Two Owns lists can touch the same file: an entry of one is a path, glob or prefix the other covers."""
    return any(owned(x, [y]) or owned(y, [x]) for x in a for y in b)


def gone_owns(owns: list[str], deleted: Iterable[str], after: Iterable[str]) -> list[str]:
    """Owns entries that matched a path and match none now: a `deleted` path matches, no `after` path does
    (docs/research/decision-records-and-drift.md check 2). An entry for a file never created is not gone."""
    deleted, after = list(deleted), list(after)
    return [o for o in owns if any(owned(p, [o]) for p in deleted) and not any(owned(p, [o]) for p in after)]


def deleting_owns(
    issues: list[Issue], deleted: set[str], after: set[str], body: str, branch: str
) -> dict[int, list[str]]:
    """Open issue -> its Owns entries a merge empties, leaving out the PR's own issues: those its `body` closes or
    refs, and the `<n>-slug` of its `branch` (a migration owns what it moves)."""
    closes, own = linked(body, branch)
    mine = {*closes, *own, *keyword_numbers(REFS, body)}
    found = {i.number: gone_owns(owns_of(i), deleted, after) for i in issues if i.number not in mine}
    return {n: paths for n, paths in sorted(found.items()) if paths}


def wave(issues: list[Issue], n: int, done: set[Ref]) -> list[Issue]:
    """Up to n startable items, in `next` order, whose Owns overlap neither each other nor any item in progress or
    in review (greedy, after a sibling project's batching wave). An item with no Owns overlaps nothing."""
    busy = [owns_of(i) for i in issues if i.status in ("in-progress", "review")]
    chosen: list[Issue] = []
    for i in startable(issues, done=done):
        if len(chosen) == n:
            break
        mine = owns_of(i)
        if not any(overlap(mine, other) for other in busy):
            chosen.append(i)
            busy.append(mine)
    return chosen


def wave_items(n: int) -> str:
    issues = fetch()
    done = resolved(issues)
    return _ranked_lines(wave(issues, n, done), issues, done) or (
        "nothing startable with Owns disjoint from work in progress (check `uv run dev board next`)"
    )


# Conventions every open item meets (docs/specs/multi-agent.md, "Conventions"). `board lint` reports them, the gate
# and orient show them, and `--fix` makes the label edits that follow from the item itself.
TITLE_MAX = 100
TITLE_PREFIX = re.compile(r"(Research|Experiment|Epic):\s")
PREFIX_KIND = {"Research": "research", "Experiment": "research", "Epic": "epic"}
COMPONENT_TITLE = re.compile(r"[\w.+#/-]+(?: [\w.+#/-]+){0,3}: \S")  # '<component>: <outcome>'
NEEDS_OWNS = ("build", "infra")
FURTHEST = ("review", "in-progress", "blocked", "ready")  # of several status labels, --fix keeps the furthest


class Finding(NamedTuple):
    number: int
    problem: str
    add: tuple[str, ...] = ()  # the label edit --fix makes; none: a person fixes it
    remove: tuple[str, ...] = ()
    warn: bool = False


def done_items(i: Issue) -> list[str]:
    """DONE WHEN entries that say something: a bare `- [ ]`, `-` or number is not a condition."""
    out = []
    for line in i.parts.get("DONE WHEN", "").splitlines():
        text = re.sub(r"^\s*[-*+]\s*(\[[ xX]\]\s*)?", "", line).strip()
        if re.search(r"[^\W\d_]", text):
            out.append(text)
    return out


def _label_findings(i: Issue, done: set[Ref]) -> tuple[str, list[Finding]]:
    """(the item's kind, "" when no label or prefix says it; its label findings). A title prefix names the kind, so
    it fixes the type label."""
    n, found = i.number, []
    m = TITLE_PREFIX.match(i.title)
    said = PREFIX_KIND[m.group(1)] if m else None
    types = [x for x in i.labels if x.startswith("type:")]
    one = len(types) == 1 and types[0].split(":", 1)[1] in TYPES
    kind = said or (i.kind if one else "")
    if not one:
        fix = ((f"type:{said}",), tuple(t for t in types if t != f"type:{said}")) if said else ((), ())
        found.append(Finding(n, f"needs exactly one type: label, has {', '.join(types) or 'none'}", *fix))
    elif said and types[0] != f"type:{said}":
        found.append(Finding(n, f"title says {m[1]}: but the label is {types[0]}", (f"type:{said}",), (types[0],)))
    prios = [p for p in PRIORITIES if p in i.labels]
    if len(prios) != 1:
        fix = (("P2",), ()) if not prios else ((), tuple(prios[1:]))  # the board's default, or keep the highest
        found.append(Finding(n, f"needs exactly one priority label, has {', '.join(prios) or 'none'}", *fix))
    states = [x for x in i.labels if x.startswith("status:")]
    valid = [s for s in FURTHEST if f"status:{s}" in states]
    waiting = [b for b in i.blockers if b not in done]
    if (len(states) > 1 if kind == "epic" else len(states) != 1) or len(valid) < len(states):
        want = [
            f"status:{s}" for s in (valid[:1] if kind == "epic" else valid[:1] or ["blocked" if waiting else "ready"])
        ]
        add = tuple(s for s in want if s not in states)
        rest = tuple(s for s in states if s not in want)
        what = "at most one" if kind == "epic" else "exactly one"
        found.append(Finding(n, f"needs {what} known status: label, has {', '.join(states) or 'none'}", add, rest))
    elif states == ["status:ready"] and waiting:
        found.append(
            Finding(
                n,
                f"status:ready with an open blocker {','.join(map(show, waiting))}",
                ("status:blocked",),
                tuple(states),
            )
        )
    return kind, found


def _title_findings(t: str, kind: str) -> list[str]:
    out = []
    if len(t) > TITLE_MAX:
        out.append(f"title is {len(t)} chars (max {TITLE_MAX})")
    if t.rstrip().endswith("."):
        out.append("title ends with a period")
    m = TITLE_PREFIX.match(t)
    if kind == "epic" and not t.startswith("Epic: "):
        out.append("title of an epic starts 'Epic: '")
    elif kind == "research" and not (m and PREFIX_KIND[m[1]] == "research"):
        out.append("title of a research item starts 'Research: ' or 'Experiment: '")
    elif kind not in ("epic", "research") and (m or not COMPONENT_TITLE.match(t)):
        out.append("title is not '<component>: <outcome>'")
    return out


def lint(
    issues: list[Issue],
    done: set[Ref],
    merged: dict[int, list[int]],
    history: tuple[set[str], set[str]] | None = None,
) -> list[Finding]:
    """Every convention an open item breaks. `done`: blockers known closed (`resolved`); `merged`: issue -> merged
    PRs that close it by keyword (`merged_closes`); `history`: (paths ever committed, paths now) from `path_history`,
    for Owns that existed and are gone. A warning (warn=True) is worth a look but breaks no convention."""
    by = {i.number: i for i in issues}
    ever, now = history or (set(), set())
    deleted = ever - now
    named_parent = {i.parent for i in issues if i.parent is not None}
    titles: dict[str, list[int]] = {}
    out: list[Finding] = []
    for i in issues:
        n = i.number
        kind, found = _label_findings(i, done)
        out += found
        out += [Finding(n, p) for p in _title_findings(i.title, kind)]
        if kind != "epic" and not done_items(i):
            out.append(Finding(n, "DONE WHEN is empty (or only bare numbers)"))
        if kind in NEEDS_OWNS and not owns_of(i):
            out.append(Finding(n, f"type:{kind} without Owns (backticked paths)"))
        for p in gone_owns(owns_of(i), deleted, now) if deleted else ():
            out.append(Finding(n, f"Owns `{p}` existed and is gone: close the item or fix its Owns"))
        if kind == "epic" and not (i.links and i.links.subs) and n not in named_parent:
            out.append(Finding(n, "epic with no sub-issues"))
        if n in merged:
            prs = ", ".join(f"#{p}" for p in merged[n])
            out.append(Finding(n, f"open, but merged PR {prs} closes it (every DONE WHEN met): close it or reopen"))
        if i.status in ("in-progress", "review") and kind != "epic" and i.claim() is None:
            out.append(Finding(n, f"status:{i.status} with no live claim", warn=True))
        titles.setdefault(i.title.strip().lower(), []).append(n)

    def open_deps(r: Ref) -> list[Ref]:
        return [b for b in by[r].blockers if b in by] if r in by else []

    out += [Finding(c[0], f"blocking cycle: {cycle_text(c)}") for c in cycles(sorted(by), open_deps)]
    same = [ns for ns in titles.values() if len(ns) > 1]
    out += [Finding(ns[0], f"same title as {', '.join(f'#{x}' for x in ns[1:])}", warn=True) for ns in same]
    return out


def merged_closes() -> dict[int, list[int]]:
    """Issue -> merged PRs into the default branch that close it by keyword (the last PR_LIMIT merged)."""
    out: dict[int, list[int]] = {}
    args = ("pr", "list", "-R", repo(), "--state", "merged", "--base", default_branch(), "--limit", str(PR_LIMIT))
    for p in json.loads(gh(*args, "--json", "number,body,headRefName")):
        for n in linked(p.get("body") or "", p.get("headRefName") or "")[0]:
            out.setdefault(n, []).append(p["number"])
    return out


def _findings() -> tuple[list[Issue], list[Finding]]:
    issues = fetch(with_comments=True, links=ALL_LINK_FIELDS)
    return issues, lint(issues, resolved(issues), merged_closes(), path_history())


def lint_board(fix: bool = False) -> tuple[str, int]:
    """`board lint`: one line per finding, exit 1 on any problem left; `fix` makes the label edits, one per item."""
    _, found = _findings()

    def edit(add: Iterable[str], remove: Iterable[str]) -> str:
        return " ".join([*(f"+{x}" for x in sorted(add)), *(f"-{x}" for x in sorted(remove))])

    lines = [
        f"#{f.number:<4} {'warning: ' if f.warn else ''}{f.problem}"
        + (f" (--fix: {edit(f.add, f.remove)})" if (f.add or f.remove) and not fix else "")
        for f in found
    ]
    left = [f for f in found if not f.warn and not (fix and (f.add or f.remove))]
    if fix:
        edits: dict[int, tuple[set[str], set[str]]] = {}
        for f in found:
            if f.add or f.remove:
                add, remove = edits.setdefault(f.number, (set(), set()))
                add.update(f.add)
                remove.update(f.remove)
        missing_labels_created(sorted({x for add, _ in edits.values() for x in add}))
        for n, (add, remove) in edits.items():
            args = ["issue", "edit", str(n), "-R", repo()]
            args += ["--add-label", ",".join(sorted(add))] if add else []
            args += ["--remove-label", ",".join(sorted(remove - add))] if remove - add else []
            gh(*args)
            lines.append(f"#{n:<4} fixed: {edit(add, remove - add)}")
    hint = "; `uv run dev board lint --fix` makes the label edits" if left and not fix else ""
    lines.append(f"{len(left)} problem(s) left{hint}")
    return "\n".join(lines), 1 if left else 0


def check_board() -> Ran:
    """The gate's board stage. Findings are warnings: the board is shared remote state, not this tree, and a gate
    pass is cached by the tree, so no PR fails for another item's label. Offline (or no gh) skips with a warning."""
    ran = Ran("board conventions (warnings only)", allow_empty=True)
    try:
        issues, found = _findings()
    except (BoardError, OSError) as e:
        ran.warnings.append(f"board not read, skipped: {e}")
        return ran
    ran.checked = len(issues)
    if found:
        ran.warnings.append("`uv run dev board lint --fix` makes the label edits; titles and bodies need a person")
    ran.warnings += [f"#{f.number} {f.problem}" for f in found]
    return ran


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
    """Native links first: backfill what bodies still declare (refusing cycles), render bodies from native links,
    and name any cycle already there. Then blocked <-> ready from the blockers. Manual blocks (`board block`) stay
    until a release or claim."""
    issues = fetch()
    deps = Graph(issues)
    moved = [m for i in issues for m in settle(i, deps)]
    open_ = {i.number for i in issues}  # a deadlock is a cycle among open items; closed ones cost a call each

    def open_deps(r: Ref) -> list[Ref]:
        return deps(r) if r in open_ else []

    moved += [f"cycle: {cycle_text(c)}" for c in cycles(sorted(open_), open_deps)]
    done = resolved(issues)
    for i in issues:
        waiting = any(n not in done for n in i.blockers)
        if i.status == "ready" and waiting:
            set_status(i.number, "blocked")
            moved.append(f"#{i.number} -> blocked")
        elif i.status == "blocked" and i.blockers and not waiting and not get(i.number).manually_blocked:
            set_status(i.number, "ready")
            moved.append(f"#{i.number} -> ready")
    return moved


ISSUE_LIST = r"#\d+(?:\s*(?:,|and|&)\s*#\d+)*"  # `#1`, `#1, #2`, `#1 and #2`
CLOSES = re.compile(rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s*({ISSUE_LIST})", re.I)
REFS = re.compile(rf"\brefs?:?\s*({ISSUE_LIST})", re.I)
FENCE = re.compile(r" {0,3}(`{3,}(?=[^`]*$)|~{3,})")  # CommonMark: a backtick fence's info string has no backtick
# A span closes at a backtick run of the same length, within one paragraph (no blank line inside).
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.S)


def prose(text: str) -> str:
    """Markdown `text` without its fenced code blocks and inline code spans, so a quoted example links nothing.
    Code becomes a NUL, which ends a keyword's list. An unclosed fence runs to the end, as in CommonMark; an
    unmatched backtick run stays literal text."""
    kept, fence = [], ""
    for line in text.splitlines():
        if fence:
            if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*", line):
                fence = ""
        elif m := FENCE.match(line):
            fence = m.group(1)
            kept.append("\0")
        else:
            kept.append(line)
    return CODE_SPAN.sub("\0", "\n".join(kept))


def keyword_numbers(rx: re.Pattern[str], text: str | None) -> list[int]:
    """Issue numbers after a keyword of `rx` (CLOSES, REFS), lists and `Keyword: #n` included, once each in order.
    Code is not read: GitHub links no issue from a code span or block."""
    found = rx.findall(prose(text or ""))
    return list(dict.fromkeys(int(n) for f in found for n in re.findall(r"#(\d+)", f)))


def linked(body: str, branch: str) -> tuple[list[int], list[int]]:
    """(issues a PR closes by keyword, its own issue from the `<n>-slug` branch when it does not close it).
    `Refs #N` links nothing: a mention must never move another agent's item."""
    closes = keyword_numbers(CLOSES, body)
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


FEEDBACK_QUERY = """query($owner: String!, $name: String!, $n: Int!) { repository(owner: $owner, name: $name) {
  pullRequest(number: $n) {
    author { login } headRefOid
    commits(last: 1) { nodes { commit { committedDate } } }
    comments(last: 50) { nodes { author { login } body createdAt url } }
    reviews(last: 50) { nodes { author { login } body state submittedAt url } }
    reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved path line comments(last: 1) { nodes { author { login } body url } } } }
} } }"""
FEEDBACK_CHARS = 200
THREADS_PAGE = 100  # the reviewThreads(first:) above; past it integrate refuses (fails closed) instead of paginating
# The first line of the one COMMENT review a reviewer or research auditor posts (#347, issue-pr-automation.md §3.1).
# Its findings are the author's to-do list, so feedback shows that review whole, not FEEDBACK_CHARS of it, when the
# PR's author or the gh user posted it: agents act as one of them, and anyone can review a public repository's PR.
VERDICT = re.compile(r"Verdict: (merge|send back|reconsider), round (\d+), ([0-9a-fA-F]{7,40})")


def verdict_of(body: str) -> re.Match | None:
    """The VERDICT match of a review body's first line."""
    return VERDICT.fullmatch((body.strip().splitlines() or [""])[0].strip())


@functools.cache
def gh_user() -> str:
    """The login gh is authenticated as; "" when GitHub cannot say."""
    try:
        return gh("api", "user", "--jq", ".login").strip()
    except BoardError:
        return ""


def pr_feedback(number: int) -> dict:
    """A PR's conversation: `since` (its last commit's date), comments, reviews and the unresolved review threads.
    Review threads are GraphQL-only (`gh pr view` has no field for them); past 100 threads `threads_truncated`."""
    owner, name = repo().split("/", 1)
    out = gh("api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"n={number}", "-f",
             f"query={FEEDBACK_QUERY}")  # fmt: skip
    pr = json.loads(out)["data"]["repository"]["pullRequest"]
    commits = pr["commits"]["nodes"]
    trusted = {login(pr)} if pr.get("author") else set()
    if any(login(r) not in trusted and verdict_of(r.get("body") or "") for r in pr["reviews"]["nodes"]):
        trusted.add(gh_user())  # asked only when a verdict review is not the PR author's
    return {
        "since": commits[-1]["commit"]["committedDate"] if commits else "",
        "head": pr.get("headRefOid") or "",
        "trusted": trusted - {""},
        "comments": pr["comments"]["nodes"],
        "reviews": pr["reviews"]["nodes"],
        "threads": [t for t in pr["reviewThreads"]["nodes"] if not t["isResolved"]],
        "threads_truncated": bool((pr["reviewThreads"].get("pageInfo") or {}).get("hasNextPage")),
    }


def login(node: dict) -> str:
    """The author's login; a deleted account comes back as a null author."""
    return (node.get("author") or {}).get("login", "ghost")


def pr_feedback_lines(fb: dict) -> list[str]:
    """What an agent must answer: verdict reviews whole (VERDICT, by a `trusted` login; marked stale when their SHA is
    not the head's), then other comments and reviews newer than the last commit cut to FEEDBACK_CHARS, then every
    unresolved thread. A COMMENTED review with no body only wraps inline comments, which its threads already carry."""

    def text(body: str) -> str:
        return " ".join(body.split())[:FEEDBACK_CHARS]

    since, head_sha = fb["since"], fb.get("head", "").lower()
    verdicts, reviews = [], []
    for r in fb["reviews"]:
        if (r.get("submittedAt") or "") <= since or not (r["body"].strip() or r["state"] != "COMMENTED"):
            continue
        head = f"- review {r['state']} {login(r)} {r['submittedAt'][:16]}:"
        if (v := verdict_of(r["body"])) and login(r) in fb.get("trusted", ()):
            stale = f" stale: the head is now {head_sha[:7]}" if not head_sha.startswith(v[3].lower()) else ""
            rest = r["body"].strip().splitlines()[1:]
            verdicts += [f"{head} {v[0]} ({r['url']}){stale}", *(f"  {x.rstrip()}" for x in rest if x.strip())]
        else:
            reviews.append(f"{head} {text(r['body'])} ({r['url']})")
    comments = [
        f"- comment {login(c)} {c['createdAt'][:16]}: {text(c['body'])} ({c['url']})"
        for c in fb["comments"]
        if c["createdAt"] > since
    ]
    out = verdicts + comments + reviews
    for t in fb["threads"]:
        last = (t["comments"]["nodes"] or [{}])[-1]
        out.append(
            f"- unresolved thread {t['path']}:{t.get('line') or '?'} {login(last)}: "
            f"{text(last.get('body', ''))} ({last.get('url', '')})"
        )
    if fb.get("threads_truncated"):
        out.append(f"- more than {THREADS_PAGE} review threads: the rest are not shown, read them on GitHub")
    return out


def issue_prs(number: int) -> list[int]:
    """Open PRs of an item: its `<n>-slug` branch, or a body that closes it (a `Refs #n` mention links nothing)."""
    prs = gh(
        "pr", "list", "-R", repo(), "--state", "open", "--limit", str(PR_LIMIT), "--json", "number,headRefName,body"
    )
    out = []
    for p in json.loads(prs):
        closes, own = linked(p.get("body") or "", p.get("headRefName") or "")
        if number in closes + own:
            out.append(p["number"])
    return out


def brief(number: int) -> str:
    """Everything an agent needs to start one item, so its spawn prompt can be one line."""
    i = get(number)
    p = i.parts
    out = [f"# #{i.number} {i.title}", f"{i.priority} type:{i.kind} status:{i.status}"]
    if c := i.claim():
        out.append(f"claimed by {c.agent}@{c.host} on branch {c.branch}")
    more = f"`gh issue view {i.number}`"
    for name in ("Context", "DONE WHEN", "Owns", "Refs"):
        if p.get(name):
            out += [f"\n## {name}", *cap(p[name].splitlines(), SECTION_LINES, more)]
    links = i.links or Links([], None)
    for name, refs in (
        ("Blocked by", i.blockers),
        ("Blocking", links.blocking),
        ("Parent", [i.parent] if i.parent else []),
        ("Sub-issues", links.subs),
    ):
        if refs:
            out += [f"\n## {name}", ", ".join(map(show, refs))]
    if i.comments:
        last = [
            f"- {c.get('author', {}).get('login', '?')} {c.get('createdAt', '')[:16]}: "
            f"{RELEASE.sub('', CLAIM.sub('', c.get('body', ''))).strip()[:300]}"
            for c in i.comments[-3:]
        ]
        out += ["\n## Last comments", *cap("\n".join(last).splitlines(), SECTION_LINES, more)]
    owns = owns_of(i)
    busy = in_flight_files()
    hot = sorted(f"{f} ({', '.join(w)})" for f, w in busy.items() if owned(f, owns))
    if busy:
        out.append("\n## Files other work is changing (hotspots: coordinate, do not edit blindly)")
        rows = [f"- {h}" for h in hot] or [f"- {len(busy)} files in flight elsewhere, none under this item's Owns"]
        out += cap(rows, SECTION_LINES, "`uv run dev status` and `gh pr list`")
    try:
        for pr in issue_prs(i.number):
            rows = pr_feedback_lines(pr_feedback(pr)) or ["- none: no new comments or reviews, no unresolved threads"]
            out.append(f"\n## PR #{pr} feedback since its last commit (answer or fix each, resolve threads)")
            out += cap(rows, SECTION_LINES, f"`uv run dev board feedback {pr}`")
    except BoardError:
        pass
    slug = re.sub(r"[^a-z0-9]+", "-", i.title.lower()).strip("-")[:40]
    out.append(f"""
## Commands
- branch: `{i.number}-{slug}`; claim: `uv run dev board claim {i.number} --agent <you> --branch {i.number}-{slug}`
- navigate: `uv run dev code outline|show|refs|find ...`; tests while iterating: `uv run dev test --affected`
- gate once at the end: `uv run dev check`; then push and open the PR: `uv run dev pr {i.number}`
- heartbeat every few hours: `uv run dev board beat {i.number} "<numbers>"`
- follow-up out of scope: `uv run dev board new --type build|bug|test|research|infra --title "<title>" \
--done "<measurable condition>" --owns <path> --refs {i.number}` (repeat --done and --owns)""")
    return "\n".join(out)
