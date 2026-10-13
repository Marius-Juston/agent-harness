# Question (#220)

1. Should agent-harness and its Copier template ship architecture decision records (ADRs)? If so: format,
   when one is required, how agents find and obey them, how they are superseded, and how the gate checks them.
2. Agentic drift at every level of a harness-run repository: what drifts (prose, prompts, CLI and flags, config,
   research notes vs code, decisions, board state, agent memory and HANDOFF, template vs project, model behaviour
   over time), what each level has today in agent-harness, and the cheapest reliable enforcement for each.

Scope: agent-harness and projects rendered from its template (a private downstream project, called project B, is one). Free options only.
Decision it serves: whether to add ADRs and which drift checks to build next (each filed as a build issue).
Known drift this session (lead, 2026-10-13): see issue #220 Context.

## Added by the owner, 2026-10-13 (before round 2)
3. Is there something better than ADRs for an agent-run repository? Compare, on this repo's measured decisions
   (r1-adrs.md: 23 comment decisions, 5 comment-only, 5 reversals) and drift classes (r1-taxonomy.md):
   executable decisions (fitness functions, import-linter contracts, tests), path-scoped `.claude/rules/` loaded
   when matching files are touched, hooks, inline rule + reason + `enforced-by` marker, decision-labelled issues
   with a generated index, and ADRs as the baseline. Criteria: which reversals each would have prevented, words
   loaded into agent context, drift surface added, maintenance cost, false-positive risk.
