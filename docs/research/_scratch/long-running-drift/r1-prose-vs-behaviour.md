# r1: prose vs behaviour drift over months (#282, round 1)

Search coverage: `uv run dev lit status` on 2026-10-19 showed openalex (key), s2 (key), crossref, europepmc and arxiv all up; nothing down. Also used: the GitHub API (one repository), read-only `git` on five repositories, 1,138 local Claude Code transcripts, and 7 `claude -p` runs (Claude Code 2.1.304, model `claude-sonnet-5-5`, 2026-10-19).

Code names: **L1** (170 days of calendar time, but 592 of 623 first-parent merges fall in September and October 2026; 18 AGENTS.md and 3 CLAUDE.md revisions), **L2** (9 months, 415 merges; only 22 of 543 commits touch prose agents read, and 13 of its 14 CLAUDE.md revisions fall on one day), **L3** (4 months, 100 merges, 64 CLAUDE.md revisions; active July-August and October), **E** (repo-E, 5 days, 188 merges), **H** (this repository, 8 days, 79 merges). So only L3, and L1 for a few weeks, give months of evidence. "Months" in the brief is mostly calendar time.

Method ([r1_claims_replay.py](scripts/r1_claims_replay.py)). A *claim* is a backticked token in prose, or in a code comment or docstring, that the code can confirm: a path, a file name, an identifier, a `--flag` in a command span, or the word after `dev` in a `... dev X` span. Every claim is replayed over every first-parent merge. A claim is *valid* when its token still exists in the tree or in code, with comments and docstrings stripped (for E, also in the agent-harness tag pinned that day). This is the definition of Tan, Wagner and Treude (references that survive after every code instance is deleted), applied to agent repositories. A **break** is valid -> invalid while the doc keeps the claim. A **co-fix** is when the code change and the doc edit land in the same merge. Records are excluded: board, logs, HANDOFF, CHANGELOG, generated files, research `_scratch`, and agent memory. Breaks are grouped into *episodes* (one doc, one merge; [r1_episodes.py](scripts/r1_episodes.py)). I labelled every episode by hand, one rater ([r1_labels.csv](scripts/r1_labels.csv)): **T** = a current-tense claim made false by a code change; **R** = a dated research snapshot that is now false; **H** = historical, plan or proposal text that is true as written; **X** = a checker false positive (gitignored file, database object, external tool, in-flight work).

## Findings

**F1. Instruction files barely drift mechanically. Agents edit them in the same merge as the code (VERIFIED, [r1_score.py](scripts/r1_score.py)).**
- In 990 first-parent merges across L1, L3, E and H, the mechanical replay raised 3 break episodes in instruction, agent or skill files. All 3 were false positives: 2 historical asides and 1 checker miss.
- When a code change invalidated a claim in those files, the doc was fixed in the same merge for 16 of 17 claims in L3 instruction files (94%), 6 of 8 in L3 skills, 2 of 2 in L1 agent bodies and 2 of 2 in E's CLAUDE.md.
- L2's CLAUDE.md had 0 stale claims at every snapshot (63 checkable claims at HEAD), and an LLM sweep of it found 0 true stale claims (F5).
- H had 0 breaks of any doc type in 79 merges. H has gated backticked paths and `uv run dev` names since its start (BELIEVED cause; no control).

**F2. Specs and design docs are where code-level drift accumulates. They are fixed late, in bulk, by deleting them (VERIFIED, [r1_score.py](scripts/r1_score.py), [r1_snapshots.py](scripts/r1_snapshots.py)).**
- In L3, specs were co-fixed for only 21 of 105 invalidated claims (20%), against 94% for instruction files.
- 31 of 41 spec break episodes were true drift. The mechanical stale share of L3 spec claims peaked at 17.4% of 484 claims on 2026-07-26. For claims older than 30 days it reached 13.8% of 943, against 3.6% for younger ones (2026-08-09).
- Of the 52 true L3 episodes (all doc types: 31 spec, 10 docstring, 10 vendored from one copy event, 1 other), 34 were fixed after a median of 90.7 days (p25 70.6, p75 105.1; median 63 merges). 33 of the 34 fixes were agent-made commits, and 31 of them came from one agent-written commit on 2026-10-18 that deleted every shipped spec and plan (19,127 lines removed, 2,650 added). It replaced them with one feature map and a schema snapshot read from the production database. 31 of the 34 fixes deleted the doc rather than correcting it. No check prompted the commit, and its message names no trigger (how it was noticed is not recorded).
- Replay point (added after the PR #303 audit): L3 was replayed at commit `ab79ea29a1`, which is now on no branch. The auditor's rerun on current branches gives T = 51 true episodes and a median of 83.7 days to fix.
- L3's own instruction file already had a lifecycle rule to delete a spec when its slice ships. The specs drifted for about 3 months anyway, until the bulk deletion.
- In L1, true drift was 3 episodes in 623 merges: 2 in docs, 1 in a docstring. One was fixed in the next merge by an agent; 2 are still open after 0.7 and 9.7 days.

**F3. Code docstrings and comments drift, and doc sweeps miss them (VERIFIED).**
- L3 had 10 true docstring episodes. 5 are in app code and still open after 57-85 days: comments that name types or functions removed since.
- The 2026-10-18 bulk doc rewrite did not touch any of them, because it rewrote docs, not code.
- The other 5 come from L3's in-progress fork of this harness (merges 95-99): docstrings and vendored agent bodies still name `dev lit`, `dev reuse` and `dev cite-check`, which the fork dropped. Its 10 vendored-copy episodes are the same event (a copy, not a link).
- L1's docstring co-fix rate was 3 of 4 claims, and L3's 33 of 49 (67%).

**F4. Research notes "drift" only in the sense of dated snapshots (VERIFIED).**
- L1 had 11 break episodes in research notes, all R. E had 4: 2 R (the pre-migration `devkit/` paths), 1 claim pinned to a named commit (H) and 1 file of an external tool (X). They are notes recording "what exists today".
- Co-fix share in research notes: 0 of 11 (L1) and 0 of 12 (E).
- H's path gate already skips the research directory, which is the right call. They are dated evidence, and a gate would make agents rewrite history (BELIEVED).

**F5. An LLM claim-check sweep finds about twice as much true drift as the token check on drifted docs, but on maintained instruction files it raises mostly false alarms (VERIFIED, [r1_llm_sweep.sh](scripts/r1_llm_sweep.sh), [r1_sweep_score.py](scripts/r1_sweep_score.py), labels [r1_sweep_labels.csv](scripts/r1_sweep_labels.csv)).**
- Setup: 7 runs, one doc each, read-only tools (Read, Grep, Glob), no hooks, no git history. I verified every STALE verdict by grep or by reading the code at the same snapshot.
- Overall: 48 flags, 36 true, so precision is **0.75**. The false positives were: 5 historical or proposal text, 3 cases where the model searched badly (the file or function exists), 1 misread scope, and 3 ambiguous.
- By doc state:

  | docs | runs | true / flags | precision |
  |---|---|---|---|
  | already drifted (L3 architecture doc and spec, L1 proposal doc) | 3 | 34 / 39 | 0.87 |
  | maintained instruction files (L1 AGENTS.md, L2 CLAUDE.md, L3 CLAUDE.md before and after its rewrite) | 4 | 2 / 9 | 0.22 |

  The 2 true flags on maintained files were in L3's CLAUDE.md, rewritten the day before:
  - a hook described as blocking once per diff, which in code re-asks up to 3 times;
  - a displayed fallback string that differs from the code.

  Neither is a backticked token.
- Recall on stale claims known beforehand: 7 of 9 (all 7 found by the token replay). It missed the 2 that an earlier in-the-wild audit found: a symbol named without backticks, and a git branch name (git state is not code).
- On the three drifted docs the sweep found 34 true stale claims, against 15 stale tokens (T) from the token replay. The extra ones are semantic: versions, defaults, API names, "no offline cache", "read-only screen", removed sections, and test counts.
- Cost: 2.70 M input tokens (mostly cache reads), 79 k output tokens, 527 s and USD 2.36 at list price for 7 docs, 433 claims and 18,700 words of prose. That is about USD 0.34 and 75 s per doc. Model-dated: `claude-sonnet-5-5`, 2026-10-19.

**F6. An owner-triggered LLM audit in the wild found stale instruction text that had been wrong for about 99 days (VERIFIED from transcript and git).**
- On 2026-10-11 an L3 session ran a "prompt audit" of CLAUDE.md, commands, agents and skills, and merged PR 70. Among its fixes were 3 stale facts:
  - a branch named as current, wrong from the day it was written (2026-07-05; the branch had merged the day before);
  - a view named without backticks, deleted on 2026-07-04;
  - a database column the skill called current, though it was retired.
- It also left 2 contradictions between config files for the owner to decide.
- The mechanical replay saw none of the 3 (unticked text, git state, and a database object).
- So in L3, instruction-file drift was semantic and was caught only by an LLM pass the owner started.

**F7. Agents acting on stale prose: one confirmed case. The agent noticed and stopped (VERIFIED, [r1_transcripts.py](scripts/r1_transcripts.py), [r1_tx_errors.py](scripts/r1_tx_errors.py)).**
- On 2026-10-11 the owner asked an L3 agent to implement a design doc. The doc had specified a sheet that the code deleted on 2026-07-19 (84 days stale).
- The agent grepped for the type, found it gone, and used `git log -S` to find the commit that removed it. It then wrote a plan that opens by naming the conflict and asking the owner to decide before building. The cost was one owner round trip and about 6 tool calls.
- Scan of stale tokens inside their stale windows: L1 0 hits in 46,404 tool calls (407 transcripts); L3 34 hits, from this session and from the harness-fork work on merge 95-99. E's 373 hits are confounded: the same `devkit/` paths are live in the agent-harness checkout those sessions also edit.
- Failed tool calls whose missing path or rejected argument a prose file named that day: L3 4 of 12 failures, L1 20 of 148. I traced the source of 9 of these 24. None came from stale prose: branch or worktree skew (a command not yet on the agent's branch), argument mistakes, and the agent's own outdated idea of a split module. The other 15 are unread.
- Silent misuse, where an agent follows stale prose and nothing fails, cannot be measured this way.

**F8. CLAUDE.md growth and churn (VERIFIED, [r1_growth.py](scripts/r1_growth.py); "rule" = a bullet or numbered line outside fences, so an edited rule counts as removed plus added).**

| repo | file(s) | revisions | days | words start -> max -> end | rules start -> end | added / removed | agent revisions |
|---|---|---|---|---|---|---|---|
| L3 | CLAUDE.md | 64 | 119 | 617 -> 8,153 (day 63) -> 1,094 | 24 -> 33 | 230 / 221 | 45 |
| L3 | CLAUDE.md + 3 path rules | 64 | 119 | 617 -> 8,153 -> 5,408 | 24 -> 93 | 243 / 174 | 45 |
| L1 | AGENTS.md | 18 | 22 | 454 -> 1,041 | 11 -> 36 | 33 / 8 | 15 |
| L2 | CLAUDE.md | 14 | 163 (13 on one day) | 611 -> 2,585 | 28 -> 59 | 48 / 17 | 8 |
| E | CLAUDE.md | 7 | 2 | 506 -> 59 (rules moved into the template's HARNESS) | 17 -> 4 | 6 / 19 | 7 |
| H | template HARNESS.md | 8 | 1 | 513 -> 543 (word budget) | 17 -> 17 | 6 / 6 | 8 |

- L3 grew about 120 words a day for 63 days, with no budget. It was then cut by moving 4,000 words into path-scoped rules, so total instruction text still ended at 8.8 times the start.
- Large-sample context: Chakrabarti reports +226% growth and +4.9 net instructions per commit over 247,694 instruction lifetimes. Chatlatanagulchai et al. find that context files "evolve like configuration code through frequent, small additions" (2,303 files). SOURCED, below.
- The budgeted files (H, E) are flat.

**F9. The `uv run dev`-span check (#229) on held-out merges: 0 true events and 1 false positive (VERIFIED, [r1_cli_heldout.py](scripts/r1_cli_heldout.py), which reuses the #220 worker and its five syntax rules unchanged).**
- Held-out data: H merges 69-77 (9 merges, 5,728 span occurrences) and E merges 56-187 (132 merges, 32,679 span occurrences), all made after the #220 replay.
- Gate-eligible flags: E 0. H 1, a false positive: [decision-records-and-drift.md](../../decision-records-and-drift.md) quotes the historical bad span `uv run dev board block <n> "<why>"` as an example.
- Bare `dev X` spans: 3 old findings in E (future commands, as in #220) and 5 in H. These are outside the gate's scope.
- Consequences for promotion: E passes the 30-merge, 0-FP bar. H fails it until research notes are skipped (or quoted spans are exempt) the way the path check already skips them. #229 never fired on a real drift in held-out data.

**F10. Generated docs and pin tests show up as the mechanisms that hold at scale (VERIFIED counts; effect BELIEVED, no control).**
- L1 keeps 113 generated reference files, which its gate fails when stale. They were regenerated in 159 of 623 merges (26%), so that prose cannot drift.
- L1 also has a test (`test_prompts_render_code_names_from_live_catalogs_and_reject_drift`) that pins the code names in its prompts to live catalogs.
- L1 shows the lowest break rate of the active repositories: true drift in 3 of 623 merges.
- In L3 the fixes did not converge on a check either: drift in vendored copies came from copying instead of linking, and the bulk rewrite replaced specs with a feature map plus a schema snapshot taken from the database.

## Mechanism precision measured here

| mechanism | where replayed | flags | true | precision | notes |
|---|---|---|---|---|---|
| token-existence break check (Tan et al. style), at merge, all prose | L1 + L3 + E, 911 merges | 108 episodes | 68 (T+R) | 0.63 | instruction/agent/skill 0/3; spec 31/41; docstring 11/16; plan 0/10; research R 13/15 |
| same, without plan and research docs | L3 | 79 episodes in 23 of 100 merges | 52 | 0.66 | about 1 warning per 4 merges in L3; L1 4 episodes in 623 merges |
| LLM sweep, drifted docs | L3, L1, 3 runs | 39 | 34 | 0.87 | about 2x the token check's yield (15 tokens) |
| LLM sweep, maintained instruction files | L1, L2, L3, 4 runs | 9 | 2 | 0.22 | 3 model search errors; recall 0/2 on the audit's finds |
| `uv run dev` span parse (#229), held-out | H 9 + E 132 merges | 1 | 0 | 0.00 | the FP is a quote in a research note |

## Gaps (seen, not closed)
- One rater for every label (episodes and sweep flags), with no inter-rater agreement. The precision figures above are single-rater.
- No months-long repository with a mechanical prose gate. H and E are days old, so the claim "a gate keeps instruction drift at 0" has no long-run control. L1, which has generated docs but no prose path gate, comes closest.
- Recall of the token check against all semantic drift was measured only on 7 docs (F5). Recall of the LLM sweep beyond the 9 known items is unknown: no full hand audit of any doc was done.
- Only one model (Sonnet 5.5) and one prompt were tried for the sweep. Haiku cost and precision were not measured, and repeated runs (variance) were not either.
- Silent misuse of stale prose by agents is not measurable from tool errors. A planted-stale-claim experiment would measure it.
- L3's main contributor works on another machine, so most L3 sessions (and the trigger for the 2026-10-18 rewrite) are not in local transcripts.
- Docstring drift in Python repositories (L1, E, H) was replayed, but only L3's app-code comments showed long-lived cases.

## Sources fetched
- Tan, Wagner and Treude, [Detecting outdated code element references in software repository documentation](https://doi.org/10.1007/s10664-023-10397-6), EMSE 2023 (abstract): most of over 3,000 GitHub projects held an outdated code element reference at some point. SOURCED.
- Tan, Wagner and Treude, [Wait, wasn't that code here before? Detecting Outdated Software Documentation](https://arxiv.org/abs/2307.04291), ICSME 2023 (abstract): more than a quarter of the 1,000 most popular GitHub projects held at least one outdated reference; the method was shipped as a GitHub Action. SOURCED.
- Wen et al., [A Large-Scale Empirical Study on Code-Comment Inconsistencies](https://doi.org/10.1109/icpc.2019.00019), ICPC 2019 (abstract): 1.3 billion AST-level changes over 1,500 systems, plus a taxonomy from 500 commits. SOURCED.
- Liu et al., [Just-In-Time Obsolete Comment Detection and Update](https://doi.org/10.1109/tse.2021.3138909), TSE 2021 (abstract): generated comment updates matched the ground truth in 41.8% of predicted-obsolete cases. SOURCED.
- Chatlatanagulchai et al., [Agent READMEs: An Empirical Study of Context Files for Agentic Coding](https://arxiv.org/abs/2511.12884), TOSEM (abstract): 2,303 context files from 1,925 repositories that "evolve like configuration code through frequent, small additions". SOURCED.
- Chakrabarti, [Why Does CLAUDE.md Keep Growing? Catastrophic Remembering in Agentic Coding](https://arxiv.org/abs/2608.11095), preprint 2026 (abstract): +226% lifetime growth, +4.9 net instructions per commit, deletion log-hazard -0.032 per commit. SOURCED.
- Sharma, [ContextCov: Deriving and Enforcing Executable Constraints from Agent Instruction Files](https://arxiv.org/abs/2603.00822), preprint 2026 (abstract): compiling instructions into checks gave 88.3% constraint compliance against 67.0% for prompt-only, on SWE-bench Lite. Repository: [reSHARMA/ContextCov](https://github.com/reSHARMA/ContextCov), MIT, 1 star, pushed 2026-09-05 (GitHub API). SOURCED; a research prototype, not a dependency candidate.
- Badrinarayan and Parthasarathy, [DocSync: Agentic Documentation Maintenance via Critic-Guided Reflexion](https://arxiv.org/abs/2605.02163), 2026 (abstract): judge score 3.44/5 against 1.91 for CodeT5 on a proxy task; no drift-detection precision reported. SOURCED.
- Found but not used for numbers (the abstract gives none): Xia et al., [When Comments Mislead: An Empirical Study of Outdated Comments in LLM-Based Code Completion](https://doi.org/10.1145/3832783.3834359), ASE 2026.
