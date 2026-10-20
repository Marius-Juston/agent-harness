# r1-memory: auto-memory drift on this machine (#282, round 1)

Search coverage: `uv run dev lit status` on 2026-10-19 showed openalex (key), crossref, europepmc and arxiv up and s2 DOWN (HTTP 429), "discovery: OK, partial" (the lead's discovery line, a few minutes earlier, had s2 up). Also used: the Claude Code memory and subagent docs (WebFetch, 2026-10-19), read-only `git` and `gh` on L1, L2, L3, repo-E, repo-P, repo-N and H, 1,235 local transcripts (main and subagent) in those six projects, and 36 `claude -p` audit runs (Sonnet, Claude Code 2.1.304, 2026-10-19). Code names: L1, L2, L3 and repo-E as in [QUESTION.md](QUESTION.md); **repo-P** is the second long-lived research repo named in the round prompt; **repo-N** is a short-lived personal repo on this machine (5 entries, idle since 2026-10-07), added because it holds the one user-wide preference that leaked. Entries are coded `<repo>-NN` in file-name order; [r1_memory_gold.csv](r1_memory_gold.csv) holds my labels. Entry text, names and paths stay in the scratchpad.

Scripts (all take paths as arguments): [r1_memory_scan.py](r1_memory_scan.py) (inventory and transcript replay), [r1_memory_subagent_load.py](r1_memory_subagent_load.py), [r1_memory_cites.py](r1_memory_cites.py), [r1_memory_stats.py](r1_memory_stats.py), [r1_memory_pointer_check.py](r1_memory_pointer_check.py), [r1_memory_sweep.py](r1_memory_sweep.py).

## Findings

**F1. "Months" of memory drift cannot be observed on this machine. No entry is older than 23 days (VERIFIED, [r1_memory_stats.py](r1_memory_stats.py)).**
- 36 entries across six project memories: L1 11, L2 1, L3 2, repo-E 9, repo-P 8, repo-N 5, and H 0. By type: 25 feedback, 7 project, 2 user, 2 reference.
- At audit (2026-10-19) the youngest entry was 2 days old, the median 10 and the oldest 23. The oldest memory file on the machine was written 2026-09-25, while L1's git history starts 2026-04-18 and L2's 2026-01-19.
- Transcripts reach back only to 2026-09-06, because the platform deletes them after `cleanupPeriodDays` but keeps memory files ([memory docs](https://code.claude.com/docs/en/memory), SOURCED). So no older entry was deleted inside the window, or none ever existed. L1 ships a script that exports and imports its memory between machines (commit of 2026-10-03), so older memory may live on another machine (BELIEVED).
- Consequence: every rate below is a 3-week rate. The age trend in F3 is the only evidence of how drift grows.

**F2. The memory index reaches nearly every session, workers included, contrary to the docs. Topic bodies are almost never opened (VERIFIED, [r1_memory_subagent_load.py](r1_memory_subagent_load.py), [r1_memory_scan.py](r1_memory_scan.py)).**
- The docs say "The main conversation's auto memory isn't loaded into subagents" ([memory docs](https://code.claude.com/docs/en/memory); same on the [subagents page](https://code.claude.com/docs/en/sub-agents), SOURCED).
- On this machine, 1,064 of 1,171 subagent transcripts (91%) carry the parent project's MEMORY.md in their instructions attachment. That held in every version from 2.1.282 to 2.1.303 except 2.1.292 (0 of 12), and for every custom and `harness-ops:` type (`general-purpose`: 58 of 97). The misses cluster in 2.1.282 (77 of 85 missed), 2.1.292 and the Explore, Plan and fork types. This round's own context also carries repo-E's index.
- So a stale entry reaches workers too. That reverses a BELIEVED claim in a repo-E research note that a rule kept only in the lead's auto memory reaches no worker.
- Agents act on the one-line index, not the body. In 1,235 transcripts I found 2 Read-tool opens of a topic file, plus a few dozen `cat` or `grep` calls, mostly by the session that wrote the entry.

**F3. About a quarter of the entries are already partly or wholly stale. Staleness grows with age, and half of it starts within a day of writing (VERIFIED labels, [r1_memory_gold.csv](r1_memory_gold.csv); counts by [r1_memory_stats.py](r1_memory_stats.py)).**
- Labels against the current repo, issues and later transcripts: 26 CURRENT, 7 PARTLY-STALE, 1 STALE, 2 UNVERIFIABLE (they depend on outside state). That makes 8 of the 34 verifiable entries stale (24%).
- By age: 1 of 9 entries younger than 7 days is stale, 2 of 10 at 7-13 days, and 5 of 15 at 14 days or more. The n is tiny and there are no intervals.
- The stale classes:
  - 3 instructions that point at board tickets since finished (sequencing instructions);
  - 1 tool superseded: the entry says to use the arXiv MCP for paper search, but the shared literature client shipped about 1 h after the entry was written;
  - 1 piece of configuration that moved into the repository while the entry kept the old location;
  - 1 piece of advice later measured as harmful;
  - 1 weekly value;
  - 1 entry that says the repository lacks a fact, which the repository added about 1 h later.
- Time to stale: 4 of 8 went stale within a day of writing (about 15 min, 1 h, 1 h and at most 1 day). The other 4 went stale at 6-7 days.
- Only 2 of 8 were corrected. The owner refreshes the weekly value each week, and a research round (H [#254](https://github.com/Marius-Juston/agent-harness/issues/254)) prompted a caveat on day 6. The other 6 have been stale for 8-23 days, median about 15, and are still loaded every session. Five of those six sit in two repositories that have been idle since 2026-10-07 and 2026-10-12, where a stale entry costs nothing until work resumes (BELIEVED).
- 1 entry was deleted in the window: on 2026-10-03 an L1 lead pass that was sizing memory against its context budget moved one entry's content into HANDOFF and a research note, deleted it, and rewrote another whose builder cap no longer matched the workflow changed the day before.

**F4. Two thirds of the entries already have a home in the repository. The fully promoted ones are pure duplicates that load every session (VERIFIED by `git grep` and reading; BELIEVED where judging "the same rule").**
- Promotion: 7 FULL, 17 PARTIAL, 12 NONE. By the docs' own rule, Claude "skips anything your CLAUDE.md files already say" when saving ([memory docs](https://code.claude.com/docs/en/memory), SOURCED). But promotion happens after the entry is written, and nothing deletes the entry afterwards. The L1 pass in F3 is the only promote-then-delete event on record.
- 2 of 36 contradict a current repository rule. One is the superseded tool entry (E-07), which contradicts the researcher prompt's "literature client first". The other (L3-01) is in tension with CLAUDE.md about whether a required CI check is enforced.
- 5 of 36 are user-wide or machine-wide (owner habits, an owner-run server, a machine resource) but are stored per project, so other repositories never see them.
  - One of them, N-03, is an owner preference about what public commits may contain, stated once on 2026-10-05.
  - Since then, 152 of H's 274 public commits carry the content it forbids. H is a different repository, whose memory is empty.
  - I found no later owner reversal, but I did not ask. This is a scope failure, not staleness, and the only candidate harm in this audit (BELIEVED harm; counts VERIFIED with `git log`).
- Entry hygiene:
  - 33 of 36 entries carry a `**Why:**` line.
  - 1 wikilink points at an entry that was never written.
  - 9 of 36 have no `modified` frontmatter: they were written through Bash heredocs or an older version. The platform records `modified` only on its own writes ([memory docs](https://code.claude.com/docs/en/memory), SOURCED).
  - On 3 entries, `modified` lags the file's mtime by more than an hour. For example, a Bash edit on day 6 left `modified` at day 0, so the timestamp claims the entry is fresher than it is.
  - At least 5 entries grow by appended, dated updates, so an older instruction sits above the line that supersedes it (BELIEVED, from reading).

**F5. No verified harmful act traces to a stale entry. Repo rules beat memory when both are in context (VERIFIED, [r1_memory_cites.py](r1_memory_cites.py) and transcript tallies; harm detection is a lower bound).**
- The superseded-tool entry (E-07) was in the context of every repo-E worker (683 subagent transcripts). Even so, 239 of the 260 researcher transcripts that ran a literature search used the shared client first. 4 started with the arXiv MCP, and 17 with WebSearch. I found 0 calls to the uninstalled CLI, excluding this round's own greps. Auditors, who re-check cited papers, started with the arXiv MCP or WebSearch in 48 of 55.
- Explicit citations are rare: 1-8 per entry, almost all by the lead in the session that wrote it. Of the 63 generic "memory says" mentions, the repo-E ones that I read mostly refer to a product research note on memory as a feature, not to auto memory.
- The one entry whose advice did harm (E-09, the `cd`-prefix route for cross-repository workers) was followed by the lead. Its advice was wrong from the start, or was made wrong by a later harness guard; I did not determine which. The H #254 research tied it to worktree-isolation refusals. The entry records 685 and the lead's later summary says 146; I did not re-count.
- Limit: drift that silently steered work leaves no citation. "No harm found" is a lower bound (same caveat as [r1-against](r1-against.md)).

**F6. Mechanisms, measured on these 36 entries (VERIFIED where a script is named; levels BELIEVED).**

| mechanism | what it would have caught here | precision / recall vs labels | cost | level |
|---|---|---|---|---|
| list every entry at orient ([#233](https://github.com/Marius-Juston/agent-harness/issues/233) as written) | nothing new: the index already loads natively in every main session and 91% of workers (F2) | recall 1.0 by construction; precision = base rate 8/36 | ~0 | change: print only flagged entries |
| deterministic pointer check: a named ticket or issue is done, or a named path is gone ([r1_memory_pointer_check.py](r1_memory_pointer_check.py)) | the 3 finished-ticket pointers, plus 2 by chance | 5/17 = 0.29 / 5/8 = 0.62. The false positives are closed issues cited as history in `Why:` lines | seconds, `gh` calls | warning at orient, only for pointers in instruction lines |
| review date (age since `modified` or mtime) | 7-day review: 25 flags, 7 stale (0.28, recall 7/8). 14-day: 15 flags, 5 stale (0.33, 5/8). Half the stale entries were stale within a day, so a date bounds staleness but cannot prevent it | as left | ~0 | warning; needs `modified` written on every edit (F4) |
| model sweep, read-only, repository only ([r1_memory_sweep.py](r1_memory_sweep.py), Sonnet, 2026-10-19) | stale: TP 5, FP 8, FN 2 on my pre-registered labels (0.38 / 0.71). After adjudication it found 1 stale entry I had missed and 2 promotions I had mislabelled: 6/13 = 0.46 / 6/8 = 0.75. Promotion (any): 0.85 / 0.96. Conflict: 0 of 2 found, 1 FP | see left. It missed E-07 and E-09 because their evidence lives in the harness plugin and H's issues, outside the repository | $2.40 API-equivalent for 36 entries (about $0.07 each), 3.9 turns mean | advisory sweep, weekly or on demand; never a gate (the bar in [decision-records-and-drift](../../decision-records-and-drift.md) §5 is 0 FP) |
| promotion then deletion | 7 FULL entries are deletable today. 17 PARTIAL need the rest moved or the entry trimmed | — | one edit each | the rule. A promoted entry is deleted in the same change |
| user-level home for user-wide rules (`~/.claude/CLAUDE.md` or user rules) | the 5 cross-project entries, including the N-03 leak | — | one edit each | rule at write time: "applies beyond this repo? write it at user scope" |
| evidence fields (`verified:` date, `source:` link) | makes the pointer check precise (it reads `source:`, not prose) and gives the review date a real anchor | not measurable on history: no entry has them | template change | add to the harness's memory guidance |

- Subagent `memory:` frontmatter (per-agent `user`, `project` or `local` directories; the first 200 lines or 25KB of its MEMORY.md load into the agent; "project is the recommended default scope", [subagents docs](https://code.claude.com/docs/en/sub-agents), SOURCED) is barely used here. 2 project agent-memory directories exist: one empty, and one in a scratch copy of L1 with 2 entries. A `project`-scope agent memory is checked into git, so it would fall under the repository's own drift checks, unlike auto memory (BELIEVED).
- `/doctor prompt-audit` (v2.1.283+) checks instruction files "for outdated or conflicting content", but its default coverage lists CLAUDE.md, rules, skills, commands, subagents and output styles, not the auto-memory directory. It accepts a path ([memory docs](https://code.claude.com/docs/en/memory), SOURCED). I did not test it on a memory directory.
- The platform's own staleness pressure fires only near the index size limit: "merge or drop stale entries" when MEMORY.md nears 200 lines or 25KB ([memory docs](https://code.claude.com/docs/en/memory), SOURCED). The largest index here is 11 lines, so it never fires.

**F7. The literature agrees that knowledge updates and forgetting are the weak point of agent memory, and that curated memory beats self-built memory. All of it comes from QA or chat benchmarks, is model-dated, and none of it is on coding-agent rule memory (SOURCED, abstracts only).**
- Knowledge updates are one of five core abilities in LongMemEval (ICLR 2025). Commercial assistants and long-context LLMs showed a 30% accuracy drop on memorizing information across sustained interactions (Wu et al., [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813)).
- MemoryAgentBench names "selective forgetting" as a core competency that current memory agents fall short on (Hu, Wang and McAuley, [Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions](https://arxiv.org/abs/2507.05257)).
- A 2026 follow-up reports, from that benchmark's FactConsolidation task, a best result of 54% single-hop and at most 7% multi-hop across all 22 systems, even though "newer facts have larger serial numbers". Separating evidence extraction from policy execution raised accuracy, and a LongMemEval check found no significant gain (26/45 vs 29/45, McNemar p = 0.45) ([arXiv:2606.01435](https://arxiv.org/abs/2606.01435); gpt-4o and gpt-4o-mini; the search index lists this ID under a different title, "Don't Ask the LLM to Track Freshness", so I cite the fetched abstract's title, "Reliable Post-Retrieval Assembly for Agent Memory").
  - Reading for us: freshness should be a deterministic field, not something the model infers (BELIEVED).
- On 111 repository coding targets, directly injecting verified experience raised resolution by 1.1-4.5 points on 4 of 5 held-out solvers. When existing memory systems built their own memory from the same history, 11 of 12 solver-system pairings did not beat memory-off ([VibeMemBench, arXiv:2609.23570](https://arxiv.org/abs/2609.23570)).
  - Reading for us: curated, verified memory is the useful kind (BELIEVED).
- Memory formation itself is attackable at five interfaces across six LLMs ([MemForm, arXiv:2610.14431](https://arxiv.org/abs/2610.14431)). An entry written from tool output is untrusted input that loads into every worker (F2).
- Zep, a vendor system, keeps facts in a temporal knowledge graph with validity intervals and reports up to +18.5% on LongMemEval ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)). This is a vendor-authored, unreplicated result.

## Recommendation for the synthesis (BELIEVED, from F1-F7)
1. Re-scope #233. orient should print one status line per entry that needs attention: it is older than 14 days since `verified:` or `modified`, it points at finished work or a deleted path, or it duplicates a repo rule. It should not reprint the index the platform already loads. Keep it a warning.
2. Make memory hygiene a written rule in the harness's lead guidance:
   - promote the rule into the repository, then delete the entry in the same change;
   - write user-wide preferences at user scope;
   - give every entry `verified:` and `source:` lines;
   - rewrite an entry instead of appending to it.
3. Run the model sweep as an advisory, owner-visible report (weekly or at session start after N days). Give it the harness plugin and the issue tracker so it can see E-07- and E-09-style evidence. Never gate on it.
4. Ask the owner whether N-03 is user-wide. If it is, move it to user scope now. H's commit trailers have run against it since 2026-10-05.
5. Report F2 upstream or pin a probe test: workers inherit the lead's memory, contrary to the docs. The harness should treat memory as reaching workers until a version changes that.

## Gaps seen, not closed
- Months-scale drift: needs another machine's memory or L1's exported archive. On this machine, memory is at most 23 days old (F1).
- I was the only labeller, with no second rater. Three labels were changed after reading the model's output (all evidence-checked, listed in the CSV's `*_prereg` columns), which biases the adjudicated scores upward.
- One sweep run with one model, so run-to-run variance is unknown. Haiku and a sweep with plugin and issue access are untried.
- I did not test `/doctor prompt-audit` on a memory directory.
- I did not test whether workers inheriting MEMORY.md is intended, version-gated or tied to the `instructions` attachment path.
- Harm detection sees only explicit traces (F5).

## Sources fetched
- Claude Code docs, fetched 2026-10-19: [How Claude remembers your project](https://code.claude.com/docs/en/memory) (auto memory, the 200-line/25KB load, topic files on demand, worktree sharing, `modified` field, subagents, prompt-audit) and [Subagents](https://code.claude.com/docs/en/sub-agents) (the `memory` field, scopes, load limit, "isn't loaded").
- Papers (abstracts via `uv run dev lit get`): arXiv [2410.10813](https://arxiv.org/abs/2410.10813), [2507.05257](https://arxiv.org/abs/2507.05257), [2606.01435](https://arxiv.org/abs/2606.01435), [2609.23570](https://arxiv.org/abs/2609.23570), [2610.14431](https://arxiv.org/abs/2610.14431), [2501.13956](https://arxiv.org/abs/2501.13956).
- H issues [#233](https://github.com/Marius-Juston/agent-harness/issues/233) and [#254](https://github.com/Marius-Juston/agent-harness/issues/254) (read with `gh`); H's `git log` since 2026-10-05.
- Private, machine-local, counts only: 36 memory entries, 1,235 transcripts, and the git and issue state of L1, L2, L3, repo-E, repo-P and repo-N.
