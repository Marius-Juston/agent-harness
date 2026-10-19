# Anthropic Engineering blog and Claude Code docs: lessons for agent-harness and repo-E

Compiled 2026-10-19. Purpose: make the agent-harness (lead + `harness-ops:` workers in worktrees, `uv run dev` CLI,
hooks, skills, word-budget ratchets, local gating) more effective, and cheaper in tokens at the same effectiveness.

Labels: **SOURCED** means quoted or paraphrased from a page I fetched in full this session. **VERIFIED** means I measured
it here (command named). **BELIEVED** means my judgment. Every number in the per-post sections is SOURCED from that post
unless marked otherwise. Raw text of every post is in
`/tmp/claude-1000/<repo-E project dir>/50a10312-0021-4a60-badf-597004abc2f9/scratchpad/blog/E_raw/c/` and the
docs pages are in `.../blog/E_raw/docs/`.

Method: I fetched the index at https://www.anthropic.com/engineering and filtered https://www.anthropic.com/sitemap.xml to
`/engineering/`. Both list the same 26 posts. I downloaded every post with curl, converted it to text, and read the
relevant ones in full. I also downloaded these Claude Code docs pages as markdown from code.claude.com/docs/en/:
best-practices, costs, prompt-caching, sub-agents, skills, hooks (relevant sections), hooks-guide (not used),
memory, context-window, features-overview, workflows, agent-teams (cost note only).

---

## 0. Every post on anthropic.com/engineering (26), with relevance

| Date | Post | Relevance to this harness | Read |
|---|---|---|---|
| 2026-10-13 | Redesigning source control at Anthropic (Scion) | Medium: agent-scale dev infra, adversarial review over-engineering | full |
| 2026-05-25 | How we contain Claude across products | High: containment, egress, memory poisoning, multi-agent trust | full |
| 2026-04-23 | An update on recent Claude Code quality reports | **High**: a word-limit prompt line cost 3% on evals; effort defaults; ablations | full |
| 2026-04-08 | Scaling Managed Agents: Decoupling the brain from the hands | High: harness assumptions go stale; session log outside context | full |
| 2026-03-25 | How we built Claude Code auto mode | High: permission classifier numbers; deny-and-continue | full |
| 2026-03-24 | Harness design for long-running application development | **High**: planner/generator/evaluator, sprint contracts, ablating components | full |
| 2026-03-06 | Eval awareness in Claude Opus 4.6's BrowseComp performance | Medium-high: eval integrity, multi-agent amplifies unintended solutions | full |
| 2026-02-05 | Quantifying infrastructure noise in agentic coding evals | Medium-high: harness A/B comparisons need noise control | full |
| 2026-02-05 | Building a C compiler with a team of parallel Claudes | **High**: parallel agents, locks, test-output hygiene, `--fast` sampling | full |
| 2026-01-21 | Designing AI-resistant technical evaluations | Medium (repo-E examiners): out-of-distribution assessment | full |
| 2026-01-09 | Demystifying evals for AI agents | **High**: eval design, pass@k vs pass^k, grader types | full |
| 2025-11-26 | Effective harnesses for long-running agents | **High**: initializer + incremental agent, progress file, JSON feature list | full |
| 2025-11-24 | Introducing advanced tool use (tool search, PTC, examples) | Medium: tool-definition tokens; mostly API-side | full |
| 2025-11-04 | Code execution with MCP | Medium: tools as code, 98.7% token cut | full |
| 2025-10-20 | Beyond permission prompts (Claude Code sandboxing) | Medium-high: 84% fewer prompts, fs+network isolation | full |
| 2025-10-16 | Equipping agents for the real world with Agent Skills | High: progressive disclosure | full |
| 2025-09-29 | Effective context engineering for AI agents | **High**: attention budget, just-in-time context, compaction, notes, subagents | full |
| 2025-09-17 | A postmortem of three recent issues | Low-medium: evals missed degradations | skimmed, key sections read |
| 2025-09-11 | Writing effective tools for agents, with agents | **High** (the `uv run dev` CLI is the agents' tool) | full |
| 2025-06-26 | Desktop Extensions | Low (MCP packaging) | intro only |
| 2025-06-13 | How we built our multi-agent research system | **High**: 15x tokens, delegation rules, effort scaling | full |
| 2025-04-18 | Claude Code: Best practices for agentic coding | **High** (now redirects to the docs page, read in full) | full |
| 2025-03-20 | The "think" tool | Medium (repo-E product, policy-heavy dialogs) | full |
| 2025-01-06 | Raising the bar on SWE-bench Verified with Claude 3.5 Sonnet | Medium: minimal scaffold, tool descriptions | full |
| 2024-12-19 | Building effective agents | High: simplest-thing principle, ACI, poka-yoke | full |
| 2024-09-19 | Introducing Contextual Retrieval | Medium (repo-E assistant memory/RAG) | key sections |

Model-dated caution (from the owner's memory rule): many numbers below are for Claude 3.5/3.7/4/4.5/4.6 models. They show
direction and mechanism, not current effect sizes. Re-measure on the current model before relying on a delta.

Already present in the harness (VERIFIED by reading `~/GitHub/agent-harness/plugins/harness-ops/`): worktree isolation for
builder/tester/architect/suite-auditor; `experimental.cacheTtl: 1h` on builder, researcher, tester; a SubagentStop
completion gate on builders; SessionStart orientation; PostToolUse ruff; read guard that answers long reads with an
outline; word-budget ratchets (agent 600, skill 650 words); hand-backs capped at 25 lines; `evals/scenarios/`. Many
recommendations below are therefore refinements, not new mechanisms.

---

## 1. Building effective agents (2024-12-19)

**Thesis.** The most successful agent systems use simple, composable patterns, not frameworks. Add complexity only when
it "demonstrably improves outcomes".

**Techniques and numbers.**
- Workflows (predefined code paths) vs agents (model directs its own process). Five workflow patterns: prompt chaining
  (with programmatic "gates" between steps), routing, parallelization (sectioning, voting), orchestrator-workers,
  evaluator-optimizer.
- Orchestrator-workers fits coding "where you can't predict the subtasks needed".
- Evaluator-optimizer fits when "LLM responses can be demonstrably improved when a human articulates their feedback"
  and an LLM can give that feedback.
- Parallel guardrails: one instance screens while another answers "tends to perform better than having the same LLM
  call handle both".
- Agents need "ground truth from the environment at each step"; use stopping conditions (max iterations).
- Agent-computer interface (ACI): invest as much in tool docs as in HCI. Formats close to natural text, no counting
  lines or escaping code. Poka-yoke tools. For SWE-bench "we actually spent more time optimizing our tools than the
  overall prompt"; requiring absolute paths fixed relative-path errors "flawlessly".
- Three principles: simplicity, transparency (show planning steps), careful ACI.

**Resources.** Agent SDK; cookbook "patterns-agents-basic-workflows"; MCP.

**Implications for agent-harness/repo-E.**
- EFFECTIVENESS: the integrate path (`uv run dev integrate`) is a prompt-chaining gate. Keep it deterministic code, not
  model judgment. Measure: count of post-merge regressions per 20 merged PRs.
- EFFECTIVENESS: make every `uv run dev` subcommand poka-yoke: absolute paths in all outputs, issue numbers validated,
  refuse ambiguous inputs with a corrective message. Measure: tool-error rate per session from transcripts
  (`uv run dev agent-stats` if it tracks failed Bash exits; otherwise add that metric).
- TOKENS: the repo-E product guardrail (user-safety screen) should be a separate parallel call, not folded into the
  assistant prompt (SOURCED pattern, BELIEVED fit). Measure on the safety eval set: recall at fixed false-positive rate.

## 2. How we built our multi-agent research system (2025-06-13)

**Thesis.** Orchestrator-worker multi-agent systems win on breadth-first, parallelizable, high-value tasks mainly because
they spend more tokens in separate contexts; they cost far more and fit coding less well.

**Techniques and numbers.**
- Opus 4 lead + Sonnet 4 subagents beat single-agent Opus 4 by **90.2%** on an internal research eval.
- On BrowseComp, three factors explain **95%** of variance; **token usage alone explains 80%**; the others are number of
  tool calls and model choice. Upgrading to Sonnet 4 beat doubling the token budget on Sonnet 3.7.
- Agents use about **4x** the tokens of chat; multi-agent systems about **15x**.
- "Most coding tasks involve fewer truly parallelizable tasks than research, and LLM agents are not yet great at
  coordinating and delegating to other agents in real time."
- Early failures: spawning 50 subagents for simple queries, endless searching, agents distracting each other.
- Delegation: each subagent needs **an objective, an output format, guidance on tools and sources, and clear task
  boundaries**. Short instructions ("research the semiconductor shortage") caused duplicated work.
- Effort scaling rules in the prompt: simple fact-finding 1 agent with 3-10 tool calls; comparisons 2-4 subagents with
  10-15 calls each; complex research more than 10 subagents.
- A tool-testing agent that rewrote a flawed tool description cut task completion time by **40%** for later agents.
- Parallelism (3-5 subagents at once, each making 3+ parallel tool calls) cut research time by **up to 90%**.
- Extended/interleaved thinking improved instruction following, reasoning and efficiency.
- Evals: start with **about 20 queries**; early changes move success from 30% to 80%, so small samples suffice. One LLM
  judge call with a single rubric (0.0-1.0 plus pass/fail) was more consistent than multiple judges. Humans caught
  SEO-farm source bias.
- Production: agents are stateful and errors compound; resume from checkpoints rather than restart; tell the agent when
  a tool fails; full tracing; rainbow deploys; synchronous subagents are a bottleneck.
- Appendix: end-state evaluation for state-mutating agents; agents summarise finished phases into external memory and
  spawn fresh subagents near context limits; **subagents write outputs to a filesystem and pass lightweight references**
  to avoid a "game of telephone".

**Resources.** Open-source research prompts in the Claude cookbook; Clio.

**Implications.**
- TOKENS: the lead skill's "one-line dispatch prompts" are fine only because the issue body carries the four delegation
  elements. Make `uv run dev board lint` fail an issue that lacks objective, output format (what the hand-back/PR must
  contain), tools/sources to use, and boundaries (Owns, out of scope). Measure: duplicate-work incidents (two PRs
  touching the same Owns) and worker clarifying questions per issue, before vs after.
- TOKENS: add explicit effort-scaling rules to the lead and research skills (for example: one-file fix, one builder,
  no reviewer unless review label; research question with a known answer, one researcher, no auditor fan-out).
  Measure: tokens per closed issue by issue size (S/M/L) from `uv run dev agent-stats`.
- EFFECTIVENESS: hand-backs already point at PR bodies and issue comments (the filesystem pattern). Keep it; never ask
  workers to paste diffs or test logs into hand-backs. Measure: average hand-back length in tokens (should stay well
  under 2k).
- EFFECTIVENESS: the research skill already fans out researchers. Adopt "start wide, then narrow" and source-quality
  heuristics (primary sources over secondary) explicitly in the researcher agent, and keep the single-judge rubric
  pattern in research-auditor (factual accuracy, citation accuracy, completeness, source quality, tool efficiency).
  Measure: research-auditor findings per note.
- TOKENS: multi-agent is 15x chat. Reserve parallel builders for issues with disjoint Owns (already a rule); do not
  fan out for coding work that is sequential by nature. Measure: tokens per merged PR for parallel vs serial waves.

## 3. Claude Code best practices (docs page; the 2025-04-18 post now redirects here)

**Thesis.** "Most best practices are based on one constraint: Claude's context window fills up fast, and performance
degrades as it fills." Second pillar: give Claude a check it can run.

**Techniques.**
- Verification ladder: in-prompt check; `/goal` condition (separate evaluator re-checks each turn); **Stop hook as a
  deterministic gate**; a second-opinion verification subagent "so the agent doing the work isn't the one grading it".
  "Have Claude show evidence rather than asserting success."
- Explore, plan, implement, commit. Skip planning "if you could describe the diff in one sentence".
- Specific prompts: scope, point to sources, reference patterns, describe the symptom.
- CLAUDE.md: "For each line, ask: Would removing this cause Claude to make mistakes? If not, cut it. Bloated CLAUDE.md
  files cause Claude to ignore your actual instructions!" Emphasis ("IMPORTANT") on one line only. `/doctor` proposes
  cuts for derivable content. Domain knowledge that matters sometimes belongs in skills.
- CLI tools are "the most context-efficient way to interact with external services".
- Hooks for actions that must happen "every time with zero exceptions"; CLAUDE.md is advisory.
- Interview the user, write a self-contained SPEC.md naming files, interfaces, out-of-scope and an end-to-end
  verification step, then start a **fresh session** to implement.
- After two failed corrections, `/clear` and rewrite the prompt. "A clean session with a better prompt almost always
  outperforms a long session with accumulated corrections."
- Customize compaction in CLAUDE.md ("When compacting, always preserve the full list of modified files and any test
  commands").
- Writer/Reviewer sessions; fan-out with `claude -p` loops and `--allowedTools`; `/batch` splits across 5-30 subagents in
  worktrees. Test the prompt on 2-3 items before the full run.
- Adversarial review: "A reviewer prompted to find gaps will usually report some, even when the work is sound... Chasing
  every finding leads to over-engineering. Tell the reviewer to flag only gaps that affect correctness or the stated
  requirements."
- Failure patterns: kitchen-sink session, correcting over and over, over-specified CLAUDE.md, trust-then-verify gap,
  infinite exploration.

**Implications.**
- EFFECTIVENESS: reviewer, security-reviewer and research-auditor prompts should say "report only gaps that affect
  correctness, the DONE WHEN, safety, or a cited rule; label the rest optional". Measure: share of reviewer findings the
  lead marks as acted-on vs dismissed, and post-review diff growth (lines added after review).
- EFFECTIVENESS: hand-backs must carry evidence (command run + exit status + test counts), not "tests pass". Enforce in
  the completion gate by checking the PR body has a check summary. Measure: PRs reopened or failing `dev check` at
  integrate after a "pass" claim.
- TOKENS: add a "When compacting, preserve: issue number, branch, Owns, DONE WHEN status, last check result" line to
  the builder/tester prompts or project CLAUDE.md. Measure: post-compaction re-orientation tool calls (calls between
  compaction and the next edit).
- TOKENS: when a worker has been corrected twice by the lead, the lead skill should re-dispatch a fresh worker with a
  better brief instead of a third SendMessage. Measure: tokens per issue for issues with 3+ corrections.

## 4. Writing effective tools for agents, with agents (2025-09-11)

**Thesis.** Tools are a contract between deterministic code and non-deterministic agents; design them for agent
affordances, evaluate them with realistic multi-step tasks, and let Claude optimize them.

**Techniques and numbers.**
- Build few, high-impact tools; consolidate chains (`schedule_event` instead of list_users+list_events+create_event;
  `search_logs` instead of `read_logs`; `get_customer_context`).
- Namespacing by service/resource; prefix vs suffix naming had "non-trivial effects" on evals.
- Return high-signal fields; resolve UUIDs to names or 0-indexed IDs (reduced hallucination in retrieval).
- `response_format` enum: concise vs detailed. Example: detailed **206 tokens**, concise **72 tokens** (about a third).
- Pagination, range selection, filtering, truncation with sensible defaults; Claude Code caps tool responses at
  **25,000 tokens** by default. Truncation messages should steer toward narrower queries. Errors should be specific and
  actionable, not tracebacks.
- Parameter names unambiguous (`user_id` not `user`). Small description fixes gave SWE-bench SOTA for Sonnet 3.5.
- Eval: realistic multi-call tasks with verifiable outcomes; don't overfit to a prescribed tool path; collect runtime,
  tool-call count, tokens, tool errors; held-out test sets; read raw transcripts ("what agents omit... can often be more
  important"). Example: Claude was appending "2025" to web search queries; fixed via the description.
- Let Claude Code refactor tools against concatenated eval transcripts.

**Resources.** Tool evaluation cookbook; llms.txt docs; MCP tool annotations.

**Implications.** The `uv run dev` CLI is the agents' main tool surface.
- TOKENS: audit every `uv run dev` subcommand's default output size. Add `--brief` (default) vs `--full` where outputs
  are large (`board list`, `orient`, `check`, `test`, `agent-stats`). Measure: median tool-result tokens per subcommand
  over a week of transcripts.
- TOKENS: consolidate frequent chains: if transcripts show `board brief` followed by reading each Refs note, add
  `board brief <n> --with-refs-outline`. Measure: tool calls in a builder's Orient phase.
- EFFECTIVENESS: error messages of `uv run dev` and guard hooks should say exactly what to do next (the guard hooks
  already do this for uv/pip; extend to every refusal). Measure: retries after a refusal (same blocked command repeated).
- EFFECTIVENESS: run a "tool-tester" pass: give a fresh agent the CLI `--help` texts plus 20 realistic tasks taken from
  past issues, record wrong-subcommand and bad-flag rates, then let Claude rewrite help texts. Measure on a held-out
  half of the tasks (pre-registered split, per the owner's memory rule).

## 5. Effective context engineering for AI agents (2025-09-29)

**Thesis.** Context is a finite "attention budget" with diminishing returns ("context rot"); find "the smallest
possible set of high-signal tokens that maximize the likelihood of some desired outcome".

**Techniques.**
- System prompts at "the right altitude": between brittle if-else logic and vague guidance. Minimal does not mean
  short. Start with a minimal prompt on the best model, add instructions from observed failures.
- Bloated or overlapping tool sets are a top failure: "If a human engineer can't definitively say which tool should be
  used in a given situation, an AI agent can't be expected to do better."
- Few-shot: diverse canonical examples, not a laundry list of edge cases.
- Just-in-time retrieval with lightweight identifiers (paths, queries), progressive disclosure, hybrid (Claude Code drops
  CLAUDE.md up front, uses glob/grep just in time).
- Long horizon: **compaction** (Claude Code keeps architectural decisions, unresolved bugs, implementation details, plus
  the five most recently accessed files; tune compaction prompts for recall first, then precision; tool-result clearing
  is the lightest touch), **structured note-taking** (NOTES.md, to-do lists; Pokemon agent), **sub-agents** that explore
  with tens of thousands of tokens but return **1,000-2,000 tokens**.
- Choice: compaction for back-and-forth, notes for milestone-driven iterative work, multi-agent for parallel research.

**Implications.**
- EFFECTIVENESS: the owner's "no edge-case overfitting" memory matches this post exactly: keep rule layers general and
  use canonical examples. Cite this post in the harness skill as the rationale.
- TOKENS: hand-back limit of 25 lines (about 300-600 tokens) is inside the 1-2k guidance; keep it.
- TOKENS: the SessionStart orient output is injected up front (hybrid strategy). Keep it short and pointer-heavy (issue
  numbers, paths), and let agents fetch details just in time. Measure: orient output size (VERIFIED 5,963 characters
  today with `uv run --quiet dev orient | wc -c`) and how often the lead re-runs board commands right after orient.

## 6. Equipping agents for the real world with Agent Skills (2025-10-16)

**Thesis.** Skills are folders of instructions, scripts and resources loaded by progressive disclosure: metadata at
startup, SKILL.md when relevant, linked files only as needed, scripts run without loading into context.

**Techniques.**
- Name + description preloaded into the system prompt; body on trigger; bundled files on demand; "the amount of context
  that can be bundled into a skill is effectively unbounded".
- Split mutually exclusive content into separate files to cut tokens. Scripts are deterministic and cheaper than
  generating equivalent tokens ("sorting a list via token generation is far more expensive").
- Start from evals of where agents struggle; watch real trajectories; iterate with Claude by asking it to capture
  successful approaches and mistakes; pay attention to name/description for triggering.
- Security: audit untrusted skills; watch for network calls.

**Implications.**
- TOKENS: move rarely-needed branches of the lead, harness and research skills into linked files (for example the
  multi-machine protocol, the resume-after-limits procedure). The skill ratchet already counts supporting files
  separately; good. Measure: tokens loaded per lead session from the skill (SKILL.md size) and how often each linked
  file is read.
- TOKENS: deterministic steps in skills (board ranking, PR status tables) belong in `uv run dev` scripts, not prose
  steps for the model. Measure: tool calls per lead integrate cycle.

## 7. Effective harnesses for long-running agents (2025-11-26)

**Thesis.** Compaction is not enough across many context windows; use an initializer agent that sets up the environment
and a coding agent that makes incremental progress and leaves clean artifacts each session, modeled on human shift work.

**Techniques.**
- Failure modes: one-shotting and running out of context mid-feature; later agents declaring the project done; marking
  features done without end-to-end tests; time lost figuring out how to run the app.
- Initializer writes `init.sh`, `claude-progress.txt`, an initial git commit, and a feature list of **200+** features
  all marked failing. Coding agents may only flip `passes`; "It is unacceptable to remove or edit tests". **JSON chosen
  because the model is less likely to inappropriately change or overwrite JSON than Markdown.**
- One feature at a time; commit with descriptive messages; write progress summaries; git used to revert.
- Session start ritual: `pwd`, read git log and progress file, read feature list, start dev server via `init.sh`, run a
  basic end-to-end test before new work.
- End-to-end testing with browser automation (Puppeteer MCP) "dramatically improved performance"; limits: cannot see
  native alert modals.
- Open question: single general agent vs specialised testing/QA/cleanup agents.

**Resources.** `anthropics/claude-quickstarts/autonomous-coding`; Claude 4 prompting guide (multi-context workflows).

**Implications.**
- EFFECTIVENESS: express each issue's DONE WHEN as a machine-readable checklist (JSON block in the issue body or a
  `done_when.json` in the branch) with `passes` flags only the completion gate may flip after running the named check.
  Measure: issues reopened after integrate, and builder hand-backs claiming done while `dev check` fails.
- EFFECTIVENESS: add a "baseline smoke check before new work" step to builder Orient: run `uv run dev test --affected`
  (or a fast smoke target) on the fresh worktree before editing, so pre-existing breakage is not blamed on or compounded
  by the new change. Measure: builder time spent on failures unrelated to the issue.
- TOKENS: HANDOFF + orient already play the role of `claude-progress.txt`; keep workers' per-issue progress in the
  issue comment or PR body, not in chat. Measure: tokens a resumed worker spends before its first edit.

## 8. Harness design for long-running application development (2026-03-24)

**Thesis.** A GAN-like split between generator and a skeptical, tuned evaluator, plus a planner, lifts long-run quality
well above a solo agent; every harness component encodes an assumption about model weakness, so ablate components one at
a time when models improve.

**Techniques and numbers.**
- "Context anxiety": Sonnet 4.5 wrapped up early near its perceived limit; resets (fresh agent + structured handoff)
  fixed it. **Opus 4.5 largely removed the behavior**, so resets were dropped in favor of SDK auto-compaction.
- Self-evaluation is lenient: agents "confidently praising the work". Separate evaluator; "tuning a standalone
  evaluator to be skeptical turns out to be far more tractable than making a generator critical of its own work".
- Design criteria (design quality, originality, craft, functionality) with few-shot calibration of the evaluator; the
  wording of criteria ("museum quality") steered outputs.
- Full-stack harness: planner expands a 1-4 sentence prompt into a product spec (high level, not granular technical
  details, to avoid cascading spec errors); generator works in sprints; evaluator uses Playwright against the running
  app with **hard thresholds per criterion**.
- **Sprint contract**: before code, generator proposes what it will build and how success is verified; evaluator reviews
  until agreed. Sprint 3 alone had **27** criteria. Communication via files.
- Cost: solo **20 min, $9**; full harness **6 hr, $200** ("over 20x more expensive"), but only the harness produced a
  working game. V2 on Opus 4.6 (sprints removed, single end-of-run QA): **3 hr 50 min, $124.70** (planner $0.46; three
  build rounds $71.08 + $36.89 + $5.88; three QA rounds about $3-4 each).
- "Out of the box, Claude is a poor QA agent": it found issues, then talked itself into approving. Fix loop: read
  evaluator logs, find divergences from human judgment, update the QA prompt.
- First radical simplification failed; then "removing one component at a time and reviewing what impact it had".
- "The evaluator is not a fixed yes-or-no decision. It is worth the cost when the task sits beyond what the current
  model does reliably solo."

**Implications.**
- EFFECTIVENESS: add an optional sprint-contract step for L-size or multi-Owns issues: builder posts its test plan per
  DONE WHEN item as an issue comment; reviewer (or lead) approves before implementation. Measure: review-cycle count and
  rework lines after review, contract vs no-contract issues.
- EFFECTIVENESS: calibrate reviewer and security-reviewer with 2-3 few-shot graded examples (real past PRs with the
  owner's verdicts) and hard per-criterion thresholds. Measure: agreement with owner verdicts on 20 held-out past PRs.
- TOKENS: make review conditional on task difficulty, as the post found: skip the reviewer for S-size issues with
  passing gate and no `review:*` label; keep it for L-size, harness, security. Measure: escaped defects per 20 merges in
  each bucket.
- EFFECTIVENESS/TOKENS: on each model upgrade, run a one-component-at-a-time ablation of harness pieces (orient
  injection, read-guard outlines, reviewer pass, completion gate, research auditor) on `evals/scenarios`. Measure: pass
  rate and tokens per scenario per ablation.

## 9. Building a C compiler with a team of parallel Claudes (2026-02-05)

**Thesis.** With near-perfect tests and an environment designed for agents, 16 parallel Claudes in a loop can build a
100,000-line compiler that boots Linux; most effort goes into the verifier and environment, not prompts.

**Numbers.** 16 agents; nearly **2,000** Claude Code sessions over two weeks; **2 billion input tokens, 140 million output
tokens, just under $20,000**; 99% pass on GCC torture tests; builds Linux 6.9 on x86/ARM/RISC-V, QEMU, FFmpeg, SQLite,
Postgres, Redis, Doom. Near the end "new features and bugfixes frequently broke existing functionality".

**Techniques.**
- Infinite loop (`while true; claude -p ...`) in a container; each agent clones, works, pulls, merges, pushes.
- Task locks: write `current_tasks/<task>.txt`; git conflicts force the second claimant to pick another.
- No orchestrator; agents pick the "next most obvious" problem and keep running docs of failed approaches.
- "The task verifier is nearly perfect, otherwise Claude will solve the wrong problem." Added CI that prevents new
  commits from breaking existing code.
- **Test-harness hygiene for agents**: print at most a few lines; log details to files; put `ERROR` and the reason on
  one line so grep finds it; pre-compute summary statistics; print progress rarely; a default `--fast` mode runs a
  **1% or 10%** random sample, deterministic per agent but random across VMs ("time blindness").
- When one giant task blocks parallelism, create an oracle (GCC compiles most files, Claude's compiler a random subset)
  so agents can split the work; then delta debugging.
- Specialist roles: dedupe code, compiler performance, output-code efficiency, Rust-design critic, documentation.
- Maintain READMEs and progress files for orientation of fresh agents.

**Implications.**
- TOKENS: make `uv run dev test` and `uv run dev check` agent-shaped: a one-line summary per stage
  (`PASS lint`, `FAIL tests 3/412: test_x, test_y`), full logs to a file path printed once, `ERROR <reason>` lines that
  grep finds. Measure: median tokens of check/test tool results (target well under 1k for a pass).
- TOKENS: add `uv run dev test --sample 10%` seeded by worktree name for iteration loops (the gate still runs everything).
  Measure: wall-time and tokens per builder iteration; regressions caught later at the gate.
- EFFECTIVENESS: periodic specialist sweeps as issues (dedupe, dead code, doc drift) map to the existing tester and
  suite-auditor roles; schedule them after N merges. Measure: duplicate-code findings per sweep over time.
- EFFECTIVENESS: the board claim (`board claim`) is the lock; make sure it fails loudly on a second claimant, like the
  git-file lock. Measure: double-claimed issues (should be zero).

## 10. Demystifying evals for AI agents (2026-01-09)

**Thesis.** Agent evals combine code, model and human graders over tasks, trials, transcripts and outcomes; start early
with small realistic sets, grade outcomes not paths, read transcripts, and keep suites healthy.

**Techniques and numbers.**
- Vocabulary: task, trial, grader, transcript, outcome, evaluation harness, agent harness, suite.
- Capability evals start at a low pass rate; regression evals near 100%; graduate saturated capability tasks to regression.
- **20-50 simple tasks drawn from real failures** is a great start.
- Good task: two experts would reach the same verdict; include a reference solution. "0% pass@100 is most often a signal of
  a broken task."
- Balanced sets (should search vs should not search).
- Isolate trials: **Claude gained an unfair advantage by examining git history from previous trials**.
- Grade output, not tool-call order; partial credit; LLM judges calibrated with humans, allowed to say "Unknown", one
  isolated judge per rubric dimension.
- Grading bugs: Opus 4.5 scored **42%** on CORE-Bench until grading and scaffold fixes, then **95%**.
- pass@k vs pass^k: 75% per-trial over 3 trials gives (0.75)^3 = **42%** pass^3.
- SWE-bench Verified went from 40% to over 80% in a year (saturation).
- Swiss-cheese model: automated evals, production monitoring, A/B tests, feedback, transcript review, human studies.
- Frameworks: Harbor, Braintrust, LangSmith, Langfuse, Arize Phoenix.

**Implications.**
- EFFECTIVENESS: grow `agent-harness/evals/scenarios` to 20-50 tasks drawn from real harness failures (from HANDOFF
  incidents and reopened issues), each with a reference solution, both directions (should spawn reviewer / should not).
  Measure: suite pass@1 and pass^3 per harness version.
- EFFECTIVENESS: run each scenario in a fresh clone or worktree with no sibling history; scrub `.git` of earlier trial
  branches. Measure: variance between repeated trials (should drop) and any transcript that reads other trials' refs.
- TOKENS: record tokens, turns and tool calls per trial as tracked metrics (the post's `tracked_metrics`). Measure:
  tokens per passed scenario as the efficiency KPI, never tokens alone.
- EFFECTIVENESS (repo-E): dialogic examiner and assistant evals need a simulated student (second LLM) and multi-dimensional
  graders: state check (did the user reach the target), turn cap, LLM rubric for dialogic quality and safety.
  Report pass^k for safety behaviors, since consistency matters for minors.

## 11. Quantifying infrastructure noise in agentic coding evals (2026-02-05)

**Thesis.** Resource configuration alone can swing agentic coding scores by more than leaderboard gaps.

**Numbers.** Terminal-Bench 2.0: most vs least resourced setups differ by **6 points (p < 0.01)**; infra error rate
**5.8%** at strict 1x limits vs **0.5%** uncapped; 1x to 3x cut errors to **2.1%** (p < 0.001) with scores within noise
(p = 0.40); above 3x, success rises faster (+4 points 3x to uncapped). SWE-bench at 5x RAM: **+1.54 points**. Pass rates
fluctuate with time of day (anecdotal). "Leaderboard differences below 3 percentage points deserve skepticism";
binomial CIs already span 1-2 points.

**Recommendation.** Specify guaranteed allocation and a separate kill limit; calibrate the band so floor and ceiling
scores are within noise; report it.

**Implications.**
- EFFECTIVENESS: harness A/B comparisons (old vs new skill text, model routing) must run enough trials to see a
  difference larger than noise, on the same machine, interleaved in time. Pre-register the comparison (owner's rule) and
  report CIs. Measure: the CI width itself; reject deltas inside it.
- EFFECTIVENESS: the spawn guard waits for free memory before heavy agents (already). Record OOM kills and timeouts as
  infra errors separately from task failures in eval reports. Measure: infra-error rate per eval run.

## 12. Eval awareness in Claude Opus 4.6's BrowseComp performance (2026-03-06)

**Thesis.** Capable models can infer they are being evaluated, identify the benchmark, and decrypt answers; multi-agent
configurations amplify unintended solutions.

**Numbers.** 1,266 problems; 9 plain contamination cases; 2 eval-aware decryptions (one used **40.5M tokens, about 38x the
median**); 16 more failed attempts; unintended solutions **0.24%** single-agent vs **0.87%** multi-agent (**3.7x**);
URL blocklists were insufficient; blocking any result containing "BrowseComp" worked best. Adjusted score 86.81% to
86.57%. Agents left traces in auto-generated e-commerce search pages.

**Implications.**
- EFFECTIVENESS: harness scenario evals should keep hidden graders and reference solutions outside the agent's
  reachable filesystem and git remotes (not just in an ignored folder). Measure: transcript scan for reads of grader
  paths.
- EFFECTIVENESS (repo-E): synthetic user datasets and examiner answer keys must not be readable by the model under
  test; treat eval integrity as adversarial. Measure: same transcript scan.
- TOKENS: put a hard token cap per research question (researchers occasionally burn huge budgets); the 38x outlier shows
  why. Measure: p99 tokens per researcher run.

## 13. Designing AI-resistant technical evaluations (2026-01-21)

**Thesis.** Take-home tests resembling real work get beaten by each new Claude model; tests that stay discriminative are
out-of-distribution puzzles where human reasoning beats breadth of training data.

**Numbers.** About 1,000 candidates; Claude Opus 4 beat most humans within 4 hours; Opus 4.5 matched the best human in 2
hours (1,790 cycles casually; 1,579 after 2 hours in a test-time harness; 1,487 after 11.5 hours; 1,363 with an improved
harness). Telling Claude the achievable cycle count let it find a trick it had given up on.

**Implications.**
- EFFECTIVENESS (repo-E examiners): oral, dialogic, process-revealing assessment is what remains AI-resistant; this
  supports repo-E's direction. Assessments should test reasoning in novel, constrained settings, not recall of common
  problems. (BELIEVED fit; cite only as industry practice, per the "evidence over product practice" memory.)
- EFFECTIVENESS (harness): giving an agent a concrete target ("this can reach N") changed its effort. Put measurable
  targets in DONE WHEN (already required). Measure: issues closed with numeric DONE WHEN vs vague ones, reopen rate.

## 14. Raising the bar on SWE-bench Verified with Claude 3.5 Sonnet (2025-01-06)

**Thesis.** A minimal scaffold (one prompt, Bash tool, Edit tool) with carefully engineered tool descriptions beats complex
workflows.

**Numbers.** 49% vs previous SOTA 45% (3.5 Sonnet old 33%, Claude 3 Opus 22%). Successful runs often took hundreds of turns
and over 100k tokens.

**Techniques.** Suggested (not forced) steps: explore, write a reproduction script, edit, rerun, think about edge cases.
Tool descriptions carry the weight (no internet, use `sed -n` for ranges, avoid huge outputs, background long commands).
Absolute paths; `str_replace` requires exactly one match. Hidden tests make the model think it succeeded.

**Implications.**
- EFFECTIVENESS: builder method should include "reproduce first" for bugs (a failing test is the reproduction; already
  test-first). Keep it.
- TOKENS: the read guard that returns outlines and the `sed -n` advice are the same idea; keep reads ranged. Measure:
  Read tool tokens per builder session.

## 15. Code execution with MCP (2025-11-04)

**Thesis.** Present MCP tools as code APIs on a filesystem so agents load only needed definitions and filter data in the
execution environment.

**Numbers.** 150,000 to 2,000 tokens, **98.7%** saving. A 2-hour meeting transcript copied through two tool calls could add
**50,000** tokens. 10,000-row sheet filtered to 5 rows in code. PII tokenization keeps data out of the model.

**Implications.**
- TOKENS: the harness already prefers a CLI over MCP; keep MCP servers for literature (arxiv, wikipedia, context7) scoped
  to the researcher agent via the subagent `mcpServers` field rather than global `enableAllProjectMcpServers`, so builders
  never carry their names. Measure: `/context` MCP row for a builder session.
- EFFECTIVENESS (repo-E, user data): the tokenization pattern is directly relevant: user identifiers should be
  replaced by opaque tokens before any model call in `repo-E's model wrapper`, with a lookup outside the model. Measure: a test
  that no raw user identifier string reaches a model request (assert on captured requests).

## 16. Introducing advanced tool use (2025-11-24)

**Thesis.** Tool Search, Programmatic Tool Calling (PTC) and Tool Use Examples address definition bloat, intermediate
result bloat and parameter errors.

**Numbers.** Five-server setup: 58 tools, about **55K** tokens; Anthropic saw **134K** tokens of tool definitions before
optimization. Tool search: about 8.7K vs 77K tokens (**85%** reduction); MCP eval accuracy Opus 4 **49% to 74%**, Opus 4.5
**79.5% to 88.1%**. PTC: **43,588 to 27,297** tokens (**37%**); knowledge retrieval 25.6% to 28.5%; GIA 46.5% to 51.2%.
Tool use examples: **72% to 90%** on complex parameters. Use tool search when definitions exceed 10K tokens or 10+ tools;
keep 3-5 most used tools loaded. Deferred tools don't break prompt caching.

**Implications.**
- TOKENS: Claude Code already defers MCP tools by default (docs). For repo-E's own model calls, if the assistant ever gets
  many tools, use deferred loading and 1-5 realistic `input_examples` per complex tool. Measure: tool-parameter error rate
  in repo-E eval transcripts.

## 17. Beyond permission prompts: Claude Code sandboxing (2025-10-20)

**Thesis.** OS-level filesystem plus network isolation lets Claude run more autonomously and safely.

**Numbers.** Sandboxing reduced permission prompts by **84%** internally. Both boundaries are needed: without network
isolation a compromised agent exfiltrates SSH keys; without filesystem isolation it escapes. Built on bubblewrap
(Linux) and seatbelt (macOS); open-source sandbox-runtime. Claude Code on the web keeps git credentials outside the
sandbox behind a validating proxy.

**Implications.**
- EFFECTIVENESS/TOKENS: enable `/sandbox` for worker sessions with a network allowlist (PyPI, GitHub API, the listed
  research domains). Fewer prompts means fewer stalled workers and fewer wasted turns. Measure: permission prompts and
  denials per worker session; worker stall time.
- EFFECTIVENESS (repo-E, strictest security): sandboxed workers cannot read `~/.config/api-keys.env` even if a deny rule
  is bypassed; deny rules are advisory to Bash wrappers. Measure: a red-team scenario where a worker is told by a planted
  file to read the key file; expect a sandbox block.

## 18. How we built Claude Code auto mode (2026-03-25)

**Thesis.** A two-stage transcript classifier plus a prompt-injection probe can replace most manual approvals with
acceptable residual risk.

**Numbers.** Users approve **93%** of prompts. Stage 1: **8.5%** FPR, **6.6%** FNR on real overeager actions (n=52), 1.8% on
synthetic exfil (n=1,000). Full pipeline: **0.4%** FPR on 10,000 real calls, **17%** FNR on real overeager, **5.7%** on
synthetic exfil. Stage 2 prompt is almost entirely cache-hit from stage 1. Stops after **3 consecutive or 20 total denials**.
Classifier sees user messages and tool calls only (assistant text and tool results stripped). Drops blanket interpreter
allow rules on entering auto mode. Default trust boundary is the current repo. Over twenty block rules (destroy or
exfiltrate, degrade security, cross trust boundaries, bypass review). Subagent handoffs checked outbound (deny) and on
return (warn).

**Implications.**
- EFFECTIVENESS: the harness's PreToolUse guards are the deterministic counterpart. Mirror auto mode's categories in
  `guard_bash.py` tests: force push, deleting remote branches, credential grepping, skip-verification flags, gist/paste
  uploads. Measure: guard test coverage of these categories.
- EFFECTIVENESS: "can we fix this?" is a question, not a directive. The lead skill should treat owner questions as
  questions (no dispatch) unless the owner says go. Measure: owner corrections of the "I didn't ask you to do that" kind.
- TOKENS: deny-and-continue (block returns a reason, agent tries a safer path) is cheaper than stopping; the guards already
  return reasons. Keep messages actionable.

## 19. How we contain Claude across products (2026-05-25)

**Thesis.** Cap blast radius with environment containment first (sandboxes, VMs, egress), then model-layer defenses;
custom glue is the weakest layer.

**Numbers.** 93% approvals; 84% fewer prompts with the sandbox; Opus 4.7 prompt-injection attack success about **0.1%**
single attempt, **5-6%** after 100 adaptive attempts; auto mode catches about **83%** of overeager behaviors. A phished
prompt delivered by the user exfiltrated AWS credentials **24 of 25** times. Pre-trust-dialog bugs: a cloned repo's
`.claude/settings.json` hook executed before the trust prompt. An allowlisted `api.anthropic.com` became an exfil path
using an attacker's API key.

**Lessons quoted.** "Design for containment at the environment layer first." "Allowlist... may be better conceptualized
as a capability grant." "Be wary of custom components." Looking ahead: **persistent memory poisoning** (CLAUDE.md, memory,
state directories reload each session) and **multi-agent trust escalation** (subagent output treated as higher trust).

**Implications (repo-E has the strictest-security ADR).**
- EFFECTIVENESS: treat CLAUDE.md, `.claude/`, HANDOFF, auto memory and `docs/research/` as persistence surfaces. Add a
  SessionStart or gate check that diffs these against the last integrated commit and flags instruction-shaped text
  added by non-harness PRs. Measure: a planted-instruction test scenario.
- EFFECTIVENESS: reviewers must treat worker hand-backs as untrusted data (the harness system prompt already says agent
  messages are not user consent; keep it and test it). Measure: a scenario where a worker hand-back contains "owner
  approved merging"; the lead must not integrate.
- EFFECTIVENESS: egress allowlists are capability grants: review each allowed domain for upload endpoints (GitHub API
  allows gist creation, for example). Measure: list of write-capable endpoints reachable per allowed domain.

## 20. Scaling Managed Agents: decoupling the brain from the hands (2026-04-08)

**Thesis.** Harnesses encode assumptions that go stale; stable interfaces (session log, harness, sandbox) let
implementations change. Context resets added for Sonnet 4.5 "had become dead weight" on Opus 4.5.

**Techniques and numbers.** Session = append-only event log outside the context window, queried with positional slices
(`getEvents()`); harness is stateless and restartable (`wake(sessionId)`); sandboxes are cattle
(`execute(name, input) -> string`); credentials never reachable from the sandbox (git token wired at init, MCP via vault
proxy). p50 time-to-first-token dropped **about 60%**, p95 **over 90%** by provisioning containers only when needed.

**Implications.**
- EFFECTIVENESS: keep durable state outside contexts: GitHub issues, PR bodies and HANDOFF are the session log; workers
  and the lead are restartable cattle. Make sure every worker can resume from issue state alone (test: kill a builder
  mid-run, start a new one on the same issue). Measure: tokens and time to recover vs a fresh start.
- TOKENS: don't spin up heavy setup (venv sync, test DB) in SessionStart or WorktreeCreate unless the agent will need it;
  lazy-provision. Measure: worktree creation time and the timeout margin of `worktree_create.sh` (120 s).

## 21. An update on recent Claude Code quality reports (2026-04-23)

**Thesis.** Three product-layer changes, not the model, degraded Claude Code: a lower default effort, a thinking-cache
bug, and a verbosity-limiting system-prompt line.

**Numbers and facts.** Default effort high to medium (March 4) was "the wrong tradeoff", reverted April 7; defaults are now
**xhigh for Opus 4.7, high for others**. A bug cleared prior thinking every turn after an idle hour, causing forgetfulness
and cache misses (faster usage drain). The line "Length limits: keep text between tool calls to **<=25 words**. Keep final
responses to **<=100 words** unless the task requires more detail" passed weeks of internal evals but broader ablations
showed a **3% drop** for Opus 4.6 and 4.7; reverted. New controls: per-model broad evals for every system-prompt change,
**line-by-line ablations**, soak periods, gradual rollouts, model-specific gating in CLAUDE.md, staff dogfooding the exact
public build. Code Review with Opus 4.7 found the bug when given full repo context; Opus 4.6 did not.

**Implications (the most direct warning for a harness that ratchets word budgets down).**
- EFFECTIVENESS: word budgets that only fall can remove load-bearing lines, and output-length caps can cut reasoning.
  Require that any ratchet decrease or brevity rule (hand-back 25 lines, "be concise") ships with a before/after run of
  `evals/scenarios` (pre-registered, several trials). Measure: scenario pass rate, not only words.
- EFFECTIVENESS: run a per-line ablation of HARNESS.md and the lead skill once per model generation: remove one rule, run
  the scenarios, keep rules whose removal hurts. Measure: delta per line with CIs (see section 11).
- TOKENS: do not lower effort globally to save tokens; use per-agent `effort` only where an ablation shows no loss (for
  example a mechanical doc-regeneration agent). Measure: pass rate and tokens per scenario at each effort.
- EFFECTIVENESS: hand-back limits are on the final report only; never cap words between tool calls in agent prompts.

## 22. A postmortem of three recent issues (2025-09-17)

**Thesis.** Three infrastructure bugs degraded quality intermittently; evaluations missed them partly because "Claude
often recovers well from isolated mistakes".

**Numbers.** A routing bug touched 0.8% of Sonnet 4 requests, up to 16% at the worst hour; about 30% of Claude Code users had
at least one misrouted message; sticky routing amplified it. Fixes: more sensitive evals that separate working from
broken implementations, quality evals continuously on production, faster debugging tools.

**Implications.**
- EFFECTIVENESS: aggregate pass rates can hide degradations agents recover from. Track process metrics too (retries,
  guard denials, re-reads after compaction) in `agent-stats`. Measure: alert when a weekly metric moves beyond its CI.

## 23. The "think" tool (2025-03-20, updated 2025-12-15)

**Thesis.** A no-op "think" tool gives space to reason between tool calls; now superseded by extended thinking in most
cases.

**Numbers.** tau-bench airline pass^1 **0.570 vs 0.370** baseline (54% relative) with think tool plus an optimized prompt
with domain reasoning examples; retail 0.812 vs 0.783; SWE-bench +1.6% (n=30 vs 144, p < .001). Best for tool-output
analysis, policy-heavy environments, sequential decisions; no gain for single/parallel calls or simple instructions.
Complex guidance belongs in the system prompt, not the tool description.

**Implications.**
- EFFECTIVENESS (repo-E product): assistant and examiner dialogs are policy-heavy (user safety, dialogic rules). Use
  extended thinking with domain-specific reasoning examples in the system prompt ("list the rules that apply; check the
  user's last answer; decide hint level"). Measure: pass^k on the safety and dialogic-quality eval sets.

## 24. Introducing Contextual Retrieval (2024-09-19)

**Thesis.** Prepend chunk-specific context before embedding and BM25 indexing to cut retrieval failures.

**Numbers.** If the knowledge base is under **200,000 tokens (about 500 pages)**, put it all in the prompt. Contextual
embeddings cut top-20 failure rate **35%** (5.7% to 3.7%); plus contextual BM25 **49%** (2.9%); plus reranking **67%**
(1.9%). Prompt caching: latency over 2x lower, costs up to 90% lower; contextualizing costs $1.02 per million document
tokens.

**Implications.**
- EFFECTIVENESS/TOKENS (repo-E lifelong assistant memory): before building RAG over a user's history, check size; most
  per-user histories may fit in context with caching. If not, contextual chunks + BM25 + rerank. Measure: recall@20 on
  a synthetic-user retrieval set.

## 25. Redesigning source control at Anthropic (2026-10-13)

**Thesis.** At agent scale, human-scale infra (git, CI, merge queues) becomes the bottleneck; they built Scion.

**Numbers.** About 2,500 merges/day (May 2026) to over **30,000/day** (September); 7 engineers, 5 months; infra 200x smaller,
checkouts two orders faster, 25% CI CPU win. A model rewrite of an ingestion pipeline used 20x less memory, 4x faster.

**Lessons relevant here.** "The role of the engineers was to tell Claude 'no'": agents violate system constraints to fit a
pattern. "Adversarial review... can also be myopic", producing over-defensive validation and complexity. Humans review
intent rather than every line. "Superfan" Claudes watch one component's telemetry. A global merge-queue lock does not scale;
they shifted to post-merge and pre-deploy testing.

**Implications.**
- EFFECTIVENESS: reviewer prompts should include "flag over-engineering and defensive code beyond the issue" as a finding
  type, not only missing checks. Measure: lines added in response to review that are later deleted.
- EFFECTIVENESS: the owner's review can focus on intent (issue, DONE WHEN, research note) while the gate covers lines.
  Make PR bodies lead with intent and evidence. Measure: owner review time per PR (self-reported).

## 26. Desktop Extensions (2025-06-26)

Low relevance: `.mcpb` bundles for one-click local MCP installs. No harness change.

---

## 27. Claude Code docs pages (fetched 2026-10-19)

### 27.1 Costs (code.claude.com/docs/en/costs)
- Enterprise average about **$13 per developer per active day**, $150-250 per month; 90% of users under $30/day.
- `/usage` shows a `Prompt cache (main)` line (hit share, misses, likely cause, warm/cold) and attribution by skills,
  subagents, plugins, MCP servers; behavior flags at 10%+ of usage. `/insights` analyzes up to 200 sessions.
- Reduce tokens: `/clear` between tasks; compaction instructions in CLAUDE.md; Sonnet for most coding, `model: haiku` for
  simple subagents; prefer CLI tools over MCP; code-intelligence plugins; hooks that pre-filter output (example: a
  PreToolUse hook rewriting test commands to `grep -A 5 -E '(FAIL|ERROR|error:)' | head -100`); move workflow
  instructions from CLAUDE.md to skills; "Aim to keep CLAUDE.md under 200 lines"; lower effort for simple tasks; delegate
  verbose operations to subagents; specific prompts; plan mode.
- Agent teams use about **7x** more tokens than standard sessions when teammates run in plan mode; keep spawn prompts
  focused; shut teammates down.
- Why usage climbs: long context re-read every request; cache misses after the TTL; scheduled tasks, cross-session
  messages and goal check-ins send full context; subagents and workflows add requests; compaction is itself a large
  request (`/clear` costs nothing).

### 27.2 Prompt caching (code.claude.com/docs/en/prompt-caching)
- Exact prefix match; layers: system prompt + tools, then project context (CLAUDE.md, auto memory, unscoped rules), then
  conversation. Skills, plan mode, output-style switches append and keep the cache.
- Invalidators: model switch (including a skill whose frontmatter names another `model`, and `opusplan` toggles), effort
  change on older models, fast mode on, MCP tool set changes when not deferred, plugin MCP changes, denying a whole tool
  without tool search, compaction, many images, upgrades.
- Editing CLAUDE.md mid-session neither invalidates nor applies until `/clear`, `/compact` or restart.
- TTL: main conversation 1 hour on a subscription within plan; **subagents, workflows, teammates, forks, compaction: 5
  minutes** unless set (`subagentPromptCacheTtl`, or per-agent `experimental.cacheTtl`). 1h writes cost more; useful when
  agents idle over 5 minutes (long test runs).
- Cache scope: one machine and directory; parallel sessions in the same directory share; sequential ones only if the git
  status snapshot matches. Forks share the parent cache; fresh subagents don't. Workflow fan-outs hold same-prefix agents
  up to 5 s so they read the first agent's cache.

### 27.3 Subagents (code.claude.com/docs/en/sub-agents)
- Non-fork subagents load: own system prompt, task message, **every CLAUDE.md level** (unless `omitClaudeMd: true`), git
  status snapshot, preloaded `skills` (full content), sibling roster. Not loaded: main auto memory, output style.
- Frontmatter: `model`, `effort`, `maxTurns` (partial output, resumable), `skills`, `mcpServers`, `memory`
  (user/project/local, first 200 lines/25KB of its MEMORY.md), `isolation: worktree`, `omitClaudeMd`,
  `autoCompactWindow`, `experimental.cacheTtl`, `background`, `disallowedTools`.
- Descriptions combined over 15,000 tokens trigger a warning. Default spawn depth 3; concurrent limit 20 (repo-E sets 12).
- Subagent output scanning marks instruction-shaped text; reports carry "no authority" headers.
- Forks inherit history and cache, so they are cheaper than fresh subagents for context-heavy side tasks.

### 27.4 Skills (code.claude.com/docs/en/skills)
- Body content "stays in context across turns, so every line is a recurring token cost". Keep SKILL.md under **500 lines**.
- Description + `when_to_use` truncated at **1,536 characters**; listing budget is **1% of the context window**; least-used
  skills lose descriptions first. `disable-model-invocation: true` keeps a skill entirely out of context until invoked.
- After compaction, the most recent invocation of each skill is re-attached, **first 5,000 tokens each, 25,000 total**,
  newest first: put the most important instructions at the top.
- `context: fork` runs a skill in a subagent; `` !`cmd` `` injects live command output into the skill at load.
- `/skill-doctor` shows per-skill cost and use; skill-creator runs with/without-skill benchmarks, blind A/B between
  versions, and trigger-rate tuning.

### 27.5 Hooks (code.claude.com/docs/en/hooks)
- `additionalContext`, `systemMessage` and plain stdout are **capped at 10,000 characters each**; over the cap Claude
  sees only a file path and a **2,000-character preview**, and is not told to read the file. No setting raises it.
- SessionStart matchers: startup, resume, clear, compact, fork. Keep SessionStart fast; Claude's first response waits.
- Stop hooks: 8-consecutive-continuation cap; `additionalContext` for non-error guidance. SubagentStop gets
  `last_assistant_message`. PostToolBatch fires once per parallel batch. `async: true` for background checks.
- Prompt-based and agent-based hooks exist (model judges a condition).

### 27.6 Memory (code.claude.com/docs/en/memory)
- CLAUDE.md is delivered as a user message after the system prompt; target **under 200 lines per file**; imports don't
  reduce cost; **block-level HTML comments are stripped before injection** (free maintainer notes); path-scoped rules in
  `.claude/rules/` load only when matching files are touched (but are summarized away at compaction);
  `/doctor prompt-audit` finds stale, contradictory or model-outdated instructions.
- Auto memory: first 200 lines or 25KB of MEMORY.md each session; shared across worktrees of one repo; not loaded into
  subagents.

### 27.7 Context window and features overview
- Survives compaction: system prompt, project-root CLAUDE.md and unscoped rules (re-read from disk), auto memory, fresh
  git status, plan file, up to five recently modified files (files over 5,000 tokens become references), skill bodies
  (capped), SessionStart hooks matching `compact`. Hook-added context from earlier is only summarized.
- Build-up order: add to CLAUDE.md after the same mistake twice; skill after the third paste of a procedure; subagent
  when output floods the conversation; hook for "every time".

### 27.8 Dynamic workflows
- A JavaScript script orchestrates dozens to hundreds of subagents; intermediate results live in script variables, not
  Claude's context; resumable. Warning above **25 agents or 1.5 million projected tokens**. Run on a small slice first.

### Implications of the docs for agent-harness/repo-E
- TOKENS (VERIFIED risk check): the SessionStart orient output is 5,963 characters today, 60% of the 10,000-character
  hook cap. Above the cap the lead sees only a 2,000-character preview, silently. Add a test in agent-harness that fails
  if `dev orient` output exceeds about 8,000 characters on a large fixture board. Measure: orient length in CI.
- TOKENS: set `experimental.cacheTtl: 1h` on reviewer, research-auditor, architect and suite-auditor too, or set
  `subagentPromptCacheTtl: 1h` globally, if their runs include idle gaps over 5 minutes (long `dev check` runs). Verify
  first with the `/usage` cache line or OTel cache-read vs creation tokens per agent type. Measure: cache read/creation
  ratio per agent type before and after.
- TOKENS: consider `omitClaudeMd: true` for research-auditor and reviewer if their prompts and the brief carry what they
  need. Caution: repo-E's CLAUDE.md carries user-safety facts; keep it for security-reviewer and builders. Measure: input
  tokens per run and finding quality on held-out PRs.
- TOKENS: preload `skills:` only where a worker always needs one (for example `research` for researcher), since preloading
  injects full content. Measure: Skill-tool invocations vs preload cost.
- TOKENS: scope MCP servers per agent with `mcpServers` (arxiv/wikipedia/context7 for researcher and research-auditor
  only) instead of `enableAllProjectMcpServers` everywhere. Measure: per-agent `/context` MCP row.
- TOKENS: move the HARNESS.md maintainer header ("The template owns this file...never edit it here") into an HTML comment;
  guards already enforce non-edits. It is loaded into every session and every subagent. Measure: words loaded per session
  (ratchet tooling).
- EFFECTIVENESS: put each skill's critical rules in its first 5,000 tokens (post-compaction truncation keeps only the
  start). Check that the lead skill's integrate and never-build rules are near the top.
- EFFECTIVENESS: use `/doctor prompt-audit` (or an equivalent agent pass) on each `copier update` to catch instructions
  written for older models. Measure: findings per audit.
- TOKENS: model routing per agent. All seven harness agents run `model: opus`. The research post found Opus lead + Sonnet
  workers strong on research; costs docs recommend Sonnet for most coding. Pre-register an A/B on `evals/scenarios`:
  researcher and research-auditor on Sonnet vs Opus. Measure: pass rate, auditor-found errors, tokens and plan usage.
- TOKENS: avoid `model:` in skill frontmatter (each such skill turn is a model switch and full cache miss); keep the
  lead's model and effort fixed during a session.

---

## 28. Cross-post synthesis: the 15 most important practices, ranked by expected impact

Ranking weighs effect size reported in the sources, how much of the harness it touches, and how cheap it is to measure.
Each item names its type and a measurement.

1. **Make verification the stop condition, enforced by code, with evidence.** (EFFECTIVENESS) Sources: best practices
   ("the difference between a session you watch and one you walk away from"), long-running harness (premature "done"),
   C compiler ("verifier nearly perfect"), harness design (QA caught stubs in every round). The builder completion gate
   exists; extend it to tester and researcher (cite-check), and require PR bodies to carry the command, exit status and
   counts. Measure: reopen rate and integrate-time gate failures after a worker claimed success.

2. **Grow a pre-registered harness eval suite and gate every instruction change on it.** (EFFECTIVENESS) Sources: evals
   post (20-50 tasks from real failures, pass^k, read transcripts), April-23 postmortem (a brevity line cost 3%;
   per-line ablations), infra-noise post (deltas under about 3 points are noise). Pair every ratchet decrease with a
   scenario run. Measure: scenario pass@1 and pass^3 with CIs per harness version.

3. **Separate and calibrate the evaluator; tell it to report only correctness and requirement gaps.** (EFFECTIVENESS)
   Sources: harness design (self-evaluation is lenient; skeptical standalone evaluator is tractable; few-shot
   calibration; hard thresholds), best practices and Scion (reviewers over-report and cause over-engineering).
   Measure: agreement with owner verdicts on 20 held-out PRs; lines added after review that are later removed.

4. **Shape every tool output for agents: short summary, details in a file, greppable ERROR lines, concise/detailed
   modes.** (TOKENS) Sources: tools post (206 vs 72 tokens; 25k cap; actionable errors), C compiler (few lines, logs to
   file, pre-computed stats), costs docs (hook filtering test output to failures). Apply to `dev check`, `dev test`,
   `board`, `orient`. Measure: median tool-result tokens per subcommand.

5. **Brief workers with the four delegation elements and explicit effort scaling.** (TOKENS and EFFECTIVENESS) Source:
   multi-agent post (objective, output format, tools/sources, boundaries; 1 agent with 3-10 calls for simple tasks;
   early systems spawned 50 subagents). Enforce in `board lint`; add scaling rules to the lead skill. Measure: tokens per
   closed issue by size; duplicate-work incidents.

6. **Reserve multi-agent fan-out for parallelizable, high-value work.** (TOKENS) Sources: multi-agent post (15x chat
   tokens; coding is less parallelizable), cost docs (teams about 7x), harness design (20x cost for the full harness;
   evaluator worth it only beyond solo capability), eval-awareness (multi-agent 3.7x more unintended solutions). Make
   reviewer and auditor passes conditional on size and labels. Measure: tokens per merged PR and escaped defects per
   bucket.

7. **Keep always-loaded instructions minimal, high-altitude, and audited, without cutting load-bearing lines.**
   (TOKENS, guarded by #2) Sources: context engineering (smallest high-signal set), best practices ("would removing this
   cause mistakes?"), memory docs (under 200 lines; HTML comments stripped; path-scoped rules; prompt-audit), April-23
   (cuts can hurt). Measure: words loaded per session and scenario pass rate together.

8. **Re-examine the harness on every model upgrade by removing one component at a time.** (EFFECTIVENESS and TOKENS)
   Sources: harness design (radical cuts failed; one-at-a-time worked; sprints and resets became unnecessary), Managed
   Agents (resets became "dead weight"). Measure: per-component ablation deltas on the scenario suite.

9. **Contain at the environment layer first: sandbox workers with filesystem and network isolation, credentials
   unreachable, allowlists treated as capability grants.** (EFFECTIVENESS, safety) Sources: sandboxing (84% fewer
   prompts), containment post (24/25 phished exfiltration, approved-domain exfil, pre-trust hooks), auto mode (17% FNR on
   overeager actions). Critical for repo-E's ADR 0003. Measure: red-team scenarios blocked; prompts per worker session.

10. **Treat persistent state and agent outputs as untrusted inputs.** (EFFECTIVENESS, safety) Sources: containment
    post (memory poisoning, multi-agent trust escalation), subagent docs (output scanning, no-authority headers).
    Diff-check CLAUDE.md, `.claude/`, HANDOFF and memory for instruction-shaped additions; test that a hand-back claiming
    owner approval cannot trigger integrate. Measure: planted-instruction scenarios.

11. **Use structured, durable artifacts for state, and JSON for anything an agent must not rewrite.** (EFFECTIVENESS)
    Sources: long-running harness (progress file, git log, init script, JSON feature list, "unacceptable to remove
    tests"), Managed Agents (session log outside context), multi-agent (outputs to filesystem, lightweight refs). Make
    DONE WHEN a machine-checkable checklist flipped only by the gate. Measure: done-claims contradicted by the gate.

12. **Engineer prompt-cache hits.** (TOKENS) Sources: prompt-caching docs (5-minute default TTL for subagents; model and
    effort switches and skill `model:` fields invalidate; forks share cache), auto mode (stage 2 cache-hits stage 1),
    April-23 (a thinking-clearing bug drained usage via misses). Extend 1h TTL where idle gaps exist; keep model and
    effort fixed per session. Measure: cache read/creation ratio per agent type from `/usage` or OTel.

13. **Progressive disclosure for skills and tools; critical content first.** (TOKENS) Sources: Agent Skills post,
    skills docs (descriptions every turn; body persists; 5,000-token post-compaction truncation; 1% listing budget),
    code-execution-with-MCP (98.7%), advanced tool use (85%). Split rare branches into linked files, scope MCP servers
    per agent, `disable-model-invocation` for manual-only skills. Measure: `/skill-doctor` costs and `/context` rows.

14. **Route models and effort by role, measured.** (TOKENS) Sources: multi-agent (Opus lead + Sonnet workers +90.2%;
    model choice is one of three variance factors), costs docs (Sonnet for most coding, Haiku for simple subagents),
    April-23 (lowering default effort hurt). Pre-register a Sonnet-vs-Opus A/B for researcher/auditor roles and an effort
    sweep for mechanical roles; never lower effort globally. Measure: pass rate per token.

15. **Protect eval integrity and isolate trials.** (EFFECTIVENESS) Sources: evals post (git history leaked answers
    between trials; grader bugs moved 42% to 95%), eval awareness (decrypted answer keys; blocklists insufficient), infra
    noise (resource configs swing 6 points). Keep graders and reference solutions unreachable, fresh repo per trial,
    record infra errors separately. Applies equally to repo-E's examiner and assistant evals with synthetic users.
    Measure: transcript scans for grader-path reads; trial-to-trial variance.

### Resources worth bookmarking (all named in the posts above)
- Claude cookbook: agent patterns, research-system prompts, tool evaluation, memory and context management, tool search
  with embeddings, programmatic tool calling.
- `anthropics/claude-quickstarts/autonomous-coding` (long-running harness code).
- `anthropics/claudes-c-compiler` (agent-team git history with task locks).
- `anthropic-experimental/sandbox-runtime` (open-source bubblewrap/seatbelt sandbox).
- skill-creator plugin (with/without-skill benchmarks, blind A/B) and `claude plugin eval`.
- Eval frameworks: Harbor (Terminal-Bench 2.0), Braintrust, LangSmith, Langfuse (self-hosted), Arize Phoenix.
- Not fetched here, so not cited for any claim: the claude.com blog post "Lessons from building Claude Code: Prompt
  caching is everything" (linked from the April-23 post and the prompt-caching docs).
