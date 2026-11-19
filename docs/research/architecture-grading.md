# Research: architecture grading for agent-built code: fitness functions, layering contracts, coupling and complexity hotspots

Status: v1 (2026-11-19). Issue: #496. Serves the `dev arch` build issues in Follow-ups.
Labels: SOURCED = cited and fetched; VERIFIED = measured in this repo (script named); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit status` on 2026-11-19 showed openalex, s2, crossref, europepmc and arxiv up; s2 then rate-limited after the first query (`note:` lines), so later `dev lit search` runs used openalex alone. Papers read as abstracts unless a section is named. Tool metadata from `uv run dev reuse check` (PyPI) and the GitHub API on 2026-11-19.
Companions: [architecture-consolidation.md](architecture-consolidation.md) (the review trigger, `dev consolidation report` and the architect agent this note extends; its §7 tool table is the starting point), [test-floors-and-coverage.md](test-floors-and-coverage.md) (the ratchet pattern `.devkit/ratchets.toml` already uses). Scripts: [_scratch/architecture-grading/scripts/](_scratch/architecture-grading/scripts/history.py).

## 0. Findings in one paragraph
Two deterministic checks carry almost all the signal, and both fit in a handful of output lines. **(1) Declared import contracts, ratcheted in the gate.** Consolidation review 1 broke a `devkit.* -> devkit.cli` import cycle on 2026-10-13; nothing kept it broken, and PR #417 brought the same pattern back 89 first-parent commits later (`checks -> cli`), where it sat until review 2 found it and step #449 removed it again. Two import-linter contracts (no cycle among `devkit` siblings; only the entry point imports `devkit.cli`) report BROKEN at both review commits and KEPT at today's main, so in the gate they would have refused #417 at its own PR (VERIFIED, §4). **(2) A churn x size hotspot ranking.** Replayed over all 166 first-parent commits, "first-parent commits touching the file in the last 30, times its LOC" ranked `devkit/workflow.py` first without a break from commit 80 (2026-10-19) to the review that split it (commit 144, 2026-11-01): 64 commits earlier than the review and 44 earlier than the shipped `churny` smell, while the file grew from 928 to 1,637 non-blank, non-comment lines (VERIFIED, §3). Process measures also predicted the next 30 commits' bug-fix touches better than code measures (median Spearman +0.42 for the hotspot against +0.22 for summed cognitive complexity, whose sign turns negative once size is held fixed), which agrees with the defect-prediction literature (SOURCED, §2). Cyclomatic and cognitive complexity, Martin's instability, fan-in and module cohesion add lines without adding lead time or precision here and stay out of the report; the most complex function (`_integrate`, cognitive 91) was flagged from commit 22 and is still there after the split (95), so function complexity names a different, smaller target. Recommendation (§7): `dev arch` prints at most 8 lines (contracts, largest cycle, top-3 hotspots, modules over a 1,000-LOC ceiling), and a gate stage ratchets the contract count, the largest cycle and the per-module ceilings in `.devkit/ratchets.toml`. Bounds: one repository, 21 days, six labelled modules and 3-18 bug-fix touches per window, so the intervals are wide (§3.4).

## 1. Question and scope
Which architecture metrics and contract tools would have flagged, early and with few false alarms, the files this repository later had to refactor, and how should a generic `dev arch` command grade and gate them across Python projects (and other languages)? The labelled cases are the six modules the two consolidation reviews turned into refactor steps: review 1 (#138, merged at 3725af6) `docs.py` (import cycle, #140), `citecheck.py` and `lit.py` (shared helpers, #139); review 2 (#441, measured at b8e2e49) `workflow.py` (1,906 lines in the epic's count, split in six steps #442-#448), `checks.py` (cycle, #449) and `stats.py` (two transcript readers, #450). Out of scope: duplication (pyscn already runs in `dev consolidation report`; [architecture-consolidation.md](architecture-consolidation.md) §6-§7) and languages other than Python beyond what the tool survey shows (#510 owns that).

Owner constraint, received mid-task (2026-11-19, relayed by the lead): "a high effectiveness + low token usage system". So the design is deterministic code with a short report, and §5 ranks the candidates by signal per line an agent must read.

## 2. What the literature says about each candidate
- **Process metrics beat code metrics for predicting defects.** SOURCED: across 85 releases of 12 large open-source projects, code metrics were "generally less useful than process metrics", and they have high stasis, so models keep flagging the same files, which turn out to be "comparatively less defect-dense" ([How, and why, process metrics are better](https://doi.org/10.1109/icse.2013.6606589), Rahman and Devanbu 2013, ICSE, abstract). Relative churn predicts defect density far better than absolute churn (cited in [architecture-consolidation.md](architecture-consolidation.md) §3).
- **Hotspots, as practitioners use them.** SOURCED: CodeScene ranks refactoring targets by "high change frequency and low Code Health" and gives one dashboard example in which prioritised hotspots are 1.2% of the code, 12.5% of development effort and 45% of fixed bugs ([CodeScene hotspots guide](https://codescene.io/docs/guides/technical/hotspots.html), vendor documentation, one example, not a study). Across 39 proprietary codebases (30,737 files), low Code Health files had 15 times more defects and 124% longer issue resolution than high Code Health files ([Code Red: The Business Impact of Code Quality](https://arxiv.org/abs/2203.04374), Tornhill and Borg 2022, TechDebt; Code Health is the vendor's proprietary score, so the result does not transfer to a free metric as is). A 2026 multi-case industrial study found the vendor's "friction" view "often aligned with known problematic areas" and with files that later got maintenance, while its practical relevance "depended strongly on context" ([Technical Debt Friction for Maintenance Prioritization](https://arxiv.org/abs/2607.01850), Tverdal et al. 2026, arXiv, abstract, qualitative).
- **Change coupling.** SOURCED: change coupling (files that change together) correlates with defects in three large systems and can improve metric-based bug prediction ([On the Relationship Between Change Coupling and Software Defects](https://doi.org/10.1109/wcre.2009.19), D'Ambros, Lanza and Robbes 2009, WCRE, abstract); in a 150,000-file financial legacy system the correlation held, but explanatory power "decreases dramatically" in modules with low correlation ([The effect of evolutionary coupling on software defects](https://doi.org/10.1145/2652524.2652577), Kirbas et al. 2014, ESEM, abstract).
- **Cyclomatic complexity is not just size, but close.** SOURCED: on 17.6M Java methods and 6.3M C functions the linear CC/SLOC correlation is "only moderate", and aggregation (to files) strengthens it ([Empirical analysis of the relationship between CC and SLOC](https://doi.org/10.1002/smr.1760), Landman et al. 2015, JSEP, abstract). BELIEVED: at file level, where `dev arch` would rank, CC mostly re-ranks by size; §3.4 shows the same here.
- **Cognitive complexity measures snippet understandability, not module structure.** SOURCED: a meta-analysis of about 24,000 understandability evaluations of 427 snippets found cognitive complexity correlates with comprehension time (combined effect 0.54) and subjective ratings, with "little to no support" for correctness of comprehension tasks ([An Empirical Validation of Cognitive Complexity](https://arxiv.org/abs/2007.12520), Muñoz Barón, Wyrich and Wagner 2020, ESEM, §10.1).
- **Architecture-level metrics.** SOURCED: Decoupling Level (how well a system splits into small, independently replaceable modules), measured on 108 open-source and 21 industrial projects: "the larger the DL, the better the architecture", meaning bugs and changes localise better ([Decoupling level](https://doi.org/10.1145/2884781.2884825), Mo et al. 2016, ICSE, abstract). At Google, across 1,252 C++ and Java projects and 7,200 survey responses, higher propagation cost and more anti-pattern instances went with a larger share of LOC spent on bug fixing rather than features ([Understanding Architectural Complexity, Maintenance Burden, and Developer Sentiment](https://doi.org/10.1109/icse55347.2025.00168), Cai et al. 2025, ICSE, abstract; correlational). The top 5 architectural debts cover 20-61% of maintenance effort (cited in [architecture-consolidation.md](architecture-consolidation.md) §3). BELIEVED: these need a design-structure-matrix tool (DV8 is commercial) and a system of many modules; for a 30-module package the import cycle and the hotspot list carry the same information in fewer lines.
- **Fitness functions and ratchets.** SOURCED: a fitness function "provides an objective integrity assessment of some architectural characteristics", and may reuse tests, metrics and monitors ([Thoughtworks Radar: architectural fitness function](https://www.thoughtworks.com/radar/techniques/architectural-fitness-function), Trial ring, 2017-2018, practitioner). SonarQube's "Clean as You Code" says "You should always focus on new code, so we do not recommend adding conditions for overall code to your quality gate" ([Clean as You Code, SonarQube 10.3](https://docs.sonarsource.com/sonarqube-server/10.3/user-guide/clean-as-you-code/), vendor docs). Shopify blocks dependency and privacy violations in every PR with a static check (cited in [architecture-consolidation.md](architecture-consolidation.md) §2). No study measures the outcome of ratchets against fixed thresholds; this is practitioner evidence.
- **Martin's instability and abstractness.** BELIEVED: no fetched study validates I = Ce/(Ca+Ce) or the distance from the main sequence against outcomes, and abstractness (abstract classes over all classes) has little meaning in a Python package of functions; §3 tests instability only.

## 3. Replay on this repository
VERIFIED with [history.py](_scratch/architecture-grading/scripts/history.py) (reads git objects at each of the 166 first-parent commits of main to f4ba1c9, 2026-10-11..11-01; complexipy 9.0.0 for cognitive complexity, an AST count with the mccabe rule set for cyclomatic) and [analyse.py](_scratch/architecture-grading/scripts/analyse.py) (networkx 3.5 for Louvain communities); output in the gitignored `.scratch/`. Scope: the 17-33 modules of `devkit/`. Churn = first-parent commits touching the file in the last 30. Hotspot = churn x LOC. Communities = Louvain groups of top-level definitions (reference and shared-global edges) of at least 100 LOC. Dispersion = share of pairs among the last 6 commits on a file that touched disjoint definitions. Bug-fix commit = the closed issue carries `type:bug`, or the subject starts with "fix" (35 of 166).

### 3.1 Lead time on workflow.py
| metric | ranks workflow.py 1st, unbroken until review 2, from | commits before review 2 |
|---|---|---|
| cognitive, most complex function | 22 (2026-10-12) | 122, but it names `_integrate`, which the split left in place (91 before, 95 after) |
| cyclomatic, most complex function | 26 (2026-10-12) | 118, same function |
| **hotspot, churn x LOC** | **80 (2026-10-19)** | **64** |
| churn alone | 84 (2026-10-19) | 60 |
| LOC | 112 (2026-10-20) | 32 |
| hotspot, churn x summed cognitive | 124 (2026-10-31) | 20 |
| summed cognitive | 124 (2026-10-31) | 20 |
| communities | 131 (2026-10-31) | 13 |
| summed cyclomatic, fan-in, fan-out, instability, dispersion, function-level imports | never ranked it 1st for the whole stretch | |

The shipped consolidation trigger, replayed with today's thresholds ([trigger_replay.py](_scratch/architecture-grading/scripts/trigger_replay.py)), first named workflow.py as `churny` at commit 124 (1,427 LOC) and was first due after review 1 at commit 84, through a `dormant` smell that review 2 judged a false positive (#451). Absolute thresholds on workflow.py: LOC > 1,000 first fired at commit 84 and flagged one other module (`board.py`) both then and at review 2; "over 1,000 LOC and 15 first-parent commits" also fired at 84 and flagged no other module; cognitive > 25 in any function fired at commit 16 but flagged 11 other modules at review 2. Between commit 80 and the review, 13 feature commits changed workflow.py, and when the review ran 14 open issues touched it (epic #441), so the late split forced the rebase work the epic's ordering section describes. The calendar lead is 13 days, 11 of them a quiet gap (commits 112-120).

### 3.2 Precision at the two reviews
Labelled modules among each metric's top 5 (3 labelled modules per review; full lists in analyse.py output):

| metric | review 1 (21 modules) | review 2 (24 modules) |
|---|---|---|
| hotspot churn x LOC | 1 | 2 (workflow 1st, checks 2nd) |
| churn | 1 | 2 |
| LOC | 1 | 2 |
| cognitive: functions over 15 | 2 | 3 |
| cognitive: most complex function | 1 | 3 |
| summed cognitive or cyclomatic | 1 | 2 |
| fan-out | 1 | 2 (behind cli and gate, the entry points) |
| fan-in, instability | 0 | 1 |
| communities, modularity | 1 | 2 |
| dispersion | 1 | 0 |

The size, complexity and hotspot metrics all put `board.py` (1,339 LOC at review 2, 1,630 today, never split) in their top 5. It is unlabelled, not a known false positive: no review has looked at it as a split candidate.

### 3.3 The import cycle came back
VERIFIED with the replay data: `docs.py` imported `devkit.cli` from the first commit; #140 removed it (commit 44, 2026-10-13); PR #417 (#229, commit 133, 2026-10-31) added `from devkit import cli` inside a function in `checks.py`, closing a 7-module cycle; review 2 found it 11 commits later and #449 removed it (commit 154). Function-level imports hid it from pyscn ([architecture-consolidation.md](architecture-consolidation.md) §6 notes pyscn skips them); the regex graph in `devkit/consolidation.py` and import-linter both see it.

### 3.4 Forward validity
Spearman correlation, across modules, between a metric at snapshots 40, 60, 80, 100 and 120 and the bug-fix commits touching the module in the next 30 first-parent commits (22-23 modules; 3-8 modules and 3-18 touches with a fix per window). Partial = with LOC held fixed.

| metric | median ρ | median partial ρ | at snapshot 100 (18 touches) |
|---|---|---|---|
| hotspot churn x LOC | +0.42 | +0.23 | +0.63 (95% CI 0.29-0.83) |
| churn, last 30 | +0.36 | +0.18 | +0.70 (0.40-0.86) |
| churn, all history | +0.48 | +0.37 | +0.57 |
| fan-out | +0.46 | +0.40 | +0.57 (0.21-0.80) |
| LOC | +0.21 | | +0.35 (-0.07-0.67) |
| summed cognitive | +0.22 | -0.44 | +0.24 (-0.19-0.59); partial -0.44 (-0.72 to -0.03) |
| summed cyclomatic | +0.24 | -0.34 | +0.28 |
| instability | +0.23 | +0.13 | +0.38 |
| communities | +0.28 | +0.09 | +0.23 |

Intervals: Fisher z, [ci.py](_scratch/architecture-grading/scripts/ci.py), rough for ranks with ties. Caveats: windows overlap, so the five snapshots are not independent; the outcome counts touches, so churn partly predicts itself (files being worked on keep being worked on); fan-out is led by `cli.py`, the entry point that every feature edits (7 of the 35 fix commits touched it). BELIEVED: the direction matches §2 (process over code, complexity mostly size), and the size-partialled complexity coefficients argue against complexity as a ranking signal here; the magnitudes are not transferable.

## 4. Contracts on three commits
VERIFIED: import-linter 2.15 (through `uvx`, on `git archive` copies of `devkit/`) with two contracts in [importlinter.toml](_scratch/architecture-grading/scripts/importlinter.toml): `acyclic_siblings` on `devkit`, and `forbidden` from `devkit.*` to `devkit.cli`.

| commit | modules, import edges | acyclic siblings | only the entry point imports cli |
|---|---|---|---|
| 3725af6 (review 1 merged) | 21, 61 | BROKEN: remove `.docs -> .cli` | BROKEN: docs, and through it gate, setup, workflow |
| b8e2e49 (review 2 measured) | 24, 83 | BROKEN: remove `.checks -> .cli`, `.suite_audit -> .gate`, `.weaken -> .checks` | BROKEN: checks at l.421, and 5 modules through it |
| f4ba1c9 (today's main) | 33, 124 | KEPT | KEPT |

A run took 1.3 s at b8e2e49 without import-linter's cache. The acyclic contract prints the minimal set of edges to cut, which is the review's step list in one line per edge; the forbidden contract prints every transitive chain (26 non-blank lines at b8e2e49), so a report should print only the first edge of each chain. Reviews 1 and 2 both spent a step on exactly what these contracts report. BELIEVED: adopted after review 1, they would have failed PR #417 in the gate, and the cycle would never have reached review 2.

## 5. Signal per line of output
| check | lines an agent reads | evidence here | verdict |
|---|---|---|---|
| declared contracts (import-linter) | 1 when kept; 1 per edge to cut when broken | caught both cycles the reviews fixed, at the PR that made them | **gate + report** |
| largest import cycle (devkit's own regex graph) | 1 | same cycles, no declaration needed; already in `consolidation.signals` | **ratchet + report** |
| hotspot top 3, churn x LOC | 3 | workflow.py 1st from 64 commits early; 2 of 3 labelled at review 2; best median ρ | **report** |
| modules over a LOC ceiling | 1 per module (2 at review 2, 1 today) | fired on workflow.py at commit 84 with one other module | **ratchet + report** |
| cognitive complexity per function | 66-73 functions over 15 in 24-33 modules | names `_integrate`, which no review acted on; negative once size is fixed | **not in v1**; a count ratchet costs 1 line if wanted later |
| summed or file-level cyclomatic | 1 per module | dominated by size | drop |
| instability, fan-in, fan-out | 1 per module | no lead; fan-out led by entry points | drop |
| communities, dispersion | 1 per module | 13 commits of lead at best; dispersion 0 of 3 | drop from the report; the architect agent can compute groups when it plans a split |

Lines counted at review 2 and today by [head_lines.py](_scratch/architecture-grading/scripts/head_lines.py). Caveat for the hotspot: refactor commits count as churn, so today's main still ranks workflow.py first (its 6 split commits are in the window); churn must count from the last `Consolidation-Review:` base, the reset point the trigger already uses.

## 6. Tools and generality
Metadata VERIFIED on 2026-11-19 (`uv run dev reuse check`, GitHub API).

| tool | licence, last release or push | role | decision |
|---|---|---|---|
| [import-linter](https://github.com/import-linter/import-linter) 2.15 | BSD-2-Clause (GitHub; PyPI metadata says BSD-1-Clause), released 2026-11-18 | contracts: layers, forbidden, independence, protected, acyclic siblings | **use**, pinned through `uvx`, when a project declares `[tool.importlinter]`; the contract types are those of its [contract types page](https://github.com/seddonym/import-linter/blob/main/docs/contract_types/index.md) |
| devkit's regex import graph and Tarjan pass (`devkit/consolidation.py`) | this repository's | largest cycle with no declaration, from git objects | **reuse** for the zero-config cycle ratchet |
| [tach](https://github.com/tach-org/tach) | MIT, pushed 2026-10-29 | module boundaries and public interfaces | alternative to import-linter; not run here |
| [complexipy](https://github.com/rohaquinlop/complexipy) 9.0.0 | MIT, released 2026-11-13 | cognitive complexity, ships diff and regression helpers | not in v1 (§5); the tool to use if a complexity ratchet is added |
| [lizard](https://github.com/terryyin/lizard) | MIT on PyPI (GitHub says NOASSERTION), released 2026-11-16 | cyclomatic and NLOC for about 20 languages | the non-Python complexity fallback, not in v1 |
| [rust-code-analysis](https://github.com/mozilla/rust-code-analysis) | licence not reported by the GitHub API; last release v0.0.25 on 2023-01-13 | tree-sitter metrics for many languages | skip: no release in nearly 3 years |
| radon, wily, xenon | MIT, Apache-2.0, MIT; dormant 760-1,335 days (G4 HOLD) | complexity and its history | skip |
| [pydeps](https://github.com/thebjorn/pydeps) | BSD-2-Clause, released 2026-10-01 | import graph pictures | not needed |
| [emerge](https://github.com/glato/emerge) | MIT, pushed 2026-08-07 | multi-language dependency graphs and metrics, HTML output | not run; its output is for people, not a gate |

BELIEVED, on generality: the hotspot and the LOC ceiling need only git and the line counter devkit already has for 21 extensions, so they work in any language today; cycles and contracts need a per-language import graph (import-linter and tach are Python-only), which belongs to #510.

## 7. Recommendation for this project
All BELIEVED, built from §3-§6; numbers are this repository's and need recalibration elsewhere (#125).
1. **`uv run dev arch`**, deterministic, at most 8 lines (plus `--json`), each with the number that triggered it:
   - `contracts: 2 kept` or `contracts: BROKEN acyclic: cut checks -> cli` (one line per edge to cut, at most 3), only when `[tool.importlinter]` exists, running a pinned `import-linter==2.15` through `uvx`;
   - `largest import cycle: N modules (ratchet M)` from the existing regex graph;
   - `hotspots (first-parent commits since the last review, last 30) x LOC:` the top 3 as `path churn x loc`, churn counted from the `Consolidation-Review:` base;
   - `over the module ceiling (1,000 LOC):` each module with its LOC and its ratchet value.
   It should run in a few seconds here: one import-linter run took 1.3 s, and the trigger replay read 129 commits in 13 s, about 0.1 s each (BELIEVED from those timings), so `dev orient` may show its first line when anything is broken.
2. **A gate stage, `arch`**, reading an `[arch]` table in `.devkit/ratchets.toml` and reusing `check_ratchets`' owner-approved `[[raise]]`: broken contracts must be 0; the largest import cycle may not grow (today 0 modules, both by import-linter and by the regex graph in `consolidation.signals` at f4ba1c9); each module over `module_loc_ceiling` (default 1,000, a `[tool.devkit.arch]` key) is pinned at its current LOC and may only shrink, and no other module may cross the ceiling. Hotspots stay report-only: a rank is relative, so it cannot be a gate.
3. **This repository adopts the two contracts** of §4 in `pyproject.toml`, and the template renders them commented out with the project's package name.
4. **The consolidation trigger** keeps its thresholds until #125 recalibrates on longer histories; the replay here suggests counting first-parent commits for `churny` (15 first-parent commits would have fired 40 commits earlier on workflow.py with no other module), but that is one file in one repository.

## Sources
Inline links above, fetched 2026-11-19 through `dev lit get`, the arXiv MCP (abstracts; §10.1 of 2007.12520 read as text), WebFetch (CodeScene, Thoughtworks) and WebSearch (SonarQube). Earlier sources reused from [architecture-consolidation.md](architecture-consolidation.md) are cited there. Scripts: [history.py](_scratch/architecture-grading/scripts/history.py), [analyse.py](_scratch/architecture-grading/scripts/analyse.py), [ci.py](_scratch/architecture-grading/scripts/ci.py), [trigger_replay.py](_scratch/architecture-grading/scripts/trigger_replay.py), [head_lines.py](_scratch/architecture-grading/scripts/head_lines.py), [importlinter.toml](_scratch/architecture-grading/scripts/importlinter.toml); `history.py` needs `.scratch/arch_issues.json` from `gh issue list --state all --limit 1000 --json number,labels,title,createdAt`.

## Follow-ups
- [ ] #527 Build `dev arch`, the report of §7 item 1. DONE WHEN `uv run dev arch` prints at most 8 lines, a test on a fixture repository checks each line, and run at b8e2e49 it lists devkit/workflow.py first among hotspots and reports the `checks -> cli` cycle.
- [ ] #529 Build the `arch` gate stage and adopt the contracts (§7 items 2-3). DONE WHEN the gate fails on a seeded change that adds `from devkit import cli` inside a function in devkit/checks.py, fails when a module crosses the LOC ceiling without an owner-approved raise, passes on main, and the rendered template carries the commented contracts.
- [ ] #125 Recalibrate the trigger on longer histories, including first-parent `churny` counts (§7 item 4). DONE WHEN #125's own DONE WHEN holds.
- [ ] #510 Per-language import graphs for contracts and cycles beyond Python. DONE WHEN #510's own DONE WHEN holds.
