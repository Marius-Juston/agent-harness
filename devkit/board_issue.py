"""The board's issue model and body schema: sections, references, native links, claims and Owns, parsed from what
`gh` returns (devkit.board has the whole story)."""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import NamedTuple

from devkit import board_gh

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
    return n if not where or where.lower() == board_gh.repo().lower() else f"{where}#{n}"


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
        raise board_gh.BoardError(f"not one issue reference: {text!r} (use 12, #12, owner/repo#12 or an issue URL)")
    return found[0]


def show(ref: Ref) -> str:
    return f"#{ref}" if isinstance(ref, int) else ref


def split(ref: Ref) -> tuple[str, str, int]:
    """(owner, name, number) of a Ref."""
    where, _, n = (f"{board_gh.repo()}#{ref}" if isinstance(ref, int) else ref).rpartition("#")
    owner, _, name = where.partition("/")
    return owner, name, int(n)


def ref_from_url(url: str) -> Ref:
    m = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", url or "")
    if not m:
        raise board_gh.BoardError(f"not an issue URL: {url}")
    return ref_of(m.group(1), m.group(2))


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
    issue = (board_gh.graphql(ISSUE_QUERY, owner=owner, name=name, number=n).get("repository") or {}).get("issue")
    if not issue:
        raise board_gh.BoardError(f"{show(ref)}: no such issue, or no access to it")
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


def cycle_text(path: list[Ref]) -> str:
    return " -> ".join(map(show, path))


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
