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
| reviewer | subagent `harness-ops:reviewer` | read-only pre-merge review of a PR | edit, commit |

Several machines can each run a lead. They coordinate only through GitHub: issues, claim comments, PRs and `main`.
Nothing assumes that two machines share a disk.

## 2. The cycle (one issue)
1. **File.** `uv run dev board new --title ... --type research|build|test|infra|bug --prio P0..P3 --done "<measurable>" [--owns <path>] [--blocked-by N]`.
   Every DONE WHEN item is something a reviewer can check: a test, a number with a threshold, or an artifact.
2. **Pick.** `uv run dev board next` lists ready items whose blockers are closed, by priority, then by how much they unblock.
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
   starts with `Closes #N` only if every DONE WHEN item is met, otherwise `Refs #N`. The item moves to review.
   The reply to the lead is at most 25 lines.
7. **Integrate** (lead). Read the PR and check its numbers against the data, then run `uv run dev integrate <pr>`.
   This merges the PR onto `origin/main` in a throwaway worktree and re-runs the gate there. A worker's report is
   not evidence. If the gate passes, the PR merges, blocked items whose blockers closed move to ready, and the
   worktree is removed. A PR that touches harness files is refused until the lead has read that diff (`--harness-ok`).
   Integrate also refuses a PR with failing or pending checks, a standing CHANGES_REQUESTED review or unresolved
   review threads, unless `--override '<reason>'` is given; the reason is logged on the PR. The lead reads
   `uv run dev board feedback <pr>` first and answers or fixes each comment.

## 3. Avoiding collisions
- **Owns.** An issue lists the paths it may change. Two workers run at once only when their Owns do not overlap
  (`board brief` shows the overlaps). A small edit outside Owns that the issue needs is allowed. Name it in the hand-back.
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
