# r1: drift taxonomy, measured on agent-harness and project B (#220)

Search coverage: `uv run dev lit status` 2026-10-13: openalex, crossref, europepmc and arxiv ok; s2 down (HTTP 429, shared pool). Most evidence here is these two repositories' own records, read through `gh api` (read-only).

Method. [dump_prs.py](scripts/dump_prs.py) and [dump_issues.py](scripts/dump_issues.py) pulled every merged PR (agent-harness 66, project B 56) with its body, comments, reviews and review comments, and every issue (agent-harness 154, project B 150) with its comments. [drift_candidates.py](scripts/drift_candidates.py) picked out sentences with drift wording (101 documents, 168 sentences). I read those sentences and all 304 issue titles, opened the likely bodies, and classified each drift finding by hand in [findings.csv](scripts/findings.csv). One rater did this, with no second coder, so recall is unknown. [tally.py](scripts/tally.py) counts the table. Current-state scans are separate scripts, named under each finding. A *drift finding* is a defect where one artifact states something that another artifact, or reality, contradicts, and the fix makes them agree. Plain logic bugs and feature requests are excluded.

## Findings

**F1. Most drift was not caught by a check. Today's checks would still miss about half of it (VERIFIED, [tally.py](scripts/tally.py)).** I found 32 drift-finding classes across the two repositories. One of them counts 27 events.
- At the time, a check caught 7 of them, partly caught 3, and missed 22.
- Today's checks (including the board lint, frontmatter allowlist and render test added since) would catch 12, partly catch 3, and miss 17.
- Not caught today, by level: prompts 4, CLI 3, notes 2, decisions 2, model 2, config 1, upstream 1, board 1, memory 1.
- PR review comments are a thin source. 122 merged PRs carry only 12 non-empty comments, and 2 of those are drift findings ([#189 review → #188](https://github.com/Marius-Juston/agent-harness/issues/188), [PR #186 review](https://github.com/Marius-Juston/agent-harness/pull/186)). Reviewers and auditors turn their findings into issues, so the issue bodies were the main corpus (VERIFIED, [dump_prs.py](scripts/dump_prs.py)).

**F2. Taxonomy with counts, what exists today, and the cheapest enforcement.** "Hist" is the number of finding classes in [findings.csv](scripts/findings.csv). "Now" is today's scan. All counts are VERIFIED by the named script unless marked otherwise.

| Level | Hist (caught then / today) | Now | Has today | Cheapest enforcement |
|---|---|---|---|---|
| Prose: paths, links, anchors | 2 (1+½ / 1+½) | not re-run (the gate runs it on every PR) | `check_links` | **check**: extend to gitignored and untracked targets ([AH#6](https://github.com/Marius-Juston/agent-harness/issues/6)) |
| Generated files | 1, 3 events (1 / 1) | — | `check_generated`, integrate regenerates | done |
| Agent prompts and skills vs tools and platform | 4 (0 / 0) | — | word budgets only | **hook** for "use tool X" rules (F5); **generated** text for tool behaviour; prompts point at `--help` |
| CLI commands and flags | 3 (0 / 0) | 0 unknown flags of 74 (AH) and 15 (project B); 101 and 40 bare `` `dev X` `` spans unchecked | command and subcommand names only (`DEV_CMD`) | **check**: options and positionals against the parser, and bare `dev X` spans too (F3) |
| Config, hooks, floors | 2 (0+½ / 0+½) | `min_tests` 1021 against 1497 selected tests | ratchets (budgets only go down) | **generated**: integrate writes the floor raise the gate already prints; **check**: a test that subprocess timeouts fit the hook timeout |
| Research notes vs code | 2 (0 / 0) | 4 note lines name a code constant, 0 disagree | none | **check**: notes cite decision numbers as `` `CONST` value ``, compared to code (F4) |
| Decisions | 2 (0 / 0) | binding decisions live in issue comments, notes, memory | project B: 5 ADRs; agent-harness none | **generated** decision index plus a reference check (input to the ADR sub-question) |
| Board vs merged work | 3, one with 27 events (1 / 2) | lint warnings left standing: AH 90, project B 70; 3 of 32 project-B open issues own deleted paths; 3 open issues have met boxes left unticked | `board sync`, `board lint` (warnings only) | **check** in `board lint`: Owns path deleted in history; **generated**: integrate ticks the boxes the PR marks met |
| Agent memory and HANDOFF | 1 (0 / 0) | 4 of 5 project-B memory entries have no home in the repo, and 1 contradicts it; HANDOFF 3 of 11 claims stale after 4 commits | none | **sweep** at orient: memory newer than HANDOFF; **generated** HANDOFF State section (F6) |
| Template vs project | 6 (3+½ / 5+½) | 26 of 38 rendered files identical; template-owned files identical | `render_check`, update-no-conflict test | **check**: template-side budget ([AH#208](https://github.com/Marius-Juston/agent-harness/issues/208)); pin derived from the template version ([AH#163](https://github.com/Marius-Juston/agent-harness/issues/163)) |
| Upstream platform (Claude Code docs and behaviour) | 3 (0 / 2) | `IMPORT_HOPS = 5` against documented 4, open since 2026-10-12 | frontmatter allowlist | **sweep**: re-fetch the doc URLs that code comments cite |
| Model behaviour over time | 2 (0 / 0) | 8 of 22 (AH) and 31 of 90 (project B) note sentences name a model and a number but no year | none | **check**: the [AH#37](https://github.com/Marius-Juston/agent-harness/issues/37) lint; **sweep**: dated re-run issues |
| Code copies | 1 (1 / 1) | — | consolidation review | **sweep** (exists) |

The "upstream platform" and "code copies" levels are not in the issue's list. They came out of the data: Claude Code changed its docs or its load set three times ([AH#47](https://github.com/Marius-Juston/agent-harness/issues/47), [#44](https://github.com/Marius-Juston/agent-harness/issues/44), [#153](https://github.com/Marius-Juston/agent-harness/issues/153)), and [#141](https://github.com/Marius-Juston/agent-harness/issues/141) duplicated the trigger code. Hook timeouts are counted under config (BELIEVED classification).

**F3. CLI and flags: prose is clean today, but the gate cannot see options or bare `dev X` spans (VERIFIED).**
- [flag_drift.py](scripts/flag_drift.py) walks `devkit.cli.build()` and checks every option written after `uv run dev <cmd> [<sub>]` in tracked text. It found 0 unknown flags of 74 checked in agent-harness and 0 of 15 in project B. A planted `uv run dev board block 5 --nope` was flagged and valid flags passed, so the scan works. It is about 60 lines.
- [bare_dev.py](scripts/bare_dev.py) found 101 backticked `` `dev X ...` `` spans without `uv run` in agent-harness and 40 in project B. The gate's `DEV_CMD` regex never checks these. Among them are 2 and 2 unknown command names and 3 unknown flags (project B's `dev eval --model`). All of these are proposed commands that link their issues (#134, #62, #82), so none is drift today.
- Three past drift findings were invisible to every check:
  - [#179](https://github.com/Marius-Juston/agent-harness/issues/179): prompts said `dev reuse check`, but `--help` did not list it.
  - [#188](https://github.com/Marius-Juston/agent-harness/issues/188): a reason string names `--allow-copyleft`, but the hook entry point reads `DEVKIT_ALLOW_COPYLEFT`.
  - [PR #151](https://github.com/Marius-Juston/agent-harness/pull/151): an issue DONE WHEN used a positional reason, but the CLI takes `--reason`. Issue bodies are not repository text.
- Recommendation (BELIEVED):
  - Fold the flag check into `check_dev_commands`, accepting `` `dev X` `` without `uv run` as well.
  - Run the same function over issue bodies in `board lint`.
  - Message strings that name an option need a per-entry-point test. A regex cannot know which entry point prints them.

**F4. Notes vs code: drift is short-lived when the lead checks by hand, and a check could only see constants that notes name (VERIFIED).**
- The [software-discovery.md](../../software-discovery.md) drift in the issue context lasted 7 minutes. Commit 49ed040 (09:50) still said "Ship R4 in PR #171" and "R0 (PR #171 as built)" after #171 had merged with R4. Commit 3635292 (09:57) fixed it ("note reflects merged PR #171") (`git log -p`).
- Reviewers checked constants against notes by hand twice: "Constants match r1-ranking.md F1/F2", then "`PASS_BEFORE_HOLD=True`, `R_FLOOR=0.20`, `LAMBDA=1.0` ... match `chosen.json` R4" ([PR #171 comments](https://github.com/Marius-Juston/agent-harness/pull/171)).
- [note_constants.py](scripts/note_constants.py) parses 74 upper-case numeric constants in `devkit/`, the hooks and `scripts/`. Only 4 note lines name one, all in software-discovery.md §10, and none disagree. Notes mostly state numbers in prose ("730 days", "λ = 1.0"), and code comments point back to notes (`reuse.py`: "fitted on #169's TUNE split").
- So the cheap check works only under a convention: a number a decision rests on is written as `` `NAME` value `` (BELIEVED).
- The other note finding was note vs its own round file ([PR #148](https://github.com/Marius-Juston/agent-harness/pull/148): section 9 contradicted r2 F8). Only the auditor catches that kind.

**F5. Prompts drift against tool behaviour and the platform, and enforcement beats wording (SOURCED; VERIFIED here).**
- Four prompt findings, none caught by a check:
  - Project B, one issue: subagents were told to write a report file, which Claude Code refuses.
  - Project B, another issue: researcher.md described the old `--source` default.
  - [AH#174](https://github.com/Marius-Juston/agent-harness/issues/174): the auditor prompt never named `dev lit`, and 7 of 11 auditor runs curled index APIs.
  - [AH#174 comment](https://github.com/Marius-Juston/agent-harness/issues/174): a budget trim removed the reason behind a rule.
- Outside evidence:
  - [SWE-CC](https://arxiv.org/abs/2610.06193) (2026, four LLMs, 500 tasks): agents broke 43.1 % of applicable machine-checkable repository policies even when their patches were correct.
  - [TRACE](https://arxiv.org/abs/2606.13174) (2026): memory (Mem0) still left 57.5 % of preference checks violated. Compiling corrections into runtime checks cut held-out violation from 100 % to 37.6 % in distribution and 2.0 % out of distribution, on simulated users (SOURCED abstracts).
- So a "use X, never Y" prompt rule needs a hook. A prompt that describes a tool's flags or defaults will drift unless the text is generated from `--help` (BELIEVED).

**F6. Memory and HANDOFF drift fastest and have no check (VERIFIED, read 2026-10-13).**
- Memory: agent-harness has no memory files. Project B's machine-local auto memory holds 5 entries:
  - 4 owner rules or decisions with no home in either repository (grep of the plugin, template, CLAUDE.md, ADRs, OPEN_QUESTIONS).
  - 1 that contradicts the repository: paper-search says "use the arxiv MCP for paper search", while `researcher.md` puts `uv run dev lit` first.
  - A project-B issue was filed when there were 2 such rules, and there are now 5, so the gap grows while its fix waits.
  - Auto memory is machine-local and does not reach subagents (as that issue states from the Claude Code docs; not re-fetched here).
- HANDOFF: Project B's `docs/HANDOFF.md` (2026-10-13 01:58) has 11 checkable claims. Four commits later, 3 are stale: it says harness v0.1.6, when project B is on v0.1.7 (`.copier-answers.yml`); it says project B "adopts it" in a PR that had merged and was then superseded by a later one; and its Next item is to land that PR. It also omits work now in flight (AH #196, #220).
- agent-harness has no HANDOFF of its own, so its lead state lives in project B's HANDOFF.
- Cheapest fix (BELIEVED): `dev orient` already prints board state. It should list memory entries newer than the last HANDOFF commit and ask for each to be promoted, and the HANDOFF "State" section (tags, open PRs, in-flight claims) should be generated, leaving only judgement as prose.

**F7. Board state: closure drift is automated; semantic drift is not (VERIFIED).**
- Closure drift: 27 "PR merged without closing #n" events (AH 15, project B 12), each caught by board automation and moved back to ready.
- Semantic drift:
  - [open_done.py](scripts/open_done.py) found 20 candidate open issues. I checked 12 by hand and none is fully met. The other 8 were regex noise or DONE WHEN text rebuilt after a `board new` bug in project B, with no merged work behind them.
  - 3 are partly met with stale unticked boxes: [AH#174](https://github.com/Marius-Juston/agent-harness/issues/174) 2 of 3, [AH#143](https://github.com/Marius-Juston/agent-harness/issues/143) 1 of 2, and one project-B issue 1 of 2.
  - [AH#196](https://github.com/Marius-Juston/agent-harness/issues/196) changed its scope in a comment, and the DONE WHEN body was not changed.
- Owns paths: [owns_exist.py](scripts/owns_exist.py) found that 3 of 32 project-B open issues with Owns point at paths that existed and were deleted in its migration to agent-harness: `.claude/agents/researcher.md`, `.claude/skills/lead/` and `devkit/gate.py` (`git log --all`). Other missing paths are files still to be created. agent-harness has 0 such issues of 40, plus 1 malformed Owns (#79).
- History: a `board new` bug emptied DONE WHEN on at least 5 project-B issues, which the lead rebuilt by hand. The lint now catches that.
- Warnings-only checks pile up: `dev board lint` currently lists 90 problems in agent-harness and 70 in project B.

**F8. Template vs project is the largest historical class, and the most of it is caught (VERIFIED).**
- 6 classes; 3 caught at the time and 1 partly; 5 caught today and 1 partly:
  - [AH#22](https://github.com/Marius-Juston/agent-harness/issues/22): update conflicts.
  - [#115](https://github.com/Marius-Juston/agent-harness/issues/115), a project-B issue: local copies of generic files.
  - [#163](https://github.com/Marius-Juston/agent-harness/issues/163): pins never move on update.
  - [#165](https://github.com/Marius-Juston/agent-harness/issues/165): README cite-check failure.
  - [#208](https://github.com/Marius-Juston/agent-harness/issues/208): budget pushed over.
  - A project-B PR: dangling anchor.
- A fresh `copier copy` of v0.1.7 with project B's answers has 38 files. Compared with project B:
  - 26 are byte-identical, including `.devkit/HARNESS.md`, all workflows and `docs/specs/multi-agent.md`.
  - 12 differ. Of those, 6 hold project content (CLAUDE.md, README, HANDOFF, OPEN_QUESTIONS, answers) and 5 hold deliberate settings (settings.json env and skillOverrides, labeler, task.yml, pyproject `quota_cap = 35`, `_typos`).
  - `docs/SETUP.md` differs by 46 lines, and I did not judge which side is current.

**F9. Model behaviour over time: undated model numbers are common in notes (VERIFIED as an upper bound).**
- [model_dates.py](scripts/model_dates.py) (the lint [AH#37](https://github.com/Marius-Juston/agent-harness/issues/37) proposes, run read-only) found that 36 % of note sentences naming a model with a number carry no year in agent-harness (8 of 22), and 34 % in project B (31 of 90). Some carry the year in a neighbouring sentence (for example agent-harness.md §4 ImpossibleBench), so these are upper bounds.
- Behaviour of the "same" model service does change: GPT-4 went from 84 % to 51 % on prime identification between the March and June 2023 versions ([How is ChatGPT's behavior changing over time?](https://arxiv.org/abs/2307.09009) (Chen, Zaharia & Zou 2023), SOURCED abstract).
- Repository decisions that rest on one model and date: [#169 → #176](https://github.com/Marius-Juston/agent-harness/issues/176), [#155](https://github.com/Marius-Juston/agent-harness/issues/155), [#209](https://github.com/Marius-Juston/agent-harness/issues/209).

**F10. Outside baseline: documentation drift is common in human repositories too (SOURCED).**
- Most of more than 3,000 GitHub projects held at least one outdated code-element reference in their docs at some point ([Detecting outdated code element references in software repository documentation](https://doi.org/10.1007/s10664-023-10397-6) (Tan, Wagner & Treude 2023, EMSE)). Their detector looks for code elements that survive in docs after every code instance is deleted. That is the same shape as this repository's dangling-path and command-name checks, and as an Owns-path check.
- [A Large-Scale Empirical Study on Code-Comment Inconsistencies](https://doi.org/10.1109/icpc.2019.00019) (Wen et al. 2019, ICPC) mined 1.3 billion AST-level changes in 1,500 systems and built a taxonomy of code-comment inconsistencies from 500 commits.
- Outdated comments may mislead LLM code completion, not just degrade it ([When Comments Mislead: An Empirical Study of Outdated Comments in LLM-Based Code Completion](https://doi.org/10.1145/3832783.3834359) (Xia et al., ASE 2026)). The abstract fetched gives no effect size, so this is unquantified.

## Gaps seen, not closed
- The classification has one rater and no second coder. The candidate set came from keywords and titles, so recall of drift findings is unknown. A second rater on [findings.csv](scripts/findings.csv) would bound it.
- I did not mine transcripts (this machine's `~/.claude/projects/*/` JSONL). Drift that agents hit and fixed in passing never reaches an issue, so per-level rates from issues undercount, most of all for prompts and CLI.
- I did not judge the `docs/SETUP.md` 46-line difference (F8).
- I did not check whether the [AH#174](https://github.com/Marius-Juston/agent-harness/issues/174) curl behaviour persists after PR #190. That is the issue's own third DONE WHEN.
- Chen et al. 2023 has a published critique (capability vs behaviour; BELIEVED, not fetched). Read it before leaning on the 84 → 51 % figure.
- I did not look at the decision level's supersession mechanics (project B's in-place ADR amendment). That belongs to the ADR sub-question.
- I did not measure how fast HANDOFF goes stale across sessions (one snapshot only).

## Sources fetched
- [Detecting outdated code element references in software repository documentation](https://doi.org/10.1007/s10664-023-10397-6), Tan, Wagner & Treude 2023, EMSE (`dev lit get`, abstract)
- [A Large-Scale Empirical Study on Code-Comment Inconsistencies](https://doi.org/10.1109/icpc.2019.00019), Wen, Nagy, Bavota & Lanza 2019, ICPC (abstract)
- [When Comments Mislead: An Empirical Study of Outdated Comments in LLM-Based Code Completion](https://doi.org/10.1145/3832783.3834359), Xia, Huang, Chen & Zheng 2026, ASE (abstract)
- [Getting Better at Working With You: Compiling User Corrections into Runtime Enforcement for Coding Agents](https://arxiv.org/abs/2606.13174) (TRACE), Zhou et al. 2026 (abstract); its skill repo [YujunZhou/tellonce](https://github.com/YujunZhou/tellonce): 8 stars, pushed 2026-09-12, MIT, not archived (GitHub API). It is too small to adopt, but its idea fits F6.
- [Correct Code, Broken Contributions? SWE-CC: Benchmarking Repository Policy Compliance for Coding Agents](https://arxiv.org/abs/2610.06193), Truong et al. 2026 (abstract)
- [How is ChatGPT's behavior changing over time?](https://arxiv.org/abs/2307.09009), Chen, Zaharia & Zou 2023 (abstract)
- [lycheeverse/lychee](https://github.com/lycheeverse/lychee): 4,004 stars, pushed 2026-10-13, Apache-2.0 (GitHub API). It is already in the template's maintenance workflow.
- Repository records: agent-harness and project B issues and PRs via `gh api` (2026-10-13), git history, `dev board lint`, and a `copier copy` of v0.1.7.
