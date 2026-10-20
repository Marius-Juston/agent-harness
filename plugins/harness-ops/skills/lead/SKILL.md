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
- A worker's report is not evidence: `dev integrate` re-gates the merged tree; check PR numbers against data.
- At the quota cap: start nothing; commit, update HANDOFF, stop.

## 1. Where things stand
!`uv run dev status 2>/dev/null | head -20 || true`

Then `uv run dev orient` if SessionStart context is old; `uv run dev board list --status review`.
`Suite audit due` in orient: run what it names; `harness-ops:suite-auditor` takes its judgement items.

## 2. Close the loop first
Per item in review, by priority:
1. `gh pr view <pr>`, `gh pr diff <pr> --name-only` and `uv run dev board feedback <pr>`.
2. Spawn a `harness-ops:reviewer` for PRs touching harness files, several Owns, or anything a user sees; a `harness-ops:research-auditor` for decision-driving research.
3. Answer or fix every comment (people and bots; resume the worker for fixes), resolve its thread (outdated ones block too).
4. Only Blocking or untagged findings return the PR, with at most 3 Nits: send the worker a pointer of at most 200 characters, `PR #<pr> round <N>: <review URL>; uv run dev board feedback <pr>`, and post no review text yourself; the worker posts a fix-to-finding map. Re-review after a Blocking fix only, passing round number, last reviewed SHA and earlier review URLs (no confirmation round; a project's security reviewer, if any, re-reviews any code change). After round 3, take "reconsider the approach" to the owner: never merge with an open Blocking finding.
5. Follow-ups, Pre-existing and Follow-up findings: a fix-now (multi-agent.md §3) or a new-code defect returns to the worker; file the rest as issues.
6. `uv run dev integrate <pr>`; `--harness-ok` only after reading the harness diff; `--override '<reason>'` only on the owner's word. `RESULT-JSON` outcome `conflict`: resume the named worker; `refused`, `stopped`, `gate-failed`: `--why` shows why (never rerun); comment it on the PR; resume or respawn. `waiting`: rerun once; `again` (same head timed out twice): `--local-ci` if Actions cannot run, else ask the owner. A retargeted stacked PR's worker takes the printed cherry-pick route to a `-v2` branch, never rebase and force push.

## 3. Pick the next wave
- `uv run dev board next -n 10`: P0 first, then what unblocks most.
- A build issue whose Refs has no note waits for research.
- Parallel builders only when `board brief` shows no Owns overlap. Researchers fan out wide.
- `Consolidation review due` in orient: spawn a `harness-ops:architect`; feature cuts wait for the owner. Only the lead resets it: `dev integrate <pr> --consolidation-review <epic>`.
- New work: `uv run dev board new` with measurable `--done`, `--owns`, `--blocked-by` if waiting. Split an item whose DONE WHEN needs two measurements. Duplicates: `gh issue close <n> --duplicate-of <m>`.

## 4. Dispatch
- Issue researchers need `isolation: worktree` (builders, testers set it). Prompt `Work on #N. Start with uv run dev board brief N.`, plus only owner words or your decisions.
- Effort `high` for brownfield bugs, security or cross-layer issues. After a gate failure or `board block`, respawn one level up (`high`, then `xhigh`; resume keeps the old level); after two `xhigh` failures, `model: fable`. Log every dispatch: `board beat N "effort <level>"`.
- Agent types: `harness-ops:<name>` for plugin agents, bare for `.claude/agents/`; new or edited agents load next session.
- Claim first: `uv run dev board claim N --agent <type> --branch N-<slug>`.
- Another repository's issue: claim it, add its worktree there, prompt `Work on <repo> #N in <abs path>; prefix every command with cd <abs path> &&`. Same guards; recursive `rm` refused. Keep its empty isolation worktree until it stops.
- Broad research: the `harness-ops:research` skill.

## 5. Resume after a limit, crash or compaction
Per worktree in `uv run dev status`:
- Commits or edits: SendMessage a resume (what it had; "finish, gate, `dev pr`").
- Neither: respawn, same prompt.
- STALE claim (another machine, idle 6 h): `board claim --force` only after reading its last comment.

## 6. Keep the record
Update HANDOFF (`[tool.devkit] handoff`, default `docs/HANDOFF.md`) when agents change and after each integrate batch (before `autoCompactWindow` compaction), under 40 lines: in flight (issue, agent, branch), just merged, next (ranked startable items, why), owner decisions pending (also in open questions).
Land it with other lead edits on a branch via `dev pr`/`dev integrate`.
