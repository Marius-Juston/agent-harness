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
- At the quota cap: start nothing; commit, update HANDOFF, stop.

## 1. Where things stand
!`uv run dev status 2>/dev/null | head -20 || true`

Then `uv run dev orient` if the SessionStart context is old, and `uv run dev board list --status review`.
`Suite audit due` in orient: run what it names; `harness-ops:suite-auditor` takes its judgement items.

## 2. Close the loop first
For each item in review, in priority order:
1. `gh pr view <pr>`, `gh pr diff <pr> --name-only` and `uv run dev board feedback <pr>`.
2. Spawn a `harness-ops:reviewer` for PRs touching harness files, several Owns, or anything a user sees; a `harness-ops:research-auditor` for decision-driving research PRs.
3. Answer or fix every comment, from people and bots (resume the worker for fixes), resolve its thread (outdated threads block too), and wait for green checks.
4. Triage the PR's follow-ups: a fix-now (multi-agent.md §3) or a defect in its own new code returns to the worker; the rest need a reason.
5. `uv run dev integrate <pr>`; add `--harness-ok` only after reading the harness diff. `--override '<reason>'` only on the owner's word. Its `RESULT-JSON` outcome `conflict`: resume the named worker; `stopped` or `gate-failed`: comment the failure on the PR, then resume or respawn it.

## 3. Pick the next wave
- `uv run dev board next -n 10`. Prefer P0, then what unblocks most.
- A build issue whose Refs has no note waits for research.
- Run builders in parallel only when `board brief` shows no overlap in Owns. Researchers fan out wide.
- Orient says `Consolidation review due`: spawn a `harness-ops:architect`. Feature cuts wait for the owner. Only the lead resets the trigger: `dev integrate <pr> --consolidation-review <epic>`.
- New work found: `uv run dev board new --title ... --type ... --prio ... --done "<measurable>" [--owns ...] [--blocked-by N]`. Split items whose DONE WHEN needs two measurements.

## 4. Dispatch
- Issue researchers need `isolation: worktree` (builders and testers set it). Prompt `Work on #N. Start with uv run dev board brief N.`, plus only the owner's words or your decisions.
- Effort: pass `high` for brownfield bugs, security or cross-layer issues. After a gate failure or `board block`, respawn one level up (`high`, then `xhigh`; resume keeps the old level); after two `xhigh` failures, `model: fable`. Log every dispatch: `board beat N "effort <level>"`.
- Plugin agent types are `harness-ops:<name>`; a project's own in `.claude/agents/` keep bare names. Changed agent files register only at the next session start.
- Claim for them first: `uv run dev board claim N --agent <type> --branch N-<slug>`.
- Another repository's issue: claim it and add its worktree there, then prompt `Work on <repo> #N in <abs path>; prefix every command with cd <abs path> &&`. Same guards; recursive `rm` refused. Keep the agent's empty isolation worktree until it stops.
- Broad research questions take the `harness-ops:research` skill.

## 5. Resume after a limit, crash or compaction
Per worktree in `uv run dev status`:
- Commits or edits: SendMessage the worker a resume (what it had; "finish, gate, `dev pr`").
- No commits and no files: respawn it with the same one-line prompt.
- A STALE claim (another machine, idle 6 h): `board claim --force` only after reading its last comment.

## 6. Keep the record
Update the handoff file (`[tool.devkit] handoff`, default `docs/HANDOFF.md`) when agents change and after each integrate batch (compacts at `autoCompactWindow`), in under 40 lines:
- In flight: issue, agent, branch.
- Just merged.
- Next: the ranked startable items, and why.
- Owner decisions pending (also in open questions).

Commit HANDOFF with your other lead edits on a branch; land them via `dev pr`/`dev integrate`.
