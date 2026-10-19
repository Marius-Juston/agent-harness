---
name: research
description: Recursive deep research in a repository run with agent-harness — decompose a question, fan out researcher subagents in rounds, chase the gaps each round reveals until saturation, then synthesize one cited house-style note, audit it adversarially, and verify every source.
when_to_use: For any broad or decision-driving question (the domain's evidence, methods, models, tools, prior art), or when the owner asks to research something "in depth".
argument-hint: "[question]"
---

# Recursive research

One round of research rarely finds everything. Each round's findings reveal new gaps. This loop runs rounds until a
round stops changing the recommendation. All search tools here are free: arxiv MCP, OpenAlex, Semantic Scholar,
Crossref, WebSearch/WebFetch, context7, Wikipedia (orientation only).

## 0. Before round 1
- Read `docs/research/INDEX.md`. If a note covers the topic, the goal is to **extend** it. Pass its path to every agent.
- Claim the research issue and create its worktree: `git worktree add ../<repo>-worktrees/<n>-<slug> -b <n>-<slug>`.
  Every file of this research lives there, under `docs/research/_scratch/<slug>/`, one file per sub-question.
  Give researchers absolute paths into it. Each writes only its own file, so nothing collides, and the synthesis
  later sees every round.
- The question: $ARGUMENTS (empty: take it from the issue or the conversation). Write it, its scope and the
  decision it serves to `_scratch/<slug>/QUESTION.md`.

## 1. A round
0. **Check discovery.** Every `uv run dev lit search` prints a `discovery` line first. If a researcher's says DEGRADED
   (OpenAlex and Semantic Scholar both down or rate limited; one is enough), it stops and reports; hold the round,
   wait and re-run it. A round on the fallbacks alone misses papers silently. That line is the note's `Search coverage:`.
1. **Decompose** what is still open into sub-questions that do not overlap and together cover it. Use 3 by default
   and up to 8 for a broad topic; split by mechanism, evidence base, prior art, tools (`uv run dev reuse check`), and risks or counter-evidence.
   Always give one researcher the job of arguing *against* the current leading answer.
2. **Fan out** all researchers in one response (parallel Agent calls, `subagent_type: harness-ops:researcher`,
   background).
   Each prompt names: the sub-question, what is already known (file paths, not pasted text), the scratch file to
   write (`docs/research/_scratch/<slug>/r<round>-<topic>.md`), and "round mode". A researcher hands back only once
   `uv run dev cite-check` passes on that file (link text is the title; author/year/venue follow the link).
3. **Gap pass.** When all have returned, read their hand-backs, not the files. Write
   `_scratch/<slug>/gaps-r<round>.md` with: new findings that change the recommendation, contradictions between
   researchers, claims resting on one weak source, and questions nobody answered. Snowball the key papers
   (`uv run dev lit refs <id>`, `uv run dev lit cited-by <id>`); new papers go to the next round.
4. **Stop or loop.** Run another round only on the gaps, never the whole question again. Stop when any of these holds:
   - a round adds no finding that changes the recommendation (saturation);
   - 4 rounds have run;
   - the quota cap is near (spawn_guard refuses).
   Record the stop reason in the gaps file.

## 2. Synthesis
Spawn ONE `harness-ops:researcher` in issue mode, told to work in the issue worktree (absolute path; no `isolation`, because
the worktree already exists). It reads every scratch file of the slug and writes or extends `docs/research/<slug>.md` in house style. Contradictions are resolved by
evidence strength (a meta-analysis beats a single study; a replication beats a claim), and unresolved ones are
stated as such. It runs `uv run dev cite-check` until it passes and files follow-up issues for the gaps it left open.

## 3. Audit, then integrate
- Spawn a `harness-ops:research-auditor` on the note. On "return" or "accept after fixes", send the findings to the synthesis
  researcher (SendMessage) and audit again: at most 2 cycles, then put the rest in the note's open-issues section.
- The synthesis researcher opens the PR (`uv run dev pr <n>`). The lead integrates it.
- The scratch folder stays in git as the audit trail. It merges with the union driver.

## Cheap alternative
For a quick narrative answer that does not drive a decision, one `harness-ops:researcher` in issue mode is enough. Anthropic's
`deep-research` skill also works for a one-off report, but its output is not house style. Run it through
`dev cite-check` and fold it into a note before anything depends on it.
