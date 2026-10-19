# Blog review B: Sonnet 5.5, eval design + hillclimbing, task cost on Opus 5.5, effort

Reviewed 2026-10-19. All four posts were read in full from their markdown sources (`https://claude.dev/blog/<slug>.md`,
fetched with curl; WebFetch gave only summaries). Raw copies are in
`/tmp/claude-1000/<repo-E project dir>/50a10312-0021-4a60-badf-597004abc2f9/scratchpad/blog/raw/`.

Linked resources I followed and read: "Reducing cost and improving performance with Claude Platform" (claude.com
blog), "Choosing a Claude model and effort level in Claude Code" (claude.com blog), "Maximizing the value of your
Claude Code sessions" (claude.com blog), Claude Code docs `costs` and `sub-agents`, the Sonnet 5.5 prompting guide, and
three files from the claude-api skill on GitHub (`shared/evals/eval-hillclimb.md`, `shared/evals/eval-audit.md`,
`shared/prompt-audit.md`, plus the SKILL.md command table).

**Not fetched or not fully read:** the interactive figures in all four posts (their underlying data is not in the
markdown, only the captions); the Sonnet 5.5 migration guide; the Effort, Prompt caching, Pricing and Batch docs pages;
`model-config` docs (downloaded, only skimmed); "Demystifying evals for AI agents" and "Getting the most out of Opus
5.5" (other posts in the same series that I did not re-read here); the Terminal-Bench 3.0 release page.

I checked the harness state these recommendations depend on: `~/GitHub/agent-harness/plugins/harness-ops/agents/*.md`
all set `model: opus`; only `researcher` and `research-auditor` set `effort: high`; builder, tester, reviewer,
architect and suite-auditor inherit the session effort, which defaults to medium for Opus 5.5 in Claude Code.
`devkit/evals.py` runs `claude -p` scenarios with N trials and reports pass rate, pass^k and `total_cost_usd`. It has
no model or effort override, no train/test split, and does not record turns, output tokens or cache share. There are
20 scenarios: 10 `conflicting-*` and 10 `reuse-*`. `docs/research/agent-harness.md:80` rejected `omitClaudeMd` for
workers. repo-E `.claude/settings.json` sets only `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=12`, with no model or effort env
vars.

---

## 1. "Building with Claude Sonnet 5.5" (Addy Osmani, 2026-09-28)

### Core thesis
Sonnet 5.5 is the cheaper, roughly 30% faster member of the 5.5 family. It is "smarter, more efficient and 30%
faster" than Sonnet 5 at the same per-token price, and "costs up to 30% less for most work" because it uses fewer
tokens per task. Use it for well-scoped, checkable work. Use Opus 5.5 for judgment-heavy and long-horizon work.

### Every concrete item
- **Model choice table:** Sonnet 5.5 for well-scoped everyday coding ("fixing bugs, quickly iterating on features,
  verifying against requirements"), high-volume development, polished documents and slides, and "well-defined agent
  tasks you run repeatedly: investigation, review, drafting". Opus 5.5 for "complex work requiring careful judgment,
  including long-horizon agentic coding and knowledge work" and "the hardest problems". Sonnet "fits best when the
  task has a clear spec and a way to check the result". Quoting the prompting guide: "for the hardest long-horizon
  work, an Opus model is the better choice". Haiku 5.5 is announced "in the coming weeks" for high-volume,
  low-latency work.
- **Pricing per MTok, Sonnet 5.5 vs Opus 5.5:** input $2 / $4, output $10 / $20, 5-minute cache write $2.50 / $5,
  1-hour cache write $4 / $8, cache read **$0.20 / $0.20**. Cache reads cost the same on both models, so in
  cache-heavy agent loops the gap between them comes only from fresh input and output. US-only inference
  (`inference_geo: "us"`) costs 1.1x. Images use the high-resolution tier (2576 px on the long edge), so a 2000x1500
  image costs about 2.5x the tokens it did on Sonnet 4.6.
- **Specs:** ID `claude-sonnet-5-5`; 1M context native; 128k max output (300k on Batches with the
  `output-300k-2026-03-24` header); knowledge cutoff June 2026; adaptive thinking on by default; effort levels
  low/medium/high/xhigh/max; **default effort is `high` on the API and `medium` in Claude Code**; minimum cacheable
  prompt is **512 tokens** (Sonnet 5 needed 1,024).
- **Effort guidance:** "Start with `high` unless your workload is agentic or latency-sensitive. For agentic coding and
  multistep tool use, start with `medium` for well-specified tasks and move to `high` for harder or longer ones.
  Use `xhigh` or `max` only where your evals show a quality gain." Levels are recalibrated, so "your old setting won't
  carry over". The post warns that at xhigh or max, Sonnet "will think longer and cost more… you may lose some of what
  makes Sonnet useful… consider Opus 5.5."
- **Thinking control:** "To get less thinking, lower the effort level, because asking the model in the system prompt
  to think less doesn't reliably reduce it." Thinking counts toward `max_tokens`. For agentic coding, use 128,000 and
  stream.
- **Migration breaking changes:** (1) `thinking: disabled` returns 400; use `between_tools`, which is valid only at
  low, medium or high, takes no other fields, and does not allow effort changes mid-conversation. (2) `tool_choice`
  any or tool returns 400; use `auto` with `strict: true` and `additionalProperties: false`. (3) Thinking blocks are
  model-bound; keep conversations append-only. (4) Computer use moves to `computer_toolset_20260801`. (5) The advisor
  tool rejects Opus 4.8, Opus 4.7 and Sonnet 5 as advisors, and advice comes back as `advisor_redacted_result`.
  (6) Text between tool calls now arrives as `thinking` blocks, which are empty unless `display` is set to
  `"updates"` or `"summarized"`. `/claude-api migrate this project to claude-sonnet-5-5` automates the migration.
- **"Remove Sonnet 5 workarounds"** such as refusal steering, tool-call retry shims and "do not be lazy", then re-run
  evals before tuning anything else.
- **Verification prompt at low effort** (verbatim): "When you change code that can be run, built, or type-checked, run
  a real check that exercises the change before reporting it done: the project's tests, type-checker, or build, or
  the changed command itself. A syntax-only check, or a check command that failed to start, does not count; … Only if
  no real check can run here, say which one you did not run and why instead of reporting the change as done." The
  guide says this makes skipped checks rare "with no measurable change in task quality and only a slightly higher
  cost per task".
- **Don't ask the model to write its reasoning in the response.** That invites `reasoning_extraction` refusals.
  Read `display: "summarized"` thinking instead.
- **Caching:** the 512-token minimum, and cache reads at one tenth of the input price. "Changing the top-level effort
  between requests invalidates the cache"; per-message effort (beta) keeps it.
- **Refusals:** HTTP 200 with `stop_reason: "refusal"` and a `stop_details` category of cyber, bio, frontier_llm,
  reasoning_extraction or general_harms. Server-side fallback retries only cyber and frontier_llm.
- **Claude Code:** v2.1.284 or later; the `sonnet` alias resolves to Sonnet 5.5 at medium effort; thinking cannot be
  turned off; there is no fast mode; `default` stays Opus 5.5.

### Linked resource: Sonnet 5.5 prompting guide (fetched)
- **Initiative and scope.** At low and medium effort the model "sometimes checks in before the work is done". The
  guide offers this snippet: "Keep working until everything the user asked for is done, and only stop to ask when you
  can't go on without the user or before a risky step. When the work the user asked for is done and checked, stop and
  report. Don't add features, tests, files, docs or refactors that weren't asked for…" The guide notes this makes
  sessions at low and medium run longer and cost more.
- **Runaway thoroughness at xhigh and max.** The model "can start its own rounds of review and verification,
  sometimes with subagents". The guide's counter-snippet: "When the work … is done and its checks pass, stop and
  report. Don't start extra rounds of review or hardening on your own, and don't launch reviewer sub-agents unless the
  user asked for a review." **In testing at max effort this cut session cost by about one third with no change in
  quality.**
- **Tool-use discouragement is now harmful.** Remove "only use tools when strictly necessary" and "minimize tool
  calls". Sonnet 5.5 follows such lines literally and then answers from memory.
- **Mid-turn injection misreads.** Harness text appended after every tool result can make the model treat genuine
  input as a prompt injection. Keep reminders rare and in a separate system message.
- **Tolerant tool calls.** The model occasionally gets a tool name's letter case wrong. Accept unambiguous matches or
  return `is_error` with the exact name.
- **Reasoning-plus-JSON tasks.** Add "Think the problem through before you answer." At high effort this gets close to
  xhigh accuracy. Treat a `max_tokens` stop as a failure.

### New vs common knowledge
New: Sonnet 5.5's pricing and specs; `between_tools`; the 512-token cache minimum; the `reasoning_extraction` refusal
category; the measured one-third cost cut from the "don't launch reviewer sub-agents" line; and tool-minimizing
language now being harmful on Sonnet 5.5. Common: use cheaper models for scoped tasks, cache more, and remove old
workarounds.

### Implications for agent-harness / repo-E
1. **[TOKENS] Pilot Sonnet 5.5 for the repeatable, checkable worker roles.** The post names "investigation, review,
   drafting" as Sonnet work. The candidates are `reviewer` (read-only, judged against DONE WHEN), the cite-check and
   source-verification parts of `research-auditor`, and a lookup role (see section 3). Keep builder and architect on
   Opus. Input and output cost half as much and cache reads cost the same, so expect roughly 30–50% less per review
   (BELIEVED). **Measure:** `uv run dev eval` scenarios for reviewer (planted defects in a PR diff) at n≥10 trials per
   arm, for `model: opus` and `model: sonnet`. Compare pass rate, pass^k and `cost_usd`. Adopt Sonnet only if it is
   within noise on pass rate.
2. **[TOKENS+EFFECTIVENESS] Add the "stop and report, don't launch reviewer sub-agents" line to any agent or skill run
   at xhigh or max.** Today no agent is set that high. This only matters if the effort changes in section 4 put some
   agents at xhigh. **Measure:** subagent spawn count per session in `agent-stats`, before and after.
3. **[EFFECTIVENESS] Put the verification paragraph, reworded for the harness, in `builder.md` and `tester.md`.** For
   example: "a real check that exercises the change: `uv run dev test --affected` or the changed command; a check that
   failed to start does not count; if none can run, say which and why." This matters most if builders move to
   low or medium effort. **Measure:** a new eval scenario where the dependency is missing or tests can't start, graded
   on whether the agent claims done without test output (`expect_command` for the test, `forbid` a done-claim
   without it).
4. **[TOKENS] Audit the harness text for tool-minimizing and "think less" prose.** On 5.5 models both act literally
   or not at all; see the prompt-audit list in section 2. Effort is the control.
5. **[EFFECTIVENESS, repo-E product] The assistant and examiner prompts must not ask the model to show its reasoning in
   the reply.** That invites `reasoning_extraction` refusals. Handle `stop_reason: "refusal"` with its category
   explicitly in `repo-E's model wrapper`, and log categories separately from failures; this is also SB/user-safety
   relevant. Use `between_tools` rather than `disabled` when latency matters. The 512-token cache minimum means short
   examiner system prompts are now cacheable. **Measure:** count refusals by category in the examiner eval runs.
6. **[EFFECTIVENESS] Remember the default-effort trap.** Claude Code runs Sonnet at medium but the API defaults to
   high. Any eval comparing harness runs (Claude Code) with product runs (API) must pin effort explicitly.

---

## 2. "Automating eval design and hillclimbing with Claude" (Lance Martin, 2026-09-28)

### Core thesis
Good evals have four properties, and hillclimbing against them needs a held-out split, one root-cause patch per round,
and a noise check. Otherwise you overfit the harness to the eval. The claude-api skill now automates both with
`/claude-api build-eval` and `/claude-api hillclimb`.

### Every concrete item
- **The four elements of a good eval:**
  1. "Eval tasks mirror production." Do not pick tasks because they are easy to generate or grade.
  2. "Performance improves with stronger models and more thinking. … If they don't, ambiguous tasks or a
     miscalibrated grader often are hobbling performance." This is a **validity test**: an eval that is not
     monotonic in model and effort is suspect.
  3. "Passable headroom at the frontier." The best model at max effort should be "well below 100%". "A common tell is
     that a task fails every evaluation run, regardless of the number of replicates. A good task is one where two
     domain experts would reach the same verdict and everything the grader checks is stated in the task."
  4. "Low run-to-run variance." Variance comes from ambiguous tasks, graders that are unstable on identical output,
     effort not applied consistently, and "leftover state from an earlier trial (a file, a git history) can hand the
     agent the answer".
- **Adversarial sampling.** "If you pick cases because today's model fails them, you are sampling the valleys of one
  model's capability surface … measuring that model's failure fingerprint". Pick cases "because a human judged them
  hard: a useful test is to be able to say why a task is hard before you include it". Include production failures,
  but "a task distribution drawn strictly from user traffic may skew easy". The figure also shows "should-not-fire"
  cases.
- **build-eval input priority:** (1) production transcripts, after asking about retention and sensitive data;
  (2) bug reports and tickets; (3) 5–10 hand-written cases; (4) cases synthesized from the codebase. It renders a
  review page and waits for the user to confirm.
- **Grader choice:** "the cheapest grader that fits". Use programmatic checks (exact match, a fixed label set, a JSON
  schema, tests) where possible. Otherwise use an LLM judge with "a rubric written as checkable claims (not a 1-to-5
  scale)". For comparisons, show the judge both outputs in random order, blind to which is the baseline. "It should
  not be the model you are testing." Grade a handful and ask the human whether they would have scored any
  differently, and read scored transcripts.
- **Run size:** report cases × repeats × model and the time it will take. The baseline score comes with a CI.
  Outputs are one JSON line and one full transcript per case, plus a static local results page.
- **Diagnostics:** run the grader twice on the same output (verdict stability); check plumbing (timeouts, API
  errors, cut-off answers); warn about headroom if the baseline is 95% or higher, and in that case "explore cost or
  latency rather than quality".
- **Choosing a hillclimb surface:** **cheap iteration** (text such as prompts and skills; "open-ended modifications to
  an agent harness … may involve extensive code changes"); **attributable** ("several successful applications … have
  focused on skill triggering. The evaluation metric (the trigger rate for the skill) is directly coupled to the skill
  description"); **well-scoped objective** ("One generally strong objective … is cost: even if an evaluation is
  saturated, you can ask Claude to find ways to reduce cost while keeping performance at parity").
- **Overfitting.** The eval "leaks" into the harness: an OCR tool, "always cd /app, run pytest", a prompt tuned to
  distinctive phrasings, "failures you've read get one patch each", and a public answer repo the agent can curl.
  Mitigations: **split train/test** ("If the train set scores improve while the test set scores stay flat, then that
  is a common overfitting warning sign"); **never paste failures into the prompt**; **keep answers structurally out
  of reach**.
- **The hillclimb loop:** pick a goal (performance, or cost at equal performance) and split at random. Check that
  "noise … is smaller than the smallest improvement you'd act on". Each round: read the train transcripts, propose
  **one** patch that fixes the root cause ("rewrites the section … or adds a missing rule rather than rewording a
  line"), then run. **Keep the patch only if train and test both improve. Revert if train improves while test is flat,
  on any regression, or with no gain.** If the score stalls for 2–3 rounds, make no edit and bucket every remaining
  failure by cause. Finish at the best-on-test version with CIs, and "If the gain is within noise, it says so and
  recommends against merging."
- **Cost example (44 support tickets: 30 search, 14 held out).** Baseline Opus 4.8 at high effort: 74.4% accuracy,
  4.6¢ per ticket. A prompt audit removed "mandatory tool-call rituals, a scratchpad step, and contradictory rules".
  Opus 5.5 at low effort: 87.8%, 1.9¢. Sonnet 5 at low effort: 88.9%, about 1¢. Adding routing rules and a refund-cap
  cross-reference brought Sonnet 5 to 98.9% at the same cost. **Held-out: 90.5% vs 78.6%, at about one fifth of the
  cost.** (The 98.9% vs 90.5% gap between search and held-out shows the overfitting margin.)
- **Performance example (the claude-api skill's own eval).** It rose from 66.1% to 87.9% over 24 rounds: adding
  coverage for 8 missing features took it to 74%, fixing the C#/Java type tables to 77%, then a reflection round
  found the model "was simply writing older API shapes (… from its trained priors)". The fix was "a table near the
  top of the skill that guided Claude from the forms it remembered to the current ones" (80%). Then it fixed flawed
  tasks and graders: one grader wanted a chain of three error types while the task asked for one, and another
  grader's instructions contradicted the docs (about 88%).

### Linked resources (followed)
- **`eval-hillclimb.md` (claude-api skill).** Step 0.5, "Prove the eval can be climbed": noise floor (the paired-CI
  half-width), headroom, and the smallest actionable gain; prove the mechanism is wired (disable it and the score
  should drop); recompute the headline from raw per-case rows ("Mean-vs-sum … phantom results"); spot-check the
  grading on baseline failures; read the served `model` from the response. If subagents are involved, "confirm the
  parameters you're iterating on — model, effort, prompt — actually reach every subagent." State lives on disk in
  `_state.json`, `vN/results.jsonl` and traces, so it resumes. Step 4.5 has a stall-bucket table: artifact gap,
  grader disagreement, harness/infra, structural, variance. **Failure modes:** touching the held-out set; leaving
  ground truth reachable; trusting an implausible jump; "Overgeneralizing from one case"; "Untracked bundling. One
  idea per round"; "Changes too small to see"; "Accumulating fluff" (a per-round de-fluff pass);
  "Averaging refusals into the score" (record a failure class per attempt, and pre-register the scrub predicate). The
  report tags each change **[REQUIRED]** or **[TUNE]** and includes a second-model check ("a win measured on one
  model doesn't transfer by default").
- **`eval-audit.md`.** Audit cases in three tiers: programmatic checks over the full set; a stratified close read of
  20–50 cases; and a per-case LLM auditor prompt (flags: ambiguous, gold_suspect, answerable_from_memory,
  grader_too_strict/lenient, trivially_cheatable). **Noise rule of thumb: for a pass rate, the 95% CI half-width is
  about `1/sqrt(n·R)`; 25 cases × 2 reps gives about ±14 points, 100 × 2 about ±7.** Draw train and test at random
  (stratified), **never by baseline score**, because a split by score buys regression to the mean. Test both
  directions ("does the agent *not* search when it shouldn't"). Use inverted items (a weaker variant beating a
  stronger one) as a smoke test for bugs. LLM-judge hygiene: position bias, verbosity bias, self-preference,
  label deference, a concrete rubric, **calibration against human labels (well below ~90% agreement on clear-cut
  cases means iterate the judge)**, and known negatives (an empty answer, "I don't know", a confident answer to the
  wrong question).
- **`prompt-audit.md`.** The deletion rule is "could the model already know this?" Keep what only the author knows:
  context, environment facts, the quality bar, tool contracts, and the reasons behind rules. Anti-pattern groups:
  pressure language (`MUST|NEVER|ALWAYS|CRITICAL` density, emphasis without a "because"); scaffolds replaced by API
  features ("think step by step", "plan before acting", "think less", which effort replaces); over-specification
  (step choreography for judgment tasks, prohibition lists that "can anchor it toward that failure", bullet walls
  that "sever rules from reasons", grader vocabulary such as "you will be graded"); fossils (patch accretion,
  "unenforced instructions … rules no code path, eval, or reviewer checks", update suppressors like "hold all findings
  for the final response", reminder re-insertion on a cadence). **Keep list:** "Cruft != length", fragile operations
  keep exact scripts, tool contract detail stays, prohibitions against failures that still occur stay, and "working
  redundancy is not cruft". Step 7: "removal is a hypothesis": probe behavior before and after, one change at a time,
  and re-audit at every model release.

### New vs common knowledge
Common: train/test splits, LLM-as-judge with rubrics, reading transcripts. New or sharper: **effort and model
monotonicity as an eval validity check**; "say why a task is hard before including it" (do not sample the current
model's failures); the explicit **revert rule when train is up and test is flat**; categorize, don't edit, after 2–3
stalled rounds; the `1/sqrt(n·R)` noise heuristic; no train/test split by baseline score; cost-at-parity as the
default objective near saturation; and the reflection that found the model reverting to trained priors, fixed with a
"remembered form → current form" table at the top of the skill.

### Implications for agent-harness / repo-E
1. **[EFFECTIVENESS] Add model and effort axes to `uv run dev eval`.** Optional scenario or CLI fields `model:` and
   `effort:` pass `--model` and `--effort` to `claude -p`, or set `CLAUDE_CODE_EFFORT_LEVEL`. Read the served model
   from the stream-json. This enables (a) the monotonicity validity check: a scenario that Opus at high fails while
   Sonnet at low passes is a broken scenario, and (b) every model and effort decision in sections 1, 3 and 4.
   **Measure:** a 2×3 sweep (sonnet/opus × low/medium/high) on the 20 existing scenarios, then flag non-monotone ones.
2. **[EFFECTIVENESS] Print the noise floor next to every eval result.** With today's typical 3 trials per scenario, one
   scenario's pass rate has a half-width near ±55 points; across 20 scenarios × 3 trials it is about ±13. State that
   "a change smaller than X cannot be seen" and refuse to call a delta that is inside it a win. This is the same
   discipline as the existing pre-registered-evals memory. **Measure:** run the same commit twice; the run-to-run
   delta should sit inside the printed band.
3. **[EFFECTIVENESS] Use a held-out split for any hillclimb of harness text** (CLAUDE.md, HARNESS.md, agent files,
   skills). Split the scenario set at random by a fixed seed into train and test, stratified by family
   (`conflicting`, `reuse`). The agent editing the harness sees only train transcripts. Keep a change only if train
   and test both rise; revert if test is flat. Add the rule "never paste a failing transcript's content into harness
   text". This matches the existing "no edge-case overfitting" memory. **Measure:** the train–test gap per round.
4. **[EFFECTIVENESS] Grow both-directions scenarios.** Each "must do X" scenario (escalate with `board block`, run
   `reuse check`) needs a sibling "must NOT do X" scenario. Otherwise "always block" or "always reuse" passes. The 10
   `conflicting-*` scenarios only test that the agent blocks when the spec is impossible; add solvable twins that must
   not be blocked. **Measure:** precision and recall of `board block` across the twins.
5. **[EFFECTIVENESS] Add a failure class per trial.** Record infra, timeout, refusal or genuine in `evals.py`
   (stream-json `is_error`, `max_turns` hit, refusal stop reason), and exclude infra from the denominator. Add the
   "recompute headline from raw rows" step.
6. **[TOKENS] Use cost-at-parity as the default hillclimb objective once a scenario family is above 95%.** It is the
   strongest objective near saturation. Concretely: same pass^k, lower `cost_usd` and turns, by changing agent
   model, effort or prompt-audit cuts.
7. **[TOKENS+EFFECTIVENESS] Run `/claude-api prompt-audit` against the agent-harness `template/` CLAUDE.md, HARNESS.md,
   `plugins/harness-ops/agents/*.md` and the skills.** Treat its proposed diff as hypotheses, and verify each cut with
   `uv run dev eval` before and after (Step 7). Watch its keep list against our word-budget ratchets: "Cruft !=
   length". A ratchet that rewards deleting reasons ("because …") or tool-contract detail would go against the audit's
   own guidance (BELIEVED). Consider making the ratchet exempt lines that state a reason. Also check our "fossils":
   prohibition runs and rules that nothing enforces. The audit's advice "Enforce in code what can be enforced in code"
   matches the harness's hook-first stance. **Measure:** words per agent file, pass^k, and `cost_usd` per scenario,
   before and after.
8. **[EFFECTIVENESS, repo-E product] Use build-eval and hillclimb principles for the dialogic examiner and assistant
   prompts.** Sources should be synthetic-user transcripts (no real user data) plus hand-written hard cases with
   a stated reason. The judge must not be the model under test. Use a rubric of checkable claims (e.g. "did not reveal
   the answer", "asked one question"). Calibrate the judge to ≥90% agreement with owner labels. Add known-negative
   probes. Add both-directions safety cases (should escalate vs should not). Keep refusals out of the capability
   score. Do a second-model check before switching models in `repo-E's model wrapper`. This fits the existing pre-registration
   memory: freeze the split and configs, then score TEST once.
9. **[EFFECTIVENESS] Copy the "remembered form → current form" table trick** into skills where agents keep reverting
   to trained priors. Candidates: pip instead of uv, reading whole files instead of `dev code outline`, and old Claude
   API shapes in `repo-E's model wrapper`. Put the table at the top of the relevant skill or hook message.

---

## 3. "What a task costs on Opus 5.5" (Addy Osmani, 2026-09-25)

### Core thesis
A task's cost is set by **turns × resent context**, the cache hit rate, and output (thinking) tokens, more than by
the per-token price. "Every way to spend fewer tokens can also cost you a finished task… A retry costs more than those
savings."

### Every concrete number and technique
- **Opus 5.5 list prices vs Opus 5:** input $4 vs $5, output $20 vs $25, cache read $0.20 vs $0.50 (−20%, −20%,
  −60%). The cache read is now 1/20 of input. On subscriptions, limits "go about 25% further than on Opus 5". The
  "40% less to run" claim assumes fewer tokens per task at medium on top of the price cut.
- **Turn arithmetic:** context grows from 20K to 120K; 40 turns at about 70K average gives about 2.8M input tokens,
  which costs **$1.62** at a 90% cache rate. The same task in 25 turns is 1.75M tokens and **$1.02**. "The cheapest
  turn is the one you don't need." Verification tools cut turns ("A model that can check its own work finds its
  mistakes earlier"), and so does batching tool calls ("pays the resend fewer times").
- **Cache sensitivity:** 2.8M tokens cost $11.20 uncached, $1.62 at 90%, $0.99 at 96%. "No other setting moves input
  cost this much."
- **Output:** one output token costs 100× a cache read. 60K output costs $1.20, the same as reading 6M cached
  tokens. Thinking is billed as output even when only a summary is shown.
- **Illustrative session:** $3.50 on Opus 5 vs $2.40 on Opus 5.5 (−31%) on the price alone. A month at 10 tasks a day
  for 22 days: $770 vs $528.
- **Effort economics:** "say high adds 20K thinking tokens across a task. On Opus 5.5 that's $0.40. A retry loop of
  ten turns at 100K of cached context, with 10K output tokens in total, costs about the same. So high pays for itself
  on a task where it saves one retry." The Opus 5.5 default is **medium** (Opus 5's was high). "At a given level, Opus
  5.5 thinks more per turn than Opus 5, most of all at xhigh and max." Use medium for well-scoped work, high when
  medium stalls, low for mechanical renames and pattern application.
- **The "fix stops at one layer" heuristic:** at medium, a renamed field gets fixed in the handler but not in the
  client. "A check can catch the same bug… So before you raise effort, check whether the model has a way to check its
  work. A test run costs one turn and its output. More effort adds thinking to every turn. If upgrading effort levels
  and adding checks doesn't work, then switch to a bigger model."
- **Effort changes keep the cache** on Opus 5.5 with an API key or subscription, but not on Bedrock, Vertex or
  gateways.
- **Three-model ladder:** Haiku or Sonnet for lookups and subagents; Opus 5.5 as the daily driver; **Fable 5.1**
  ($10 input, $50 output, $0.25 cache read) when "the result matters more than the token price… long runs you won't
  supervise, problems with no existing pattern in the codebase, and large changes that coordinate many subagents…
  If Opus 5.5 on xhigh hits the same problem twice, switch, and switch back once it's solved." Switch at a natural
  break, because the cache belongs to the previous model; `/compact` first or start fresh with a short written plan.
- **Moving down:** "Move down to Sonnet or Haiku **for lookups, not for writing code**: subagents that search and
  summarize, reading logs and test output, and 'where is this defined' questions. For a mechanical edit across many
  files, keep Opus 5.5 and set effort to low." Set `model: haiku|sonnet` in the subagent definition, or set
  `CLAUDE_CODE_SUBAGENT_MODEL`; the definition wins. "Every subagent that inherits the main model inherits its price
  too." The risk: "a small model that misreads a search result sends the main model after the wrong file, and the main
  model pays for the detour. Keep the small model on work where a mistake is cheap to spot." "Keep judgment calls on
  the main model." `opusplan` (Opus plans, Sonnet edits) is "the opposite of the advice above. Measure it."
- **Agent teams cost about 7× the tokens** of a standard session in plan mode.
- **Prompt-audit on migration:** Opus 4.8 to Opus 5.5 at low effort saved about 18%; `/claude-api prompt-audit` saved
  another 9% (about 25% total). The audit removed "a mandatory six-step procedure, a scratchpad rule, a verify-twice
  rule, and instructions that contradicted each other".
- **Cache mechanics:** a 5-minute write costs 1.25× input and a 1-hour write 2×. At 120K, a write is about $0.60 and a
  read about $0.02, so "One write costs as much as 25 reads". Lifetimes: **1 hour on a subscription**, 5 minutes on an
  API key. Cache writes happen on a pause longer than the TTL, an effort change on cloud providers, the first
  fast-mode turn, an MCP connect or disconnect, a model switch, and compaction. "Set these up when the session starts,
  and leave them alone."
- **Long-session cost:** a cache read per turn costs $0.004 at 20K context and $0.03 at 150K. 30 turns at 150K cost
  $0.90 in reads, vs $0.12 at 20K.
- **Compaction:** about $0.25 at 150K, and it pays back in about 10 turns. Compact before a break, not after (about
  $0.75 cold). Prefer `/rewind` to drop a dead end. Compaction can drop "the one log line that mattered". Say what to
  keep.
- **Startup context:** keep CLAUDE.md under 200 lines. MCP tool definitions are deferred (names only).
- **Fast mode:** up to 2.5× faster at 2× price. Turn it on at session start.
- **Baselines:** about $13 per developer per active day; 90% of users stay under $30.
- **Measuring:** `/usage` (or `/cost`). Check three things: **cache share** (low means a pause, model switch or MCP
  change), **output vs input** ("A lot of output on a small change usually means the effort level is too high … or the
  model is retrying"), and **total input vs conversation size** ("many times the size … the session took many
  turns"). Run the same backlog task on both models, 3–4 tasks, and an effort ladder.

### Linked resources (followed)
- **"Maximizing the value of your Claude Code sessions."** Tips: `/clear` between tasks; set the model and effort
  before starting; **@-mention files** (saves a Read call); "Add quiet flags to noisy commands, or run them in a
  subagent"; `/context` in a fresh session; `/compact` before a break. Output under 30,000 characters stays inline
  (`BASH_MAX_OUTPUT_LENGTH`): "A test runner that prints 400 passing tests … those 400 lines are now part of every
  remaining turn." Put your 2–3 daily commands in CLAUDE.md with quiet flags. A `/loop` fires as a full turn in its
  session; run loops from a fresh session. Subagents "sometimes … re-read things the main session already had… For a
  small job it's just overhead. It pays off when a job produces a lot of output you don't need to keep." "If there's
  a noisy job you hand off over and over, give it a subagent definition of its own with model: haiku (or sonnet)."
  `MAX_THINKING_TOKENS=0` does not apply to 5.5 models. `ENABLE_PROMPT_CACHING_1H=1` gives a 1-hour TTL on API keys.
  `opusplan` busts the cache on every plan-mode toggle.
- **"Choosing a Claude model and effort level in Claude Code" (Lydia Hallie).** Effort "controls how much work Claude
  does on your request overall including the number of files read, tools used, and how many steps it takes before it
  checks back in". The diagnostic: "If Claude has all the pertinent context, clearly tried, and still got it wrong,
  that's a signal to pick a more capable model. If Claude got it wrong by skipping a file, not running the tests, or
  bailing on a refactor partway through, pick a higher effort level." "Larger models are also better at handling
  ambiguity, whereas specific instructions directing execution are a better recipe for success on the smaller
  models." The high-effort path in their example generates about 7× more tokens. It recommends staying at the default
  effort and treating effort as a "general preference" rather than a per-task choice. "Effort shapes token consumption
  but doesn't limit it"; task budgets are the softer control.
- **Costs docs.** A **PreToolUse hook that rewrites test commands to show only failures** (`updatedInput`). "Move
  instructions from CLAUDE.md to skills". Agent-team costs. Thinking cannot be turned off on 5.5 or Fable models.
- **Sub-agents docs.** Frontmatter supports `model`, `effort`, `maxTurns`, `omitClaudeMd`, `autoCompactWindow`,
  `skills`, `background` and `isolation`. **There is a per-invocation `effort` parameter** (v2.1.292 or later) that
  overrides the frontmatter, and `CLAUDE_CODE_EFFORT_LEVEL` beats both. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` forces
  one model. A user or project agent named `Explore` overrides the built-in, so `model: haiku` can be set on
  exploration. Built-in Explore and Plan already skip CLAUDE.md. `/tasks` shows each subagent's model and effort.
  Subagents inherit the main session's thinking setting.
- **"Reducing cost and improving performance with Claude Platform."** Cache tips: "Subagents and branches only share
  the parent's cache when the fork's prefix is byte-identical, on the same model, and using the same effort". "Avoid
  synchronous tool calls and subagents that outlive the cache TTL". Defer rarely used tools. Send system updates as
  messages. Change model or effort when the cache is already broken (at compaction). Prompt anti-patterns:
  verification rituals ("double-check", "verify twice"), emphasis boosters, mandatory procedures and scratchpads,
  stale examples, contradictory rules, dated config. In the planted-anti-pattern experiment, "verify twice"
  duplicated an order lookup on every refund, "be maximally thorough" caused dozens of extra KB searches, and the
  manual scratchpad led the model to write the tool call inside its reasoning and never execute it on 3 tickets.
  Effort curves: Fable 5 on FrontierCode Diamond scored 11.5% at $5.35 (low) vs 30.9% at $19 (max). Fable 5.1 on HLE
  scored 53% at $0.30 vs 61% at $2.23, and the last step added about 0.5 points for 46% more cost, within noise.
  "Test stronger models at lower effort": Fable 5.1 at low matched Fable 5 at high for one third of the cost. "A flat
  performance-cost curve across effort levels suggests that the task is not bound by thinking compute."
  `cost-optimize` results: LegalBench −67%, tau2 retail −73% (93% of the prompt was cacheable), OfficeQA −72%,
  SWE-bench Verified −24% (medium effort plus concise output).

### New vs common knowledge
Common: caching matters, `/clear`, smaller models for lookups. New or sharper: the **cost of one retry ≈ the cost of
20K thinking tokens** framing; **add a check before raising effort, raise effort before changing model, change model
after two xhigh failures**; Sonnet and Haiku for lookups but **not for writing code**; the "/usage triage" trio; a
1-hour subscription TTL with idle lead sessions as a cache-write risk; effort changes being cache-safe on 5.5; the
per-invocation subagent `effort` parameter; and verification rituals and scratchpads measurably hurting 5.x models.

### Implications for agent-harness / repo-E
1. **[TOKENS] Add a lookup subagent on Sonnet (or Haiku 5.5 when it ships).** Either a project `Explore` override or a
   new `harness-ops:scout`, read-only (`Read, Grep, Glob, Bash`), `model: sonnet`, `effort: low`, and possibly
   `omitClaudeMd: true` since it needs no worker rules. Its jobs are "where is X defined", reading `uv run dev check`
   or test logs, and summarizing CI output. Builders and the lead delegate log reading to it rather than pulling 400
   lines into an Opus context. Expect noticeably cheaper investigation turns (Sonnet input at $2 vs $4 and output at
   $10 vs $20; cache reads equal); a size estimate needs `agent-stats` data (ASSUMED). **Measure:** `agent-stats`
   tokens by agent type, and a scenario where the answer is buried in a long log.
2. **[EFFECTIVENESS+TOKENS] Have the lead set per-dispatch effort from the issue type,** using the per-invocation
   `effort` parameter (v2.1.292 or later), and put the rule in the `harness-ops:lead` skill. Mechanical work (renames,
   applying a known pattern, docs regen): builder at **low**. A new feature with a clear spec: **medium** (the
   default). A bug fix in brownfield code, anything touching auth or safety, or a cross-layer change: **high**.
   Escalation: after a builder blocks or fails the gate twice at high, re-dispatch at **xhigh**; after two xhigh
   failures, re-dispatch on **Fable** (`model: fable` per invocation), then return to Opus. Following "a retry costs
   more than the savings", the risky step is the low setting; it needs the real-check paragraph from section 1.
   **Measure:** gate pass on first try, number of re-dispatches per issue, and tokens per merged PR, broken down by
   effort label.
3. **[TOKENS] Lead-session cache hygiene.** The lead idles while workers run. On a subscription the TTL is 1 hour, so a
   worker that runs longer than an hour makes the lead's next turn a full cache write of a possibly 150K+ context
   (about $0.75 equivalent at list price). Rules for the lead skill: compact or write HANDOFF before a long idle, or
   resume from a fresh session plus `dev orient`, which is often cheaper than re-prefilling. Never toggle MCP servers
   or `/model` mid-session. Avoid `opusplan`. **Measure:** the cache-share line of `/usage` at lead session end, and
   whether `agent-stats` can extract `cache_read` vs `cache_creation` tokens per session from the transcripts.
4. **[TOKENS] Quiet output by default.** Make `uv run dev test --affected` and `uv run dev check` print only failures
   and a one-line summary on success, with a `-v` escape hatch. Alternatively, adopt the documented PreToolUse
   `updatedInput` rewrite hook for raw `pytest` calls. Every passing-test line otherwise rides along on each later turn
   of every worker. **Measure:** characters of tool-result output per session in `agent-stats` (`_result_chars`
   already exists), before and after.
5. **[TOKENS] Add `/usage`-style triage to `uv run dev agent-stats`** for each session: cache share, output/input ratio,
   and total input ÷ final context size (a turn-loop smell). Flag sessions above about 3× the median. This turns
   "re-check a root cause with data" into a routine check.
6. **[TOKENS] Batch tool calls.** HARNESS.md already says to batch independent reads. The post quantifies why: each
   avoided turn saves a resend of the whole context.
7. **[EFFECTIVENESS] "Check before effort."** When a worker fixes only one layer, the harness answer is a cross-layer
   test or a `dev code refs` check in DONE WHEN, not higher effort. Encourage issue authors (the lead) to write DONE
   WHEN as an executable check.
8. **[TOKENS] Do not use agent teams** (7×). The harness's subagent-per-issue model is the right shape. Keep
   `CLAUDE_CODE_SUBAGENT_MODEL` unset so per-agent `model:` fields stay authoritative.
9. **[TOKENS, repo-E product] Pricing levers for `repo-E's model wrapper`:** the Batch API (half price) for offline evals and
   intake digests; caching the stable examiner prompt prefix (≥512 tokens on Sonnet 5.5); no timestamps or IDs in the
   prefix; and recording `usage` per call so cost per assistant turn can be measured.

---

## 4. "Using Claude Code: Spending your effort" (Thariq Shihipar, 2026-09-25)

### Core thesis
Effort sets how much **verification, edge-case testing and independent judgment** Claude applies. It helps most on
tasks with many hidden edge cases (security, hardware, review). It does not fix a wrong approach. For ordinary
features: interview, implement at low, review, then verify at high.

### Every concrete item
- **Definition:** "effort gives the model an approximation of how much compute you want it to spend… higher effort will
  involve Claude taking more independent action for judgement and verification." The analogy: a 12-hour budget vs a
  1-hour budget for the same task.
- **Effort curves (Terminal-Bench 3.0, 70 tasks, GPU tasks excluded):** Opus 5.5 went from **36.6% at low to 65.7% at
  max**. **"Opus 5.5 at high matches Fable 5.1 at max (58.9% against 58.0%) on half the tokens."** Fable 5 plateaus at
  43.4% from xhigh to max. Caveats: responses were capped at 128k tokens, there was no GitHub or PyPI access, and Opus
  5's max point is its effort-120 run.
- **Toy builds on Opus 5.5:**
  - Underspecified fitness app: low 1.5 min (a log and a graph), medium 4 min, high 11 min, max 67 min (more
    features and a heat chart). Higher effort means "Claude making more choices along the way".
  - `/config` redesign: low 1 min (a rough interactive sketch), max 28 min (a polished mockup with walkthroughs). The
    author preferred low "to understand Claude's vision".
  - Highly specified build from an interview spec: low 16 min, medium 22, high 33, max 79. The implementations were
    "much more similar" across levels. **A good spec compresses the effort spread.**
- **Workflow:** "Give Claude a spec and ask it to interview me about any details I'm missing → Implement it on low
  effort → Review … iterate on low effort as needed → Verify and test on high effort."
- **"Higher effort is best for tasks with lots of hidden edge cases."** `html-js-filter` (an XSS sanitizer): Fable 5.1
  went from 1/5 at low (about 2 minutes, one pass, tested against one hand-written page) to 5/5 at xhigh. A traced
  high-effort run of about 33 minutes "adversarially reviewed its first draft, then read the installed parser's
  source…, ran many clean test cases…, ran a standard XSS test suite, and finally, wrote a random-document fuzzer."
- **Failure taxonomy:** "increasing effort tends to reduce failures due to missing edgecases… but does not fix when the
  model has the wrong approach."
- **Pass-rate gain by category from low to top effort** (from the summary of the interactive figure; I could not
  independently check per-model values): Security 64→87%, Hardware 34→75%, ML 54→73%, Science 41→61%, Software
  43→56%, Media 18→30%, Operations 12→22%.
- **Worked cases where Opus 5.5 failed at low and succeeded at high:**
  - `mvcc-lsm-compaction`: 0/5 → 4/5 at xhigh. At low (about 1 minute) it edited before reproducing and never checked
    that its test would have caught the bug. At xhigh (about 11 minutes) it "reproduced the crash first, wrote a
    randomized test against a reference that never compacts, and checked that its tests failed on half-finished
    fixes".
  - `cli-2ph-simplex`: 0/5 → 5/5 at high. At low it wrote the solver in one pass, stopped around 10k tokens, and
    warned about slowness without checking. At high it ran differential tests against brute force on random problems,
    timed large inputs, and reworked the search.
  - `gsea-proteomics`: 0/5 → 4/5 at high. At high it "tried two ways of prepping the data, noticed that the list of
    significant treatments changed, and dug into why". "If a user were in the loop, Claude may have asked… but
    without a user in the loop, high effort does better."
- **Rule of thumb:** **Low** for in-the-loop brainstorming, sketches and easy changes. **Medium** for most
  everyday software engineering and new features. **High** "where verification is important or there are edge cases,
  e.g. fixing a bug in a brownfield codebase". **Max** for "fully autonomous" hard problems: end-to-end app builds,
  security vulnerability hunting. `/effort` can be changed mid-conversation without breaking the cache.
- **Methodology note:** internal runs, 5 attempts per task, Fable safety interventions off, security tasks without
  internet. The numbers will not match the public leaderboard.

### Resources
The Terminal-Bench 3.0 task list (github.com/harbor-framework/terminal-bench releases v3.0.0; not fetched) and the
"What is effort" blog (read above, in section 3).

### New vs common knowledge
New: the Opus 5.5 effort curve (36.6→65.7%); **Opus 5.5 at high ≈ Fable 5.1 at max for half the tokens**; per-domain
effort sensitivity (security and hardware most, operations and media least); the low-implement, high-verify loop;
effort narrowing on well-specified tasks; and the concrete behaviors high effort adds (reproduce first, differential
or random tests, mutation-style checks that tests fail on half-finished fixes, trying two pipelines). Common: harder
tasks deserve more compute.

### Implications for agent-harness / repo-E
1. **[EFFECTIVENESS+TOKENS] Split build from verify by effort.** The harness already separates builder from
   tester/reviewer, which matches the low-implement, high-verify loop. Set `tester.md` to `effort: high` (adversarial
   edge cases are exactly where effort pays), and `security-reviewer` and `research-auditor` to high or xhigh
   (security showed the largest gain, 64→87%). Leave builder at medium by default, with per-issue overrides as in
   section 3. **Measure:** defects found per tester run (seeded-bug scenarios) at medium vs high, and the escaped-bug
   rate after integrate.
2. **[EFFECTIVENESS] Put the high-effort behaviors into DONE WHEN and the tester and builder prompts as outcomes, not
   rituals:** "reproduce the failure before fixing", "the new test fails on the unfixed code", "differential or
   property test against a reference when one exists". These are checks, not "verify twice" choreography, so they fit
   the prompt-audit rule. They let a medium-effort builder get some of the high-effort benefit through a check
   ("check before effort"). The tester already writes property-based tests. **Measure:** a scenario that requires the
   reproduce-first order (`order:` grader exists), at medium vs high.
3. **[EFFECTIVENESS] Effort does not fix a wrong approach,** so the architect and the issue spec handle approach. For
   ambiguous issues, the lead runs an interview or spec step (or the architect agent) before dispatching a builder.
   A well-specified issue narrows the spread between effort levels, which supports running builders at medium or low
   when DONE WHEN is precise. **Measure:** builder success by "spec quality" (DONE WHEN executable or not).
4. **[TOKENS] Do not default any agent to max.** Opus 5.5 at high already matches Fable 5.1 at max. Reserve xhigh for
   escalation after failures, and Fable for two xhigh failures. If max or xhigh is ever used, add the "don't launch
   reviewer sub-agents" stop line from section 1.
5. **[EFFECTIVENESS, repo-E product] Run the user-safety classifier and examiner grading at higher effort.** These are
   edge-case-heavy, like the sanitizer task. Routine assistant dialogue can run at low or medium. Verify this with an
   effort sweep in the pre-registered eval before fixing settings in `repo-E's model wrapper`.

---

## Cross-post synthesis: ranked changes

| # | Change | Tag | Expected impact | How to measure |
|---|---|---|---|---|
| 1 | Lead picks per-dispatch `effort` by issue type (low mechanical / medium feature / high bug or security), with escalation to xhigh and then Fable after 2 failures | EFF+TOK | Fewer re-dispatches on bugs; cheaper mechanical work | First-try gate pass and tokens per merged PR, by effort |
| 2 | `tester`, `security-reviewer` and `research-auditor` at `effort: high` (or xhigh for security) | EFF | More edge-case defects caught | Seeded-bug scenarios, escaped bugs |
| 3 | Sonnet- or Haiku-backed read-only lookup and log subagent; keep code writing on Opus | TOK | Cheaper investigation turns and less context bloat in Opus workers | `agent-stats` tokens by agent |
| 4 | Pilot `reviewer` on Sonnet 5.5 behind an eval | TOK | About half the per-token price for reviews | Reviewer scenarios, n≥10 per arm |
| 5 | `dev eval`: model and effort flags, noise floor, failure classes, both-directions twins, train/test split for harness hillclimbs | EFF | Decisions stop resting on noise | Run-to-run delta inside the band; monotonicity |
| 6 | `/claude-api prompt-audit` on template, agents and skills, with each cut verified by eval; ratchet should not penalize "because" lines | EFF+TOK | About 9% fewer tokens in the cited case, fewer ritual tool calls | `cost_usd` and pass^k before and after |
| 7 | Quiet-on-success `dev test` and `dev check`, or a PreToolUse output-filter hook | TOK | Less resent output per turn | `_result_chars` per session |
| 8 | Lead cache hygiene (compact or handoff before long idle, no mid-session model or MCP changes) | TOK | Avoid full cache rewrites | Cache share in `/usage` and transcripts |
| 9 | Real-check paragraph and reproduce-first, test-fails-unfixed as DONE WHEN outcomes | EFF | Fewer false "done" claims, especially at low or medium | Scenario with a broken test runner |
| 10 | repo-E `repo-E's model wrapper`: no reasoning-in-response prompts, refusal-category logging, Batch for offline evals, cache the examiner prefix, judge ≠ tested model with ≥90% human agreement | EFF+TOK | Fewer `reasoning_extraction` refusals; cheaper evals | Refusal counts; judge agreement |

Caveat on all cost figures: they are Anthropic's list-price illustrations and single internal benchmarks (44 tickets;
Terminal-Bench at 5 attempts per task). Per the owner's "model-dated evidence" memory, re-measure on our own scenarios
before adopting any number.
