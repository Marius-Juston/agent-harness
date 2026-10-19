# r2: why researchers and the lead cost what they cost, and which changes cut it

Round mode, sub-question of #254 (round 2). Data: the repo-E transcripts for 2026-10-11..16 (14 lead sessions with
calls, 644 subagent runs; this session, 50a10312, excluded). Script: `r2_cost_phases.py` in this folder, run as
`python3 r2_cost_phases.py ~/.claude/projects/<repo-E project dir> ~/GitHub/repo-E --exclude 50a10312`.
Every number labelled VERIFIED comes from that script's output unless another script is named.

Search coverage: no literature search was needed (measurement question). The launcher's `dev lit status` line read
"discovery: OK (openalex, s2, crossref, europepmc, arxiv answered)"; Claude Code and API docs were read through context7
on 2026-10-19.

## Method in four lines
- A turn's cost is its cache read, cache write, input and output at the r1 fitted prices (input 4.09, output 20.05,
  cache read 0.20, cache write 7.95 $/M). Transcript `output_tokens` are streaming snapshots: 4.2 M against 24.8 M in
  the cost-state records. So a turn's output is its visible characters x 0.443 tokens/char, plus 175 hidden thinking
  tokens per turn (ASSUMED: hidden thinking spread evenly). The modelled total is **$2,314 against $2,231** in
  cost-state (VERIFIED).
- Tokens per character come from a least-squares fit of cache growth on 30,818 turns where the cache held:
  **0.426 tokens/char** for tool and user content (2.35 chars/token) and 0.443 for assistant content, R² 0.56 (VERIFIED).
- "Carried cost" = a piece's tokens x (one cache write + one cache read on every later turn before a compaction). This
  model covers 77-81 % of actual cost. The rest is cache-miss rewrites and hidden output (VERIFIED).
- Phases: each tool call is classified by tool and command (`phase()` in the script). A turn's cost is split evenly over
  the phases of its calls.

## 1. Findings: researchers (50 % of modelled spend, $1,157 over 259 runs)
1. **Volume, then length. No single content type dominates.** Researchers are 259 runs against 141 builders. Of those,
   211 are round-mode runs ($788, 68 % of researcher cost) spread over 38 research questions: a median of 6 runs and
   $18 of round runs per question. Round 1 alone has 147 runs at a median of $2.84 and 55 turns; round 2 has 47 runs
   at $3.72 and 73 turns. Issue-mode synthesis is 44 runs, $356. (VERIFIED)
2. **A run costs $4.47 because context x turns compounds.** Runs have a median of 64 turns (p90 115). Median context
   grows 27k → 57k (turn 10) → 124k (turn 40) → 166k (turn 60) → 260k (turn 100). **31 % of researcher cost falls in
   turns after the 60th.** By token kind: cache read 44 %, cache write 33 %, output 23 %, input 0 %. Researchers and
   auditors run at **effort high** (5,989 and 692 requests). Builder, reviewer, security-reviewer, tester and the lead
   run at medium. (VERIFIED, from `api-request` records of CLI 2.1.300+)
3. **By phase (turns / cost)**: source reading 22 % / 17 %, literature search 15 % / 14 %, reading other files
   (scratch, code, persisted outputs) 16 % / 14 %, "other" 16 % / 13 % (ToolSearch 432 calls, shell loops, mkdir),
   **writing 6 % / 15 %** (the note text is output: 638 Write calls carry 10.1 M chars, 45 % of all visible researcher
   output), reading existing notes 5 % / 5 %, cite-check 4 % / 5 %, computing 5 % / 5 %, PR and git 6 % / 6 %,
   orientation 2.5 % / 3 %, waiting 1 % / 1 %. (VERIFIED)
4. **What the context carries (share of carried cost)**: base prompt (system, tools, CLAUDE.md, task; median 26.6k)
   17 %, other file reads 16 %, search results 12 %, existing-note reads 9 %, sources 8.5 %, the run's own written text
   8 %, **skill listing 3.4 % (11.7k chars per run)**, the rest under 2 % each. A WebFetch result is small (median 911
   chars, because WebFetch already returns a model-made extract), so source reading costs turns, not context. (VERIFIED)
5. **Persisted outputs are read back whole.** 513 Bash outputs were too large and went to `tool-results/`. Agents then
   Read those files 196 times (5.75 M chars, only 16 with offset/limit), for **$52 carried**, the largest single
   avoidable item. Most of them started as `cat` or `sed` of notes. `read_guard.py` caps only `.py`/`.md` etc.
   (`limit_for`), so a `.txt` persisted output passes. (VERIFIED. Guard read in `plugins/*/scripts/read_guard.py`)
6. **Note size does not drive auditor or extender cost.** Auditors (n = 47 with a size known at run start):
   Spearman(size, cost) = 0.01, and runs below/above the 68 KB median cost $1.46 / $1.50. Extenders (n = 15): 0.09.
   Size does raise the target-note chars read (ρ 0.20 and 0.24; median 56k and 49k chars read). All reads of existing
   notes carry $102. Whole-note reads over 30k chars carry $29. (VERIFIED. Size is the file's size at the last commit
   on any ref before the run, from `git rev-list --before`)
7. **`dev lit` output vs use** (researchers): 1,163 searches and 1,006 gets. Search output is 5.5 M chars, get 4.3 M.
   - Of 14,279 papers returned (unique per run), **18 % were followed up and 16 % cited** in the run's own writing.
     2,079 of 2,529 follow-ups went through `dev lit get`.
   - **Snippets are 46 % of search output, but an 8-word run of a snippet reappears in only 16 of 2,249 cited papers
     (0.7 %).** Venue strings reappear for 18 % of cited papers. Repeat returns within a run are 7 % of output.
   - `get` output is 75 % abstract, 14 % record line, 3 % authors.
   - Agents already pass small `-n` (3-8 most common).

   (VERIFIED. Format read in `devkit/lit.py`: `Paper.line`, `SNIPPET = 240`, `DEFAULT_N = 8`)
8. **Batching**: 72 % of search/source turns make a single call. 1,499 back-to-back single-call turns use the same tool
   (8 % of researcher turns). (VERIFIED)

## 2. Findings: the lead (16 % modelled, $372 over 3,251 calls)
1. **Context is the cost.** Mean context per call is **372k**. 73 % of calls run over 200k, 40 % over 400k, 18 % over
   600k. Cache read is 64 % of lead cost. Context climbs to 760-966k in the two long sessions. (VERIFIED)
2. **Compaction happens only at the ceiling.** All 5 compactions fired at 501-966k (4 of 5 at 783-966k), and context
   dropped to 51-57k (one to 145k). (VERIFIED) The owner ran `/compact` twice (r1-usage-logs.md §6).
3. **Worker completions drive the lead's calls.** Episodes started by a worker completion (a hand-back report and/or a
   task notification, 1,249 events, 1.8 turns each) are **70 % of lead cost**. Owner prompts are 22 %, goal check-ins
   and stop hooks 5 %, slash commands 3 %. (VERIFIED)
4. **Hand-backs fill the context.** Hand-back reports are 38 % of the lead's carried content cost: 985 reports,
   median 4.3k chars, p90 5.6k, $75. Task notifications are 14 %: 1,113 at about 1.4k chars, $28. The notification
   text says the report "is not repeated here", so this is fixed boilerplate. Agent tool results add 6 %. (VERIFIED)
5. **Rejected: double wake-ups.** Only 78 lead turns were woken by a task notification alone ($9). The rest arrived
   together with the hand-back or a tool result. (VERIFIED)
6. **Polling is negligible.** The lead made 21 waiting turns ($2). All agents together made 322 ($18, 0.8 %).
   Monitor instead of `sleep` is a latency or tidiness change, not a cost lever. (VERIFIED)

## 3. Simulated levers (same runs, same turns; no quality or re-work cost modelled unless stated)
- **Lead auto-compaction at H, down to the measured 57k** (cost of a compaction = one read of the context + 12k
  output + a rewrite of 57k):
  - H = 150k / 200k: $194-195, **48 % less**.
  - 300k: 45 % less. 400k: 42 % less.

  (VERIFIED simulation. Re-orientation turns after compaction are not modelled: BELIEVED small next to the saving,
  since one lead call at 372k costs about $0.07 in cache reads alone.)
- **Researcher or builder auto-compaction** to 60k: −1 % (150k) or 4 % (200k) for researchers, 4 % for builders.
  Runs are too short for compaction to pay. (VERIFIED)
- **Tool-result clearing** (what API `clear_tool_uses` does):
  - Naive policies cost more. Trigger 100k / keep 15: 11 % more. Trigger 150k / keep 15: 4 % more.
  - An oracle that clears only when the clear repays itself saves at most 2-4 %.
  - The reason: a 1-hour cache write costs about 40x a cache read here (7.95 vs 0.20), and clearing invalidates the
    cached prefix ([context-editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing):
    "Invalidates cached prompt prefixes when content is cleared"; SOURCED).
  - Claude Code does not send it anyway: every one of 17,239 logged requests carried only
    `context_management: {edits: [clear_thinking_20251015, keep: all]}`. (VERIFIED)

  (VERIFIED simulation)
- **Search+source helper subagent** returning a 1.5k-token extract per 10 turns: 3 % less on Opus 5.5, **16 % less on
  Sonnet 5.5** (VERIFIED simulation). The extract size is ASSUMED.
- **Whole researcher role at Sonnet 5.5 prices, same tokens: 28 % less** (VERIFIED arithmetic). This is an upper bound
  on price alone. Sonnet 5.5 halves input, cache write and output, but cache reads cost the same $0.20 on both
  (r1-blog-sonnet-evals-cost-effort.md §1, SOURCED there from the Sonnet 5.5 post). That is why the saving is 28 % and
  not 50 %.

## 4. Ranked changes (savings against the window's $2,314 modelled spend)
| # | change | estimated saving | basis | quality risk |
|---|---|---|---|---|
| 1 | Lead compaction cadence: `autoCompactWindow` about 200-300k for the lead session ([settings-reference](https://code.claude.com/docs/en/settings-reference), SOURCED; range 100k-1M), or `/compact` after each integrate batch with HANDOFF current | **$160-178 (42-48 % of lead, 7-8 % of all)** | VERIFIED sim | Lost thread after compaction; Claude Code clears older tool outputs first, then summarizes ([glossary](https://code.claude.com/docs/en/glossary), SOURCED). Measure re-orientation turns and integrate errors |
| 2 | Researchers (or only their search/source part) on Sonnet 5.5 behind a pre-registered eval | $188 (helper, 8 %) to $325 (role, 14 %) | VERIFIED sim / arithmetic | Unmeasured. Citation accuracy and verification are the risk; Sonnet aims at "investigation, review" (r1 blog note) |
| 3 | Research fan-out: fewer round-1 researchers per question (median 6 now) or stop at saturation earlier | about $3-4 per researcher run removed; 10 fewer r1 runs ≈ $28 | VERIFIED per-run cost | Coverage loss; needs the research skill's saturation rule to decide, not a fixed cut (BELIEVED) |
| 4 | Read guard also windows `Read` of `tool-results/*.txt` without limit, and `cd X && cat note` | about $30-35 of $52 carried (1.5 %) | VERIFIED carried; saved share ASSUMED 60-70 % | Low: a window plus "grep it" note keeps access |
| 5 | Shorter hand-backs (cap about 1.5-2k chars; details in the PR body or issue) | about $35-40 of $75 (10 % of lead, 1.6 %) | VERIFIED carried; halving ASSUMED | Lead may need a follow-up read of the PR. Measure lead follow-up reads |
| 6 | Keep the skill listing out of subagents (disable unused plugins per project, or `disable-model-invocation` on skills workers never call) | about $30 (1.3 %) | VERIFIED carried | Low |
| 7 | Effort medium for researchers (now high; output is 23 % of their cost) | unmeasured | VERIFIED effort setting | Unknown; eval needed |
| 8 | Batch independent searches and fetches in one turn (prompt line) | about $19 (0.8 %) | VERIFIED count; half merge ASSUMED | Low |
| 9 | `dev lit search` without snippets by default (or 120 chars) | about $17 (0.7 %) | VERIFIED: 46 % of output, 0.7 % reused | Triage precision may drop and cost more `get` calls |
| 10 | Note size cap or split notes | ≤ $29 (1.3 %) | VERIFIED: no size-cost association | Not worth a rule on cost grounds |
| 11 | Monitor instead of `sleep`/`until` | ≤ $18 (0.8 %) | VERIFIED | n/a, not a cost lever |
| 12 | Tool-result clearing | negative to +4 % of researcher | VERIFIED sim | Not offered by Claude Code; drop |

## 5. Gaps seen, not closed
- **No quality data for any lever.** Every saving above assumes equal quality. Items 1, 2, 3 and 7 need pre-registered
  evals or A/B sessions (cost per merged issue, review rounds).
- Hidden-thinking allocation is an assumption. If thinking concentrates in writing turns, the "writing 15 % of cost"
  figure moves.
- The carried model covers 77-81 % of cost. The rest (cache-miss rewrites) is not attributed by phase.
- The helper simulation's extract size (1.5k tokens per 10 turns) is assumed. A one-week trial with a real
  Sonnet-backed source reader would measure it.
- ToolSearch costs 432 researcher turns (1.7 per run) to load WebFetch, WebSearch and arxiv schemas. Whether an agent
  `tools:` list or a setting can keep them loaded was not checked.
- Lead re-orientation cost after compaction is not measured. Only 5 compactions occurred.
- Note size at run start uses the last commit on any ref. Uncommitted drafts are missed.

## Sources fetched
- Transcripts: `~/.claude/projects/<repo-E project dir>/` (*.jsonl, */subagents/*.jsonl + .meta.json), read by
  `r2_cost_phases.py` (VERIFIED). Cost-state totals and price fit: `extra_stats.py`, r1-usage-logs.md.
- `devkit/lit.py` (`Paper.line`, `Paper.full`, `SNIPPET`, `DEFAULT_N`) and `plugins/*/scripts/read_guard.py` in this
  worktree.
- https://code.claude.com/docs/en/settings-reference (`autoCompactWindow`, `autoCompactEnabled`), via context7.
- https://code.claude.com/docs/en/glossary (Compaction), https://code.claude.com/docs/en/sub-agents (subagent
  auto-compaction, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`), https://code.claude.com/docs/en/hooks (PreCompact), via context7.
- https://platform.claude.com/docs/en/build-with-claude/context-editing (clear_tool_uses options; cache invalidation),
  via context7.
- r1-blog-sonnet-evals-cost-effort.md §1 (Sonnet 5.5 vs Opus 5.5 prices), in this folder.
