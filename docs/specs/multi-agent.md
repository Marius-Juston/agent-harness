# Spec: many agents, many machines, long sessions

Status: v1 (2026-10-11). Evidence: [../research/agent-harness.md](../research/agent-harness.md). Every project made
from the template follows this protocol; agent types are those of the `harness-ops` plugin (`harness-ops:<name>`).

## 1. Roles
| Role | Who | Does | Never |
|---|---|---|---|
| owner | the human | sets the project brief, answers open questions, approves budget raises, sets `[tool.devkit] quota_cap` | |
| lead | the main Claude session on a machine (one per machine) | orient, file and rank issues, dispatch workers, review and `dev integrate` PRs, keep docs/HANDOFF.md current | build features while its workers run |
| researcher | subagent `harness-ops:researcher` | one research issue → one note in `docs/research/` | edit code, spawn subagents |
| auditor | subagent `harness-ops:research-auditor` | adversarial check of a note's claims against its sources | edit the note |
| builder | subagent `harness-ops:builder` | one build issue, test-first, in its worktree → PR | merge, push to main, edit outside the issue's Owns without saying so |
| tester | subagent `harness-ops:tester` | adversarial, property and regression tests for one issue → PR | weaken an existing test |
| architect | subagent `harness-ops:architect` | when `dev orient` says a consolidation review is due: feature inventory → an epic of step issues | edit, move or delete code |
| suite-auditor | subagent `harness-ops:suite-auditor` | when `dev suite-audit report` lists judgement items: reads those tests → at most 5 `type:test` issues | edit, move, skip or delete a test |
| reviewer | subagent `harness-ops:reviewer` | read-only pre-merge review of a PR | edit, commit |

Several machines can each run a lead. They coordinate only through GitHub: issues, claim comments, PRs and `main`.
Nothing assumes that two machines share a disk.

## 2. The cycle (one issue)
1. **File.** `uv run dev board new --title ... --type research|build|test|infra|bug --prio P0..P3 --done "<measurable>" [--owns <path>] [--blocked-by N]`.
   Every DONE WHEN item is something a reviewer can check: a test, a number with a threshold, or an artifact.
   The item meets the conventions below (§2a).
2. **Pick.** `uv run dev board next` lists ready items whose blockers are closed. It ranks them by effective priority
   (the highest of the item's own and of everything it transitively blocks), then by how many open items they
   transitively unblock, and prints that reason on each line. `uv run dev board wave N` picks up to N of them whose
   Owns overlap neither each other nor any item in progress or in review, for parallel dispatch.
3. **Claim.** `uv run dev board claim N --agent <name> --branch N-<slug>`. Claiming is race-safe across machines: the
   earliest live claim comment wins and a loser withdraws. A claim with no update for 6 h shows as STALE. Take one
   over only with `--force`, after reading its last comment.
4. **Dispatch.** The lead spawns the worker with a one-line prompt: `Work on #N. Start with uv run dev board brief N.`
   It adds only what the repository cannot know: the owner's words, or a decision. The brief carries the rest,
   including the files other work is changing now (open PRs on any machine, plus local worktrees).
   Use `isolation: worktree` for builders, testers and researchers alike, so nothing they write lands on main.
5. **Work.** Builders write failing tests first. Iterate with `uv run dev test --affected`. Post milestones with
   `uv run dev board beat N "..."` at least every ~4 h.
6. **Hand back.** Commit, run `uv run dev check` once on the tip, then `uv run dev pr N --summary <file>`. The summary
   gets `Closes #N` (every DONE WHEN item met), or `Refs #N` with `--partial`. The item moves to review.
   The reply to the lead is at most 25 lines.
7. **Integrate** (lead). Read the PR and check its numbers against the data, then run `uv run dev integrate <pr>`.
   This merges the PR onto `origin/main` in a throwaway worktree and re-runs the gate there. A worker's report is
   not evidence. The merged tree is main merged into the PR, with generated files regenerated (`dev docs`); when
   that differs from the PR head, integrate pushes it to the PR branch first. A real conflict is sent back: the
   paths go on the PR, the item is blocked ('rebase on main') and the worker to resume is named. If the gate
   passes, the PR merges, then its items close, blocked items whose blockers closed move to ready, and the
   worktree is removed. The last line, `RESULT-JSON: {...}`, says what happened (`outcome`). A PR that touches
   harness files is refused until the lead has read that diff (`--harness-ok`); a `.gitattributes` change can hide
   conflicts, so read it too.
   Integrate also refuses a PR with failing or pending checks, a standing CHANGES_REQUESTED review or unresolved
   review threads, unless `--override '<reason>'` is given; the reason is logged on the PR. The lead reads
   `uv run dev board feedback <pr>` first and answers or fixes each comment. When pending checks are the only
   unfinished feedback, integrate waits for them (bounded by `checks_wait_s`; a timeout is `waiting`) instead.
8. **No Actions minutes** (billing-blocked, Actions disabled, or a required check never queues): run
   `uv run dev integrate <pr> --local-ci`. Integrate then runs, in the merged tree, the gate and each required
   check's stand-in commands (`local_ci` in `[tool.devkit]`: context -> commands; `[]` is the gate alone), with all
   output in one log. Only after all of them passed, and only on that exact merged commit, does it post each required
   context Actions did not run as a success commit status carrying the log's sha256, comment on the PR that CI ran
   locally, and merge. A check Actions did run and fail still refuses. It refuses up front when a required check
   accepts only the GitHub Actions app (a posted status would not count; the refusal names the `app_id: -1` change
   for the owner) or has no stand-in. The board needs no scheduled job either: `dev orient` and `dev integrate` run
   `board sync`, so the template's `board.yml` is optional.

## 2a. Conventions (`uv run dev board lint`)
Every open item meets these. `board lint` lists what breaks them and exits 1. `--fix` makes the label edits that
follow from the item itself: a missing priority becomes P2 and a missing status ready (blocked while a blocker is
open); of several labels, the highest priority and the furthest status are kept; a `Research:`, `Experiment:` or
`Epic:` title sets the type. The gate (as warnings, since the board is not the tree) and `dev orient` show them too.
- **Labels.** Exactly one `type:`, one priority (P0..P3) and one `status:` label. An epic may have no status.
- **Title.** At most 100 characters, with no trailing period. A research item starts `Research:` or `Experiment:`,
  an epic starts `Epic:` (with `type:epic`), and every other item is `<component>: <outcome>`, for example
  `board: lint conventions`. The type is in the labels, never in the title.
- **Body.** DONE WHEN has at least one real condition (not empty, not a bare number). Build and infra items list
  their Owns as backticked paths or globs.
- **Links.** No blocked-by cycle. A ready item has no open blocker. An epic has sub-issues. An item that a merged PR
  closes by keyword (`Closes #N`: every DONE WHEN met) is closed, not left open.

## 3. Avoiding collisions
- **Owns.** An issue lists the paths it may change. Two workers run at once only when their Owns do not overlap
  (`board brief` shows the overlaps). A small edit outside Owns that the issue needs is allowed. Name it in the hand-back.
- **Fix now.** A defect or gap found while working goes in this PR as its own commit, prefixed `fix-now:` and
  listed under `Fixed in passing` in the PR body, when all four hold: (1) at most 20 changed lines in at most 2
  files; (2) the files are in the PR's diff or Owns, or this change exposed it; (3) no design choice, new interface
  or dependency, or owner approval, and the gate checks it; (4) no other open item or hotspot holds the file. A
  defect the PR itself introduced is fixed whatever its size. Otherwise file it and name the condition that failed.
- **Append-only files** merge with git's union driver (`.gitattributes`): research notes, logs and OPEN_QUESTIONS.
  Append sections; never rewrite another agent's lines.
- **Generated files** are never merged by hand. Regenerate them with `uv run dev docs`.
- **Hotspots** (a registry, a shared config) belong to the lead: a worker asks for the change in its hand-back.
- Rebase onto `main` before `dev pr` if main moved. One PR per issue.

## 4. Resources (per machine)
- **Memory.** The spawn hook holds a builder or tester until enough memory is free. The gate takes a machine-wide
  lock and sizes its test workers to free memory. Researchers and reviewers run no tests and start at once.
- **Quota.** `uv run dev usage-poll` (detached) records the weekly usage percent, and the spawn hook refuses new
  agents at `[tool.devkit] quota_cap`. At the cap, commit, push the branch and hand off; do not start new work.
- **Concurrency.** The `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` setting (`.claude/settings.json`) bounds spawns.
  In practice: up to about 6 researchers, and as many builders as there are disjoint Owns and free memory.

## 5. Long sessions and compaction
- State lives in files and GitHub, never only in context: docs/HANDOFF.md (lead), issue comments (workers), PR bodies.
- The SessionStart hook re-injects `dev orient` after `/clear` and after compaction, so a compacted lead resumes
  from the current board, not from a summary.
- Resuming after a limit or a crash: run `uv run dev status`. It lists every worktree, its commits ahead and its dirty
  files. Send each worker a short resume message, or respawn it with the same one-line prompt. Its brief and its
  branch hold the state.
- The lead updates docs/HANDOFF.md whenever the set of running agents changes: merged, in flight, next.

## 6. Research fan-out
Follow the `research` skill. It runs in rounds: decompose, fan out researchers, list gaps, and run a new round only
on the gaps. It stops when a round adds no new decision-relevant finding, or after 4 rounds. Notes are synthesized
into one house-style note, then audited by `research-auditor`, then pass `dev cite-check`.
