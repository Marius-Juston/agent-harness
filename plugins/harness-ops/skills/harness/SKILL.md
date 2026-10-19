---
name: harness
description: How to change a project's agent surface safely — CLAUDE.md, .claude/ settings and agents, skills, hooks, the [tool.devkit] config, CI — without drift or bloat, and how to measure whether a change helped.
when_to_use: Before editing any of those files, when a rule is broken repeatedly, or when token use or error rates look high.
---

# Changing the harness

The harness is what every agent is judged by. The evidence on agent guidance is consistent:
- Short, procedural guidance about non-obvious things helps. SkillsBench (arXiv 2602.12670): compact skills +19
  points, comprehensive docs +0.7, self-generated skills below no skill at all.
- Repository overviews in context files add about 20 % cost and no success (arXiv 2602.11988).
- Adherence decays as instructions accumulate (IFScale, arXiv 2507.11538).
Details and sources: [agent-harness.md](https://github.com/Marius-Juston/agent-harness/blob/main/docs/research/agent-harness.md).

## Where a change belongs
- **Generic** (any project would want it): the gate, board and checks (the `devkit` package), the agents, skills
  and hooks of the `harness-ops` plugin, and the scaffold (Copier template) all live in the agent-harness
  repository. Change them there, release a tag, then bump the pin here and run `copier update`. Never patch a copy.
- **Project-specific**: CLAUDE.md, `[tool.devkit]` in pyproject.toml, `.devkit/`, `.claude/settings.json`, and any
  agents or skills of the project's own under `.claude/`.

## Rules
1. **Enforce rather than ask.** A rule broken in a quarter of runs or more becomes a hook (with a test) or a gate
   check. Prose cannot make it hold.
2. **Rewrite rather than refuse** where possible. A hook that answers with `updatedInput` (read_guard) costs no
   round trip; a refusal costs a rewrite.
3. **One source per fact.** Commands come from the CLI parser, the surface from its files, budgets from
   `.devkit/ratchets.toml`. Prose links to them. After a change, run `uv run dev docs`; the gate checks freshness,
   dangling paths and unknown `uv run dev` commands.
4. **A raise needs evidence.** It needs a `[[raise]]` entry approved by the owner. Cut text before you add text.
5. **Must-hold rules go first** in a skill: after compaction only its first ~5,000 tokens survive.
6. **Descriptions say when to use.** They are the only text the model sees before invoking, and they are capped
   at 1,536 characters. Name the triggers.
7. **No checks that pass on nothing.** Every check returns a `Ran` with a count, and zero checked fails.
8. **Free tools first.** A paid service needs an open question and owner approval.

## Measure
- `uv run dev agent-stats`: per agent type, runs, median tool calls, fresh, cached and output tokens, and the share of
  tool-result characters in big results. Compare before and after a change over 10 or more runs.
- Watch: hand-backs over the agent's own line limit, whole-file reads, repeated gate failures of one kind, review findings that recur.
  A finding class that recurs in 5 % of reviews or more becomes one checklist line or a lint.
- An agent-file change that changes nothing measurable over about 30 runs gets reverted.

## Evals
- **Whole surface** (CLAUDE.md, agents, hooks, skills in project context): `uv run dev eval [name] --trials 3`.
  Each scenario (`evals/scenarios/` by default) is a real failure plus the expected decision, graded on the
  transcript. Run the scenarios a change touches, and add one for every real failure found.
- **One skill in isolation**: use skill-creator. Its with-skill vs baseline benchmark, blind version comparison and
  description-trigger loop measure a skill. Use it to *evaluate* and to tune descriptions, not to write skill
  bodies: self-generated skills scored below no skill (SkillsBench).
