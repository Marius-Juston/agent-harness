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

The code lives in one module per responsibility (#625): board_gh (the `gh` calls, retries and rechecks), board_issue
(the issue model and body schema), board_related (BM25 for `board new`), board_state (fetch, claims, sync, ranking),
board_lint and board_brief. This module re-exports their names for callers. The parts call each other through the
defining module (`board_gh.gh(...)`), so a test patches the defining module (`setattr(board_gh, "gh", f)`) and the
patch reaches every part. The functions tests replace (GitHub and the board's I/O) are re-exported late-bound
(`late`), so callers of `board.gh` outside the parts see that patch too. A patch of a name here reaches nothing:
tests/test_board_split.py refuses one, and checks that every function a test patches on a part is late-bound here.
"""

import functools
from collections.abc import Callable
from types import ModuleType

from devkit import board_brief, board_gh, board_issue, board_state
from devkit.board_brief import *  # noqa: F403
from devkit.board_gh import *  # noqa: F403
from devkit.board_gh import _flag, _iso  # noqa: F401
from devkit.board_issue import *  # noqa: F403
from devkit.board_issue import _issue  # noqa: F401
from devkit.board_lint import *  # noqa: F403
from devkit.board_related import *  # noqa: F403
from devkit.board_state import *  # noqa: F403
from devkit.board_state import _line  # noqa: F401


def late[**P, R](part: ModuleType, f: Callable[P, R]) -> Callable[P, R]:
    """`f`, looked up in its defining module `part` at each call, so a patch of that module reaches this caller."""
    name = next(n for n, v in vars(part).items() if v is f)

    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        return getattr(part, name)(*args, **kwargs)

    return functools.update_wrapper(call, f)


gh = late(board_gh, board_gh.gh)
repo = late(board_gh, board_gh.repo)
deleting_owns = late(board_issue, board_issue.deleting_owns)
area_globs = late(board_state, board_state.area_globs)
block = late(board_state, board_state.block)
closed_numbers = late(board_state, board_state.closed_numbers)
comment = late(board_state, board_state.comment)
fetch = late(board_state, board_state.fetch)
fetch_with_closed = late(board_state, board_state.fetch_with_closed)
get = late(board_state, board_state.get)
merged_pr = late(board_state, board_state.merged_pr)
new = late(board_state, board_state.new)
resolved = late(board_state, board_state.resolved)
set_status = late(board_state, board_state.set_status)
sync = late(board_state, board_state.sync)
synced_board = late(board_state, board_state.synced_board)
fix_comments = late(board_brief, board_brief.fix_comments)
gh_user = late(board_brief, board_brief.gh_user)
in_flight_files = late(board_brief, board_brief.in_flight_files)
issue_prs = late(board_brief, board_brief.issue_prs)
marked_comments = late(board_brief, board_brief.marked_comments)
pr_feedback = late(board_brief, board_brief.pr_feedback)
