# r2: feature inventory beyond static reachability, and a paper consolidation of repo-L's app/ (#111, gap 2)

Sub-question: (a) how can a harness list the features a repo actually provides and uses, beyond import reachability
(test and runtime coverage, CLI and transcript use, feature-to-code maps, behaviour inventories)? What can be copied
from Anthropic's code-modernization plugin and from pyscn's skills? (b) As a paper exercise, what consolidation
would a design-level review propose for repo-L's `app/`, how many LOC would each step remove, and can the total go
past r1's mechanical ceiling of about 25%?

Search coverage: `uv run dev lit status` on 2026-10-12 reported OK, partial: openalex, crossref, europepmc and arxiv
were up; s2 was DOWN (HTTP 429), so Semantic Scholar was skipped on every search. GitHub and raw files were fetched
on 2026-10-12. coverage.py docs came through context7.

Measured at: repo-L `086fb686`. Everything ran on a `git archive` copy in a scratch directory with repo-L's own
interpreter and `PYTHONDONTWRITEBYTECODE=1`. repo-L's `git status` was empty before and after the run. Scripts are
in this folder's `scripts/` (moved there at synthesis): `r2_dev_classes.py`, `r2_orphans.py`, `r2_coverage.py`,
`r2_req_areas.py`, `r2_milestones.py` and `r2_derive.py`. "LOC" follows r1: non-blank lines that are not only a
comment. "Statements" are coverage.py statements, which is a smaller unit than LOC.

## Findings

### F1. Runtime coverage agrees with static reachability: about 94% of repo-L's app code runs, and 99% of what tests run backs a stated requirement (VERIFIED, `r2_coverage.py`)

- **The run.** pytest-cov with `--cov-context=test` (per-test contexts), on 16 xdist workers, deselecting the
  `postgres`, `scale` and `perf` markers. Of 5,938 collected tests, 5,856 passed, 9 failed and 1 was skipped, in
  549 s. All 9 failures are tests that inspect "this checkout" (ratchets, deadline, merge gate). The archive copy
  has no `.git`, which is the likely cause (BELIEVED, not checked one by one).
- **The split of `app/`** (86,100 statements):

  | class | statements | share |
  |---|---|---|
  | never executed | 5,085 | 5.9% |
  | executed only at import, with no test context (module-level defs, tables, models) | 23,758 | 27.6% |
  | executed only by tests that are not any requirement's evidence | 644 | 0.7% |
  | executed by evidence tests | 56,613 | 65.8% |

  "Evidence tests" are the pytest node ids listed under each requirement in repo-L's `docs/requirements.toml`:
  56 requirements, 3,063 distinct ids, 2,432 of which ran here.
- **Runtime and static agree.** r1's static import reach was 93.7% of non-test LOC; here 94.1% of statements ran.
  So static reachability was not hiding much unused code in repo-L (VERIFIED, both numbers).
- **What never runs.** The files with the most never-executed statements:
  - `app/adapters/discord.py`: 573 of 2,904;
  - `app/dev/scale.py`: 171 of 260;
  - `app/dev/workflow.py`: 155;
  - `app/dev/testmon_plugin.py`: 153 of 203;
  - `app/dev/dice_samples.py`: 63 of 63.

  These are live-service and operator paths, not features nobody wanted.
- **Per-requirement code (software reconnaissance).** This is Wilde and Scully's method: compare the coverage of
  the tests that exercise a feature with the coverage of those that do not
  ([Wilde & Scully 1995](https://doi.org/10.1002/smr.4360070105), SOURCED, abstract). The largest blocks of
  statements reached by only one requirement's evidence:

  | requirement | statements unique to it |
  |---|---|
  | DISCORD-FOUNDATION | 10,162 |
  | DEV-AGENT-WORKFLOW | 6,556 |
  | ONBOARDING-CHARACTERS | 1,314 |
  | RULES-PACK-V0 | 1,072 |

  10 of the 56 requirements own no unique statement.

  The abstract warns that the method finds code unique to a feature "though it may not find all components that
  make up the feature's delocalized plan". In line with that, 26,551 of the 56,613 evidence-backed statements
  (47%) serve two or more milestones (`r2_milestones.py`). Code unique to one milestone:

  | milestone | unique statements |
  |---|---|
  | M8 (Discord, web, deployment) | 12,800 |
  | M0 (agent foundation) | 6,680 |
  | M3 (gameplay integration) | 5,285 |
  | M7 (player learning and story) | 3,683 |
  | M5 (backstory generation) | 1,055 |
  | M4 (cities and wars) | 247 |
  | M2 | 51 |
  | M6 | 17 |

  Read these as lower bounds. The evidence tests are integration tests that cross features.
- **Hidden features.** The dice-reveal art has no requirement of its own; it sits inside DISCORD-FOUNDATION:
  - 7,872 of that requirement's unique executed lines are in `presentation/dice` (`r2_req_areas.py`, line basis);
  - 163 of its 335 evidence ids are `test_dice_*` tests.

  The agent harness is in the same position. DEV-AGENT-WORKFLOW holds 569 evidence ids (18.6% of all) and 7,435
  unique lines in `app/dev`. A requirements file therefore inventories features only as finely as its authors cut
  the requirements. Reconnaissance is what exposed these two large features hidden inside other requirements
  (BELIEVED, inference).
- **Cost.** Contexts were cheap. The run took 549 s, against the 410 to 551 s median full-suite time that repo-L's
  own `greentip.py` docstring reports. The analysis scripts take about 5 s on the 2.5 MB `.coverage` file
  (VERIFIED). The coverage.py APIs used are `dynamic_context = "test_function"` (or pytest-cov
  `--cov-context=test`) and `CoverageData.contexts_by_lineno` and `set_query_contexts`
  ([coverage.py contexts docs](https://github.com/coveragepy/coveragepy/blob/main/doc/contexts.rst) and
  [CoverageData API](https://github.com/coveragepy/coveragepy/blob/main/doc/api_coveragedata.rst), SOURCED via
  context7).
- **Pitfall.** Lines run at import time carry the empty context. A first version of the script counted them as "no
  test" and reported 33.5% "unused"; separating `missing` (never executed) from import-only lines corrected this
  to 5.9%. Any harness check built on contexts must make the same split (VERIFIED, both runs of
  `r2_coverage.py`).

### F2. A domain-level surface registry finds feature orphans that import graphs cannot see, but they are thin (VERIFIED, `r2_orphans.py`; registry SOURCED from repo-L's generated `docs/generated/surfaces.md`)

- **What the registry does.** repo-L's own `app/dev/surfaces.py` lists every command, event, process, prompt and
  table from the registries and one AST pass, then computes "reach": play, service, world, setup or none.
- **What it reports.** 46 of 162 commands have reach none, as do 4 of 27 process kinds, 5 of 39 stages and 1 of 27
  prompts. There are 7 dangling event consumers, and 1 of 10 story patterns cannot complete in play. Import
  reachability calls all of this code live, because the handlers are imported.
- **How much code it is.** The 46 orphan commands carry at least 930 LOC: 369 in contract classes, 402 in direct
  `@handles` functions and 159 in private helpers that name the command. That is 0.4% of repo-L. Only 77 of the 162
  contract classes have a direct `@handles` decorator, so the true figure is higher. Even so, orphans here are thin
  entry points onto shared subsystems; deleting them buys little LOC. What the registry buys is a list of owner
  decisions: wire each command into play, or drop it (BELIEVED, inference).
- **Lesson for #113.** A generic harness cannot know the domain registries. It can offer the pattern: a
  repo-declared list of surfaces, with reach computed from declared entry kinds (BELIEVED).

### F3. Anthropic's code-modernization plugin: what it does for a feature inventory, and what to copy (SOURCED, files fetched 2026-10-12 from [plugins/code-modernization](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization))

- **Licence.** Apache-2.0: the plugin folder carries its own `LICENSE`, which is Apache 2.0. Its Python scripts use
  only the standard library (each docstring says "Standard library only").
- **Behaviour inventory (`modernize-extract-rules` and `workflows/extract-rules.js`).**
  - **Shards.** Extraction is split per module. `make_shards.py` caps a shard at 25 files or 5,000 LOC.
  - **Extractors.** One extractor per shard emits Given/When/Then "rule cards". Each card carries one
    `file:line` source, a P0/P1/P2 priority and a confidence. Below High confidence, the card must state the exact
    question for an expert.
  - **Referees.** One referee agent per rule reads only the cited lines and returns confirmed, wrong-citation or
    refuted. A rule supported only by a comment is refuted.
  - **P0 panel.** Each surviving P0 rule then goes to a panel of two independent judges.
  - **Stop rule.** In lens mode, a round that adds fewer than 3 rules, or under 15% of the catalogue, counts as dry.
- **Behaviour-level duplicate finder (the Consolidate phase, `extract-rules.js` lines 620 to 705).**
  - **The prompt.** An agent sees the confirmed rules in chunks of 150, each one line: category, priority, name,
    plain English and source. It runs only when there are at least 12 rules. It is asked to group rules that
    describe "the SAME behavior implemented in more than one place (a C and a C++ version, a fixed-point variant, a
    wrapper that repeats a formula, two modules with the same validation)". It must merge only what "would be
    implemented and tested as one thing (same inputs, same outputs, same thresholds)" and must not open files.
  - **What a merge keeps.** The kept rule takes the highest priority, the lowest confidence and every suspected
    defect and expert question in its group. Every folded rule is logged.

  For #113 this is the missing detector for "parallel mechanisms doing the same job". Clone detectors cannot see
  such duplicates when the code differs; this phase can (BELIEVED).
- **Cost (SOURCED, `modernize-extract-rules.md`).**
  - 4 shards (1,600 lines): 58 agents.
  - A 30,000-line estate: 466 to 647 agents, about 8.8M tokens, 50 to 80 minutes.
  - A run is capped at 1,000 agents; more than about 70 shards must be split.

  Scaled linearly to repo-L's 143,468-LOC `app/`, that is about 2,200 to 3,100 agents and 42M tokens (VERIFIED
  arithmetic; linear scaling ASSUMED). The full inventory is too expensive to run routinely. Run it per module or
  per suspected duplicate pair (BELIEVED).
- **Dead code (`modernize-map.md`).** Entry points "usually live in deployment config", and the command says:
  "Never call something dead if it could be the target of an unresolved dynamic call". Such call sites are recorded
  as observations instead. This matches r1's Alembic and importlib false positives.
- **The owner decision point.**
  - `modernize-reimagine` asks one question: "Which of these capabilities are P0 for the reimagined system, and
    should any be deliberately dropped?"
  - `modernize-brief` stops until explicit approval: "'no objection' is not approval".

  This is the owner-decision step that cutting features needs (F5).
- **Proof scripts worth copying for same-stack consolidation.** In the plugin's terms, a consolidation is an
  "uplift": the contract is the recorded test baseline.
  - `baseline_diff.py` compares fresh JUnit results with the baseline. It reports regressions, new failures, tests
    that no longer run, missing modules and a drop in executed tests.
  - `trace_rules.py` grades each rule id as tested, named-not-run, code-only, claimed-only or none.
  - `proof_pack.py` computes PROVEN / PARTLY PROVEN / NOT PROVEN from six fixed checks. One is a canary: a one-line
    break must turn tests red.

  `baseline_diff.py` (809 lines) and the canary check are the parts to reuse or reimplement. The rest assumes a
  `legacy/` versus `modernized/` layout (BELIEVED).

### F4. pyscn's Claude Code skills are thin wrappers; its community map is the useful piece (SOURCED, the four `SKILL.md` files of [pyscn's plugin](https://github.com/ludo-technologies/pyscn/tree/main/.claude-plugin/pyscn-mcp); trial VERIFIED)

- **Repo.** [ludo-technologies/pyscn](https://github.com/ludo-technologies/pyscn) is MIT, has 1,091 stars, was
  pushed 2026-10-10 and is not archived (GitHub API).
- **The skills.** There are four: `health-check`, `refactoring`, `architecture-review` and `cli-analysis`. Each is
  a table mapping a user request to a `uvx pyscn@latest analyze --select ...` command, plus hints for reading the
  output. The plugin itself only registers the `uvx pyscn-mcp` MCP server.
  - **What is missing.** The skills keep no baseline or ratchet; the before/after comparison is left to the agent.
  - **No pinned version.** They call `@latest`, which works against the pinned, reproducible versions a gate
    needs (BELIEVED).
- **Trial.** `uvx pyscn@1.32.4 analyze --select clones,communities,deps --json` on repo-L's `app/` took 3.5 s and
  scored health 88, grade B.
  - **Clones.** 280 clone groups. Folding each group to one copy would remove at most 4,327 lines; 4,984 of the
    9,336 cloned lines are in `kernel/`, and 3,969 of those are table declarations (`kernel/tables/*`).
  - **Communities.** Leiden found 17 communities, modularity 0.464. Two match cuttable feature slices exactly:
    - one of 34 modules, 31 of them in `app/dev`;
    - one of 53 modules, 50 of them in `presentation/dice`.

    The largest, 94 modules (62 kernel, 30 domain), is the shared world simulation.

  So `--select communities` gives a static, feature-shaped map in seconds, and it found the same two slices that
  F1's reconnaissance and F5 identify (VERIFIED, pyscn JSON `community_context_map`).

### F5. Paper consolidation of repo-L's app/ (design level; read-only; numbers VERIFIED with `r2_derive.py` unless labelled)

**Feature inventory used.** Four sources, cross-checked:
- repo-L's `docs/requirements.toml`: 9 milestones and 56 requirements with pytest evidence (F1);
- its surface registry (F2);
- pyscn communities (F4);
- a module-by-module reading of `app/dev` docstrings.

**Duplicate mechanisms found.**

1. **`app/dev` against H's devkit.** `r2_dev_classes.py` classifies the 61 modules (23,505 LOC) by their
   docstrings; the classification is BELIEVED, the counts VERIFIED:

   | class | LOC | what it is |
   |---|---|---|
   | H | 5,139 | H's devkit already provides it |
   | M | 6,523 | merge and gate throughput for many builders on one machine |
   | X | 668 | repository checks and registries |
   | Q | 771 | mutation testing, swallowed-error lint |
   | P | 8,790 | product verification harnesses |
   | O | 1,614 | ops and live commands |

   The H class, against H's own code:

   | repo-L `app/dev` | LOC | H | LOC |
   |---|---|---|---|
   | `workflow` | 1,952 | `workflow` + `gate` | 761 + 322 |
   | `board` + `board_edit` | 666 | `board` (GitHub issues) | 1,120 |
   | `codegraph` | 966 | `code` | 154 |
   | `weakening` | 781 | `weaken` | 138 |
   | `ratchets` | 682 | ratchet checks inside `checks` | n/a |
   | `ran` | 86 | `ran` | 20 |

   The M class does the same job as H's GitHub-PR plus `integrate` flow (landing gated changes safely) by a
   different mechanism: a local batch-and-bisect queue, merge drivers, flake ledger, memory ledger, testmon
   selection. Test files that import only H- or M-class modules, and no other app package, hold 4,235 LOC.
2. **Dice-reveal art.**
   - **Size.** `presentation/` holds 13,805 LOC; 11,265 of them are in `scenes/`. 30 scenes are registered, and
     `STYLE_RULES` routes them by kind, tag or genre (23 `StyleRule(` lines).
   - **History.** 63 of the 69 commits that ever touched `presentation/` landed on 2026-09-26.
   - **Disabled but tested.** The owner disabled the attractor scene on 2026-09-26 (comment in
     `scenes/__init__.py`), yet `attractor*.py` (691 LOC) and `test_dice_attractor.py` (144 LOC) stay in the
     tree and in the gate.
   - **Tests.** Dice tests total 3,213 LOC; 2,108 of them are per-scene.
3. **Eight evaluation and playtest harnesses** (3,535 LOC): `agent_eval`, `preflight_session`, `playtest`,
   `simulation`, `model_eval`, `interface_bench` and two stub models. Each builds its own throwaway world:
   `agent_eval._world`, `preflight_session._world`, `simulation.SimWorld` and the playtest's enrolment flow. Each
   also drives turns through `application.turns`. One scenario runner and one world fixture would serve all eight
   (BELIEVED; not measured as clones).
4. **The legacy runtime.** r1's frozen v1 tree is 21,359 LOC. The README still says "The existing bot remains a
   separate legacy runtime" and "Runtime migration from the legacy bot is not implemented" (SOURCED, repo-L
   README). Deleting it is mechanical in code but needs the owner to confirm the bot is retired.
5. **Clone groups and orphan commands** (F2, F4): small.

**Steps, in order of risk** (cumulative share of 259,163 LOC):

| step | kind | LOC removed | cumulative |
|---|---|---|---|
| S1 delete the legacy v1 tree | mechanical, owner confirms retirement | 21,359 | 8.2% |
| S2 replace the H class with a dependency on H, plus its own tests | mechanical | 5,449 | 10.3% |
| S3 delete the disabled attractor scene and its test | mechanical (owner already decided) | 835 | 10.7% |
| S4 fold pyscn clone groups in `app/` | mechanical, upper bound | 4,327 | 12.3% |
| S5 drop the M class by adopting H's PR flow, plus tests importing only H/M (less S2's) | owner decision | 10,448 | 16.4% |
| S6 keep 3 dice scenes (bauhaus default, heartbeat, ember), drop 27 and their tests | owner feature cut | 11,407 | 20.8% |
| S7 one scenario runner for the 8 harnesses (30% of 3,535) | design, BELIEVED | 1,060 | 21.2% |
| S8 drop the 46 orphan commands (direct LOC only) | owner feature cut | 930 | 21.5% |

S4 overlaps S5 and S6 by at most 733 lines (the `dev` and `presentation` clone lines).

- **The mechanical subtotal is 12.3%,** lower than r1's 17.7%. r1 counted all of `app/dev` minus a devkit-sized
  remainder as mechanical. This split shows that 6,523 LOC of it (class M) has no counterpart in H. Dropping it
  is an owner choice of workflow, not a deletion of copies.
- **With every owner decision the total is 21.5%** (55,815 LOC). That still leaves 203,348 LOC; a one-fifth
  target would need 151,516 more lines cut.

**Can the reduction go beyond about 25%?** Only through scope or test redesign, which these data cannot show to be
safe (BELIEVED).
- **The code that is left is essential to its requirements.** 94% of statements run, and 99% of what tests run
  backs a requirement (F1).
- **Dropping a milestone frees little unique code.** M4 has 247 unique statements and M6 has 17, while 47% of
  evidenced code is shared (F1). Cutting a scope milestone would mostly remove tests, docs and data, not kernel
  code.
- **The remaining large blocks.** One is the per-feature three-layer split: `domain/` 41k, `kernel/` 37k and
  `application/` 17k LOC. Same-named modules appear in two or three layers: director, mind, look, memory,
  economy, character_depth. The other is the test suite: `tests_engine/` 85k LOC, a ratio of 0.60 to `app/`.
- **Collapsing layers or test suites is the kind of redesign** that, per r1-against-and-safety, ships silently
  broken 4 to 7% of the time for LLM refactorings. It also needs the behaviour inventory and proof of F3 before any
  number can be claimed.
- **A plausible design-level range** for repo-L is therefore 20 to 30%, conditional on owner decisions D1 to D4
  below. Evidence for the owner's 80% does not exist in this data (BELIEVED).

**Owner decisions the proposal needs:**
- **D1.** Retire the legacy bot and its data paths (S1).
- **D2.** Move repo-L onto H's GitHub-issue board and PR/`integrate` flow. That migrates 874 file tickets and drops
  the local merge queue (S2, S5). Alternatively, upstream the useful M pieces into H, such as the flake ledger and
  coverage-based test selection.
- **D3.** Decide the scope of the dice-reveal art: how many styles, and whether it gets its own requirement (S3,
  S6).
- **D4.** For each of the 46 orphan commands, wire it into play or drop it (S8).
- **D5, only for more than 30%.** Decide whether to keep the three-layer architecture and
  what test-to-code ratio to keep.

### F6. What this means for #113's inventory step (BELIEVED, from F1 to F5)

1. **A cheap, deterministic layer that can run on every report.**
   - pyscn `--select communities,clones` (3.5 s) for feature-shaped slices.
   - pytest-cov contexts plus a declared requirement-to-evidence file for reconnaissance. It costs about one extra
     suite run and needs import-only lines treated separately.
   - CLI and transcript usage (r1 F3) for commands.
2. **An expensive, agent layer, run only on a flagged pair or module.** Use code-modernization's extract,
   referee, consolidate and judge loop, its Consolidate prompt and its owner checkpoint question.
3. **Proof for the consolidation PR.** A `baseline_diff.py`-style comparison of test results before and after, plus
   a canary break.
4. **The report separates three things:** mechanical deletions, owner decisions (feature cuts and workflow
   choices) and redesign. The size estimate for each comes with the script that measured it.

## Gaps seen, not closed

- PostgreSQL-marked tests (631 evidence ids did not run) and live-model paths were not covered. Some of the 5.9%
  "never executed" is reached only there.
- The 9 failed tests were not confirmed to fail only for lack of `.git`.
- Code-modernization's extract-rules workflow was not run; it needs the Workflow tool and many agents (F3). Its
  Consolidate prompt was not tested on repo-L, for example on the 8 harnesses.
- S7's 30% is judgement; a pyscn clone pass restricted to those 8 files, or a trial merge, would measure it.
- The dice keep-set (3 scenes) is an illustration, not an owner preference.
- The orphan-command LOC is a lower bound; 85 of 162 contract classes are dispatched without a direct `@handles`.
- The test-suite reduction a layer collapse would allow was not estimated.
- pyscn's MCP server was not exercised, only the CLI the skills call.

## Sources fetched

- Anthropic, `claude-plugins-official/plugins/code-modernization` (Apache-2.0). Fetched files: `LICENSE`,
  `README.md`, `.claude-plugin/plugin.json`, the agents `business-rules-extractor`, `legacy-analyst`,
  `architecture-critic` and `test-engineer`, the commands `modernize-extract-rules`, `-assess`, `-map`, `-verify`,
  `-brief`, `-transform` and `-reimagine`, the scripts `trace_rules.py`, `render_rules.py`, `proof_pack.py`,
  `compare.py`, `make_shards.py` and `baseline_diff.py`, and `workflows/extract-rules.js`.
  <https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization>
- pyscn, the four `SKILL.md` files and the plugin manifest; repo metadata from the GitHub API.
  <https://github.com/ludo-technologies/pyscn/tree/main/.claude-plugin/pyscn-mcp>
- coverage.py docs, contexts and the CoverageData API, via context7 (`/coveragepy/coveragepy`).
- N. Wilde, M. C. Scully, "Software reconnaissance: Mapping program features to code", J. Softw. Maint. Res. Pract.
  1995, [doi:10.1002/smr.4360070105](https://doi.org/10.1002/smr.4360070105). Abstract only, via `dev lit get`.
- B. Dit et al., "Feature location in source code: a taxonomy and survey", J. Softw. Evol. Process 2011,
  [doi:10.1002/smr.567](https://doi.org/10.1002/smr.567). Abstract only: 89 articles from 25 venues classified.
  Orientation; no number used.
- S. Eder et al., "How much does unused code matter for maintenance?", ICSE 2012,
  [doi:10.1109/icse.2012.6227109](https://doi.org/10.1109/icse.2012.6227109), and "Which features do my users
  (not) use?", ICSME 2014, [doi:10.1109/icsme.2014.71](https://doi.org/10.1109/icsme.2014.71). Abstracts only:
  both use dynamic analysis of deployed software to find unused code or features. The abstracts give no figures,
  so none is quoted.
- repo-L at `086fb686`, read-only: README, `docs/requirements.toml`, `docs/generated/surfaces.{md,json}`, module
  docstrings, and git log of `presentation/`.
