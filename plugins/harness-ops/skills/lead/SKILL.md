---
name: lead
description: The lead session's routine in a repository run with agent-harness — orient, integrate finished PRs, file and rank issues, dispatch workers with one-line prompts, resume after limits or compaction, keep HANDOFF current.
when_to_use: At the start of a working session, when the owner says "continue", "/goal", "dispatch", "integrate", or after a compaction.
allowed-tools:
  - Bash(uv run dev status*)
  - Bash(head *)
---

# Lead routine

Rules:
- Never build while workers run: the lead plans, dispatches, integrates.
- A worker's report is not evidence: `uv run dev integrate` re-gates the merged tree; check PR numbers against data.
- Spawn prompts are one line. The brief carries the rest.
- At the quota cap: start nothing; commit, update HANDOFF, stop.

## 1. Where things stand
!`uv run dev status 2>/dev/null | head -20 || true`

Then `uv run dev orient` if the SessionStart context is old, and `uv run dev board list --status review`.
`Suite audit due` in orient: run what it names; `harness-ops:suite-auditor` takes its judgement items.

## 2. Close the loop first
For each item in review, in priority order:
1. `gh pr view <pr>`, `gh pr diff <pr> --name-only` and `uv run dev board feedback <pr>` (new comments, unresolved threads).
2. Spawn a `harness-ops:reviewer` when the PR touches harness files, several Owns, or anything a user sees. Spawn a `harness-ops:research-auditor` for research PRs that drive a decision.
3. Read every comment, from people and bots. Answer or fix each one (resume the worker for fixes), resolve its thread (outdated threads block too), and wait for green checks.
4. `uv run dev integrate <pr>`; add `--harness-ok` only after reading the harness diff. It refuses failing or pending checks, requested changes and unresolved threads; `--override '<reason>'` only on the owner's word (logged on the PR). Its last line, `RESULT-JSON`, gives the outcome: on `conflict`, resume the worker it names; on `stopped` or `gate-failed`, comment the failure on the PR and resume or respawn the worker.

## 3. Pick the next wave
- `uv run dev board next -n 10`. Prefer P0, then what unblocks most.
- Research comes first: a build issue whose Refs has no note waits for its research issue.
- Run builders in parallel only when `board brief` shows no overlap in Owns. Researchers fan out wide (no tests, little memory).
- Orient says `Consolidation review due`: spawn a `harness-ops:architect` with `isolation: worktree`. Feature cuts wait for the owner. Only the lead resets the trigger: `dev integrate <pr> --consolidation-review <epic>`.
- New work found: `uv run dev board new --title ... --type ... --prio ... --done "<measurable>" [--owns ...] [--blocked-by N]`. Split an item when its DONE WHEN would need two different measurements.

## 4. Dispatch
- Builders, testers and issue researchers run with `isolation: worktree`. Use one line, for example: `Work on #N. Start with uv run dev board brief N.` Add only what the repository cannot know: the owner's words, or a decision you made.
- Agents come from the `harness-ops` plugin, so their types are `harness-ops:builder`, `harness-ops:tester`,
  `harness-ops:researcher`, `harness-ops:reviewer`, `harness-ops:research-auditor`, `harness-ops:architect`, `harness-ops:suite-auditor`. A project's own agents in
  `.claude/agents/` keep bare names. Agent files changed during a session register only at the next session start.
- Claim for them first: `uv run dev board claim N --agent <type> --branch N-<slug>`.
- A broad research question takes the `harness-ops:research` skill (researcher rounds, synthesis, audit), not one researcher.

## 5. Resume after a limit, crash or compaction
`uv run dev status` lists every worktree with its commits ahead and dirty files.
- A worker with commits or edits: SendMessage it a resume (what it had; "finish, gate, `dev pr`").
- A worker with no commits and no files: respawn it with the same one-line prompt.
- A claim on another machine idle for 6 h shows STALE. Take it over with `board claim --force` only after reading its last comment.

## 6. Keep the record
Update the handoff file (`[tool.devkit] handoff`, default `docs/HANDOFF.md`) whenever the set of agents changes, in under 40 lines:
- In flight: issue, agent, branch.
- Just merged.
- Next: the ranked startable items, and why.
- Decisions waiting on the owner (also in the open-questions file).

Commit HANDOFF with your other lead edits on a branch; land them via `dev pr`/`dev integrate`.
