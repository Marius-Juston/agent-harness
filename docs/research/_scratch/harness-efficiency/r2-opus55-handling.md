# r2: handling Opus 5.5, and choosing Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.1 per role

Round mode (round 2, sub-question "Opus 5.5 handling per role"). Written 2026-10-19.
Search coverage: `uv run dev lit status` 2026-10-19: openalex ok, crossref ok, europepmc ok, arxiv ok, **s2 DOWN**
(HTTP 429); discovery OK, partial. No literature search was needed: every source below is official Anthropic
documentation, fetched as markdown with curl (platform.claude.com and code.claude.com `.md` pages) plus one context7
query (`/websites/code_claude`). Raw copies: a local raw copy (not committed) of the research session. The two blog posts covered in
r1-blog-sonnet-evals-cost-effort.md and r1-blog-speed-opus55-context-skills.md are not redone here.

Prices are list prices and serve only as ratios: repo-E runs on a subscription, so the binding resource is the usage
window, which is not documented per model beyond separate "Opus limit"/"Sonnet limit" messages
([Manage costs effectively](https://code.claude.com/docs/en/costs), Claude Code docs).

## 1. Findings

### 1.1 Effort, not prose, is the control; set it explicitly
- **SOURCED.** Opus 5.5 thinking is always on (adaptive only); `thinking: disabled` returns 400; effort is "the
  primary control for how much the model reasons and what a request costs". Default `medium` (Opus 5 was `high`);
  "run an effort sweep on your own evals rather than carrying settings over"
  ([Effort](https://platform.claude.com/docs/en/build-with-claude/effort), Anthropic docs). Best practice #1 on the
  same page: "Set effort explicitly."
- **SOURCED.** Opus 5.5 at `medium` "matches or exceeds Claude Opus 5 at `high` on coding and knowledge-work
  evaluations, and on several coding evaluations `low` comes close to it at much lower cost"; "Reserve `xhigh` and
  `max` for work where you've measured a quality gain"; "To get less thinking, lower the effort level first"
  ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5),
  Anthropic docs).
- **SOURCED.** Effort shapes tool use too: lower effort makes "fewer tool calls", combines operations and uses terse
  confirmations; higher effort makes more tool calls and longer summaries
  ([Effort](https://platform.claude.com/docs/en/build-with-claude/effort)). Relevant because repo-E cost is context ×
  turns (r1-usage-logs §1).
- **SOURCED, measured by Anthropic (SWE-bench Pro subset, 2 pooled runs).** Opus 5.5: `low` 87.4 % solved at
  $0.11/task, `medium` 92.8 % at $0.21, `high` 95.3 % at $0.29, `xhigh` 96.7 % at $0.74 (single run). The policy
  "`medium`, failures re-run at `high`" reached about 97 % at about $0.24; "`low`, failures re-run at `high`" about
  97 % at about $0.17; "everything at `high`" 95.3 % at $0.29. Condition: "you need a failure signal … a checker
  that passes bad work lets those failures through"
  ([Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence),
  Anthropic docs).
- **SOURCED.** Research is effort-sensitive in a model-specific way: Opus 5.5 at `low` scored 13.3 points below
  `medium` on DRACO, 3.4 on HLE and 14.0 on internal physics problems (3-4 attempts per task), while Fable 5.1 was
  flat from `low` to `high` on DeepResearch Bench II (same page). So `low` is wrong for researchers; `medium` vs
  `high` is unmeasured for Opus 5.5 research.
- **SOURCED.** In Claude Code, thinking cannot be turned off on Opus 5.5, Sonnet 5.5, Haiku 5.5 or Fable;
  `MAX_THINKING_TOKENS=0` and `alwaysThinkingEnabled: false` have no effect there; "There is no per-subagent thinking
  setting" (subagents inherit the session's)
  ([Model configuration](https://code.claude.com/docs/en/model-config);
  [Create custom subagents](https://code.claude.com/docs/en/sub-agents), Claude Code docs). The thinking column of
  the role table is therefore "adaptive, fixed": the only per-role lever is `effort`.
- **SOURCED.** Claude Code "passes other phrases such as 'think', 'think hard' … through as ordinary prompt text";
  only `ultrathink` is a keyword, and it adds an in-context instruction without changing the API effort
  ([Model configuration](https://code.claude.com/docs/en/model-config)). Prompt steering of thinking works but is
  "sensitive to exact wording"; "Lowering the effort level is usually the better first lever, since it is a
  calibrated control rather than a wording-sensitive instruction"
  ([Steering thinking](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost), Anthropic
  docs).

### 1.2 Where effort comes from in Claude Code (mechanics the harness depends on)
- **SOURCED.** Subagent effort order: per-invocation `effort` parameter (v2.1.292+) > frontmatter `effort` > session
  level; `CLAUDE_CODE_EFFORT_LEVEL` beats all of them; `maxEffortLevel` caps all of them. Model order: per-invocation
  `model` > frontmatter `model` > `CLAUDE_CODE_SUBAGENT_MODEL` > main model; `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`
  ignores frontmatter. A family alias such as `opus` resolves to the main conversation's exact model when the main
  model is in that family ([Create custom subagents](https://code.claude.com/docs/en/sub-agents)). Skill frontmatter
  also takes `effort` and `model`; a skill's `model` lasts for the current turn (context7 `/websites/code_claude`,
  skills frontmatter table).
- **SOURCED.** A top-level `effortLevel` in user settings "doesn't count for Opus 5.5"; Opus 5.5 starts at `medium`
  until `/effort` or the `/model` picker saves a per-model level under `modelSettings`
  ([Model configuration](https://code.claude.com/docs/en/model-config)).
- **VERIFIED (read 2026-10-19).** The owner's `~/.claude/settings.json` has `model: opus`, top-level
  `effortLevel: medium`, no `modelSettings`; repo-E `.claude/settings.json` sets no effort or model variable. So every
  harness agent without `effort:` (builder, tester, reviewer, architect, suite-auditor, security-reviewer) runs at
  whatever the lead session runs at, today `medium`; a `/effort xhigh` in the lead would silently move all of them.
- **SOURCED.** A non-fork subagent's system prompt is "the agent's own prompt plus environment details …, not the
  Claude Code system prompt" ([Create custom subagents](https://code.claude.com/docs/en/sub-agents)). Whatever
  Opus 5.5 guidance a worker needs (stop rules, scope) must be in the agent body or the delegation prompt; Claude
  Code's own Opus delegation instruction is added only with the `claude_code` preset
  ([Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5),
  Anthropic docs).

### 1.3 Model choice
- **VERIFIED (script `scripts/model_price_ratio.py`, prices from
  [Pricing](https://platform.claude.com/docs/en/about-claude/pricing), Anthropic docs).** Holding repo-E's Opus token
  mix fixed (r1-usage-logs §1: cache read 48 %, cache write 29 %, output 20 %, input 3 % of cost), the same tokens cost
  **Sonnet 5.5 0.50×, Haiku 5.5 0.037× (prompt ≤100k) or 0.185× (prompt >100k), Fable 5.1 1.90×** of Opus 5.5.
  Sonnet may use 2.0× the tokens, Haiku 27× (≤100k) or 5.4× (>100k), before costing more.
- **Correction to r1-blog-sonnet-evals-cost-effort.md.** That file says Sonnet 5.5 and Opus 5.5 cache reads cost the
  same ($0.20). The pricing page lists **$0.10 for Sonnet 5.5** (0.05× of its $2 input) and $0.20 for Opus 5.5;
  "Each of Claude Opus 5.5's token prices, cache reads included, is twice Claude Sonnet 5.5's"
  ([Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence)).
  SOURCED.
- **SOURCED.** Haiku 5.5 is priced by prompt length: $0.10/$0.50 in/out up to 100k prompt tokens, $0.50/$2.50 above,
  counted per request including cache reads ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
  repo-E subagents end at a median 103-171k context (r1-usage-logs §1), so an unbounded Haiku worker would spend most
  turns in the 5× tier; `autoCompactWindow` (min 100000, v2.1.296+) can hold it near the threshold
  ([Create custom subagents](https://code.claude.com/docs/en/sub-agents)).
- **SOURCED, Anthropic measurements.** Coding: Sonnet 5.5 at its default `high` solved 87.6 % ($0.11/solved);
  Opus 5.5 at `low` 87.4 % ($0.12/solved), "inside run-to-run noise, for about 7 % more"; Opus 5.5 `medium`
  92.8 % ($0.22); Fable 5.1 `high` 92.3 % ($1.19, single run). Sonnet 5.5 at `medium` scored ~7 points below its
  `high` on SWE-bench Pro and 62.7 % vs 77.8 % on an internal agentic-coding set. Haiku 5.5 `medium`: GPQA 85 % vs 91 %
  for Opus and Sonnet at about 1/20 of Opus's cost per correct answer; 93 % on a 79-task Managed Agents set at about
  $0.011 per pass ([Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence)).
  Reading: for writing code, Sonnet 5.5 buys nothing that Opus 5.5 at `low` does not; Sonnet's case is read-only
  judgement where Opus's extra capability is not used.
- **SOURCED.** Model selection matrix: "Most workloads start with Claude Opus 5.5"; Fable 5.1 for "agent sessions that
  run for hours, multistep deep research"; Haiku 5.5 for "sub-agent tasks"; move to Fable 5.1 only "if your evals at
  `xhigh` or `max` effort still fall short"; "Tuning effort is often a better lever than switching models"
  ([Choosing the right model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model), Anthropic
  docs). Claude Code's cost page: "For simple subagent tasks, specify `model: haiku`"
  ([Manage costs effectively](https://code.claude.com/docs/en/costs)).
- **SOURCED.** Multi-model pairings: an Opus 5.5 `high` executor with a Fable 5.1 advisor gained 1.7 points (edge of
  noise) for 2.1× cost, "about what more effort does". Orchestrator delegation to cheaper workers saved money only for
  routine work with a long cost tail and for work larger than one context window; otherwise "the orchestrator's model
  alone at lower effort came out ahead in every case measured"
  ([Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence)).
  Advisor and `opusplan` are not worth adopting before an effort sweep (BELIEVED).
- **SOURCED.** `opusplan` = Opus in plan mode, Sonnet in execution
  ([Model configuration](https://code.claude.com/docs/en/model-config)); r1-blog B records that it busts the cache on
  every plan-mode toggle. Fast mode: same Opus model, up to 2.5× faster, $8/$40 per MTok on Opus 5.5 (2× in/out),
  billed to usage credits on subscriptions (outside the plan's limits), first enable re-bills the whole context
  uncached; recommended for interactive work, not "long autonomous tasks" or "cost-sensitive workloads"
  ([Speed up responses with fast mode](https://code.claude.com/docs/en/fast-mode), Claude Code docs). Neither fits a
  free, non-commercial project's unattended workers (BELIEVED).

### 1.4 Prompt style for Opus 5.5 (beyond the two blog posts)
- **Stop/continue. SOURCED.** In unattended loops Opus 5.5 ends some turns with a progress report
  (`end_turn`) while work is open. Remedies in order: keep a checklist; at each end of turn have a separate check
  compare against the completion condition and send the reason back; stop after "two or three automatic
  continuations"; optionally a long system-prompt paragraph naming four kinds of premature stop (summary announcing
  the next step, offer to continue, non-blocking decision list, "good place to report"). Expect "somewhat more tool
  calls and output tokens" with the paragraph; leave it out where a human answers
  ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).
  The builder's SubagentStop `completion_gate.py` already is the "separate check" with CAP = 2 per HEAD, TOTAL_CAP = 6
  (VERIFIED, read in this worktree). Tester and researcher have no such gate.
- **Verification rituals and self-delegation. SOURCED.** Opus 5 "verifies its own work without being told to";
  "include a final verification step" or "use a subagent to verify" cause over-verification and removing them
  "reduces wasted tokens with no loss in quality"; avoid "double-check your answer"; sample damping line: "do not use
  subagents to verify or double-check your own work"
  ([Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)).
  The Opus 5.5 guide says Opus 5 patterns "remain a reasonable starting point". Same page: Opus 5 "coordinates teams
  of subagents well, with effective writer-verifier patterns". Claude Code's best practices recommend a fresh-context
  reviewer subagent before calling work done, and warn that "A reviewer prompted to find gaps will usually report
  some, even when the work is sound … Tell the reviewer to flag only gaps that affect correctness or the stated
  requirements, and treat the rest as optional"
  ([Best practices for Claude Code](https://code.claude.com/docs/en/best-practices), Claude Code docs). Reconciled:
  an independent reviewer of another agent's diff is the endorsed writer-verifier pattern; a worker re-checking or
  self-delegating its own verification is the anti-pattern.
- **Reviewer filters. SOURCED.** "If your review prompt says 'only report high-severity issues' or 'be
  conservative', the model may follow that instruction literally and report less; ask it to report everything and
  filter in a separate pass" ([Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)).
  This conflicts with the Opus 5.5 blog's "List only problems you'd block the merge for" (r1-blog C §2). The two
  combine as: report all, label each finding blocking or not.
- **Subagent overuse. SOURCED.** Opus 4.6+/5 "delegate to subagents more readily"; give guidance on when delegation
  is warranted or cap it (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`)
  ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices);
  [Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)).
  **VERIFIED (repo-E transcripts, 655 subagent runs with meta files):** subagents made **0** Agent/Task calls, so
  worker self-delegation is not happening; all fan-out is the lead's, by design. 26 runs were `general-purpose`
  agents spawned by the lead.
- **Emphasis and "why". SOURCED.** "Where you might have said 'CRITICAL: You MUST use this tool when…', you can use
  more normal prompting"; prefer giving the reason ("NEVER use ellipses" → explain the TTS reason)
  ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).
  Claude Code: emphasise one line only — "If you emphasize many lines, none of them stands out"
  ([Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)).
- **Examples. SOURCED, with a tension.** The general guide still calls 3-5 examples "one of the most reliable ways to
  steer … output format, tone, and structure" ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)),
  while the context-engineering post says examples constrain exploration (r1-blog C §1). Both hold: examples for
  output shape (hand-back format), not for method.
- **Parallel tool calls. SOURCED.** Already high by default and steerable "to ~100 %" with a short instruction
  ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices));
  Fable 5.1 in coding loops may issue implied calls one per turn and needs the nudge per turn
  ([Prompting Claude Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1),
  Anthropic docs).
- **Verbosity. SOURCED.** Opus 5.5 reports "say plainly what it did, what it found, and what it needs from you"
  ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5));
  Opus 5 wrote longer responses and documents and needed explicit length calibration
  ([Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)).
  The harness's line caps are the documented remedy; they need no strengthening.
- **Scope. SOURCED.** Fable 5.1: an instruction to report unrequested fixes as follow-ups instead of doing them made
  "unrequested additions and committed test code drop substantially with no measurable change in task success"
  ([Prompting Claude Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1)).
- **Time signals. SOURCED.** Opus 5.5 "pays close attention to information about elapsed time"; an `elapsed 340s /
  1200s` line per message made small research teams finish sooner at comparable quality; "under time pressure the
  model might search and verify a little less" ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).
- **Haiku specifics. SOURCED.** At `low` in long agent prompts Haiku 5.5 more often skips a search, stops early or
  skips a check; `low`→`medium` roughly halved early stopping and more than doubled output tokens; telling it to
  "answer directly" did not stop thinking; give it today's date when it searches
  ([Prompting Claude Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5),
  Anthropic docs).
- **Safeguards. SOURCED.** Opus 5.5 runs bio, cyber and `reasoning_extraction` classifiers; "Finding vulnerabilities
  in source code is allowed" ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).
  On GPQA, Opus 5.5 refused six biology questions per run against Sonnet 5.5's one
  ([Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence)).
  The harness asks for no reasoning in replies (grep, VERIFIED); keep it so.

## 2. Per-role recommendation

Spend shares are from r1-usage-logs §1 (VERIFIED there). "Thinking" is adaptive and fixed for every role (§1.1); the
only setting is effort, and no role should carry "think" prose. Every row is BELIEVED until its eval runs; the
evidence column says why it is the starting point.

| Role (spend share) | Model | Effort | Evidence | Eval that would confirm it |
|---|---|---|---|---|
| **lead** (20 %) | Opus 5.5 (`opus`); no `opusplan`, no fast mode | `medium` (default), set once at session start; no mid-session `/effort` or `/model` | "Most workloads start with Opus 5.5"; judgement and ambiguity stay on the larger model (r1-blog B); effort changes mid-session risk a cache rewrite of a ~368k context; fast mode is 2× price on usage credits | Low priority. Compare lead tokens per merged PR across ≥10 sessions at medium vs high, sessions assigned by coin flip before they start |
| **builder** (21 %) | Opus 5.5 | `medium` explicit in frontmatter; lead passes `effort: high` for brownfield bugs, security, cross-layer; re-dispatch at `high` after a gate failure or `board block`; two `xhigh` failures → `model: fable` | Opus 5.5 low ≈ Sonnet 5.5 high on coding, so Sonnet saves nothing for code; medium + re-run failures at high ≈ 97 % at $0.24 vs all-high 95.3 % at $0.29; the gate is the failure signal | Frozen set of ≥20 closed repo-E issues (code reverted to the parent of the merge), arms: medium, low, medium→high escalation. Outcome: gate pass plus the merged PR's own tests held out; cost per solved issue; pre-registered, n≥3 trials per issue |
| **tester** (1.1 %) | Opus 5.5 | `high` | Claude Code: `high` for "work where verification matters or edge cases are likely"; higher effort "tested more edge cases and verified more of its work" | Mutation scenarios: ≥10 merged changes with seeded defects; mutants killed per run at medium vs high |
| **reviewer** (1.5 %) | Opus 5.5 | `medium` explicit | Opus 5/5.5 review precision holds at lower effort; Sonnet would save at most ~0.75 % of total spend (1.5 % × 0.5) | Planted-defect replay: ≥20 past PRs with a known later fix re-introduced; recall, false alarms per PR, cost; arms Opus medium, Opus low, Sonnet 5.5 high (a different model also tests self-preference, r1-outside §6) |
| **security-reviewer** (2.6 %) | Opus 5.5 | `high` | Security showed the largest low→top effort gain (64→87 %, r1-blog B §4); Claude Code puts vulnerability hunting at `max`; strict security rules in repo-E; vulnerability finding is allowed under the cyber classifier | Seeded violations of repo-E's own security rules in ≥15 synthetic PRs; recall per rule class at medium, high, xhigh |
| **researcher** (48 %) | Opus 5.5 (its guide: "much less likely to state an incorrect figure or cite the wrong source") | keep `high` until the sweep; never `low` (−13.3 pts on DRACO) | Largest cost line; research effort sensitivity differs by model and benchmark; effort raises tool calls, and cost is context × turns | Highest-value eval: ≥10 past sub-questions with an audited note as gold; arms medium vs high; outcome = claims confirmed by research-auditor, cite-check problems, tokens per run |
| **research-auditor** (4 %) | Opus 5.5 | `high` (keep) | Adversarial checking is edge-case work; mis-accepting a claim propagates to design | Notes with planted overstated or unsourced claims; detection rate at medium vs high |
| **architect** (rare) | Opus 5.5 | `high` | Effort "does not fix a wrong approach" (r1-blog B §4), so approach-heavy work gets the larger model and more effort; Fable 5.1 for "problems with no existing pattern" is the escalation, not the default (1.9× cost) | Blind owner ranking of proposals from two arms (Opus high vs Fable 5.1 high) on one frozen repository snapshot; too few runs for statistics, so treat as judgement |
| **suite-auditor** (rare) | Opus 5.5; Sonnet 5.5 as the trial arm | `medium` | Bounded, read-only, checkable output (named missed defect); low volume, so savings are small | Tests with planted vacuous oracles; precision and recall, Opus medium vs Sonnet high |
| **lookup** (new, read-only) | **Haiku 5.5** | `medium` (not `low`: early stops and skipped searches) | Haiku for "sub-agent tasks"; "Move down … for lookups, not for writing code" (r1-blog B §3); 0.037× Opus price below 100k; GPQA 85 % vs 91 % | ≥30 "where is X / why did check fail" questions on repo-E with known answers; accuracy and parent-side tokens vs the built-in Explore on Opus; adopt only if the parent's detour cost does not erase the saving |

Lookup agent frontmatter (BELIEVED): `model: haiku`, `effort: medium`, `tools: Read, Grep, Glob, Bash`,
`omitClaudeMd: true`, `autoCompactWindow: 100000`, `maxTurns` small. Alternatively a project agent named `Explore`
with `model: haiku`, which overrides the built-in ([Create custom subagents](https://code.claude.com/docs/en/sub-agents)).

Prerequisite for every eval: `devkit/evals.py` has no model or effort override (r1-blog B, VERIFIED there). Add
`--model` and `--effort` passthrough to the `claude -p` call (Claude Code accepts `--effort` per session, per
[Model configuration](https://code.claude.com/docs/en/model-config)) and record cost per solved task, priced per
category, as the optimizing page prescribes. Never set `CLAUDE_CODE_EFFORT_LEVEL` in settings: it would override
every per-agent `effort`.

## 3. Audit of the harness text against the guidance

Files: `plugins/harness-ops/agents/*.md`, `plugins/harness-ops/skills/*/SKILL.md`, `template/.devkit/HARNESS.md.jinja`
(agent-harness worktree at #254), and repo-E `.claude/agents/security-reviewer.md`. Ordered by expected impact.

| # | Quote (file:line) | Guidance | Fix |
|---|---|---|---|
| A1 | builder.md, tester.md, reviewer.md, architect.md, suite-auditor.md, security-reviewer.md frontmatter: `model: opus` with no `effort:` | "Set effort explicitly" (Effort, best practices #1); Opus 5.5 guide: "set it explicitly"; session effort flows to these agents (§1.2) | Add `effort:` per the §2 table (builder medium, tester high, reviewer medium, security-reviewer high, architect high, suite-auditor medium) |
| A2 | lead/SKILL.md:39 "Prompt `Work on #N. Start with uv run dev board brief N.`, plus only what the repository cannot know" | Re-run failures at higher effort is the cheapest point on the curve; per-invocation `effort`/`model` exist (v2.1.292+) | One line in §4 Dispatch: effort by issue kind (low mechanical, medium default, high bug/security/cross-layer); a gate failure or block re-dispatches at `high`; two `xhigh` failures → `model: fable` |
| A3 | tester.md:19 "finish with `uv run dev check` and `uv run dev pr …`" and researcher.md:35 (issue mode PR) — no end-of-turn check; only `matcher: "builder$"` in hooks.json SubagentStop | Opus 5.5 unattended runs end turns with progress reports; a separate end-of-turn check with 2-3 continuations is the documented fix; custom subagents get no Claude Code system prompt (§1.2) | Extend `completion_gate.py` matcher to `tester$` and issue-mode researchers (same CAP 2 / TOTAL 6). Prefer the hook over adding the 170-word stop paragraph to every body (word budgets), then measure |
| A4 | reviewer.md:21 "findings ranked by severity, each with file:line, the problem and the fix"; security-reviewer.md:40 same shape | Report everything, filter separately (Opus 5 guide); reviewers find gaps even in sound work, so mark the rest optional (Claude Code best practices) | Add "mark each finding blocking or non-blocking; only blocking ones send back". Do not add "only report high severity" |
| A5 | researcher.md:7 `effort: high` on the role that is 48 % of spend | "Reserve higher levels for measured gains"; Opus 5.5 medium ≈ Opus 5 high | Keep until the medium-vs-high sweep (§2) runs; then set from data |
| A6 | builder.md:25 "Edits outside Owns: make one when the issue needs it end to end or it is a fix-now" | Fable 5.1 measured fewer unrequested additions with no loss when told to report, not fix; Opus 5 widens scope | Not a defect: the fix-now rule is deliberate (multi-agent.md §3). Measure out-of-Owns edits per PR and conflicts they cause before changing it |
| A7 | builder.md:24 "Never edit generated files. Never read `.env`." repeats HARNESS.md.jinja:34-35; hooks enforce both | "Add context" over bare NEVER; one home per rule (r1-blog C, C1/C2) | Delete from builder.md (hook + HARNESS already carry it). Overlaps r2-surface-audit |
| A8 | "ONE" in caps once per agent (builder:11, tester:11, reviewer:9, researcher:12, research-auditor:10, suite-auditor:10, security-reviewer:9, research/SKILL.md:47) | Emphasis works on one line; aggressive language overtriggers on 4.5+ | Compliant (one emphasized word per file). Leave it |
| A9 | HARNESS.md.jinja:33 "Batch independent reads and searches as parallel tool calls in one response." | Parallel calls steerable to ~100 % | Compliant. Note r1-usage-logs: agents batch with `&&` in one Bash call (96 % compound) rather than parallel tool calls, which the guidance does not cover |
| A10 | No "think", "step by step", "double-check", "verify twice" or "explain your reasoning" anywhere (grep, VERIFIED) | Thinking prose is wording-sensitive; reasoning-in-reply invites `reasoning_extraction` declines | Compliant. Add a harness lint that keeps it so (cheap) |
| A11 | reviewer.md:15 "Do the numbers in the PR body match … Re-run the key one." | Writer-verifier re-checking another agent's evidence is endorsed; self double-checks are not | Compliant |
| A12 | builder.md:15 "stop and ask the lead for a researcher. Do not guess."; HARNESS.md.jinja:39 "add it to OPEN_QUESTIONS … then proceed on that default" | Name the stops you want; state assumptions and keep building | Compliant |
| A13 | No elapsed-time signal anywhere | Opus 5.5 paces to a time budget and parallelises sooner | Optional experiment: `dev status` prints elapsed time per worker for the lead. Low priority; may cut verification |

## 4. Gaps seen and not closed
- The effort a past run actually used is not in the transcripts' meta files I read, so the per-role effort of the
  644 repo-E runs is inferred from settings (§1.2), not observed.
- No measured early-stop rate for repo-E workers: hook decisions do not appear in subagent transcripts (the only
  "Not finished" strings found were reads of the script's source), so A3's size is unknown.
- How subscription usage windows weight Opus, Sonnet and Haiku is undocumented beyond separate per-family limit
  messages; savings in §1.3 are list-price ratios, not usage-window ratios.
- Claude Code advisor page (`code.claude.com/docs/en/advisor`) and the Sonnet 5.5 "Verification on coding tasks"
  section were not re-read; the Sonnet guide is covered in r1-blog B.
- All Anthropic numbers are vendor measurements on public or internal benchmarks (2-3 runs pooled, some single
  runs); per the owner's model-dated-evidence rule, none transfers without the §2 evals.

## 5. Sources fetched (2026-10-19)
- [Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5) — Anthropic docs, full page.
- [Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5) — Anthropic docs, full page.
- [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) — Anthropic docs, general principles, tool use, thinking, agentic systems, migration.
- [Prompting Claude Haiku 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5) — Anthropic docs, full page.
- [Prompting Claude Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1) — Anthropic docs, effort, batching, finishing, scope, subagents sections.
- [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) — Anthropic docs, levels, per-model recommendations, tool use, best practices.
- [Steering thinking](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost) — Anthropic docs, steering and cost control.
- [Choosing the right model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model) — Anthropic docs, full page.
- [Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence) — Anthropic docs, prompt audit, model comparison, effort, re-run policy, combining models, measurement.
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing) — Anthropic docs, model price table and cache multipliers; also the Opus 5.5, Sonnet 5.5, Haiku 5.5 and Fable 5.1 model overview pages.
- [Model configuration](https://code.claude.com/docs/en/model-config) — Claude Code docs, aliases, opusplan, effort, thinking, Haiku context pricing.
- [Create custom subagents](https://code.claude.com/docs/en/sub-agents) — Claude Code docs, frontmatter, model and effort order, what loads at startup.
- [Speed up responses with fast mode](https://code.claude.com/docs/en/fast-mode) — Claude Code docs.
- [Manage costs effectively](https://code.claude.com/docs/en/costs) — Claude Code docs, model choice and limits.
- [Best practices for Claude Code](https://code.claude.com/docs/en/best-practices) — Claude Code docs, CLAUDE.md, verification, adversarial review.
- context7 `/websites/code_claude`: skill frontmatter `effort`/`model`, `maxEffortLevel`, `modelSettings`.
- Local: harness-ops agents, skills, hooks.json and completion_gate.py in this worktree; repo-E security-reviewer.md,
  `.claude/settings.json`, `~/.claude/settings.json`; repo-E subagent transcripts (Agent-call count).
