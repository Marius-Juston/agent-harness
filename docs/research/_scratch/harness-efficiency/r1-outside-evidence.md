# F. Agentic-coding workflow practice outside Anthropic (2025-2026): evidence and what it means for agent-harness

Date: 2026-10-19. Scope: practice and measurements from people other than Anthropic's own blogs, read against
agent-harness v0.1.9 (`~/GitHub/agent-harness`: README, CLAUDE.md, `template/`, `plugins/harness-ops/`,
`docs/research/agent-harness.md`, `devkit/stats.py`).

Evidence labels:
- **SOURCED-A**: I fetched the primary page and read the abstract or summary, but not the full tables.
- **SOURCED-T**: I fetched the primary page and the numbers come from its tables or body.
- **SECONDARY**: the figure comes from a blog or summary of the primary source, and I did not confirm it there.
- **BELIEVED**: my judgment.

None of these results was measured on this repository. Model-dated caveat (owner memory): results on GPT-4-era
models are not comparable to Opus/Sonnet 4.x and later. Each item names its model where the source does.

---

## 1. Context files (AGENTS.md / CLAUDE.md): measured effects

**1a. Gloaguen et al., "Evaluating AGENTS.md" (ETH/LogicStar, Feb 2026)**
https://arxiv.org/abs/2602.11988 (SOURCED-A)
- Context files "do not generally improve task success rates" and raise inference cost by more than 20 % on average.
- This holds across LLMs, across agents, and for both LLM-generated and developer-written files.
- Instructions in the files *are followed*. Repository overviews do not help. The files are useful for non-standard
  practices.
- Already in agent-harness: yes. It is cited in `docs/research/agent-harness.md` §1 and drives the word budgets
  and the rules-only CLAUDE.md.

**1b. Lulla et al., "On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents" (Jan/Mar 2026)**
https://arxiv.org/html/2601.20404v2 (SOURCED-T)
- Setup: Codex CLI with gpt-5.2-codex, 10 repositories, 124 small PRs (at most 100 lines and 5 files), each paired
  with and without the root AGENTS.md.
- Wall-clock time: median −28.6 % and mean −20.3 % (significant).
- Output tokens: median −16.6 % (significant).
- Input tokens: median **+3.4 %**. The difference in total tokens is not significant.
- Correctness was not measured, only a 50-task sanity check. Each condition ran once, with no repeats.
- Reading: a good file mostly saves *exploration turns and time*, not input tokens. This is compatible with 1a.

**1c. "Agent READMEs: An Empirical Study of Context Files for Agentic Coding" (Nov 2025)**
https://arxiv.org/abs/2511.12884 (SECONDARY via search snippet; abstract-level)
- 2,303 context files from 1,925 repositories.
- They evolve "like configuration code" through frequent small additions.
- Common content: build/run commands (62 %), implementation details (70 %), architecture (68 %).
- Security and performance are each specified in about 14.5 % of files.

**1d. Codex AGENTS.md convention**
https://learn.chatgpt.com/docs/agent-configuration/agents-md (SOURCED-T)
- Files are concatenated from the git root down to the current directory, one per directory. The nearest file
  comes last and wins.
- `AGENTS.override.md` takes precedence over `AGENTS.md`.
- There is a hard cap: `project_doc_max_bytes` defaults to 32 KiB, and anything past it is silently truncated.

**1e. SkillsBench and IFScale** are already cited in the harness note (§1): compact curated skills help;
self-generated skills hurt; adherence decays with the number of instructions.

**Implications for agent-harness**
- **[TOKENS] Already done, keep going.** These are rules-only CLAUDE.md/HARNESS.md, word budgets that only fall
  (`.devkit/ratchets.toml`), and "facts live in one place".
  - 1b suggests the payoff of a good file is fewer exploration turns.
  - Add a metric: **median tool calls before the first Edit** per agent type, compared before and after each harness
    change. The harness note already reports 42 calls before the first edit in repository A. Make it a column in
    `uv run dev agent-stats`.
- **[EFFECTIVENESS] Directory-scoped instructions.** The Codex nearest-file convention, and Claude Code's nested
  CLAUDE.md loaded on demand, let area-specific rules (for example `apps/<area>/`) load only when an agent works
  there. This keeps them out of every agent's always-loaded prefix.
  - Measure: always-loaded words per run (agent-stats) against the gate-failure rate in that area.
- **[EFFECTIVENESS] Ablate before adding.** 1a's conclusion matches the harness rule "delete guidance that changes
  nothing". The template already has `uv run dev eval` micro-scenarios (`template/evals/scenarios/`).
  - Make an eval with and without the change a required part of any harness-text PR that *adds* words. Today the
    harness skill asks for it but nothing enforces it (BELIEVED, from reading the skill list only).

## 2. Tool-result size, observation management and repo navigation

**2a. SWE-agent ACI ablations (NeurIPS 2024; GPT-4 Turbo, SWE-bench Lite)**
https://arxiv.org/html/2405.15793 (SOURCED-T, Table 3). The default scores 18.0 %.

| Component | Variant | Resolve rate |
|---|---|---|
| File viewer | 100-line window (default) | 18.0 % |
| File viewer | 30-line window | 14.3 % |
| File viewer | Full file | 12.7 % |
| Search | Summarized search (default) | 18.0 % |
| Search | Iterative search | 12.0 % |
| Search | No search tool | 15.7 % |
| Editor | Edit with linting (default) | 18.0 % |
| Editor | Edit without linting | 15.0 % |
| Editor | No edit | 10.3 % |
| Context | Collapsing old observations (default) | 18.0 % |
| Context | Full history | 15.0 % |

- An iterative search interface is worse than no search tool at all.
- Model-dated (GPT-4 Turbo). The direction has held in later work (2b, 2c).

**2b. Lindenbauer et al., "The Complexity Trap" (JetBrains, Aug 2025)**
https://arxiv.org/abs/2508.21433 (SOURCED-A)
- Simple observation masking (old tool outputs replaced by placeholders) roughly halves cost against the raw agent.
- It matches or slightly beats LLM summarization on SWE-bench Verified (SWE-agent scaffold, 5 model
  configurations).
- A hybrid cuts a further 7 % against masking and 11 % against summarization.

**2c. OpenHands context condenser**
https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents (SOURCED-T)
- Per-turn cost is up to 2× lower, and cost grows linearly instead of quadratically.
- Solve rate on a SWE-bench Verified subset: 54 % with the condenser against 53 % without.
- It condenses only at a size threshold, so the cached prefix survives between condensations.

**2d. AgentDiet, Xiao et al. (FSE 2026)**
https://arxiv.org/abs/2509.23586 (SOURCED-A)
- Useless, redundant and expired information is widespread in trajectories.
- Removing it cuts input tokens by 39.9-59.7 % and total cost by 21.1-35.9 %, with no performance drop.

**2e. CORVUS (Jul 2026)**
https://www.alphaxiv.org/abs/2607.22711 (SECONDARY, search snippet)
- Stale file-read snapshots get re-read and duplicated.
- Synchronizing them cuts average input tokens per task by 9-50 % at comparable pass rates.

**2f. Weinberger & Hozez, "Token Reduction Is Not Cost Reduction" (Jul-Aug 2026)**
https://arxiv.org/abs/2607.12161 (SOURCED-A). This is the most relevant result for agent-harness.
- About 2,900 paired, provider-billed **Claude Code** runs (the run count is from the search snippet).
- The most aggressive tool-output compression cut delivered tool-output tokens by **38.4 % but raised billed cost by
  6.8 %**.
- Token reduction and cost reduction correlate at only r = 0.15.
- Cache creation and cache reads dominate input-side cost.
- Compression changed trajectories (more retrieval, testing and turns). On a SWE-bench Go subset, aggressive
  compression lowered patch application.
- Recommendation: judge by **cost per successful task**.

**2g. ClayBuddy (Jun 2026)**
https://arxiv.org/pdf/2606.19380 (SECONDARY, search snippet)
- One ~700k-token tool output cut accuracy on one problem by 70 points.
- Truncation was the only intervention that recovered any of it.

**2h. Minification, Hrubec & Cito (May 2026)**
https://arxiv.org/abs/2606.01326 (SOURCED-A)
- Minifying source shown to the agent cut input tokens by 42 % but cost **12 points** of resolve rate (GPT-5-mini).
- Lossy compression of *code* the model must edit is a bad trade.

**2i. Aider repo map**
https://aider.chat/2023/10/22/repomap.html (SOURCED-T)
- Tree-sitter definitions and references, ranked by graph importance and fitted into `--map-tokens` (default 1k).
- No published benchmark of the map itself.

**2j. LSP vs grep, Xu (Jun 2026, preliminary, single author)**
https://arxiv.org/abs/2608.13568 (SOURCED-A)
- Models: Claude Opus 4.8, Sonnet 4.6 and Haiku 4.5.
- Symbol-named localization: LSP costs +6 % to +118 % more tokens than grep. Models used semantic tools 0-6 % of
  the time.
- Reference completeness: LSP improves precision but saves tokens only for the weakest model.
- Multi-file renames: grep was perfect, while a location-only LSP failed about 3/4.
- Conclusion: route by task class, not "LSP always".

**2k. mini-swe-agent**
https://github.com/SWE-agent/mini-swe-agent (SOURCED-T, README)
- About 100 lines, bash only, linear history, and it claims more than 74 % on SWE-bench Verified (model not named
  on the page).
- With strong models, a large custom tool surface is not needed for headline performance.

**Implications for agent-harness**
- **Already done:**
  - `read_guard.py` rewrites whole-file reads of long files into outlines or windows. This matches 2a (full file
    12.7 % against a 100-line window 18.0 %).
  - `bashOutputMaxChars: 20000` and `uv run dev code outline|show|refs|find` with capped output.
  - Ruff at edit time (`lint_after_edit.py`), which matches 2a's edit-with-linting (+3 points).
- **[TOKENS, important] Change the success metric from tokens to cost per merged issue.** 2f shows token cuts can
  *raise* billed cost through cache churn and longer trajectories. `devkit/stats.py` reports medians of fresh tokens
  (which merge input and cache creation), cached tokens and output, per agent type. It has no cost and no success
  join. Add:
  - (i) cache-creation tokens as a separate column, priced separately (writes cost more than reads);
  - (ii) a USD-equivalent per run;
  - (iii) a join with the board: cost per issue that reached `integrate`, and the number of re-dispatches per issue.
  - Gate harness token changes on this metric, not on raw tokens.
- **[TOKENS] Do not lower `bashOutputMaxChars` further, or add generic compression, without an A/B on cost per
  success.** 2f and 2h show blunt compression backfiring. Targeted measures are better: the read rewrite, `code show`
  of one symbol, and pytest `-q`/`--tb=short` defaults inside `dev test`.
- **[TOKENS] Make `uv run dev test`/`check` print a capped summary** (counts plus the first N failures) and write the
  full log to a file path.
  - Rationale: a gate failure is the one tool result that is both large and needed (2g); a file can be read by window.
  - Check whether this is already done: I did not read `devkit/gate.py`.
  - Measure: the `big%` column in agent-stats for builder runs.
- **[TOKENS] Keep `dev code` as a lexical/AST tool; do not add an LSP MCP by default.** 2j finds LSP costs tokens
  for localization and models ignore it anyway. `refs` is grep-based already, which suits renames. Add an LSP only
  for reference completeness on a large typed codebase, behind a measured eval.
- **[TOKENS, low effort] An Aider-style ranked repo map for `orient` and `board brief`.** A 1k-token PageRank map of
  the files named in the issue's Owns may replace the 42 exploration calls before the first edit.
  - Measure: calls before the first edit and cost per merged issue, as an A/B over about 20 issues.
  - Evidence is weak (no benchmark). Treat it as an experiment.
- **[TOKENS] Observation masking is not in the harness's control** (Claude Code owns history and compaction). The
  levers that are: short worker lives (one issue each, already done), a small `maxTurns` for read-only agents, and
  re-spawning a fresh worker instead of continuing a long one after a failed review round (see §4, superpowers).

## 3. Caching and prefix stability

**3a. Lumer et al., "Don't Break the Cache" (PwC, Jan 2026)**
https://arxiv.org/abs/2601.06007 (SECONDARY via search summary; arXiv page in results)
- Prompt caching cuts API cost by 41-80 % and TTFT by 13-31 % across OpenAI, Anthropic and Google.
- Benefits are more consistent when dynamic content (timestamps, session IDs) goes at the *end* and tool sets are
  stable. Naive full-context caching can raise latency.

**3b. Manus, "Context Engineering for AI Agents"**
https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus (SECONDARY via search summary)
- The KV-cache hit rate is called "the single most important metric". For Sonnet, cached input costs $0.30/MTok
  against $3/MTok uncached.
- Practices: append-only context, deterministic serialization, mask tools rather than add or remove them mid-run.

**Implications for agent-harness**
- **Already done:** `subagentPromptCacheTtl: "1h"`. Repository A's numbers in the harness note §6 (43 M cached
  against 1.13 M fresh per builder) show cache reads dominate.
- **[TOKENS] Audit the SessionStart hook for cache-busting content.** `session_brief.sh` injects `dev orient`
  (quota, memory, worktrees, time-varying values) at session start. Put stable text first and volatile lines last,
  keep it short, and avoid timestamps near the top.
  - Measure: cache-creation tokens on turn 1 to 3 per run (needs the agent-stats split above).
- **[TOKENS] Keep the tool and MCP surface stable within a run**, and keep MCP servers few. The template ships
  `.mcp.json`. Reported per-server schema overhead has a median of about 2k tokens, and GitHub's MCP about 42-55k
  (SECONDARY: https://getunblocked.com/blog/mcp-token-budget-autopsy/).
  - Prefer the `gh` CLI, as the harness already does, and deferred tool loading.
  - Measure: system-prompt token count per agent type.

## 4. Popular Claude Code workflow repositories (star counts are VERIFIED via `gh repo view`, 2026-10-19)

| Repository | Stars | What it is |
|---|---|---|
| obra/superpowers | 302k | Skills methodology: brainstorm → worktree → plan → subagent-per-task → two-stage review → verification |
| github/spec-kit | 142k | Spec-driven development: constitution, spec, plan, tasks |
| OpenHands/OpenHands | 91k | Open agent platform, condenser (2c) |
| hesreallyhim/awesome-claude-code | 56k | Curated list |
| bmad-code-org/BMAD-METHOD | 54k | Persona-agent agile method (PM, architect, dev, QA) |
| Aider-AI/aider | 50k | Repo map (2i) |
| wshobson/agents | 41k | Multi-harness plugin marketplace of many specialist agents |
| musistudio/claude-code-router | 38k | Routes Claude Code requests to other models and providers |
| ccusage/ccusage | 19k | Token and cost reports from local Claude Code JSONL logs |
| SWE-agent/mini-swe-agent | 9k | 100-line bash-only agent (2k) |

**obra/superpowers.** I read `skills/subagent-driven-development` and `skills/verification-before-completion` with
`gh api` (SOURCED-T for content; it has no measured outcomes).

Subagent-driven development:
- A fresh implementer subagent per task, built from a crafted prompt, "never inherit[ing] your session's context".
- A task review after each task with two parts: spec compliance, then code quality. A broad whole-branch review comes
  at the end.
- Up to 5 fix rounds: **rounds 1-3 resume the same implementer; round 4 and later use a fresh implementer on a more
  capable model.**
- Decisions are recorded in a ledger file as `Ruling: what — why — cost if wrong` instead of stalling on questions.
- Only four things stop the run: an irreversible operation, a security-sensitive action, an external side effect,
  and a plan that is broken.
- "Narrate at most one short line" between tool calls.

`writing-skills`:
- Skills are written TDD-style: a pressure scenario run *without* the skill first (RED), then the skill (GREEN).
- Tested finding: a skill description that summarises the workflow makes agents follow the description and skip the
  body.

`verification-before-completion`:
- "No completion claims without fresh verification evidence."
- An agent's "success" report must be checked against the VCS diff.

Evidence status: practitioner, no published A/B (BELIEVED to be valuable because of adoption, not measured).

**spec-kit and BMAD.** I found no controlled study of agent outcomes.
- arXiv 2605.01160 is a 4-month pilot with 14 engineers, explicitly "not a controlled experiment" (SECONDARY).
- A practitioner review (Scott Logic, via codemyspec.com, SECONDARY) reports "a sea of markdown documents, long agent
  run-times".
- Treat heavy spec pipelines as unproven and costly. That matches SkillsBench's finding that heavy pipelines with no
  lightweight fallback hurt.

**ccusage** reads the same JSONL logs as `dev agent-stats` and prices them per model.

**Implications for agent-harness**
- **Already done:**
  - Fresh subagent per unit of work in its own worktree (one issue per worker).
  - A gated PR, with re-gating at `integrate`.
  - The `completion_gate.py` SubagentStop hook on builders, which is the hook form of verification-before-completion.
  - Skill descriptions as triggers.
  - `dev eval` scenarios, which are a form of RED/GREEN for harness text.
- **[EFFECTIVENESS] Escalation on repeated failure.** When a worker's PR fails review or the gate twice, re-dispatch
  a *fresh* worker with the review findings in the issue, instead of continuing the long one. That gives a clean
  context, and context rot is documented by Chroma (https://research.trychroma.com/context-rot, SECONDARY summary:
  performance grows less reliable as input grows, across 18 models).
  - Encode it in the lead skill as a counted rule (round ≥ 2 → fresh worker).
  - Measure: integrate rate and cost per merged issue for re-dispatched against continued workers.
- **[EFFECTIVENESS] Two-stage review: spec compliance against DONE WHEN, separate from code quality.** The harness
  reviewer reviews "against its issue's DONE WHEN, the research it cites and the project rules" in one pass.
  - Splitting it, or ordering the DONE WHEN check first with an early stop, matches MAST's largest verification
  failures (§5).
  - Cheap variant: one reviewer, two sections, verdict on spec first.
- **[TOKENS] A rulings ledger instead of questions.** The harness already has "Unclear requirement: add to
  OPEN_QUESTIONS.md with your default, then proceed". Superpowers adds a *cost-if-wrong* field. That lets the lead
  triage rulings by risk instead of reading all of them. Small text change, measurable by lead review time (BELIEVED).
- **[TOKENS] Narration cap.** "At most one short line between tool calls" targets output tokens, the most expensive
  per token. Measure: output tokens per run in agent-stats before and after.
- **[TOKENS] Add `ccusage`-style pricing to `agent-stats`** rather than adopting ccusage (Node). Reuse its per-model
  price table approach.

## 5. Multi-agent coordination failures

**5a. MAST, Cemri et al. (Berkeley, 2025)**
https://arxiv.org/abs/2503.13657 and https://arxiv.org/html/2503.13657v3 (SOURCED-T)
- 1,642 traces from 7 multi-agent frameworks, 14 failure modes, κ = 0.88.
- Category shares:
  - System design: about 44 %. Leading modes: step repetition 15.7 %, unaware of termination 12.4 %, disobey task
    spec 11.8 %.
  - Inter-agent misalignment: about 32 %. Leading modes: reasoning-action mismatch 13.2 %, task derailment 7.4 %,
    failure to ask for clarification 6.8 %.
  - Verification: about 24 %. Modes: incorrect verification 9.1 %, no or incomplete verification 8.2 %, premature
    termination 6.2 %.
- Interventions on ChatDev (GPT-4o): better role specification gave +9.4 %. **A high-level objective verification
  step gave +15.6 %.**
- Model-dated (GPT-4/Claude 3 era).

**5b. Merge conflicts among agent PRs, Xu et al. (2026)**
https://arxiv.org/abs/2607.04697 (SOURCED-A)
- Replaying 747 co-active PR pairs: textual conflicts in 19.8 % of same-agent pairs and 41.7 % of cross-agent pairs.
- 84 % of conflicted files were source code. About 42 % of conflicts were structural (modify/delete, add/add).

**5c. CAID, Geng & Neubig (CMU, 2026)**
https://arxiv.org/abs/2603.21489 (SOURCED-A)
- A central manager, a dependency graph, an isolated git worktree per agent, and test-based verification before
  merge.
- Against single-agent baselines: +25.6 points on PaperBench and +14.7 % on Commit0.
- Names the failure "each agent's output looks fine in isolation, but integration fails because agents hold
  inconsistent views of shared state".

**5d. Single-agent vs multi-agent cost**
https://arxiv.org/pdf/2511.00872 and https://arxiv.org/pdf/2505.18286 (SECONDARY)
- Multi-agent usually spends more tokens but can be cheaper per *successful* outcome.
- Comparisons are often not compute-matched.

**Implications for agent-harness**
- **Already done:**
  - Worktree isolation, a central lead, test-gated merges (CAID's recipe).
  - `Owns` sections, with "parallel builders only on issues whose Owns do not overlap".
  - Race-safe claims, STALE detection, `maxTurns` caps, and the completion gate.
  - Re-gating on the merged tree, which is "incorrect verification" defended by re-derivation.
- **[EFFECTIVENESS] Measure conflict rate.** 5b's 20 % same-agent conflict rate is a base rate to beat. Log, in
  `dev integrate`, whether the PR needed a rebase or merge resolution and which files conflicted.
  - If conflicts concentrate on shared files (CHANGELOG, generated docs, registries), make them fragment files or
    generated (the harness already generates INDEX).
  - Measure: conflicts per integrate.
- **[EFFECTIVENESS] Termination and step-repetition (MAST's top modes) as hooks, not prose.** Examples:
  - A PostToolUse counter that warns when the same Bash command (normalized) runs 3 or more times with the same
    exit code. This is cheap and catches loops; repository A's 52/87 wait loops is the precedent in the harness note.
  - Measure: the repeated-call share in agent-stats.
- **[EFFECTIVENESS] Objective verification against the issue, not just the gate (+15.6 % in 5a).**
  - The gate proves "tests pass". It does not prove DONE WHEN is met.
  - Have `completion_gate.py` require the hand-back to quote each DONE WHEN clause with the command and output that
    shows it, so the lead or reviewer checks evidence rather than claims.
  - Measure: the share of integrated PRs later reopened or followed by a fix issue.

## 6. Verification, self-review and review bots

**6a. SWE-Review (Jul 2026)**
https://arxiv.org/abs/2607.06065 (SOURCED-A; numbers SECONDARY)
- An agentic reviewer explores the repository, approves or requests changes, and the generator revises once.
- It beats single-turn, fixed-context review on decision accuracy and on resolve rate after revision.
- Secondary sources report Qwen3-30B-A3B going from 27.5 % to 56.9 %, and up to +29.4 points on SWE-bench Verified.
- Open models only. No evidence yet for frontier models reviewing themselves.

**6b. R2E-Gym hybrid verifiers (2025)**
https://arxiv.org/abs/2504.07164 (SOURCED-A)
- Execution-based verifiers alone, and execution-free (LLM judge) verifiers alone, each saturate around 42-43 % on
  SWE-bench Verified with best-of-n.
- A hybrid reaches 51 %.
- Tests have low distinguishability; judges are biased toward style. Combining the two beats either.

**6c. Self-preference, "Do LLM Evaluators Prefer Themselves" (v3, Dec 2025)**
https://arxiv.org/html/2504.03846v3 (SOURCED-T)
- Most self-preference of strong models is legitimate.
- *Harmful* self-preference (choosing its own wrong answer over a correct alternative) is 50-75 % for large models on
  MBPP+ code, and 86 % for Qwen2.5-72B on MATH500.
- Reasoning before the verdict (CoT, long CoT) lowers it the most.
- Foundation: Panickssery et al. https://arxiv.org/abs/2404.13076 (SOURCED-A via search).

**6d. Review bots in the field**
- **Sun et al.**, https://arxiv.org/abs/2508.18771 (SOURCED-A): 16 AI review GitHub Actions, more than 22k comments,
  178 repositories. Effectiveness "varies widely". Comments that are **concise, contain code snippets, are manually
  triggered, and come from hunk-level tools** more often lead to code changes.
- **Chowdhury et al. (MSR 2026)**, https://arxiv.org/abs/2604.03196 (SOURCED-A):
  - PRs reviewed only by a code-review agent merged at 45.2 %, against 68.4 % for PRs reviewed only by humans, with
    higher abandonment.
  - 60 % of closed CRA-only PRs were in the 0-30 % signal band, and 12 of 13 CRAs averaged below 60 % signal.
  - Their advice: "augment rather than replace" human reviewers.
- **Beko / Qodo industry case**, https://arxiv.org/abs/2412.18531 (SECONDARY): 73.8 % of automated comments were
  resolved, but PR closure time rose.
- **Nguyen et al., "Benchmarking Agentic Review Systems"** (Jun 2026), https://arxiv.org/abs/2606.19749 (SOURCED-A).
  This is about paper review, not code, but the mechanism is the same:
  - The best single configuration caught 71.6 % of injected errors.
  - **The union over 6 models caught 83.3 %**: different models catch different errors.
  - The main user complaints were false positives and nitpicks.

**6e. Agent PR acceptance**
AIDev dataset (https://arxiv.org/abs/2507.15003 and the follow-ups 2602.04226, 2601.18749; SECONDARY via search)
- Agentic PRs are accepted significantly less than human PRs.
- 67.9 % of rejected agentic PRs had no explicit reviewer feedback.

**Implications for agent-harness**
- **Already done:**
  - `harness-ops:reviewer`, read-only, for PRs touching the harness, several Owns, or anything a user sees.
  - `research-auditor` for research PRs.
  - repo-E's `security-reviewer`.
  - The lead reads harness diffs.
  - The deterministic gate is the execution-based verifier; the reviewer is the execution-free one. This is the
    hybrid of 6b.
- **[EFFECTIVENESS] Make the reviewer a different model, or at least reason before the verdict.**
  - All 7 harness agents are `model: opus`, so builder and reviewer share a model.
  - 6c shows harmful self-preference is concentrated exactly where the generator was wrong. 6d shows models catch
    different errors.
  - Options: (a) give the reviewer a different model tier or family; (b) keep Opus but give it `effort: high` and
    require the verdict *after* listing evidence.
  - Measure: defects found later in reviewed PRs (fix issues within 14 days), per reviewer model.
- **[EFFECTIVENESS + TOKENS] Review output format from 6d.** Findings should be few, hunk-anchored (file:line), each
  with a code snippet or a failing command, and ranked as blocking or not. No nitpicks: lint owns style.
  - Measure: the share of reviewer findings that lead to a commit before integrate. This is Sun et al.'s metric, and
    it can be computed from the PR timeline.
- **[EFFECTIVENESS] A reviewer as the *only* check is weak (45 % against 68 % merge, low signal).** Keep the owner's
  or lead's diff read for user-facing and security PRs, which repo-E already does. Do not extend auto-review to
  replace it.
- **[TOKENS] Review only where it pays.** The lead skill already scopes reviewers to risky PRs. Keep that scoping
  rather than reviewing every PR. 6a's gains are on weak open models; for Opus-class builders behind a strong gate the
  marginal value of review on small PRs is unmeasured (BELIEVED).
  - Measure: fix-issue rate for reviewed against unreviewed PRs of similar size.

## 7. Model routing (cheaper models for exploration and review)

**7a. RouteLLM (ICLR 2025)**, via https://getunblocked.com/blog/model-routing-coding-agents/ (SECONDARY)
- Kept about 95 % of GPT-4 quality with 14-26 % of calls to the strong model, a 75-85 % cost cut.
- Chat, not agentic.

**7b. SWE-Router (ICML 2026 workshop)**, https://arxiv.org/abs/2607.00053 (SOURCED-A)
- A cheap model runs a few exploratory turns, then a router reads the partial trajectory and escalates or not.
- Routing on the task description alone hits a ceiling, because similar issues hide a typo or a refactor.
- Reports "greatly improved cost efficiency" keeping "the majority" of the strong model's performance (no numbers in
  the abstract).

**7c. LiteLLM per-phase routing experiment** (SECONDARY)
- Cheap model for exploration, mid-tier for verification, top tier for implementation.
- Same 9/9 SWE-bench Verified tasks solved for $1.51 against $2.82 (−46 %). n = 9, anecdotal.

**7d. Bai et al., "How Do AI Agents Spend Your Money?" (Apr 2026)**, https://arxiv.org/abs/2604.22750 (SOURCED-A)
- 8 frontier models on SWE-bench Verified.
- Input tokens drive cost. Repeat runs of the same task vary up to 30× in tokens.
- Accuracy peaks at intermediate cost.
- Claude Sonnet 4.5 and Kimi-K2 used 1.5 M or more tokens more than GPT-5 on the same tasks.
- Models predict their own cost poorly (r ≤ 0.39).

**Implications for agent-harness**
- **Not done:** every plugin agent is pinned to `model: opus`.
- **[TOKENS] Route read-only, bounded agents to a cheaper tier first, with eval gating.** Candidates are the
  `suite-auditor`, and exploration-only subagents (Claude Code's built-in Explore already does this).
  - The researcher's search and fetch phase is a candidate; its synthesis is not.
  - Keep builder, reviewer, architect and research-auditor on the top tier until measured.
  - Make the model a `[tool.devkit]` key per agent type so projects can A/B it.
  - Measure: for 20 or more paired issues, cost per merged issue and post-merge fix rate. This follows the owner's
    pre-registered-eval rule: freeze the split first.
- **[TOKENS] Escalate, don't pre-route** (7b and superpowers' round-4 rule).
  - Option: start builders on the mid tier for issues labelled `size:s`, and escalate to Opus on the first gate or
    review failure.
  - 7d's 30× variance means you need many runs per arm to see a difference. Budget n ≥ 30 issues per arm (BELIEVED).
- **[TOKENS] Watch the caveat in 7d.** A cheaper model that takes more turns can cost more in total. Only cost per
  success settles it, which loops back to the agent-stats upgrade.

## 8. Self-improving context (ACE)

**8a. Agentic Context Engineering, Zhang et al. (ICLR 2026)**
https://arxiv.org/abs/2510.04618 (SOURCED-A)
- Contexts evolve as "playbooks" through incremental *delta* updates (generate, reflect, curate), not rewrites.
- Gains: +10.6 % on agent tasks (AppWorld) and +8.6 % on finance.
- Names two failure modes of LLM-rewritten context: **brevity bias** (dropping domain insights for concise summaries)
  and **context collapse** (iterative rewriting erodes detail).

**Implications for agent-harness**
- **[EFFECTIVENESS] Make HANDOFF.md and memory updates additive, as deltas.**
  - The lead rewrites `docs/HANDOFF.md` at session end. ACE's context collapse predicts that detail erodes over many
    rewrites.
  - Prefer appending dated entries, with periodic curation that only deletes entries marked resolved.
  - This tensions with word budgets. Resolve it by budgeting the always-loaded part and keeping history in a
    non-loaded file.
  - Measure: facts lost across handoffs (spot-check 10 handoffs for issues that were dropped and later re-discovered).
- Note that SkillsBench (already cited) found *self-generated* skills hurt. ACE's gains come from curated deltas
  backed by execution feedback, not from free-form self-writing. The harness rule that harness text changes need
  evidence stays right.

---

## What agent-harness already does (checked in the repository)

| Practice (external source) | agent-harness |
|---|---|
| Short, rules-only context file; overviews don't help (1a) | Rules-only CLAUDE.md + HARNESS.md, ratcheted word budgets |
| Windowed viewer beats full file (2a) | `read_guard.py` rewrites long reads to outline or window |
| Linting at edit time (2a) | `lint_after_edit.py` |
| Capped tool output (2g) | `bashOutputMaxChars: 20000`, capped `dev code` |
| Cache-friendly long-lived prefix (3a, 3b) | `subagentPromptCacheTtl: 1h` |
| Fresh agent per task, worktree isolation (superpowers, 5c) | One issue per worker, worktree hooks |
| Verification before completion (superpowers, MAST FC3) | `completion_gate.py`, `dev integrate` re-gates the merged tree |
| Hybrid verification (6b) | Gate (execution) + reviewer / research-auditor (judgment) |
| Measuring the surface from transcripts (ccusage) | `dev agent-stats` (tokens, calls, big-result share) |
| Eval before shipping guidance (1a, superpowers writing-skills) | `dev eval` micro-scenarios |
| Ownership to avoid conflicts (5b) | `Owns`, non-overlap rule for parallel builders |

Not done, or only partly:
- cost per *successful* task;
- separate cache-creation accounting;
- model routing;
- a reviewer on a different model;
- a fresh worker on escalation;
- logging the merge-conflict rate;
- repetition and loop detection as a hook;
- DONE-WHEN evidence in hand-backs (partial: the gate exists);
- additive handoff.

## Ranked top 15 (impact × confidence ÷ effort)

1. **[TOKENS] Cost per merged issue in `dev agent-stats`.**
   - Price input, cache-write, cache-read and output separately, and join runs to the issues that reached integrate.
   - Every other token change should be judged on this (2f: r = 0.15 between token and cost cuts; 7d: 30× variance).
2. **[EFFECTIVENESS] DONE-WHEN evidence in the hand-back, enforced by `completion_gate.py`.** Each clause gets the
   command and its output (MAST: +15.6 % from objective verification; 1a: instructions are followed).
3. **[EFFECTIVENESS] Reviewer on a different model or family, or with forced evidence before the verdict.**
   Harmful self-preference on code is 50-75 % (6c); a union of models catches 83 % against 72 % for one (6d).
4. **[TOKENS] Cheaper tier for bounded read-only agents** (suite-auditor; the researcher's search phase), as a
   `[tool.devkit]` model key per agent, A/B'd on item 1 (7a-7c).
5. **[EFFECTIVENESS] Fresh worker plus escalation after the second failed gate or review round**, instead of continuing
   a long context (superpowers round-4 rule, context rot, MAST step repetition).
6. **[TOKENS] Capped gate and test summaries, with full logs written to a file** (2g, SWE-agent's summarized search
   beats iterative search).
7. **[EFFECTIVENESS] Review format: few hunk-anchored findings with a snippet or command, blocking first, no style
   nits.** Track the share of findings that lead to a commit (6d).
8. **[TOKENS] SessionStart and orient prefix audit:** stable first, volatile last, and fewer lines (3a, 3b).
   Measure cache-creation on early turns.
9. **[EFFECTIVENESS] Loop and repetition detector hook** (same normalized command with the same result 3 or more
   times → warn). Step repetition is MAST's top mode at 15.7 %.
10. **[EFFECTIVENESS] Log merge conflicts at integrate** and turn hot shared files into fragments or generated files
    (5b base rate 19.8 %).
11. **[TOKENS] Calls-before-first-edit as a tracked metric**, and an A/B of a 1k-token ranked repo map in
    `board brief` (1b, 2i).
12. **[TOKENS] Keep the MCP and tool surface minimal and stable.** Prefer CLIs (`gh`, `dev`); defer tool loading
    (3b; schema overhead in §3).
13. **[EFFECTIVENESS] Additive, delta-style HANDOFF and memory** with curation by deletion only, to avoid context
    collapse (8a).
14. **[TOKENS] Narration cap and rulings ledger with a cost-if-wrong field** for workers (superpowers). Cheap output
    and lead-time savings; measure output tokens per run.
15. **[EFFECTIVENESS] Do not adopt heavy spec pipelines (spec-kit/BMAD) or an LSP-by-default MCP.** Neither has
    controlled evidence, and LSP measured worse on tokens for localization (2j). Keep the issue's DONE WHEN as the
    spec.

Read-depth caveat: several 2026 sources were read at abstract level only (labelled SOURCED-A), and figures
marked SECONDARY must be confirmed in the primary text before entering a research note or `cite-check`.
