"""Reading and changing the board: fetch, labels, new items and their native links, claims, sync, ranking and the
stale sweep (devkit.board has the whole story)."""

import functools
import json
import os
import re
import secrets
import socket
import sys
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import yaml

from devkit import board_gh, board_issue, board_related, config, paths


def new_identity() -> str:
    return f"{os.getpid()}-{secrets.token_hex(4)}"


IDENTITY = new_identity()  # with the host, unique per claiming process even when two share an agent name


def fetch(
    state: str = "open", with_comments: bool = False, links: str = board_issue.LINK_FIELDS
) -> list[board_issue.Issue]:
    fields = board_issue.FIELDS + (",comments" if with_comments else "")
    args = ("issue", "list", "-R", board_gh.repo(), "--state", state, "--limit", str(board_issue.ISSUE_LIMIT))
    return [board_issue._issue(d) for d in board_gh.gh_json(*args, fields=fields, links=links)]


def closed_numbers() -> set[int]:
    """Issues closed as done: the only blockers that count as resolved (closed as not planned does not)."""
    data = json.loads(
        board_gh.gh(
            "issue",
            "list",
            "-R",
            board_gh.repo(),
            "--state",
            "closed",
            "--limit",
            str(board_issue.ISSUE_LIMIT),
            "--json",
            "number,stateReason",
        )
    )
    return {d["number"] for d in data if d.get("stateReason") != "NOT_PLANNED"}


def get(number: int) -> board_issue.Issue:
    args = ("issue", "view", str(number), "-R", board_gh.repo())
    return board_issue._issue(
        board_gh.gh_json(*args, fields=board_issue.FIELDS + ",comments", links=board_issue.ALL_LINK_FIELDS)
    )


def resolved(issues: list[board_issue.Issue]) -> set[board_issue.Ref]:
    """Blockers known done: issues of this repository closed as done, and other repositories' blockers closed as
    done (asked by GraphQL; one that cannot be read stays waiting)."""
    done: set[board_issue.Ref] = set(closed_numbers())
    for ref in {b for i in issues for b in i.blockers if isinstance(b, str)}:
        try:
            r = board_issue.remote(ref)
        except board_gh.BoardError:
            continue
        if r.get("state") == "CLOSED" and r.get("stateReason") != "NOT_PLANNED":
            done.add(ref)
    return done


@functools.cache
def repo_labels() -> frozenset[str]:
    return frozenset(
        d["name"]
        for d in json.loads(board_gh.gh("label", "list", "-R", board_gh.repo(), "--limit", "500", "--json", "name"))
    )


def set_status(number: int, status: str) -> None:
    missing = [f"status:{s}" for s in board_issue.STATUSES if f"status:{s}" not in repo_labels()]
    if missing:  # gh would fail after a partial edit; refuse before touching the issue
        raise board_gh.BoardError(f"labels missing from the repo: {', '.join(missing)}; run `uv run dev board init`")
    args = ["issue", "edit", str(number), "-R", board_gh.repo(), "--add-label", f"status:{status}"]
    for s in board_issue.STATUSES:
        if s != status:
            args += ["--remove-label", f"status:{s}"]
    board_gh.gh(*args)


def comment(number: int, body: str) -> str:
    """Post a comment; returns its URL (which carries the comment id)."""
    return board_gh.gh("issue", "comment", str(number), "-R", board_gh.repo(), "--body-file", "-", input=body)


def ensure_labels() -> list[str]:
    existing = repo_labels()
    made = []
    for name, color in {**board_issue.LABELS, **dict.fromkeys(area_globs(), board_issue.AREA_COLOR)}.items():
        if name not in existing:
            board_gh.gh("label", "create", name, "-R", board_gh.repo(), "--color", color, "--force")
            made.append(name)
    repo_labels.cache_clear()
    return made


def area_globs(path: Path | None = None) -> dict[str, list[str]]:
    """Area label -> globs from the map (actions/labeler format). Only its simplest shape is accepted,
    `label: [changed-files: [any-glob-to-any-file: globs]]` without `!` negation; anything else is an error here."""
    area_map = config.get().area_map
    path = path or paths.root() / area_map
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
                    raise board_gh.BoardError(
                        f"{area_map}: `{label}` uses a rule dev board cannot read (any-glob-to-any-file only)"
                    )
                globs = m["any-glob-to-any-file"]
                globs = [globs] if isinstance(globs, str) else list(globs)
                if any(g.startswith("!") for g in globs):
                    raise board_gh.BoardError(f"{area_map}: `{label}` negates a glob, which dev board cannot read")
                out.setdefault(label, []).extend(globs)
    return out


def tracked_files() -> list[str]:
    return paths.git("ls-files").splitlines()


def path_history() -> tuple[set[str], set[str]] | None:
    """(every path ever committed in HEAD's history, the paths tracked now), or None outside a git checkout."""
    try:
        ever = paths.git("log", "--format=", "--name-only", "--no-renames", "HEAD")
        return set(ever.splitlines()) - {""}, set(tracked_files())
    except RuntimeError:
        return None


def areas(owns: list[str], globs: dict[str, list[str]], files: list[str]) -> list[str]:
    """Area labels for an item: a label applies when one of its globs matches a tracked file under an Owns entry,
    or a stand-in path for the entry itself (so a path nothing occupies yet still gets its area)."""
    stand_ins = {re.sub(r"\*+|\?", "x", o.rstrip("/")) for o in owns}
    return path_areas(
        stand_ins | {f"{s}/x" for s in stand_ins} | {f for f in files if board_issue.owned(f, owns)}, globs
    )


def path_areas(paths: set[str] | list[str], globs: dict[str, list[str]]) -> list[str]:
    """Area labels whose globs match any of these paths (a PR's changed files, in `dev pr`)."""
    return [label for label, gs in globs.items() if any(PurePosixPath(p).full_match(g) for p in paths for g in gs)]


def missing_labels_created(labels: list[str]) -> None:
    """`gh ... --label` fails on a label the repo lacks: create the board's labels first when any is missing."""
    if any(x not in repo_labels() for x in labels):
        ensure_labels()


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
    related_issues: bool = True,
) -> str:
    """Create an item, then link it natively (blocked-by, sub-issue of `parent`); the body renders those links.
    Every reference is looked up before anything is created, so a typo or an unreadable issue creates nothing.
    With `related_issues`, issues like it (`related`) are printed after the URL line and added to Refs; any failure
    there only leaves them out. Programmatic callers that parse the one-line result pass False (and skip the fetch)."""
    if kind != "epic" and not done:
        raise board_gh.BoardError("a work item needs at least one --done (a measurable DONE WHEN condition)")
    blockers = list(dict.fromkeys(board_issue.parse_ref(b) for b in blocked_by))
    up = board_issue.parse_ref(parent) if parent else None
    info = {r: board_issue.remote(r) for r in [*blockers, *([up] if up else [])]}
    cited = [int(x) for x in refs if x.strip().isdigit()] + [r for x in refs for r in board_issue.refs_in(x)]
    named = {r for r in [*blockers, up, *cited] if isinstance(r, int)}
    found: list[tuple[board_issue.Issue, float]] = []
    try:
        found = (
            board_related.related(title, context, done, board_related.related_pool(), named) if related_issues else []
        )
    except Exception as e:  # advisory: no answer of any shape stops the filing
        print(f"board new: related issues skipped ({type(e).__name__}: {e})", file=sys.stderr)
    if found:
        refs = [*refs, " ".join(f"#{i.number}" for i, _ in found) + f" ({board_related.RELATED_NOTE})"]
    closed = closed_numbers() if blockers else set()  # not `done`: that is the DONE WHEN list
    status = "blocked" if any(not board_issue.is_done(b, closed, info[b]) for b in blockers) else "ready"
    area = areas(owns, area_globs(), tracked_files()) if owns else []
    labels = [f"type:{kind}", priority] + ([] if kind == "epic" else [f"status:{status}"]) + area
    try:  # a scripted loop reads the exit code: any failure names the item it did not create (#387)
        missing_labels_created(area)
        out = board_gh.gh(
            "issue",
            "create",
            "-R",
            board_gh.repo(),
            "--title",
            title,
            "--body-file",
            "-",
            "--label",
            ",".join(labels),
            input=board_issue.body_from(context, done, owns, blockers, refs, up, rendered=bool(info)),
        )
    except board_gh.BoardError as e:
        raise board_gh.BoardError(f"board new: {title!r} not created ({e})") from e
    url = (out.strip().splitlines() or [""])[-1].strip()
    if not re.search(r"/issues/\d+$", url):
        raise board_gh.BoardError(
            f"board new: {title!r} not created, or not confirmed: gh issue create printed no issue URL"
            f" ({out.strip()!r}); check the board before retrying"
        )
    if info:
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        try:
            me = board_issue.remote(number)["id"]
            for b in blockers:
                board_gh.graphql(board_issue.ADD_BLOCKED_BY, issue=me, blocker=info[b]["id"])
            if up:
                board_gh.graphql(board_issue.ADD_SUB_ISSUE, parent=info[up]["id"], sub=me, replace=False)
        except board_gh.BoardError as e:  # unmark the body, so `board sync` backfills the links still missing
            plain = board_issue.body_from(context, done, owns, blockers, refs, up)
            board_gh.gh("issue", "edit", str(number), "-R", board_gh.repo(), "--body-file", "-", input=plain)
            raise board_gh.BoardError(
                f"{url} created, but linking failed ({e}); `uv run dev board sync` retries"
            ) from e
    return "\n".join([f"{url} ({status})", *board_related.related_lines(found)])


def settle(
    i: board_issue.Issue,
    deps: board_issue.Graph,
    blockers: list[board_issue.Ref] = (),
    parent: board_issue.Ref | None = None,
) -> list[str]:
    """Native links for `blockers` and `parent` plus what i's body still declares, then the body rendered from
    them. A link that would close a cycle is refused; a body whose links failed for another reason stays unmarked
    (so a later sync retries). An explicit `parent` replaces a native one; a declared one never does."""
    if i.links is None:
        return []
    native, up, msgs, failed, me = list(i.links.blocked_by), i.links.parent, [], False, None
    for b in dict.fromkeys([*blockers, *i.declared_blockers]):
        if b in native:
            continue
        if path := board_issue.closing_cycle(i.number, b, deps):
            msgs.append(
                f"#{i.number}: refused blocked by {board_issue.show(b)}: it closes the cycle {board_issue.cycle_text(path)}"
            )
            continue
        try:
            me = me or board_issue.remote(i.number)["id"]
            board_gh.graphql(board_issue.ADD_BLOCKED_BY, issue=me, blocker=board_issue.remote(b)["id"])
        except board_gh.BoardError as e:
            failed = True
            msgs.append(f"#{i.number}: blocked by {board_issue.show(b)} not linked: {e}")
            continue
        native.append(b)
        deps.add(i.number, b)
        msgs.append(f"#{i.number} blocked by {board_issue.show(b)}: linked")
    want = parent or i.declared_parent
    if want and want != up:
        if up and not parent:
            msgs.append(
                f"#{i.number}: kept native parent {board_issue.show(up)}; the body named {board_issue.show(want)}"
            )
        else:
            try:
                me = me or board_issue.remote(i.number)["id"]
                board_gh.graphql(
                    board_issue.ADD_SUB_ISSUE, parent=board_issue.remote(want)["id"], sub=me, replace=bool(up)
                )
                up = want
                msgs.append(f"#{i.number} sub-issue of {board_issue.show(want)}: linked")
            except board_gh.BoardError as e:
                failed = True
                msgs.append(f"#{i.number}: sub-issue of {board_issue.show(want)} not linked: {e}")
    stated = (
        set(board_issue.refs_in(i.parts.get("Blocked by", ""))),
        next(iter(board_issue.refs_in(i.parts.get("Parent", ""))), None),
    )
    if not failed and (stated != (set(native), up) or (not i.rendered and (stated[0] or stated[1]))):
        i.body = board_issue.render_links(i.body, native, up)
        board_gh.gh("issue", "edit", str(i.number), "-R", board_gh.repo(), "--body-file", "-", input=i.body)
    i.links = i.links._replace(blocked_by=native, parent=up)
    return msgs


def link(number: int, blocked_by: list[int | str], parent: int | str | None = None) -> list[str]:
    """Add native links to an existing item: blocked by each of `blocked_by`, sub-issue of `parent`. A blocked-by
    link that would close a cycle refuses the whole call, before anything changes, and names the cycle."""
    blockers = list(dict.fromkeys(board_issue.parse_ref(b) for b in blocked_by))
    deps = board_issue.Graph(fetch())
    for b in blockers:
        if path := board_issue.closing_cycle(number, b, deps):
            raise board_gh.BoardError(
                f"refused: #{number} blocked by {board_issue.show(b)} closes the cycle {board_issue.cycle_text(path)}"
            )
    i = get(number)
    if i.links is None:
        raise board_gh.BoardError("this gh cannot read native issue links (blockedBy, parent); upgrade gh")
    return settle(i, deps, blockers, board_issue.parse_ref(parent) if parent else None)


def _line(i: board_issue.Issue, done: set[board_issue.Ref]) -> str:
    who = ""
    if c := i.claim():
        who = f" @{c[0]}/{c[1]}"
    stale = " STALE" if i.status == "in-progress" and i.comments and i.hours_idle() > board_issue.STALE_H else ""
    waits = [n for n in i.blockers if n not in done]
    wait = f" waits {','.join(map(board_issue.show, waits))}" if waits else ""
    return f"#{i.number:<4} {i.priority} {i.kind:<8} {i.status:<11}{who}{stale}{wait}  {i.title if len(i.title) <= 90 else i.title[:89] + '…'}"


def listing(status: str | None = None, kind: str | None = None, as_json: bool = False) -> str:
    issues = fetch(with_comments=True)
    done = resolved(issues)

    def wanted(i: board_issue.Issue) -> bool:
        if kind:
            return i.kind == kind and (not status or i.status == status)
        return i.kind != "epic" and (not status or i.status == status)

    rows = [i for i in issues if wanted(i)]
    rows.sort(
        key=lambda i: (
            board_issue.STATUSES.index(i.status) if i.status in board_issue.STATUSES else 9,
            i.priority,
            i.number,
        )
    )
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
    inherited: board_issue.Ref | None
    downstream: list[board_issue.Ref]


def ref_key(r: board_issue.Ref) -> tuple[bool, int | str]:
    """Sorts this repository's issues by number before other repositories' references."""
    return (isinstance(r, str), r)


def ranks(issues: list[board_issue.Issue]) -> dict[board_issue.Ref, Rank]:
    """Each fetched item's Rank over the blocked-by graph of the fetched (open) items. Priority inheritance lets a
    P3 that blocks a P0 start like a P0; a cycle or a closed blocker adds nothing."""
    by = {i.number: i for i in issues}
    dependents: dict[board_issue.Ref, list[board_issue.Ref]] = {}
    for i in issues:
        for b in i.blockers:
            dependents.setdefault(b, []).append(i.number)
    out: dict[board_issue.Ref, Rank] = {}
    for i in issues:
        seen: set[board_issue.Ref] = set()
        todo: list[board_issue.Ref] = [i.number]
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
    prio = f"{r.priority} from {board_issue.show(r.inherited)}" if r.inherited is not None else r.priority
    shown = ",".join(map(board_issue.show, r.downstream[:5])) + (",…" if len(r.downstream) > 5 else "")
    return f"[{prio}, unblocks {len(r.downstream)}{': ' + shown if r.downstream else ''}]"


def startable(
    issues: list[board_issue.Issue], kind: str | None = None, done: set[board_issue.Ref] = frozenset()
) -> list[board_issue.Issue]:
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


def _ranked_lines(rows: list[board_issue.Issue], issues: list[board_issue.Issue], done: set[board_issue.Ref]) -> str:
    rank = ranks(issues)
    return "\n".join(f"{_line(i, done)}  {reason(rank[i.number])}" for i in rows)


def next_items(kind: str | None, count: int) -> str:
    issues = fetch()
    done = resolved(issues)
    rows = startable(issues, kind, done)[:count]
    return _ranked_lines(rows, issues, done) or "nothing startable (check `uv run dev board list`)"


def wave(issues: list[board_issue.Issue], n: int, done: set[board_issue.Ref]) -> list[board_issue.Issue]:
    """Up to n startable items, in `next` order, whose Owns overlap neither each other nor any item in progress or
    in review (greedy, after a sibling project's batching wave). An item with no Owns overlaps nothing."""
    busy = [board_issue.owns_of(i) for i in issues if i.status in ("in-progress", "review")]
    chosen: list[board_issue.Issue] = []
    for i in startable(issues, done=done):
        if len(chosen) == n:
            break
        mine = board_issue.owns_of(i)
        if not any(board_issue.overlap(mine, other) for other in busy):
            chosen.append(i)
            busy.append(mine)
    return chosen


def wave_items(n: int) -> str:
    issues = fetch()
    done = resolved(issues)
    return _ranked_lines(wave(issues, n, done), issues, done) or (
        "nothing startable with Owns disjoint from work in progress (check `uv run dev board next`)"
    )


def claim(number: int, agent: str, branch: str, force: bool = False) -> str:
    issue = get(number)
    if issue.state.upper() == "CLOSED":
        raise board_gh.BoardError(f"#{number} is closed")
    held = issue.claim()
    if held and not force:
        raise board_gh.BoardError(
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
    mine = board_issue.comment_id({"url": url}) or token
    time.sleep(2)  # let a racing claim land, then the earliest live claim wins
    winner = get(number).claim()
    if not winner or (winner.host, winner.token) != (host, token):
        comment(number, f"<!-- devkit:release claim={mine} -->\nWithdrawn: {agent}@{host} lost a simultaneous claim.")
        raise board_gh.BoardError(
            f"#{number}: lost the claim race to {winner.agent if winner else '?'}; pick another item"
        )
    set_status(number, "in-progress")
    board_gh.gh("issue", "edit", str(number), "-R", board_gh.repo(), "--add-assignee", "@me")
    return f"#{number} claimed by {agent}@{host} on {branch}"


def release_tag(issue: board_issue.Issue) -> str:
    """A release marker naming every live claim (ids without a comment id fall back to their token)."""
    ids = [x.cid or x.token or "" for x in issue.live_claims()]
    return f"<!-- devkit:release claim={','.join(ids)} -->" if ids and all(ids) else "<!-- devkit:release -->"


def release(number: int, reason: str, status: str = "ready") -> None:
    comment(number, f"{release_tag(get(number))}\n{reason}")
    set_status(number, status)


def block(number: int, reason: str) -> None:
    """Withdraw any claim and block with a reason; sync keeps it blocked until a release or claim."""
    comment(number, f"{release_tag(get(number))}\n{board_issue.BLOCK_MARK}\nBlocked: {reason}")
    set_status(number, "blocked")


def sync() -> list[str]:
    """Native links first: backfill what bodies still declare (refusing cycles), render bodies from native links,
    and name any cycle already there. Then blocked <-> ready from the blockers. Manual blocks (`board block`) stay
    until a release or claim."""
    issues = fetch()
    deps = board_issue.Graph(issues)
    moved = [m for i in issues for m in settle(i, deps)]
    open_ = {i.number for i in issues}  # a deadlock is a cycle among open items; closed ones cost a call each

    def open_deps(r: board_issue.Ref) -> list[board_issue.Ref]:
        return deps(r) if r in open_ else []

    moved += [f"cycle: {board_issue.cycle_text(c)}" for c in board_issue.cycles(sorted(open_), open_deps)]
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


def merged_pr(number: int, body: str, head: str, base: str) -> list[str]:
    """After `dev integrate` merges a PR into the default branch: each issue it closes is closed unless GitHub got
    there first, with one "Merged in PR" comment either way; its own issue, merged without being closed, goes back
    to ready; then blockers are re-synced so dependents start. A merge into another branch closes nothing."""
    if base != paths.default_branch():
        return []
    closes, own = board_issue.linked(body, head)
    moved = []
    for n in closes:
        note = f"Merged in PR #{number}."
        if get(n).state.upper() == "CLOSED":
            comment(n, note)
        else:
            board_gh.gh("issue", "close", str(n), "-R", board_gh.repo(), "--reason", "completed", "--comment", note)
            moved.append(f"#{n} -> done")
    for n in own:
        if (i := get(n)).state.upper() != "CLOSED" and i.status == "review":
            release(n, f"PR #{number} merged without closing #{n}; back to ready.")
            moved.append(f"#{n} -> ready")
    return moved + sync()


def sweep(hours: float = board_issue.STALE_H, now: datetime | None = None, only: list[int] | None = None) -> list[str]:
    """Label and comment once on claimed in-progress items idle over `hours`; unlabel them once they move again.
    `only` limits the sweep to those issue numbers (a manual sweep with a low threshold must not touch the rest)."""
    ensure_labels()
    moved = []
    for i in (x for x in fetch(with_comments=True) if not only or x.number in only):
        stale = i.status == "in-progress" and i.claim() is not None and i.hours_idle(now) > hours
        if stale and "stale" not in i.labels:
            c = i.claim()
            board_gh.gh("issue", "edit", str(i.number), "-R", board_gh.repo(), "--add-label", "stale")
            comment(
                i.number,
                f"{board_issue.STALE_MARK}\nStale: no comment for {i.hours_idle(now):.1f} h on the claim by {c.agent}@{c.host} "
                f'(branch `{c.branch}`). Claimant: post `uv run dev board beat {i.number} "<progress>"`. Others: '
                f"read the thread, then take over with `uv run dev board claim {i.number} --agent <you> --force`.",
            )
            moved.append(f"#{i.number} -> stale")
        elif not stale and "stale" in i.labels:
            board_gh.gh("issue", "edit", str(i.number), "-R", board_gh.repo(), "--remove-label", "stale")
            moved.append(f"#{i.number} -> fresh")
    return moved


def event(name: str, payload: dict, now: datetime | None = None) -> list[str]:
    """The scheduled board job (name + the JSON at GITHUB_EVENT_PATH): re-sync blockers, then sweep stale claims."""
    if name in ("schedule", "workflow_dispatch"):
        hours = (payload.get("inputs") or {}).get("hours") or board_issue.STALE_H
        return sync() + sweep(float(hours), now)
    raise board_gh.BoardError(f"no board automation for the {name!r} event")
