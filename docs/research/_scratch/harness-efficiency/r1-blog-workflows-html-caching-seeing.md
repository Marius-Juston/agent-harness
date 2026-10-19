# D. Four claude.dev posts: dynamic workflows, HTML outputs, prompt caching, tool design

Reviewed 2026-10-19 for agent-harness (v0.1.9) and repo-E. The full text of all four posts was fetched: WebFetch only returned summaries, so the raw HTML was pulled with curl and the article text extracted (copies in a local raw copy (not committed)). Figures are images, so only their captions were readable. Linked resources fetched in full: the Claude Code workflows docs, the Claude Code prompt-caching docs, the API prompt-caching docs (first 100k of 160k characters), the tool-search-tool docs, the anthropics/html-effectiveness repo, the "harnessing Claude's intelligence" blog and the "multi-agent: when and how" blog.
**Not fetched**: X/Twitter threads (Jarred Sumner on the Bun rewrite, RLanceMartin, trq212 posts), the Opus 4.8 news page, the "AI code migration" blog, the support.claude.com Research and security-review articles, and agentskills.io.

Harness facts used below were checked on disk:
- `plugins/harness-ops/hooks/hooks.json`.
- The frontmatter of every agent: all are `model: opus`. builder, tester and researcher set `experimental.cacheTtl: 1h`. The reviewers' `tools:` lists are restricted.
- repo-E `.claude/settings.json` has `subagentPromptCacheTtl: "1h"` and `bashOutputMaxChars: 20000`.
- `devkit/stats.py` lumps `input_tokens + cache_creation_input_tokens` into "fresh".
- `docs/research/agent-harness.md` §6 gives the builder median: 138 tool calls, 1.13 M fresh and 43 M cached tokens.
- `uv run dev orient` is about 833 words and is injected through SessionStart `additionalContext`.

---

## 1. "A harness for every task: dynamic workflows in Claude Code" (Shihipar & Bidasaria, 2 Jun 2026)

### Core thesis
Claude (Opus 4.8+) can now write a task-specific multi-agent harness: a JavaScript "workflow" that the runtime executes. This replaces both the default single-context harness and hand-built static harnesses (Agent SDK / `claude -p` scripts) for long, highly parallel, structured or adversarial tasks. The post warns: "dynamic workflows often use more tokens and are best suited for complex, high value tasks."

### Why workflows (the three named failure modes of one long context)
- **Agentic laziness**: "declares the job done after partial progress, for example addressing 35 of the 50 items in a security review."
- **Self-preferential bias**: Claude prefers "its own results or findings, especially when asked to verify or judge them against a rubric."
- **Goal drift**: "gradual loss of fidelity to the original objective across many turns, especially after compaction. Each summarization step is lossy, and details like edge-case requirements or 'don't do X' constraints can get lost."
- The fix is separate subagents "with their own context windows and focused, isolated goals."

### Mechanics (post plus the workflows docs)
- **Primitives**:
  - `agent(prompt, opts)` spawns one subagent. Options include `schema` (JSON output, validated; 5 retries via `MAX_STRUCTURED_OUTPUT_RETRIES`), model choice, worktree isolation, agent type, `label`, and `stallMs`.
  - `parallel()` runs a set of tasks and waits for all of them. `pipeline(items, fn)` runs one agent per item.
  - `phase()` groups agents for display, `log()` prints a message, and `args` is a global input.
  - Plain JS is available (JSON, Math, Array). There is no `import()`, no filesystem or shell access, and `Date.now()`, `Math.random()` and `new Date()` throw, so that replay is deterministic.
- **Where results live**: "A workflow script holds the loop, the branching, and the intermediate results itself, so Claude's context holds only the final answer." The docs compare subagents, skills, agent teams and workflows. With workflows, *the script* decides what runs next, results live in script variables, and a run scales to "dozens to hundreds of agents."
- **Limits**:
  - 16 concurrent agents by default (`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS`, 1–256).
  - At most 4,096 items per `parallel` or `pipeline` call, and 1,000 agents per run.
  - No mid-run user input; for sign-off, run each stage as its own workflow.
- **Cost guards**:
  - A `Large workflow` warning appears above 25 agents or 1.5 M projected tokens.
  - `workflowSizeGuideline` sets the target size: small (<5 agents), medium (<10, the default), large (<50) or unrestricted.
  - Prompt budgets also work: "use 10k tokens" sets a cap.
- **Caching in fan-outs**:
  - Agents with the same model, effort, agent type, tools, output schema **and working directory** share a prefix.
  - The runtime holds all but the first agent until the first agent's response starts (`CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS`, default 5000), so the others read its cache.
  - Workflow agents are in the "everything else" TTL bucket (5 min by default; set `subagentPromptCacheTtl: 1h` to extend).
- **Resume**: replay is deterministic. Completed agents return their cached results, and the first changed or failed agent *plus every agent started after it* runs again. A mid-fan-out failure therefore reruns finished siblings.
- **Usage limits**: on a subscription, a run that hits the usage limit pauses and auto-continues after the reset (v2.1.271+, `autoContinueAtUsageLimit`). It waits at most twice and only for resets within 24 h.
- **Stalls**: an agent that stalls is restarted up to 5 times (`stallMs`, `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS`).
- **Triggering**:
  - The keyword `ultracode`, or simply asking for "a workflow".
  - `/effort ultracode` turns on orchestration for every substantive task in the session. It also lifts the concurrent-subagent limit and the large-run warning.
  - The keyword does **not** fire from `-p`, scheduled tasks, or relayed PR comments or webhooks. That makes it a useful prompt-injection boundary.
- **Permissions**:
  - In `-p` or the SDK, a workflow needs `Workflow` / `Workflow(<name>)` in allow rules, or a PreToolUse hook that returns allow (not in plan mode).
  - Script-computed `agent()` prompts are **not** treated as user intent by the auto-mode classifier.
- **Save and share**:
  - Press `s` in `/workflows`. Saves go to `.claude/workflows/` (project, shared) or `~/.claude/workflows/`, and the saved file becomes the `/<name>` command.
  - Plugins ship workflows from a `workflows/` directory, namespaced `/<plugin>:<name>`.
  - Skills can bundle `.js` files, "as a template instead of a script that needs to be run verbatim."
  - The `/workflow-authoring` bundled skill (v2.1.248+) carries the authoring reference.

### Six patterns (verbatim names)
1. Classify-and-act.
2. Fan-out-and-synthesize: "the synthesize step is a barrier".
3. Adversarial verification: one verifier per output against a rubric.
4. Generate-and-filter.
5. Tournament: N approaches, pairwise judge.
6. Loop until done: the stop condition is "no new findings, or no more errors", "instead of a fixed number of passes".

### Use cases with concrete recipes
- **Migrations**: "Spin off a subagent for every fix in a worktree… then have another agent adversarially review, and merge them. Consider telling the agent not to use resource intensive commands so that you can maximally parallelize." Bun was rewritten from Zig to Rust this way.
- **Deep research** (`/deep-research`): fan-out search, then fetch, then adversarial claim verification ("votes on each claim"), then a cited report. Unverifiable claims are listed as *unverified*, not *refuted*.
- **Deep verification**: one agent extracts the claims, one checker runs per claim, and an optional source auditor checks each checker.
- **Sorting**: for 1,000+ items, use pairwise tournaments, because "comparative judgment is more reliable than absolute scoring". Alternatively, bucket-rank in parallel and then merge.
- **Rule adherence**: "one verifier per rule" in a clean context, then a skeptic agent filters false positives. Reverse direction: "mine your recent sessions and code review comments for corrections you keep making, cluster them…, adversarially verify each candidate (would this rule have prevented a real mistake?), and then distill the survivors back into a CLAUDE.md."
- **Root cause**: hypotheses come from *disjoint evidence* (logs, files, data), then face a panel of verifiers and refuters. Example prompt: "don't stop until one theory survives the evidence."
- **Triage quarantine**: "reader agents touch untrusted content with no privileges; the actor agent only ever sees their summaries." Pair with `/loop`.
- **Evals**: run the arms in worktrees, then grade with comparison agents against a rubric (for example, to refine a skill).
- **Model routing**: a classifier agent researches the size of the task and then routes it to Sonnet or Opus.

### When not to use
"Most traditional coding tasks do not need a panel of 5 reviewers." "Parallelism and specialization have to earn their coordination cost."

### Linked resources (summaries)
- **Multi-agent: when and how** (claude.com blog):
  - Multi-agent setups cost **3–10× tokens**.
  - Isolate a subtask when it produces **>1,000 tokens** that are mostly irrelevant to the parent (example: a 2,000-token history reduced to a 50–100 token summary).
  - "The primary benefit of parallelization is thoroughness, not speed."
  - Decompose by **context boundary, not by role**. Planner, implementer, tester and reviewer splits play a "telephone game", and in one experiment the subagents "spent more tokens coordinating than working".
  - Verifier subagents work because verification needs little context. Their failure mode is the "early victory problem" (run a few tests, declare a pass). The mitigation is "You MUST run the complete test suite before marking as passed."
  - Selection degrades from 15–20+ tools; Tool Search cuts tokens by up to 85%.
- **Harnessing Claude's intelligence** (Lance Martin):
  1. Lean on tools Claude already knows: bash plus a text editor scored 49% on SWE-bench Verified.
  2. Strip the harness down. Letting Opus 4.6 filter its own tool output with code execution raised BrowseComp from 45.3% to 61.6%. Subagents added +2.8%. With compaction, Opus 4.6 reached 84% while Sonnet 4.5 stayed at 43%. A memory folder raised Sonnet 4.5 from 60.4% to 67.2%.
  3. Set boundaries:
     - Cache-friendly context.
     - Declarative tools for gating, rendering and audit.
     - Reversibility as the criterion for confirmation.
     - A staleness check on edit.
  - "Context anxiety" resets for Sonnet 4.5 became dead weight on Opus 4.5. Ask "what can I stop doing?"

### New vs common knowledge
- **New**: workflows as model-written, resumable, deterministic JS orchestration. Also new are the fan-out prefix stagger, the size guidelines and caps, ultracode, the quarantine pattern as a named primitive, and the skeptic layer on per-rule verification.
- **Already known** (and already in agent-harness): adversarial review, fan-out and synthesize, and research rounds until saturation.

### Implications for agent-harness / repo-E
1. **EFFECTIVENESS + TOKENS: port the `research` skill's round loop to a saved workflow template.** Ship it from the harness-ops plugin `workflows/` directory, or as a `.js` template inside the skill.
   - Today the lead spawns researchers with parallel Agent calls and reads the gaps files. A workflow keeps the per-researcher results out of the lead's context ("Claude's context holds only the final answer").
   - Same-prefix researchers (same type, tools and cwd) get the 5 s stagger for a shared cache.
   - The workflow makes "stop at saturation or 4 rounds" deterministic code rather than prose the lead can drift from (goal drift, laziness).
   - Measure: lead-context tokens per research issue, and the round count actually honoured, before and after over 5 issues. `agent-stats` already reports cached and fresh tokens per run.
2. **EFFECTIVENESS: a per-rule compliance workflow for the harness rules.**
   - One verifier per HARNESS.md rule over a PR diff (uv only, Owns respected, labels on claims, no generated-file edits, 25-line hand-back), followed by a skeptic filter.
   - This complements the single `reviewer`, which can suffer self-preferential bias or skim a long rule list.
   - Measure the recall of planted violations on 10 historical PRs against the current reviewer. Keep it only if recall rises without a false-positive rise (the post itself warns about false positives).
3. **EFFECTIVENESS: a "mine corrections into rules" workflow, run monthly with `/loop`.**
   - Input: owner corrections in session JSONL files and review comments. Cluster them, then for each candidate rule ask "would this rule have prevented a real mistake?"
   - This turns the harness skill's rule ("a finding class that recurs in ≥5% of reviews becomes a checklist line or lint") into an automated pipeline.
   - Tie it to the word-budget ratchet: a new rule must displace text.
4. **EFFECTIVENESS (user safety): quarantine for anything that ingests untrusted text.** The cases are scraped web content and issue or PR comments relayed to agents.
   - Readers run with no write or network tools and emit structured `schema` JSON. The actor sees only that JSON.
   - Also document that `ultracode` cannot be triggered by relayed PR comments or webhooks.
5. **TOKENS: set an explicit `workflowSizeGuideline` (small or medium) in harness settings.**
   - The lead can also tell agents not to run heavy commands (the post's tip). The harness already has a `spawn_guard` on `Agent|Workflow`, so add the guideline next to it.
   - Measure the agents per run in `/workflows` and the weekly quota burn.
6. **EFFECTIVENESS: pre-registered evals as a workflow** (fits the memory note "pre-registered evals"). Freeze the arms, run each in a worktree, and grade pairwise with a judge that never sees which arm is which. The tournament beats absolute scoring.
7. **Caution (TOKENS)**: workflow resume reruns everything after the first failed agent. For long builder fan-outs, the harness's own issue-per-worktree dispatch, which restarts per issue, is cheaper. Use workflows for read-only fan-outs (research, audit, verification), not to replace the lead/builder loop.

---

## 2. "Prompt caching is everything" (Shihipar, 30 Apr 2026)

### Core thesis
Long-running agents are only feasible because of prompt caching. Claude Code "build[s] our entire harness around prompt caching… we run alerts on our prompt cache hit rate and declare SEVs if they're too low."

### Rules (with quotes)
1. **The prefix is the cache.** "the API caches everything from the start of the request up to each cache_control breakpoint". The order is "static content first, dynamic content last":
   - Static system prompt and tools (cached globally).
   - CLAUDE.md (per project).
   - Session context (per session).
   - Messages.

   They broke this ordering by "putting an in-depth timestamp in the static system prompt, shuffling tool order definitions non-deterministically, and updating parameters of tools (e.g., what agents the Agent tool can call)."
2. **Updates go in messages.** Information such as the time or a changed file goes into a `<system-reminder>` "in the next user message or tool result", never into a system prompt edit.
3. **Don't switch models mid-session.** "if you're 100k tokens into a conversation with Opus… it would actually be more expensive to switch to Haiku than to have Opus answer." Switch through a subagent and a hand-off message instead; the Explore agents use Haiku this way.
4. **Never add or remove tools mid-session.**
   - Plan Mode keeps every tool. `EnterPlanMode`/`ExitPlanMode` are tools, and a system message carries the mode's rules. Because entering plan mode is itself a tool, the model can enter it on its own "without any cache break."
   - Deferred tools are "lightweight stubs (just the tool name, with defer_loading: true)… The full tool schemas are only loaded when the model selects them."
5. **Cache-safe compaction or fork.** A naive summarize call, with a different system prompt and no tools, "diverge[s] at the very first token" and bills the whole history uncached. Instead:
   - Reuse the exact system prompt, user and system context and tools.
   - Append the compaction prompt as a new user message.
   - Keep a "compaction buffer" of room in the window.

   Compaction is now available in the API.
6. **Monitor the hit rate like uptime.** "A few percentage points of cache miss rate can dramatically affect cost and latency."
7. **Side computations (compaction, summarization, skill execution) must share the parent's prefix.**

### Details from the linked docs (exhaustive on what breaks the cache)
**API invalidation hierarchy (tools → system → messages)**:

| What changes | Tools | System | Messages |
|---|---|---|---|
| Tool definitions (names, descriptions, params) | breaks | breaks | breaks |
| Web-search or citations toggle, `speed: fast` | kept | breaks | breaks |
| `tool_choice` or images | kept | kept | breaks |
| Thinking mode or budget | – | – | breaks |
| Effort | model-specific | model-specific | model-specific |

- Setting effort to the model's default is the same as omitting it.
- On current models, dropping a non-preserved thinking block invalidates from that block onward.
- Mid-conversation `role:"system"` messages add instructions *without* invalidating, on Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable/Mythos 5.x and Opus 4.8/5. They are not available on Sonnet 5.

**API pricing and limits**:
- A 5-minute write costs 1.25× base input; a 1-hour write costs 2×.
- A read costs 0.1× by default. On **Opus 5.5 and Sonnet 5.5 a read costs 0.05×** (Opus 5.5: $4/MTok input, $0.20 read, $8 per 1-hour write).
- The minimum cacheable prefix is 512 tokens on 5.x models.
- Up to 4 breakpoints. Each breakpoint looks back 20 blocks; consecutive tool_use or tool_result blocks count as one.
- Top-level automatic `cache_control` moves the breakpoint forward as the conversation grows.

**Claude Code: actions that invalidate the cache**:
- `/model` switch. This includes `opusplan`, where each plan-mode toggle is a model switch, and **a skill or command whose frontmatter names a different `model`** (that turn).
- Effort change. This keeps the cache on Opus/Sonnet/Haiku 5.5 and Fable 5.1 with an API key or subscription, and breaks it elsewhere.
- Turning on fast mode: the header is part of the cache key, so the cost is paid once per conversation.
- Connecting or removing an MCP server. This invalidates only when tools load upfront; with tool search (the default), the first request's tool list is frozen and late servers come in as deferred tools.
- Plugin MCP changes and a bare-tool deny rule. Both also invalidate only without tool search.
- `/compact` (invalidates the conversation layer by design), image eviction in batches, and upgrading Claude Code.

**Claude Code: actions that keep the cache**:
- Editing repo files: the session appends a system-reminder.
- **Editing CLAUDE.md mid-session**: "does not invalidate the cache, but the edit also doesn't apply" until `/clear`, `/compact` or a restart.
- Permission-mode changes and output-style changes.
- Skills and commands, which are injected as user messages.
- Plugin skills, agents, hooks and commands: "Claude Code never invalidates the cache for" them, since they are appended.
- `/recap`, `/rewind` (which truncates to an already-cached prefix; better than compacting a dead end), and spawning a subagent.

**Subagents and forks**:
- A subagent has its own system prompt and tools, so it does **not** read the parent's cache.
- A **fork** inherits the parent prefix and does read it.
- Resumed subagents reread their own earlier cache.

**TTL buckets**:
- Main conversation: 1 h on a subscription within plan usage, 5 min otherwise.
- Everything else (subagents, workflows, teammates, forks, compaction): 5 min.
- Overrides: `promptCacheTtl`, `subagentPromptCacheTtl` (`CLAUDE_CODE_PROMPT_CACHE_TTL`, `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`) and the subagent frontmatter `experimental.cacheTtl`.
- Precedence: `FORCE_PROMPT_CACHING_5M` > env var > setting > frontmatter > `ENABLE_PROMPT_CACHING_1H`.
- `idleCompaction` can compact shortly before a 1-hour cache expires.

**Cache scope**: "effectively scoped to one machine and directory". The system prompt embeds the auto-memory paths, and the first message announces the cwd, platform and OS. "Sequential sessions share the prefix only when the git status snapshot taken at startup matches."

**Measurement**:
- A statusline reading `current_usage.cache_creation_input_tokens` and `cache_read_input_tokens`.
- `/usage` → the `Prompt cache (main)` line (v2.1.251+), with **"likely cause: tool definitions changed"** (v2.1.260+).
- The `prompt_cache` statusline object, and OTel metrics.

### New vs common knowledge
- **Known**: prefix matching, static content first.
- **Less known and actionable**:
  - Tool *parameter* changes (such as the Agent tool's list of agents) break the cache.
  - A skill's `model:` frontmatter forces a full re-read.
  - CLAUDE.md edits are silently *not applied* mid-session.
  - Subagents get 5-minute TTLs even on a subscription.
  - Cache scope is per directory, so each worktree has its own prefix.
  - Workflow fan-outs stagger to share a prefix.
  - Effort changes are cache-safe on 5.5 models.
  - `/rewind` is cheaper than `/compact` for abandoning a path.
  - Reads cost 0.05× on Opus 5.5.

### Implications for agent-harness / repo-E
1. **TOKENS (largest lever): the cost is turns × context, so cut context growth and turn count, not static text.**
   - From the harness's own §6 numbers: 43 M cached ÷ 138 calls ≈ **310k cached tokens re-read per builder request**. That is a mean, so builders run deep into the 1M window. A 10k-token tool result added at turn 20 of 138 is re-read about 118 times, roughly 1.2 M cached tokens.
   - At the Opus 5.5 read price of 0.05×, 43 M cached costs about the same as 2.15 M fresh, so cached and fresh spend are comparable (VERIFIED arithmetic on the note's numbers; subscription quota weighting ASSUMED similar).
   - Highest return:
     - (a) Keep tool output small: `bashOutputMaxChars`, outline-on-read, and the open "grep output compaction" backlog item.
     - (b) Push exploratory reading into a subagent that returns a summary: the Claude Code Guide pattern from post 4, and the >1,000-token isolation threshold from the multi-agent blog.
     - (c) Batch calls. 13–18% of calls are chained reads that needed no earlier result (§6), and each one costs a full prefix re-read.
     - (d) Encourage `/compact` at task boundaries for long lead sessions, or `/rewind` when a path is abandoned.
   - Measure: `agent-stats` cached tokens per run and mean context at each request, over 10+ builder runs, before and after.
2. **TOKENS + observability: split "fresh" in `devkit/stats.py` into `uncached_input`, `cache_creation` and `cache_read`, and add a "cache break" detector.**
   - A break is any request after the first whose `cache_creation_input_tokens` is greater than, say, 20k, or than 50% of its prefix.
   - Report the hit rate (read ÷ (read + creation + input)) per agent type, and treat a drop as an incident, as the post does.
   - Today `fresh = input + creation`, which hides breaks. Measure the per-agent hit rate before and after any harness change; the expected baseline is >95%.
3. **TOKENS: audit for mid-session invalidators.**
   - (a) No harness skill sets `model:` in frontmatter (VERIFIED: none do). Keep it that way, and add a lint for skills and commands.
   - (b) Never recommend `opusplan` or a mid-task `/model` in the lead skill. Switch models only through a subagent.
   - (c) The lead should not change MCP servers or plugins or add deny rules mid-session (all safe only while tool search is active).
   - (d) The hooks are already cache-safe. SessionStart, PreToolUse and PostToolUse `additionalContext` all append to messages (VERIFIED in `session_brief.sh`, `read_guard.py`, `lint_after_edit.py`). Keep any dynamic data (quota, time, board state) out of CLAUDE.md and rules files.
4. **EFFECTIVENESS: CLAUDE.md and HARNESS.md edits don't reach a running lead until `/clear`, `/compact` or a restart.**
   - After integrating a PR that edits the harness, the lead should note in HANDOFF, or run `/compact` at a break, so that it works under the new rules.
   - Workers spawned afterwards do load the new files in their worktree, but only if the worktree is based on the new HEAD (`worktree.baseRef: head`).
5. **TOKENS: the directory-scoped cache means every worktree builder pays the first-turn prefix write itself.**
   - That is unavoidable, but it is a reason to keep the per-agent *system* text (agent .md and skill descriptions) stable across releases: `copier update` changes are full re-writes for every agent.
   - Bundle harness-text changes into releases rather than trickling them.
6. **TOKENS: TTL.** repo-E already sets `subagentPromptCacheTtl: "1h"` and the agents set `cacheTtl: 1h` (the setting wins over frontmatter).
   - The 1-hour TTL only pays when an agent idles more than 5 minutes (long `dev check`, `spawn_guard` waits, usage-limit pauses). Otherwise it charges 2× instead of 1.25× on writes.
   - Verify with the `ephemeral_1h_input_tokens` vs `ephemeral_5m_input_tokens` split in usage, plus the share of builder requests that follow a gap of more than 5 minutes. If below about 15%, try 5 min for reviewers and auditors (short runs).
7. **TOKENS: run read-only fan-outs from one cwd with identical agent types and tool lists.** Prefix sharing requires the same model, effort, agent type, tools, schema and cwd. Researchers (no isolation) qualify; worktree agents never share.

---

## 3. "Seeing like an agent: how we design tools in Claude Code" (Shihipar, 10 Apr 2026)

### Core thesis
Design tools around the model's actual abilities, which you learn by reading its outputs. Revisit them as models improve; keep the tool count low and add capability through **progressive disclosure** rather than new tools.

### Concrete lessons
- **Tool shape follows skill**: paper, then calculator, then computer, depending on what the user can operate. "You pay attention, read its outputs, experiment. You learn to see like an agent."
- **AskUserQuestion, three attempts**:
  1. A questions parameter on ExitPlanTool confused the model, which got "a plan and a set of questions about the plan".
  2. A parsed markdown format failed: Claude "would append extra sentences, drop options, or abandon the structure altogether".
  3. A dedicated, blocking tool with a modal UI worked: "Claude seemed to like calling this tool… even the best designed tool doesn't work if Claude doesn't understand how to call it."

  Lesson: **use a structured tool, not a free-text format, for anything a harness must parse.**
- **TodoWrite to Task**:
  - Reminders "every 5 turns" were once needed. Later models found them limiting: "Being sent reminders of the todo list made Claude think that it had to stick to the list instead of modifying it."
  - The fix was a Task tool with dependencies, shared across subagents, that the model can edit or delete.
  - "As model capabilities increase, the tools that your models once needed might now be constraining them." Also: "stick to a small set of models… with a fairly similar capabilities profile."
- **Search**: they went from RAG (pre-indexed vectors; "Claude was given this context instead of finding the context itself") to Grep, and then to Skills with progressive disclosure. "Over the course of a year, Claude went from not really being able to build its own context to being able to do nested search across several layers of files."
- **Tool budget**: "Claude Code currently has ~20 tools… The bar to add a new tool is high, because this gives the model one more option to think about."
- **Claude Code Guide subagent**: the system prompt would have caused "context rot"; a docs link made Claude "pull large chunks of documentation into context to find an answer the user could have gotten in one sentence". The fix was a subagent that searches the docs in its own context "and hands back only the answer." This adds to the action space without a new tool.

### Linked resources
- Tool-search docs:
  - Tool definitions for 5 servers take about 55k tokens; search cuts that by more than 85%, loading 3–5 tools.
  - Selection "degrades once you exceed 30–50 available tools".
  - Keep "your 3–5 most frequently used tools non-deferred".
  - Use namespaced names (`github_`, `slack_`).
  - Use it when there are ≥10 tools or >10k tokens of definitions.
  - Deferred tools stay out of the cached prefix.
- Agent SDK todo-tracking, skills and code execution: not re-summarized.

### New vs common knowledge
- The specific failure stories are new: a parameter on an existing tool, a parsed markdown format, and over-constraining reminders.
- Also new: the ~20-tool budget and the Guide-subagent pattern.
- Progressive disclosure itself is known.

### Implications for agent-harness / repo-E
1. **EFFECTIVENESS: retest old scaffolding against the current model (Opus 5.5).** Candidates:
   - The "batch independent calls" rule.
   - Read-rewriting hooks (`read_guard` outlines).
   - Repeated "ONE issue" framing.
   - The `completion_gate` for builders.
   - Fixed round caps in research.

   For each, run `uv run dev eval` with and without it (the harness skill already says to revert agent-file changes that change nothing over about 30 runs). Expect some guards to be dead weight, as the context-anxiety resets and TodoWrite reminders were.
2. **EFFECTIVENESS: anything the harness parses from an agent should be structured, not prose.** This covers hand-backs, review verdicts, auditor "accept / return", and gap lists. The post shows markdown formats fail; free-text hand-backs are the harness analogue.
   - Use workflow `schema` outputs (validated, 5 retries), or a tiny hand-back validator in the dev CLI (proposed; no such command exists) that validates fields such as PR, DONE WHEN met, tests, and open questions.
   - Measure the share of hand-backs the lead must re-query.
3. **TOKENS + EFFECTIVENESS: apply the Guide-subagent pattern to `uv run dev`.**
   - Agents currently run `uv run dev --help` and read docs (`multi-agent.md`, `agent-surface.md`) into their main context.
   - A cheap read-only "harness-guide" subagent (Haiku or Sonnet, Read and Grep only) answers "how do I X in this harness" in one paragraph. That keeps a 1–5k-token docs pull out of a context that is re-read about 100 times.
   - Measure the tokens that builders spend on harness docs (grep the transcripts for reads of `docs/specs`, `--help`).
4. **TOKENS: count the tools each agent sees.** repo-E has many MCP tools (arxiv, wikipedia, playwright, huggingface, context7), all deferred by tool search, so the cost is only their name stubs.
   - Agents without `tools:` restrictions (builder, tester, researcher) still see ~80 deferred names. Restrict `tools:` per agent where it is safe (builder needs no playwright or huggingface). This shrinks the stub list and the choice set.
   - Keep the restricted lists stable, since a change rewrites the prefix.
5. **EFFECTIVENESS: one model family.** All agents use `model: opus`; keep the supported set small, per the post. If Sonnet is trialled for reviewers, run it through a pre-registered eval, not by switching ad hoc.

---

## 4. "Using Claude Code: The unreasonable effectiveness of HTML" (Shihipar, 20 May 2026)

### Core thesis
For specs, plans, reviews and reports that humans must actually read, HTML beats Markdown on density, readability, shareability and two-way interaction. The real reason: "it helps me feel much more in the loop with Claude… I'd noticed I was reading plans less closely."

### Concrete points
- **What HTML can carry**: tables, CSS design data, SVG illustrations and workflows, script code, JS and CSS interactions, absolute and canvas spatial layout, and images. Without it, models make "ASCII diagrams or… estimating colors with unicode characters."
- "I tend to not actually read more than a 100-line Markdown file."
- **Data ingestion**: Claude Code can pull from the filesystem, MCPs (Slack, Linear), Claude in Chrome and git history.
- **Getting started**: just say "make an HTML file / HTML artifact"; build a skill once patterns recur.
- **Use cases with prompts**:
  - Exploration grids: "Generate 6 distinctly different approaches… in a grid… Label each with the tradeoff."
  - An implementation plan with mockups, data flow and snippets.
  - PR review: "Render the actual diff with inline margin annotations, color-code findings by severity."
  - Design prototypes with sliders and a copy-params button.
  - Explainers: "a diagram…, the 3–4 key code snippets annotated, and a 'gotchas' section… Optimize it for someone reading it once."
  - Status and incident reports and decks.
  - Throwaway editors that always end in an export: "copy as JSON", "copy as prompt", "copy diff". Examples: a 30-ticket Now/Next/Later/Cut board, a feature-flag editor with prerequisite warnings, and a prompt tuner with live re-render and a token counter.
- **Workflow**: build a *web* of HTML files (exploration, then mockups, then plan), then start a fresh implementation session and pass the files in. The verification agent also reads them.
- **Cost**: "While Markdown often uses fewer tokens… the much higher likelihood of me reading it means I get overall better output", with the 1M window on Opus 4.7.

### Linked resources
- `anthropics/html-effectiveness` (MIT): 20 standalone, dependency-free HTML examples: exploration, PR review, code understanding, design system, prototypes, slide deck, status and incident reports, flowchart, explainers, implementation plan, PR write-up, and the triage-board, feature-flag and prompt-tuner editors.
- A hosted gallery is at thariqs.github.io/html-effectiveness.

### New vs common knowledge
- Mostly a practice argument with no measurements: it is the author's opinion ("HTML maximalist").
- The "export button back into the prompt" loop and "fresh session plus spec files" are the useful, transferable parts.

### Implications for agent-harness / repo-E
1. **EFFECTIVENESS (owner in the loop): produce owner-facing outputs as HTML artifacts.** This covers the lead's HANDOFF summary, the research-note "decision brief", the review of security-labelled PRs, and the architect's consolidation proposal. The repository sources of truth stay Markdown, since the gates (cite-check, docs) parse them.
   - Example: a PR-review page that renders the diff with severity-tagged margin notes for `review:security` PRs, which fits a strict review policy.
   - Measure: the owner's turnaround time on reviews, and how many review findings the owner catches.
2. **EFFECTIVENESS: a board-triage editor that ends in "copy as prompt".**
   - The lead generates a Now/Next/Later/Cut board from `uv run dev board list`. The owner drags cards; the export is a list of `uv run dev board ...` commands or a prompt.
   - This replaces long ranking discussions in chat. Measure the owner turns per ranking session.
3. **TOKENS (caution): never route HTML into worker contexts.**
   - Agent-to-agent artifacts (briefs, hand-backs, research notes) must stay terse Markdown or JSON. HTML costs roughly 2–4× the tokens of equivalent Markdown (BELIEVED), and every token in a worker context is re-read about 100 times (section 2, implication 1).
   - Generate HTML only at the lead or owner boundary, ideally inside a subagent so the HTML never sits in the lead's cached context. The subagent writes the file and returns the path.
4. **EFFECTIVENESS: the "fresh session plus spec files" pattern supports the harness's design.** One-line spawn prompts plus `board brief` are the same idea. Optionally attach a richer HTML plan for UI-heavy issues (user-facing UI) and have the reviewer read it for verification.

---

## Cross-cutting priority list (ranked by expected value ÷ effort)

| # | Change | Tag | How to measure |
|---|---|---|---|
| 1 | Split "fresh" into creation vs uncached in `devkit/stats.py`; add hit rate and a cache-break detector per agent type | TOKENS | Hit rate per agent over the last 30 runs; flag runs with breaks |
| 2 | Context-growth discipline: small tool output, exploration in summarizing subagents, batched calls, `/compact` or `/rewind` at task boundaries for the lead | TOKENS | Mean cached tokens per request and total cached per builder run (baseline about 310k and 43 M) |
| 3 | Lint: no `model:` in skill or command frontmatter; never `opusplan` or mid-task `/model` in lead guidance | TOKENS | Zero likely-cause misses in `/usage` |
| 4 | Research rounds as a saved workflow (schema outputs, deterministic saturation stop, prefix stagger) | EFF + TOKENS | Lead-context tokens per research issue; rounds honoured |
| 5 | Structured (schema-validated) hand-backs and verdicts | EFF | Lead re-queries per hand-back |
| 6 | Per-rule verifier plus skeptic workflow for harness-rule compliance; mine corrections into rules monthly | EFF | Recall of planted violations vs the reviewer; false-positive rate |
| 7 | Quarantine readers for untrusted content (scraped web content, PR comments) | EFF / safety | Red-team injected instructions in fixtures; actor never sees raw text |
| 8 | Retest old scaffolding on Opus 5.5 via `dev eval`; prune what is dead weight | EFF + TOKENS | Eval pass rate unchanged with fewer words and hooks |
| 9 | Per-agent `tools:` restriction (drop irrelevant MCP stubs) | TOKENS + EFF | Prefix size and first-turn creation tokens |
| 10 | HTML only at the owner boundary (PR security review, board triage editor), produced in a subagent | EFF | Owner review turnaround; owner turns per ranking |
