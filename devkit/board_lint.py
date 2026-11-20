"""`board lint` and the gate's board stage: the conventions every open item meets."""

import json
import re
from collections.abc import Iterable
from typing import NamedTuple

from devkit.board_gh import BoardError, gh, repo
from devkit.board_issue import (
    ALL_LINK_FIELDS,
    PR_LIMIT,
    PRIORITIES,
    TYPES,
    Issue,
    Ref,
    cycle_text,
    cycles,
    gone_owns,
    linked,
    owns_of,
    show,
)
from devkit.board_state import fetch, missing_labels_created, path_history, resolved
from devkit.paths import default_branch
from devkit.ran import Ran

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
