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

The code lives in PARTS, one module per responsibility (#625): board_gh (the `gh` calls, retries and rechecks),
board_issue (the issue model and body schema), board_related (BM25 for `board new`), board_state (fetch, claims,
sync, ranking), board_lint and board_brief. This module is their one name space: `board.X` reads X from the part
that holds it, and setting `board.X` (a test's monkeypatch) sets it in every part that holds it, so a part calling
`gh` sees a patched `board.gh` as the single module did.
"""

import sys
from types import ModuleType
from typing import TYPE_CHECKING

from devkit import board_brief, board_gh, board_issue, board_lint, board_related, board_state

if TYPE_CHECKING:  # the names `board.X` serves, for type checkers and readers
    from devkit.board_brief import *  # noqa: F403
    from devkit.board_gh import *  # noqa: F403
    from devkit.board_gh import _flag, _iso  # noqa: F401
    from devkit.board_issue import *  # noqa: F403
    from devkit.board_issue import _issue  # noqa: F401
    from devkit.board_lint import *  # noqa: F403
    from devkit.board_related import *  # noqa: F403
    from devkit.board_state import *  # noqa: F403
    from devkit.board_state import _line  # noqa: F401

PARTS = (board_gh, board_issue, board_related, board_state, board_lint, board_brief)


def holders(name: str) -> list[ModuleType]:
    """The parts that hold `name` (none for a dunder: each module keeps its own)."""
    return [] if name.startswith("__") else [part for part in PARTS if name in vars(part)]


class _Board(ModuleType):
    """Reads come from the parts, so a part's own change (board_related lowering GH_TRIES) shows here; writes go to
    every part that holds the name and to this module's copy, which monkeypatch reads to restore."""

    def __getattribute__(self, name: str) -> object:
        if found := holders(name):
            return vars(found[0])[name]
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value: object) -> None:
        for part in holders(name):
            setattr(part, name, value)
        super().__setattr__(name, value)


globals().update({n: v for part in reversed(PARTS) for n, v in vars(part).items() if not n.startswith("__")})
sys.modules[__name__].__class__ = _Board
