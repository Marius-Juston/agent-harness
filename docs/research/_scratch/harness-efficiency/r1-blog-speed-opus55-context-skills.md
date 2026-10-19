# Blog review C: four claude.dev posts for agent-harness / repo-E

Fetched 2026-10-19. WebFetch would only summarise, so the full text of every post was pulled with `curl` and stripped
to text (raw copies: `scratchpad/blog/raw/*.txt`, followed links: `scratchpad/blog/rawC/`). All four posts were read
in full. Followed links: "A harness for every task: dynamic workflows" (full), "A field guide to Claude Fable 5"
(full), "Getting started with loops" (full), ThariqS skill-logging gist (full), the Claude Code skills doc
(frontmatter table + lifecycle sections), and the anthropics/skills frontend-design SKILL.md (head).
**Not fetched:** "How the Claude Code team does code review" (claude.com/blog/code-review), "test impact analysis at
Anthropic", "three harness design patterns", "guide to steering Claude Code", the `/doctor` (claude doctor) docs
page, the Skilljar course, the plugin-marketplace and persistent-data-directory doc pages, the Layout Instability
spec, and the X post.

Harness state checked for grounding (read-only): agent-harness v0.1.9 `plugins/harness-ops/` has 7 agents
(264–661 words each), 3 skills (586–721 words), each skill a single SKILL.md with no supporting files; hooks:
SessionStart brief, PreToolUse guards (bash, uv, read, edit, spawn), SubagentStop completion gate on builder,
PostToolUse lint, WorktreeCreate. No PreToolUse hook on `Skill`. Builder prompt has almost no emphatic caps
("ONE" only) and already says "Match the surrounding code's naming, style and comment density". repo-E
CLAUDE.md + HARNESS.md = 600 words (= the `context = 600` budget in `.devkit/ratchets.toml`);
`docs/specs/multi-agent.md` = 1,701 words.

---

## 1. "The new rules of context engineering for Claude 5 generation models" (Thariq Shihipar, Jul 24 2026, 7 min)

**Thesis.** Context (system prompt, skills, CLAUDE.md, memory) applies across many requests, so it must be general.
With Claude 5 models Anthropic was *overconstraining* Claude: "We removed over 80% of Claude Code's system prompt for
models like Claude Opus 5 and Claude Fable 5 with no measurable loss on our coding evaluations."

**Diagnosis ("Unhobbling Claude").**
- Transcripts showed conflicting messages in one request: "leave documentation as appropriate" vs "DO NOT add
  comments", as "system prompt, skills, and user requests clash with each other". Claude usually resolves intent, "but
  Claude must think more carefully about these overlapping and conflicting messages before deciding what to do"
  (i.e., conflicts cost reasoning tokens and accuracy).
- Constraints "were once needed to avoid worst case scenarios"; "we can delete many of them and let the model use
  surrounding context and judgement instead."
- CLAUDE.md used to be the only memory; now memory, artifacts and skills share that job.

**Six "then → now" shifts (each a concrete technique).**
1. *Rules → judgement.* Old system prompt: "In code: default to writing no comments. Never write multi-paragraph
   docstrings ... Don't create planning, decision, or analysis documents unless the user asks". New: "Write code that
   reads like the surrounding code: match its comment density, naming, and idiom." Pattern: replace absolute rules with
   a reference to local context the model can read.
2. *Examples → interface design.* "giving examples actually constrains them to a certain exploration space." Instead
   make parameters expressive. TodoWrite: a status enum (`pending`, `in_progress`, `completed`) plus one rule (one item
   `in_progress`) replaces a long example-heavy description (the WebFetch summary put the old description at ~9,100
   characters; the raw text I extracted does not contain that number, so treat it as unconfirmed).
3. *Upfront → progressive disclosure.* Code review and verification moved out of the system prompt into skills;
   tools use "deferred loading" via ToolSearch. "A common myth is that you want to make these [CLAUDE.md, Skill.md] a
   central repository for every known practice ... Instead, consider having a tree of files that can be loaded at the
   right time."
4. *Repeat yourself → simple tool descriptions.* Older models needed repetition / end-of-context placement; now put
   tool usage in the tool description only, not duplicated in the system prompt.
5. *Memory in CLAUDE.md → auto-memory.* The `#` hotkey workflow is superseded; Claude saves memories itself.
6. *Simple specs → rich references.* HTML artifacts, test suites, code in other codebases, and **rubrics** given to
   verifier agents via dynamic workflows ("what does a good API design look like").

**Applying it.** System prompt = product context (spend effort there only if you build your own harness).
**CLAUDE.md:** "Keep your CLAUDE.md lightweight and briefly describe what your repo is for, but spend most of the
tokens on gotchas inside of the codebase ... Avoid stating 'the obvious' things Claude should know by looking at your
file system or your repo." "Use progressive disclosure heavily ... create a verification skill and reference it from
your CLAUDE.md." **Skills:** "lightweight guides"; "Avoid making them overconstrained, except in highly important
areas"; split long skills into many files; best when they encode opinions specific to you/team/product.
**References:** @-mention; prefer code ("a HTML mockup ... will generally produce better results than a description
of the design or a screenshot"). **Tooling:** `claude doctor` / `/doctor` "to rightsize your skills, and CLAUDE.md
files" (docs not fetched).

**New vs common knowledge.** New: the 80%-cut-with-no-loss number; examples now *hurt* exploration; repetition no
longer needed; rubric-as-reference; /doctor rightsizing. Common: progressive disclosure, keep CLAUDE.md short.

**Implications for agent-harness / repo-E.**
- **C1 TOKENS+EFFECTIVENESS — conflict audit across all layers.** Grep HARNESS.md, CLAUDE.md, agent bodies, skills
  and `multi-agent.md` for rules that say the same thing twice or contradict (e.g. "Hand-backs at most 25 lines" in
  HARNESS.md *and* every agent's final line; "Never edit generated files"/"Never read .env" in HARNESS.md *and*
  builder.md, which already has hook enforcement). Rule: one home per rule; if a hook enforces it, the prose can
  shrink to a pointer or disappear. Expected: 10–20% fewer words in always-loaded text. Measure: ratchet word counts,
  `uv run dev agent-stats` tokens/issue, plus gate pass rate and rework rate unchanged over ~20 issues.
- **C2 TOKENS — delete what hooks enforce.** The post's logic: constraints exist for worst cases; Claude Code keeps
  hard guards in tools, not prose. uv-only, `.env`, generated files, whole-file reads are already hook-guarded; the
  prose only needs to exist if the hook's denial message is unclear. Make the denial messages self-explanatory and
  drop the prose. Measure: hook denial counts per session (should not rise) via transcripts.
- **C3 EFFECTIVENESS — CLAUDE.md content mix: gotchas over obvious.** repo-E CLAUDE.md "Project facts" is good; add a
  short codebase-gotchas list (things a worker would get wrong reading the tree, e.g. where model calls must go —
  already there — and any non-obvious module/test conventions learned from review comments). Source candidates by
  mining reviewer/lead corrections (see C6). Keep within the 600-word budget by trading out obvious lines.
- **C4 EFFECTIVENESS — replace examples with interface design in `uv run dev`.** Where skills/agents carry example
  command lines, prefer making `--help` and argument enums expressive and say "see `uv run dev X --help`". Measure:
  wrong-flag/usage errors in transcripts (grep for argparse "usage:" errors) before/after.
- **C5 TOKENS — progressive disclosure for the 3 skills and multi-agent.md.** Skills are single 586–721-word files;
  split into a ~200-word hub SKILL.md plus `references/*.md` read on demand (e.g. lead: integrate procedure,
  resume-after-limit, dispatch templates). `multi-agent.md` (1,701 words) is linked rather than loaded, which is
  right; check that agents read only the section they need (point to §anchors). Measure: tokens per lead session;
  skill words loaded (ratchet `skill` key counts body and supporting files separately already).
- **C6 EFFECTIVENESS — rubric references for reviewer.** Give `reviewer`/`security-reviewer` an explicit rubric file
  (what blocks merge) rather than prose procedure; rubric = reference, verified by a fresh agent. Measure: reviewer
  findings later confirmed vs dismissed by the lead.
- **C7 — run `/doctor` on both repos** and record its suggestions as issue(s); cheap, and it encodes Anthropic's own
  rightsizing heuristics. (ASSUMED behaviour: docs not fetched.)

---

## 2. "Getting the most out of Opus 5.5 in Claude and Claude Code" (9 min)

**Thesis.** Opus 5.5 "works for longer on its own, it tells you plainly what it did, and it thinks before every
reply." Hand over whole tasks with a finish line, stop telling it to think, and steer stops explicitly.

**Techniques and numbers (exhaustive).**
- **Try first:** whole task + definition of done + when to stop/ask; delete "think carefully" lines; on a finished
  long run read "what it needs from you first".
- **Say what done looks like.** "its biggest gains are on multi-step work, like carrying a change through a large
  repository until the tests pass. Early testers had it run long coding tasks for hours with little oversight."
  Template: "Migrate the payment endpoints ... Done means: every endpoint uses the new client, the old client is
  deleted, and the test suite passes. Stop and ask me only if a test fails for a reason you can't explain."
- **Stop telling it to "think hard".** "Opus 5.5 always thinks before it replies, and it decides how much ... In our
  testing in a chat product, removing a 'think carefully' line made replies start sooner, with no clear drop in
  quality." For quick answers say "Answer directly"; in Claude Code change *effort* instead.
- **Mid-run follow-ups** (type + Enter while working): "Runs are longer now, so a restart costs more."
- **Design: name the styles you don't want** — "a general instruction like 'avoid a generic look' mostly swaps one
  default for another. A list of specific patterns works much better." (cream background, italic accent words,
  "01 / 02 / 03" labels, monospace labels, pill buttons). Iterate the exclusion list.
- **Stop rules in CLAUDE.md.** Opus 5.5 sometimes stops to report "a summary that names the next step without taking
  it, an offer to continue, or a list of choices that don't block the work. It follows instructions that name these
  stops." Template: "When a step doesn't need my input, keep going. Put status notes in the same message as your next
  action. Stop and ask only when you can't continue without me, or before anything destructive: deleting data,
  force-pushing, or changing anything outside this repository." Keep permission prompts on for destructive commands.
  For pair programming the opposite rule (one-line plan, short recap) works too: "Opus 5.5 follows either one."
- **Subagents for big work:** "Give each service to its own subagent. When a subagent reports back, check its
  evidence before you accept it. Finish with one table: service, affected yes or no, and the evidence."
- **Task list in a file:** "A long run fills the context window, and Claude Code then summarizes older turns. A list in
  a file survives that." Prompt: "Keep a checklist in TASKS.md. Tick each item when it's done, and add anything new".
- **Report format:** e.g. CLAUDE.md "End every run with three headings: Blocked on me, Changed, Found."
- **Review pass before a human:** "One early tester said Opus 5.5 at its lowest effort caught more bugs than Opus 5 at
  high effort, with fewer false alarms." Prompt: "Review the diff on this branch against main. List only problems
  you'd block the merge for. For each one, give the file and line, why it's wrong, and how to show it fails."
- **Mark what it couldn't confirm:** "Mark anything you couldn't confirm, and say where you looked".
- **Apps:** attach images not retyped numbers (better spatial reading than Opus 5); ask for contradiction checks
  ("Quote each problem and say where it is"); ask for the finished file; in projects, a "treat earlier answers as
  settled" instruction cuts re-litigation latency — but omit it for long analyses.
- **Flagged messages:** first Opus with "Fable-level bio and cyber safeguards"; flagged messages move to an older
  model ("Switched to" notice; `/model` to switch back, Esc Esc to edit/retry, `/config` "Switch models when a message
  is flagged", `/feedback`). The check covers the whole conversation incl. files and search results. **Don't ask it to
  reproduce its internal reasoning in the reply** — that is a flag category; ask "Explain why you chose this approach
  in three sentences."
- **Speed:** `/fast` = same model, faster output, extra usage, higher per-token price.

**New vs common.** New: Opus 5.5-specific stop behaviour and the canonical stop-rule wording; "think" lines are
pure cost; reasoning-reproduction requests can trigger a model switch; low-effort Opus 5.5 review > high-effort
Opus 5 (anecdotal, one tester). Common: definition of done, task file, review pass.

**Implications.**
- **O1 TOKENS — purge "think" phrasing and reasoning-echo requests.** grep agents/skills/HARNESS for "think
  carefully/step by step/hard", "show your reasoning", "explain your thinking". (Quick grep found no emphatic caps or
  "think hard" in harness-ops; check repo-E prompts in `repo-E's model wrapper` / examiner prompts too — a request to reveal
  reasoning in a *product* prompt could cause model switches/flags.) Measure: count = 0, gate test for it.
- **O2 EFFECTIVENESS — adopt the stop rule for workers.** Builders/testers run autonomously; premature "want me to
  continue?" hand-backs waste a full re-spawn (cold context). Add the 2-sentence keep-going/stop-only-when rule to the
  shared worker preamble (one place), with the destructive list mapped to this repo (force-push, `integrate`, editing
  outside Owns without listing, anything touching the lab GPU server). Measure: fraction of builder hand-backs with
  unmet DONE WHEN and no `board block` reason (premature stops), via `agent-stats`/PR `--partial` rate.
- **O3 EFFECTIVENESS — hand-back format = "Blocked on me / Changed / Found".** The 25-line hand-back is already
  structured; reorder so the lead's needed decisions come first. Lead reads only that section unless it needs more.
  Measure: lead tokens per integrate cycle.
- **O4 EFFECTIVENESS — reviewer prompt = blocking-only, file+line+why+repro.** Align `harness-ops:reviewer` output
  contract with that exact prompt; it lowers false alarms and lead triage. Consider `effort: low/medium` for reviewer
  on small diffs given the quoted tester result (BELIEVED; verify with a pre-registered comparison on past PRs with
  known bugs, per the owner's pre-registered-evals rule).
- **O5 EFFECTIVENESS — "mark what you couldn't confirm, and where you looked"** in researcher and research-auditor
  hand-backs (fits the VERIFIED/SOURCED/BELIEVED/ASSUMED labels). Likely already partly present; make it the closing
  line of the note template.
- **O6 TOKENS — task file per long worker run.** For builders on multi-item issues, keep a checklist file in
  `.scratch/` (or tick DONE WHEN items in the issue) so post-compaction drift is avoided without re-reading
  scrollback. The board/issue already plays this role for the lead; HANDOFF is the lead's TASKS.md.
- **O7 TOKENS — use effort, not words.** Set per-agent `effort` in frontmatter (researcher high, tester/reviewer
  medium, lead-trivial ops low) instead of prose urging care. Measure with agent-stats tokens per issue type.

---

## 3. "Lessons from building Claude Code: How we use skills" (Thariq Shihipar, Jun 3 2026, 11 min)

**Thesis.** Skills are folders (instructions + scripts + assets + data + config + on-demand hooks), not markdown
files. Anthropic has "hundreds of them in active use"; the best fit cleanly into one of nine categories.

**Nine categories (with examples).** 1 Library/API reference (billing-lib, internal-platform-cli, sandbox-proxy);
2 Product verification (signup-flow-driver, checkout-verifier, tmux-cli-driver) — "Verification skills have had the
most measurable impact on Claude's output quality internally. It can be worth having an engineer spend a week just
making your verification skills excellent." Techniques: record a video of what it tested; "enforcing programmatic
assertions on state at each step"; 3 Data fetching/analysis (funnel-query, cohort-compare, grafana, datadog);
4 Business process automation (standup-post "delta-only", create-ticket enforcing schema, weekly-recap; "saving
previous results in log files can help the model stay consistent"); 5 Scaffolding/templates (new-migration,
create-app); 6 Code quality/review (adversarial-review: "spawns a fresh-eyes subagent to critique, implements fixes,
iterates until findings degrade to nitpicks"; code-style for what Claude does badly by default; testing-practices;
can run from hooks or a GitHub Action); 7 CI/CD (babysit-pr, deploy-<service> with auto-rollback, cherry-pick-prod in
an isolated worktree); 8 Runbooks (symptom → tools → structured report); 9 Infra ops (orphans with soak period + user
confirm, dependency-management approval workflow, cost-investigation). "the ones that try to do too much straddle
several and confuse the agent."

**Writing tips.**
- "Don't state the obvious" — "A skill that restates what Claude would do by default adds context without adding
  value"; focus on what "pushes Claude out of its normal way of thinking" (frontend-design skill: avoid Inter, purple
  gradients).
- **Gotchas section = "The highest-signal content in any skill"**, grown from observed failures; examples: append-only
  `subscriptions` table (use highest version, not latest `created_at`); `@request_id` vs `trace_id` same value; staging
  returns 200 even if webhook failed — check `payment_events`.
- **File system as progressive disclosure:** "Tell Claude what files are in your skill, and it will read them at
  appropriate times." `references/api.md`, `assets/` templates, folders of scripts/examples.
- **Avoid railroading:** "State the goal and the constraints, not every step."
- **Setup via `config.json`** in the skill dir; ask the user if missing; AskUserQuestion for multiple choice.
- **Descriptions are for the model:** "the description field is not a summary, it's a description of when to trigger
  this skill"; include trigger words ("babysit").
- **Memory inside skills:** append-only log, JSON, SQLite; `${CLAUDE_PLUGIN_DATA}` for a stable data dir.
- **Ship scripts/libraries:** "lets Claude spend its turns on composition ... rather than reconstructing
  boilerplate"; gotchas in docstrings; Claude composes one-off scripts.
- **On-demand hooks:** registered when the skill is invoked, live for the session: `/careful` (PreToolUse on Bash
  blocks `rm -rf`, `DROP TABLE`, force-push, `kubectl delete` — "having it always on would drive you insane"),
  `/freeze` (blocks Edit/Write outside one directory).
- **Distribution:** repo `.claude/skills` for small teams, but "every skill that is checked in also adds a little bit
  to the context"; plugin marketplace at scale; no central curation (sandbox folder → traction → PR).
- **Composition:** reference other skills by name; no native dependency management.
- **Measurement:** a PreToolUse hook with `matcher: "Skill"` logging `date user skill args` to
  `~/.claude/skill-usage.log` (gist fetched) to find popular vs **undertriggering** skills.
- "Most of our best skills began as a few lines and a single gotcha".

**From the skills doc (followed).** description + `when_to_use` truncated at **1,536 chars** in the listing; listing
budget is **1% of the context window**, overflow drops descriptions of least-invoked skills first; after
auto-compaction each invoked skill is re-attached keeping **first 5,000 tokens**, all skills share **25,000 tokens**,
most recent first ("put the most important instructions near the top of SKILL.md"); skill body stays in context
across turns, "so every line is a recurring token cost. State what to do rather than narrating how or why";
frontmatter: `disable-model-invocation`, `user-invocable`, `allowed-tools`, `disallowed-tools`, `model`, `effort`,
`context: fork` + `agent` + `background`, `hooks`, `paths` (glob-scoped auto-load), `arguments`.

**New vs common.** New: verification skills ranked highest-impact; on-demand session hooks; skill-usage hook for
undertriggering; config.json setup; skill-local memory. Common: concise, progressive disclosure, good descriptions.

**Implications.**
- **S1 EFFECTIVENESS (highest expected) — a repo-E verification skill.** No product-verification skill exists. Build
  one (e.g. `verify-student-flow`): scripts that drive the examiner/assistant end-to-end with synthetic users and
  assert state (safety filters fired, no PII stored, model calls via `repo-E's model wrapper`), recording transcripts as
  evidence. Reference it from CLAUDE.md in one line. Measure: post-merge defect issues per merged PR; tester-found
  bugs that the builder's own run should have caught.
- **S2 EFFECTIVENESS — Gotchas sections in each harness-ops skill and agent,** seeded from recurring
  reviewer/lead corrections and `integrate` failures (mine HANDOFF history + PR review comments). Keep each ≤ 8 bullets.
  Measure: recurrence of the same correction across PRs.
- **S3 TOKENS+EFFECTIVENESS — skill-usage logging.** Add the gist's PreToolUse `Skill` matcher hook to the template
  (or fold into `agent-stats`, which already parses transcripts: count `Skill` tool_use by name). Find undertriggering
  (e.g. `harness-ops:research` not used before design choices) and unused skills to remove (each costs listing
  tokens). Measure: invocations/session per skill.
- **S4 TOKENS — trim the skill listing.** repo-E already disables 8 anthropic-skills via `skillOverrides`; the session
  listing still carries ~20 skills (dataviz, artifact-*, schedule, loop, claude-api…). Turn off those never used in
  repo-E work (data from S3). Set `disable-model-invocation: true` on manual-only skills (e.g. repo-E `lead` add-on is
  invoked with harness-ops:lead) and `user-invocable: false` on background ones. Measure: listing size in system
  prompt (agent-stats first-turn tokens).
- **S5 TOKENS — `paths:`-scoped skills/rules.** Research-note house style only matters under `docs/research/`; make
  that guidance a `paths: docs/research/**` rule or skill rather than global text. Measure: always-loaded words.
- **S6 EFFECTIVENESS — on-demand hooks.** A `/freeze`-style hook (Edit/Write restricted to the issue's Owns) could be
  registered by the builder agent frontmatter `hooks:` instead of prose "stay inside your Owns", with an allow-list
  from `board brief`. Measure: out-of-Owns edits flagged at review.
- **S7 TOKENS — order skills for compaction.** Put the action-critical part of each SKILL.md first (the doc: only the
  first 5,000 tokens survive). Current skills are <1,000 words, so fine; keep the 650 budget.
- **S8 EFFECTIVENESS — descriptions as triggers.** Rewrite harness-ops skill descriptions to lead with trigger
  phrases (the lead skill already does: "continue", "/goal", "dispatch", "integrate"); check researcher/research skill
  descriptions say *when* (before any design choice) first.
- **S9 TOKENS — scripts over prose.** Any multi-step procedure in lead/research skills that is deterministic (e.g.
  integrate checklist, cite-check sequence) belongs in `uv run dev` (already the pattern); keep pushing prose into CLI.

---

## 4. "How we made claude.ai 3x faster in two weeks" (15 min)

**Thesis.** "With Claude, measuring something makes it tractable." Turn every concern into a deterministic number,
make it a CI ratchet, and let many narrowly scoped Claude threads hill-climb under human steering and guardrails.

**Numbers.** ~3x faster (13 measurements over 4 journeys = 95% of activity). p75: claude.ai fresh load to typeable
3.1 s → 0.55 s; Claude Code session start 0.8 → 0.3 s; Cowork cloud session 2.6 → 0.73 s. "more than three thousand
changes without a single customer-facing incident or rollback". Model: internal research model ≈ Opus 5.5 via Claude
Tag in one Slack channel. ~20 hand-picked projects, Claude estimated impact in ms; "We hit twelve of the thirteen
targets by day three." 150+ concurrent threads; one thread 50–100 PRs; >200 changes on busiest days; "about a third of
PRs included additional telemetry or guardrails"; ~200 flags, more than half retired within two weeks; sidebar
re-renders −90%; instruction counts −48%/−31% → wall clock −78%/−44% (validation that the proxy tracks the clock);
layout-shift test red 20/20 on main, green 20/20 on PR; 31% of loads shifted after usable; 6,900 hooks + 900 store
subscriptions on the typing path; one `:root:has()` selector +24 ms per DOM change; a stray `location.reload()` = half a
million hidden reloads/day; em dashes forcing UTF-16 regex paths (20-line fix); streaming main-thread blocking 750 → 200
ms, ~1/3 CPU, 120 fps on 8.33 ms budget with deterministic DevTools begin-frame control (240/240 frames); ~60 PRs in
that thread; 14-viewport ±1 px alignment test.

**Techniques.**
- **Standing instructions** for the channel (role, responsibilities incl. monitoring deploys, telemetry accuracy,
  dashboards, proposing projects; "The ultimate goal ... is for you to become as autonomous as possible, but today we
  know that isn't yet possible.")
- **Deterministic lab metrics** (Valgrind `Ir` with `node --predictable` against a checked-in baseline: "one run, no
  statistics needed"; React commits, V8 precise-coverage call counts, style recalcs, DOM mutations).
- **Every benchmark has two jobs:** a metric Claude can move, and a CI ratchet "that could only ratchet down"; plus "a
  daily job lowered each ceiling whenever the count went down". **Proxy validation:** "please prove that hill
  climbing against each of these can result in measurable wall clock perf wins. we'll unship the benches for any
  candidates that cannot prove that." Flaky or uncorrelated benches thrown out.
- **The loop:** thread on a slow spot → Claude traces + builds benchmark → PRs "sized for risk and review", user-visible
  behind a flag → watch deploy, read field data → ratchet or flag off → next slow spot.
- **Guardrails:** automated review + ≥1 human approval per PR; "unit tests always came before optimizations";
  short-lived flags classified kill-switch vs ramp, retired ASAP; staged rollout (employees → 1% → all); drift tests.
- **Steering = ambition, taste, direction.** "By default, Claude is careful about scope. It tickets findings, hedges on
  feasibility, and pads its estimates" → "please be braver"; "the targets are not the stopping point". Named human
  owner per thread; before/after recordings for taste calls. "We kept each thread deliberately narrow, focused on one
  benchmark or journey"; humans handled sequencing, merging overlapping threads, closing threads at diminishing
  returns; rejecting complexity ("2ms per send is not worth the complexity").
- Links: Claude Tag; standing-instructions docs; getting-started-with-loops (summarised below); code review post and
  test-impact-analysis post (not fetched).

**Followed: "Getting started with loops".** Four loop types: turn-based (improve with verification skills), goal-based
`/goal` (evaluator model checks the condition each time Claude tries to stop; "deterministic criteria ... are so
effective"; cap turns: "stop after 5 tries"), time-based `/loop`/`/schedule`, proactive (routines; route to smaller
models, capable model for judgement). Quality: clean codebase, self-verification skills, reachable docs, second-agent
review with fresh context; "When an individual result doesn't meet the standard, don't stop at fixing the individual
issue, try to encode it to improve the system". Tokens: right primitive/model, clear stop criteria, pilot before large
runs, scripts for deterministic work, intervals matched to change rate, review usage via `/usage` (by skills,
subagents, MCPs), `/goal` (turns + tokens), `/workflows`; "model and effort level choices are among the biggest
levers on what a loop costs."

**Followed: "A harness for every task: dynamic workflows".** Workflows = JS with `agent()`, `parallel()`,
`pipeline()`; per-agent model and worktree choice; resumable. Motivation: single-context failure modes — "Agentic
laziness" (e.g. "addressing 35 of the 50 items"), "Self-preferential bias", "Goal drift ... especially after
compaction. Each summarization step is lossy, and details like ... 'don't do X' constraints can get lost." Patterns:
classify-and-act, fan-out-and-synthesize, adversarial verification, generate-and-filter, tournament (pairwise:
"comparative judgment is more reliable than absolute scoring"), loop-until-done. Use cases incl. **rule adherence:
one verifier per CLAUDE.md rule in clean context + a skeptic subagent**, and the reverse: "mine your recent sessions
and code review comments for corrections you keep making, cluster them ..., adversarially verify each candidate
(would this rule have prevented a real mistake?), and then distill the survivors back into a CLAUDE.md"; quarantine
pattern (agents reading untrusted content have no privileges); model routing classifier. Costs: "may end up using
significantly more tokens"; "most traditional coding tasks do not need a panel of 5 reviewers"; token budgets by
prompt ("use 10k tokens"); trigger word "ultracode"; save workflows to `~/.claude/workflows` or ship in a skill as a
template.

**Followed: "Field guide to Claude Fable 5".** Quality is "bottlenecked by my ability to clarify its unknowns";
techniques: blind-spot pass, brainstorm/prototype (HTML), interview "one question at a time ... prioritize questions
where my answer would change the architecture", code references, implementation plan leading with likely-to-change
decisions, `implementation-notes.md` with a 'Deviations' section ("pick the conservative option, log it ... and keep
going"), pitches, and quizzes before merge.

**New vs common.** New: proxy-metric validation before ratcheting; daily auto-lowering of ceilings; "be braver"
steering of Claude's default scope-caution; 150 narrow threads with a named human owner each. Common: flags, staged
rollout, CI ratchets.

**Implications.**
- **P1 EFFECTIVENESS — validate harness proxies like they validated benches.** The harness ratchets word counts on
  the assumption that fewer words = same or better results. Pre-register a check that the proxy tracks the outcome:
  e.g. compare gate first-pass rate / review-blocking findings per PR before vs after a word cut. Drop or re-weight
  ratchets that don't correlate. Measure: correlation over the last N PRs from agent-stats + integrate logs.
- **P2 TOKENS — auto-lowering ceilings.** Ratchets "only go down" but are lowered manually; add a `dev` step (or
  `docs` regen) that proposes the new lower ceiling when a count drops (like their daily job). Cheap, prevents slack
  re-growth.
- **P3 EFFECTIVENESS — narrow threads, named owner, "be braver".** Workers already map to one issue each; issue DONE
  WHEN items should name a measurable target *and* that targets are floors, where appropriate ("keep going past the
  target while the ratchet still moves"). Lead prompts can counter Claude's default hedging/ticketing (it "tickets
  findings ... pads its estimates") — but keep the fix-now rule boundary.
- **P4 TOKENS — `/goal` with deterministic stop.** For long builder runs, a goal of `uv run dev check` ends
  `RESULT: passed` with all DONE WHEN tests green, turn cap N. Complements the SubagentStop completion gate already in
  hooks. Measure: premature stops and turns per issue.
- **P5 EFFECTIVENESS — mine corrections into rules (workflow pattern).** Periodically run the "mine sessions +
  review comments → cluster → adversarially verify 'would this rule have prevented a real mistake?' → distill" loop to
  produce Gotchas (S2) — and as the counterpart, a rule-by-rule verifier for the few rules the gate can't check. Owner
  memory "no edge-case overfitting" applies: only rules with ≥2 real occurrences survive.
- **P6 EFFECTIVENESS — quarantine for repo-E.** User text is untrusted; the quarantine pattern (readers without
  privileges, actors see summaries) is relevant to ADR 0003 for any agentic assistant tooling and to research agents
  reading the web. Note for security-reviewer rubric.
- **P7 TOKENS — pilot before fan-out; effort/model routing.** Research rounds (harness-ops:research) fan out
  researchers; pilot one researcher on a sub-question first and set explicit token budgets per worker. Measure:
  tokens per research note vs audit pass rate.

---

## Cross-cutting conclusions on instruction size and wording (Claude 5)

1. **Fewer, non-conflicting, judgement-based instructions.** Anthropic cut 80% of the Claude Code system prompt with
   no measured loss; conflicts force extra reasoning. The harness's word ratchets are aligned; next step is a conflict
   and duplication audit (C1/C2), not just counting words.
2. **No emphatic/"think" boosters.** "think carefully" lines only add latency on Opus 5.5; absolute "NEVER/DO NOT"
   rules were the old regime. Replace with context references ("match surrounding code") or hooks. harness-ops text
   is already mostly calm (grep found no caps keywords); HARNESS.md still uses "never ... never ..." heavily — fine
   where a hook backs it, removable otherwise.
3. **Examples constrain; interfaces guide.** Prefer expressive CLI/tool parameters and one-line rules.
4. **Spend CLAUDE.md tokens on gotchas, not on the obvious;** push procedures into skills/CLI and reference them.
5. **Stop rules are now worth writing** (Opus 5.5 stops to report); the canonical two-sentence rule is cheap.
6. **Measure skills** (usage hook, undertriggering), **validate proxies** before ratcheting them, and keep the
   verification skill as the top investment.
