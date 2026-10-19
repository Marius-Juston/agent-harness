# r2: audit of the always-loaded and per-agent harness text against Claude 5 guidance and observed behaviour

Round mode, sub-question: which parts of the harness text should be cut, rewritten, or turned into tooling?
Search coverage: discovery OK at round start (openalex, s2, crossref, europepmc, arxiv answered); my own
`uv run dev lit status` on 2026-10-19 said "OK, partial (s2 down)". Literature was used only for two abstracts. The
main evidence is the repo-E transcripts and five Anthropic posts and docs pages, all fetched 2026-10-19.

Data: the repo-E transcripts for 2026-10-11..16 (14 lead files with data, 654 subagent files). Only files whose first
timestamp is before 2026-10-17 count. The agents ran plugin v0.1.4-v0.1.7, and the text audited below is v0.1.9.
Where a rule's wording changed between the two, the finding says so.
Scripts in this folder:
- [scripts/surface_audit.py](scripts/surface_audit.py): rule compliance and first-turn components per agent type;
- [scripts/surface_audit_extra.py](scripts/surface_audit_extra.py): natural experiment, refusal causes, skill listing;
- [scripts/first_turn_shape.py](scripts/first_turn_shape.py): tool schemas and system size per agent type, from
  `api-request-shape`, plus a chars-per-token fit;
- [scripts/component_cost.py](scripts/component_cost.py): the window cost of each fixed component;
- [scripts/surface_probes.py](scripts/surface_probes.py): auto-mode steer, isolation-refusal causes, `dev pr` forms,
  check counts, literature-API calls before and after the rule, branch renames, piped `dev` output.

Prices are the r1 fit: cache read $0.20/M and cache write $7.95/M ([r1-usage-logs.md](r1-usage-logs.md) §1).

## Findings

**F1. The largest fixed cost is the tool schemas the harness agents inherit, not the harness text.** VERIFIED
([first_turn_shape.py](scripts/first_turn_shape.py), [component_cost.py](scripts/component_cost.py)).
- **First call.** Builder, researcher and tester spawns start at a median 28.3k / 27.5k / 28.0k tokens. Reviewer,
  security-reviewer and research-auditor start at 7.3k / 8.2k / 9.7k: their `tools:` lists leave out the rest.
- **Artifact tool.** The Artifact schema alone is 37,145 chars, about **12.9k tokens**: 46 % of a builder's first
  call. The fit is 2.88 chars per token over 318 runs, with an absolute residual p90 of 689 tokens.
- **Skill tool and listing.** Together about **5.0k tokens**. The listing is about 12.5k chars, of which the
  harness-ops skills take about 1.2k. The rest are user-level skills (dataviz 1,445, claude-api 1,080, docs 1,027,
  code-review 992 …) ([surface_audit_extra.py](scripts/surface_audit_extra.py) §6).
- **Agent tool.** About 1.2k tokens in builder and tester.
- **Use in the window.**

  | tool | builder (141 runs) | tester (10) | researcher (259) |
  |---|---|---|---|
  | Artifact | 0 | 0 | 4 |
  | Skill | 1 | 0 | 2 |
  | Agent | 0 | 0 | removed already (`disallowedTools: Agent`) |

- **Cost.** Each first call reads 17-18k tokens from cache and writes 9.6-10k. So the tool prefix is shared between
  agents, and its cost is the re-read on every call. Over the window:
  - Artifact: **$74** (reads only) to $116 (with one write per run);
  - Skill tool and listing: $29-45;
  - Agent tool: $2.5-4.

  All of HARNESS.md plus CLAUDE.md (about 1.4k tokens, an upper bound that assumes every agent loads them) comes to
  $10-18.
- **Fix.** Claude Code supports `disallowedTools` in subagent frontmatter, "removed from inherited or specified list"
  SOURCED ([Create custom subagents](https://code.claude.com/docs/en/sub-agents), Claude Code docs). The researcher's
  `disallowedTools: Agent` does remove Agent from its schema list (VERIFIED in its `api-request-shape`).
  Anthropic's tool guidance points the same way: "The bar to add a new tool is high, because this gives the model one
  more option to think about" SOURCED
  ([Seeing like an agent: how we design tools in Claude Code](https://claude.dev/blog/seeing-like-an-agent/),
  Shihipar 2026, claude.dev).

**F2. The platform's own steer beats the "Navigate by symbol" rule, and the outcome the rule protects already holds.**
- **The steer.** Every builder (141/141), researcher (259/259), tester (10/10) and lead (14/14) run carries Claude Code's
  auto-mode reminder. It tells agents to "read files with cat, head, or sed -n … make small, mechanical file changes
  with sed, heredocs, or short scripts instead of the dedicated Read, Edit, or Write tools". VERIFIED: it is the
  `auto_mode` attachment ([surface_probes.py](scripts/surface_probes.py)). Read-only agents with a `tools:` list do
  not get it.
- **What agents use.** `dev code` is 3-6 % of navigation calls (builder 117 against 1,120 grep, 1,007 `sed -n`,
  480 cat and 480 Read).
- **Loading HARNESS.md changes nothing.** HARNESS.md is loaded in repo-E and absent in agent-harness worktrees, a
  natural experiment within one agent type. The `dev code` share was 3.7 % with HARNESS.md and 5.8 % without for
  builders, and 3.8 % against 3.1 % for researchers. VERIFIED
  ([surface_audit_extra.py](scripts/surface_audit_extra.py) §1). The study is observational: repository and task mix
  differ, and builder.md repeats the rule in both arms.
- **Whole-file dumps are rare anyway.**
  - read_guard rewrote 19 Reads and 4 Bash cats in 654 subagent runs.
  - Only **3** `cd X && cat <long file>` calls got past the guard: 1 builder and 2 reviewer, 47.8k chars. VERIFIED
    ([surface_audit.py](scripts/surface_audit.py)).
  - r1's 123 `cd && cat` cases ([r1-usage-logs.md](r1-usage-logs.md) §4) were mostly files under the line caps.
  - So fixing the read_guard `cd` parse is worth about 3 calls in this window.

**F3. Some prescribed command forms are refused by the platform. This is the biggest retry source.** VERIFIED
([surface_probes.py](scripts/surface_probes.py), [surface_audit_extra.py](scripts/surface_audit_extra.py) §8).
- **Scale.** 139 of 141 builder runs and 10 of 10 tester runs hit at least one worktree-isolation refusal (685 in
  all). Researchers, who run without isolation, hit them in 14 of 259 runs.
- **Causes.**

  | cause | refusals | detail |
  |---|---|---|
  | piped `dev pr` heredoc | 105 of 106 | the prescribed form `uv run dev pr <n> --summary - <<'EOF'` works plain (102 ok, 3 refused); a trailing `2>&1 \| tail -N` gets it refused |
  | cross-repo `cd` | 146 | `cd <other repo worktree> && …`, the form the lead skill §4 prescribes for another repository's issue |
  | scratchpad writes | 87 | to `/tmp/.../scratchpad`, which the Claude Code system notes suggest |
  | loops, `python3 -` heredocs, computed variables | the rest | |

- **The documented rule.** Claude Code refuses "when it can't verify from the command text that any git the command
  runs stays inside the worktree" SOURCED
  ([Run parallel sessions with worktrees](https://code.claude.com/docs/en/worktrees), Claude Code docs). A `cd` in a
  subagent "doesn't persist between Bash … tool calls" SOURCED
  ([Create custom subagents](https://code.claude.com/docs/en/sub-agents)), which is why researchers launched in the
  main checkout prefix 63 % of their Bash calls with `cd`.
- **Output trimming.** Agents piped `dev` output through tail, head or grep 969 times (researchers) and 785 times
  (builders). That habit is what turns the `dev pr` heredoc into a refused command.
- **Report-file refusals.** 12 in all. 9 of them were `.scratch/summary.md` (PR bodies) or `REPORT.md`, so the "do
  not write a report file" clause fires as designed.

**F4. "Run `dev check` once at the end" is neither followed nor consistent with the gate.** VERIFIED.
- Builders ran `dev check` a median of 4 times per run, and 136 of 139 ran it more than once. That is 307 passing
  runs in 139 builds.
- The completion gate requires a pass on HEAD ([completion_gate.py](../../../../plugins/harness-ops/scripts/completion_gate.py)),
  so any commit after a pass forces another run.
- A clean tree that already passed is a cache hit ([gate.py](../../../../devkit/gate.py) docstring: "re-gating an
  unchanged clean tree is free"). So the repeats cost turns, not gate time.

**F5. The hand-back limits conflict with each other.**
- HARNESS.md says 25 lines; researcher.md and tester.md say 20; research-auditor.md says 30; reviewer.md and
  builder.md say 25.
- Researcher hand-backs exceed 20 lines in **40 %** of runs (median 19, p90 28; median 3.3k chars). Reviewers exceed
  25 in 16 %, auditors exceed 30 in 12 %, builders exceed 25 in 2 %. VERIFIED ([surface_audit.py](scripts/surface_audit.py)).
- The harness skill's "Watch: hand-backs over 25 lines" repeats the HARNESS.md number. Anthropic names exactly this
  kind of clash as a cost: "Claude must think more carefully about these overlapping and conflicting messages before
  deciding what to do" SOURCED
  ([The new rules of context engineering for Claude 5 generation models](https://claude.dev/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models/),
  Shihipar 2026, claude.dev).

**F6. "Batch independent reads" duplicates the platform prompt and shows no effect.**
- Responses with two or more tool calls:

  | agent type | HARNESS.md loaded | not loaded |
  |---|---|---|
  | researcher | 13.9 % | 15.3 % |
  | builder | 12.6 % | 19.0 % |
  | auditor | 19.9 % | 20.3 % |

  VERIFIED (natural experiment, same caveats as F2).
- Chained single read-only calls (one read-only call in a response right after another such response) are 14-25 % of
  responses for every type.
- This researcher's own system prompt already carries the parallel-call instruction. BELIEVED: seen in this run's
  prompt; not stored in the transcripts. Anthropic: "put instructions on how to use tools in the tool descriptions
  rather than the system prompt" SOURCED
  ([The new rules of context engineering…](https://claude.dev/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models/)).

**F7. Hook-enforced rules repeated as prose still get broken. The hook does the work.** VERIFIED.
- Project-rule blocks in the window: `pkill -f` 21, `.env` 13, pip 6, `--no-verify` 7, push to main 4, edits to
  generated files 0.
- The pip, `.env`, generated-files and main rules sit in HARNESS.md, builder.md, the reviewer checklist and
  agent-harness CLAUDE.md, and each hook's message already states the fix
  ([guard_bash.py](../../../../plugins/harness-ops/scripts/guard_bash.py),
  [edit_guard.py](../../../../plugins/harness-ops/scripts/edit_guard.py)).

**F8. One prose rule did change behaviour: "never curl the literature APIs".** VERIFIED (small "after" sample).
- Before the rule reached researcher.md, 77 of 236 researcher runs (33 %) and 27 of 54 auditor runs called OpenAlex,
  S2, Crossref or Europe PMC directly with curl or WebFetch (365 calls).
- After it arrived: 1 of 23 researcher runs and 0 of 5 auditor runs ([surface_probes.py](scripts/surface_probes.py)).
- `lit status` before the first search: 168 of 176 researcher runs (95.5 %).
- Both rules are deterministic, so a hook or the CLI can hold them: the researcher prose shrinks, and about 250
  `lit status` turns go away.

**F9. Smaller items.** VERIFIED unless labelled.
- **Read before Edit.** "File has not been read yet" hit 5.8 % of builder edits (112) and 4.7 % of researcher edits
  (47). This follows from the Bash-first reads (F2). Each costs one extra call, about $0.02-0.06 at 100-300k context
  (BELIEVED, from r1's context sizes), so the window total is under $10.
- **Branch rename.** Builders ran `git branch -m` in 84 of 141 runs (60 %), as builder.md Orient 1 asks.
- **SessionStart orient.** It reaches only lead sessions: 0 of 654 subagent transcripts carry it. It is about 5.2k
  chars and includes board-sync error noise, yet HARNESS.md describes it to every agent.
- **Unknown agent types.** All 9 errors fell on 2026-10-11, during the move from project agents to the plugin. They
  have not recurred.
- **Polling.** sleep or until loops: researcher 462, builder 215, lead 170 (r1 §4 item 7).

**F10. Outside evidence on the text itself.**
- Anthropic "removed over 80% of Claude Code's system prompt for models like Claude Opus 5 and Claude Fable 5 with no
  measurable loss". They advise spending CLAUDE.md tokens "on gotchas inside of the codebase" and avoiding "'the
  obvious' things Claude should know by looking at your file system" SOURCED
  ([The new rules of context engineering…](https://claude.dev/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models/)).
- "The highest-signal content in any skill is the Gotchas section" SOURCED
  ([Lessons from building Claude Code: How we use skills](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/),
  Shihipar 2026, claude.dev).
- Opus 5.5: "Put a short rule in your CLAUDE.md file about when to stop and ask, and when to keep going" SOURCED
  ([Getting the most out of Opus 5.5 in Claude and Claude Code](https://claude.dev/blog/getting-the-most-out-of-opus-5-5/),
  claude.dev). HARNESS.md has no such rule.
- Tool definitions sit at the head of the cached prefix, and changing them breaks the cache SOURCED
  ([Lessons from building Claude Code: Prompt caching is everything](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/),
  Shihipar 2026, claude.dev). So a `disallowedTools` change belongs in a release, not in a running session.
- **Instruction load.** The best frontier models reach only 68 % accuracy at 500 simultaneous instructions SOURCED
  ([How Many Instructions Can LLMs Follow at Once?](https://arxiv.org/abs/2507.11538), Jaroslawicz et al. 2025,
  arXiv). Context files "do not generally improve task success rates, while increasing inference cost by over 20%"
  SOURCED
  ([Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?](https://arxiv.org/abs/2602.11988),
  Gloaguen et al. 2026, arXiv). Neither tested Opus 5.5 (model-dated evidence).

## The table

The words column counts each item in v0.1.9. Tokens are about 1.3 per word for prose (ASSUMED). Schema tokens use
the 2.88 chars-per-token fit (VERIFIED).

| # | item (where) | words / tokens | evidence | action | expected effect | measure |
|---|---|---|---|---|---|---|
| T1 | Artifact tool schema (builder, tester, researcher inherit it) | ~12.9k tok per call | F1: 0, 0 and 4 uses; $74-116 in the window | add `Artifact` to `disallowedTools` in builder, tester, researcher | first call 28k to ~15k tok; about 3-5 % of window spend | first-call tokens per type (new agent-stats column); eval pass rate unchanged |
| T2 | Skill tool and skill listing (same agents) | ~5.0k tok per call | F1: 3 Skill calls in 410 runs | `disallowedTools: Skill` in builder, tester, researcher (the lead keeps it) | about 5k tok per call; $29-45 in the window | as T1; a builder that needs a skill would show as a failed eval scenario |
| T3 | Agent tool (builder, tester) | ~1.2k tok | F1: 0 uses | add `Agent` to `disallowedTools` | small; one less option | as T1 |
| T4 | HARNESS "Navigate by symbol … Do not read whole files. A hook answers …" and builder Orient 4 (same rule) | 25 + 22 w | F2: `dev code` 3-6 % with or without the text; dumps already rare (hook) | cut from HARNESS; in builder keep "match the surrounding code" and one tool pointer ("`dev code show <Symbol>` prints one definition") | −35 words; no behaviour change expected | `dev code` share and big_share in agent-stats stay flat |
| T5 | HARNESS "Batch independent reads … in one response" | 13 w | F6: no lift (12.6-19 %); the platform prompt says it | cut | −13 words | parallel-response share (new agent-stats column) does not fall |
| T6 | HARNESS uv-only; generated files and `.env`; main-branch line, plus the builder.md copies and reviewer Rules item 4 | 17 + 16 + 18 + 8 w | F7: hooks block with the fix in the message; prose did not prevent 6 pip and 13 `.env` attempts | cut the prose and keep the hooks; keep "Only `dev integrate` merges" | −50 to −60 always-loaded words | hook block counts per 100 runs do not rise |
| T7 | HARNESS "dev check once at the end" (and agent-harness CLAUDE.md, builder Finish 1) | 35 + 21 w | F4: median 4 checks per run; the gate design forces re-runs | rewrite as judgement: "`dev check` gates the committed HEAD; run it when you believe you are done, and again after any fix" | removes a rule that 98 % of runs break; no cost change (cached passes) | checks per builder run; gate failures at integrate do not rise |
| T8 | Hand-back limits: HARNESS 25, researcher and tester 20, auditor 30, harness skill "Watch: over 25" | 17 w (HARNESS) | F5: 40 % of researcher hand-backs over 20 | cut the HARNESS line; keep one number per agent body; point the harness-skill watch at "over the agent's own limit" | removes a conflict | over-limit share per type (new column) |
| T9 | builder Finish 2, PR command (`dev pr <n> --summary - <<'EOF'`) | 115 w | F3: plain heredoc 102 ok of 105; piped 105 of 106 refused; 5 refused `.scratch/summary.md` writes | rewrite as a gotcha: "Send the body as a heredoc with nothing after it: `\| tail` makes worktree isolation refuse the call". Tooling: `dev pr` prints only the PR URL and a one-line status, so nobody pipes it | most of the 112 refused `dev pr` calls gone | isolation refusals per builder run (new column); `dev pr` error count |
| T10 | builder Finish 2, PR body specification (what changed, evidence, verdicts, Reuse:, fit:) | ~90 w of the 115 | the completion gate already checks `Reuse:`; the other fields are free text the lead re-reads | move the format into tooling: `dev pr` prints the skeleton and validates a verdict per DONE WHEN item, the way the gate validates Reuse. Prose shrinks to one line | −70 words; structured hand-off per "Seeing like an agent" | missing-section rejections by `dev pr`; lead re-query rate |
| T11 | lead skill §4: cross-repo dispatch "prefix every command with cd <abs path> &&" | 45 w | F3: 146 cross-repo `cd` refusals from isolated builders | rewrite: another repository's issue goes to a lead session in that repository, or to a non-isolated spawn given the worktree's absolute path (as research synthesis already does) | most of the 146 refusals gone | isolation refusals with a `cd` into another repository |
| T12 | builder Orient 1: claim and "Rename your worktree branch (`git branch -m`)" | 37 w | F9: rename done in 60 % of runs; the lead claims first (164 lead claims against 4 by builders) | tooling: `dev board claim --branch` renames the current worktree branch when it is auto-named. Prose: "`dev board brief <n>`" only | −25 words; branch names right in every run | runs whose PR branch is `agent-*` |
| T13 | researcher "Run `dev lit status` first; if DEGRADED stop" and the research skill §1.0 (same rule) | ~30 w | F8: 95.5 % compliance; about 250 extra turns | tooling: `dev lit search` prints a one-line coverage header and exits non-zero on DEGRADED; the prose then only says to copy the header into `Search coverage:` | −25 words; −250 turns per window | `lit status` calls; Search-coverage lines present (cite-check could check it) |
| T14 | researcher and auditor "never curl or WebFetch those APIs" | 12 + 20 w | F8: the prose worked (33 % to 4 %, small n) | move to guard_bash (refuse curl, wget or WebFetch to those hosts, pointing at `dev lit get`), then shorten the prose to the pointer | holds at 0 % with no prose dependence | guard blocks for this rule; direct-API calls in agent-stats |
| T15 | HARNESS "A SessionStart hook injects `dev orient` … Run it again after long breaks" | 20 w | F9: only lead sessions get it | move to the lead skill §1 | −20 words for every subagent | words (ratchet) |
| T16 | HARNESS "The main session is the lead … never builds while workers run …" | 36 w | duplicates lead skill Rules line 1 | keep one line ("The main session is the lead: `harness-ops:lead` skill") | −25 words | ratchet |
| T17 | HARNESS "Reuse before build …" | 43 w | duplicates builder Orient 3 and researcher Software; the completion gate enforces `Reuse:` | cut from HARNESS (the lead does not build) | −43 words | completion-gate Reuse refusals do not rise |
| T18 | HARNESS claim labels (VERIFIED, SOURCED …) and cite-check | 33 w | research-only; duplicates researcher.md; cite-check is gated | move to a `paths: docs/research/**` rule (or the researcher body only) | −33 always-loaded words | cite-check failures per research PR |
| T19 | HARNESS "Facts live in one place …" | 31 w | duplicates harness skill Rule 3; the gate enforces dangling paths and commands | cut from HARNESS; the harness skill keeps it | −31 words | gate failures for dangling paths and unknown commands |
| T20 | HARNESS "Changing the agent surface" section | 63 w | the harness skill covers it; `--harness-ok` is enforced at integrate | one line: "Before editing CLAUDE.md, `.claude/`, `.devkit/` or CI, load `harness-ops:harness`" | −50 words | skill invocations on harness PRs (Skill-call count per harness-touching session) |
| T21 | builder "Never edit generated files. Never read `.env`." | 8 w | T6 | cut | −8 words | as T6 |
| T22 | builder Orient 3 and Finish 2 both define Reuse (two places in one file) | 56 + ~45 w | duplicate within one file | merge into one place (Finish, next to `Reuse:`) | −35 words | as T17 |
| T23 | Missing: an Opus 5.5 stop rule ("keep going; stop only when blocked or before something destructive") | +30 w | F10 (Anthropic) | add once to HARNESS, with this repository's destructive list | fewer premature hand-backs (BELIEVED) | builder `--partial` PRs and resumes per issue |
| T24 | Missing gotcha: "Edit and Write need a prior Read of that file; sed and cat do not count" | +16 w | F9: 159 errors in builders and researchers | add to builder (BELIEVED to halve these; cheap either way) | −50 % of read-before-edit errors | errors per 100 edits |
| T25 | SessionStart orient output (lead) | ~1.8k tok | F9: sync-error noise lines | tooling: `dev orient` folds board-sync and lint errors into one count line | −0.3-0.5k tok per lead call | orient chars |
| T26 | Keep as is: Opus-specific method lines (test-first, Hypothesis, no hidden state, effect sizes, never invent, check the math, OPEN_QUESTIONS default, edits outside Owns, `git pull --ff-only` after resume) | — | opinionated, non-obvious, not enforceable by a hook; matches "gotchas over the obvious" | keep | — | — |
| T27 | harness skill opening evidence paragraph (SkillsBench, IFScale numbers) | ~60 w | the skills doc says "State what to do rather than narrating how or why" (r1) | cut to a one-line link to agent-harness.md | −50 words when loaded | — |

**Net effect if T4-T8 and T15-T20 land.** HARNESS.md drops from 541 to about 250-300 words. That is roughly −300
words, about 400 tokens, in every agent: under $5 per window. The value is fewer conflicts, not money (BELIEVED).
**T1-T3 are the money:** about 18-19k tokens per call off builders, testers and researchers, $105-165 per window
(VERIFIED arithmetic in [component_cost.py](scripts/component_cost.py)). T9, T11 and T13 cut retries and turns.

## What agent-stats needs to measure this

`dev agent-stats` reports runs, median calls, fresh, cached and output tokens, big_share and top tools
([stats.py](../../../../devkit/stats.py)). To judge the table it also needs these columns per agent type. Each is
already computed by a script in this folder.
- first-call input tokens, and its parts: system, tool schemas by tool, skill listing, instruction files;
- parallel-response share and chained single read-only share;
- isolation refusals, read-before-edit errors and project-rule blocks, each per 100 runs;
- hand-back length against the agent's own limit;
- `dev check` runs per builder run;
- direct literature-API calls.

For the text cuts (T4-T8, T15-T21), an A/B over 30 or more runs per arm on `uv run dev eval` scenarios is the bar the
harness skill sets. Pre-register the scenario list before the change. The tool removals (T1-T3) need only a
before/after on first-call tokens plus an unchanged eval pass rate.

## Gaps seen but not closed
- **Unverified effects.** Whether `disallowedTools` silently ignores an unknown tool name in an installation without
  Artifact (BELIEVED harmless). Whether removing Skill hurts a builder working on harness files: there was 1 such
  call in the window.
- **Missing prompt parts.** The Claude Code base system prompt for subagents is not in the transcripts, only its
  first and agent-specific blocks. So the "platform already says batch" claim (F6) rests on this run's own prompt.
- **No outcome link.** Nothing here ties a text item to outcome quality (rework, review rounds). These are compliance
  and cost measures only.
- **Token ratio for prose.** It is ASSUMED: the 2.88 chars-per-token fit is on JSON-escaped schema and message
  characters.

## Sources fetched
- [The new rules of context engineering for Claude 5 generation models](https://claude.dev/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models/), Shihipar 2026, claude.dev (markdown copy, 2026-10-19).
- [Lessons from building Claude Code: How we use skills](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/), Shihipar 2026, claude.dev.
- [Seeing like an agent: how we design tools in Claude Code](https://claude.dev/blog/seeing-like-an-agent/), Shihipar 2026, claude.dev.
- [Lessons from building Claude Code: Prompt caching is everything](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/), Shihipar 2026, claude.dev.
- [Getting the most out of Opus 5.5 in Claude and Claude Code](https://claude.dev/blog/getting-the-most-out-of-opus-5-5/), claude.dev.
- [Create custom subagents](https://code.claude.com/docs/en/sub-agents), Claude Code docs.
- [Run parallel sessions with worktrees](https://code.claude.com/docs/en/worktrees), Claude Code docs.
- [How Many Instructions Can LLMs Follow at Once?](https://arxiv.org/abs/2507.11538), Jaroslawicz et al. 2025, arXiv (abstract via `dev lit get`).
- [Evaluating AGENTS.md: Are Repository-Level Context Files Helpful for Coding Agents?](https://arxiv.org/abs/2602.11988), Gloaguen et al. 2026, arXiv (abstract via `dev lit get`).
- Local: the repo-E transcripts; [r1-usage-logs.md](r1-usage-logs.md);
  [HARNESS.md template](../../../../template/.devkit/HARNESS.md.jinja);
  [builder.md](../../../../plugins/harness-ops/agents/builder.md) and the other agent and skill files under
  [plugins/harness-ops](../../../../plugins/harness-ops/).
