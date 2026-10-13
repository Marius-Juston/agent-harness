# r1: tools, plugins and prior art for architecture analysis and consolidation

Sub-question: which maintained free/open-source tools, Claude Code plugins, agents, skills and repos already do
architecture analysis or consolidation, so #113 reuses instead of building?

Search coverage: `uv run dev lit status` 2026-10-12: openalex, crossref, europepmc, arxiv OK; s2 DOWN (rate limited).
The arXiv API and arxiv MCP returned HTTP 429/timeouts mid-round, so paper abstracts came via `dev lit get` (OpenAlex);
no paper full text was read. GitHub/PyPI/npm metadata fetched 2026-10-12 via `gh api` and the PyPI/npm JSON APIs.

## Findings

**F1. The measurement layer exists and is maintained; the "step back and consolidate" layer does not.** BELIEVED,
from F2-F6. Every structural signal #113 needs (duplication, unused code, complexity, import cycles, layer rules,
unused dependencies, coupling/cohesion) has a maintained, permissively licensed, uv-installable tool. No tool or
plugin found measures "how much of this code is redundant relative to its features" or schedules a repo-wide
consolidation; the agent-side offerings are either diff-scoped (code-simplifier, `/simplify`), legacy-migration
oriented (Anthropic's code-modernization), or prompt-only with no measurement (wshobson, VoltAgent). So the
reusable shape is: a thin `uv run dev` wrapper over existing analyzers that emits one ratchetable report, plus a
skill/agent that borrows code-modernization's feature-inventory and proof protocol. Building analyzers is not needed.

**F2. Python structure tools (metadata fetched 2026-10-12).** SOURCED from each repo's GitHub API record and PyPI
JSON; "6-mo commits" = commits since 2026-04-12 (capped at 100) via the GitHub commits API.

| tool | licence | last push / last release | 6-mo commits (authors) | measures | uv-gate fit |
|---|---|---|---|---|---|
| [pyscn](https://github.com/ludo-technologies/pyscn) | MIT | 2026-10-10 / 1.32.4 (2026-10-08) | 100+ (8) | clones type 1-4, unreachable code, cyclomatic+cognitive complexity, import cycles, layer rules (presets), module communities, CBO, LCOM4, 0-100 health score; `check` gate mode; JSON | **best single tool**: `uvx pyscn`, Go binary wheel (no Intel-macOS wheel), `[tool.pyscn]` in pyproject; ships Claude Code plugin + 4 skills + MCP server |
| [vulture](https://github.com/jendrikseipp/vulture) | MIT | 2026-10-12 / 2.16 (2026-03-25) | n/a | unused functions/classes/variables/imports with confidence % | yes; whitelist file for false positives |
| [import-linter](https://github.com/seddonym/import-linter) | BSD-2 | 2026-09-16 / 2.15 (2026-09-04) | 31 (9) | contracts: [layers, independence, forbidden, protected, acyclic_siblings](https://github.com/seddonym/import-linter/tree/main/docs/contract_types) | yes; config in pyproject; declares intended architecture |
| [grimp](https://github.com/python-grimp/grimp) | BSD-2 | 2026-10-12 / 3.17 (2026-09-04) | n/a | import graph library (import-linter's engine); queryable chains/cycles | yes, as a library for custom checks |
| [tach](https://github.com/tach-org/tach) | MIT | 2026-10-06 / 0.35.3 (2026-10-06) | 34 (5) | module boundaries, public interfaces, no cycles, unused/external deps; Rust | yes; repo moved from `gauge-sh/tach` to `tach-org/tach` (redirect seen; reason for handover not verified) |
| [deptry](https://github.com/osprey-oss/deptry) | MIT | 2026-10-12 / 0.25.1 (2026-03-18) | n/a | unused, missing, transitive declared dependencies | yes; reads pyproject/uv |
| [pydeps](https://github.com/thebjorn/pydeps) | BSD-2 | 2026-10-01 / 3.0.9 (2026-10-01) | n/a | module dependency graph, `--show-cycles` | report only; rendering needs Graphviz |
| [complexipy](https://github.com/rohaquinlop/complexipy) | MIT | 2026-10-12 / 8.0.1 (2026-09-07) | n/a | cognitive complexity; `--snapshot-create` baseline that fails only on regressions (seen in `--help`) | yes; snapshot = built-in ratchet |
| ruff `C901`, `PLR0911/0912/0915` | MIT | (already in the gate) | | McCabe complexity, branches, statements, returns | zero new deps; not enabled in agent-harness's `[tool.ruff.lint] select` today (VERIFIED by reading `pyproject.toml`) |
| pylint `symilar` / `duplicate-code` (R0801) ([pylint](https://github.com/pylint-dev/pylint)) | GPL-2.0 | 2026-10-12 / 4.1.3 (2026-10-11) | n/a | exact line-run duplicates | runs via `uvx --from pylint symilar`; GPL fine to run, not to vendor (BELIEVED, not legal advice) |
| [jscpd](https://github.com/kucherenko/jscpd) | MIT | 2026-10-12 / npm 5.4.1 (2026-10-09) | 100+ (5) | token-based clones, 220+ languages, SARIF/JSON, GitHub Action, MCP server (per repo description) | needs Node/npx, not uv; language-agnostic, good for the generic (non-Python) case; CLI flags changed in 5.x (`--gitignore` gone, VERIFIED) |
| [lizard](https://github.com/terryyin/lizard) | MIT (PyPI) | 2026-10-11 / 1.24.1 (2026-10-05) | n/a | CCN, NLOC, params; many languages | yes; generic multi-language complexity |
| [wily](https://github.com/tonybaloney/wily) | Apache-2.0 | 2026-10-07 / 1.25.0 (**2023-10-11**) | 22 (2) | complexity/MI trend across git history | releases stale 3 years; commits continue. Use with caution |
| [radon](https://github.com/rubik/radon) / [xenon](https://github.com/rubik/xenon) | MIT | 2024-10-20 / 6.0.1 (**2023-03-26**); xenon 0.9.3 (2024-10-21) | **0** | CC, MI, Halstead, raw LOC | **stale**; ruff C901 + pyscn/complexipy supersede |
| [emerge](https://github.com/glato/emerge) | MIT | 2026-08-07 / emerge-viz 2.0.7 (**2024-08-17**) | 7 (2) | interactive dependency graph, graph metrics, many languages | exploration UI only, not a gate |
| [code-maat](https://github.com/adamtornhill/code-maat) | GPL-3.0 (README) | 2025-07-03 / n/a | | git-log mining: hotspots, change coupling, code age | JVM/Clojure, not uv; replace with [PyDriller](https://github.com/ishepard/pydriller) (Apache-2.0, 2.12 on 2026-09-11) or a short `git log --numstat` script |
| [scc](https://github.com/boyter/scc) | MIT | 2026-10-12 | | fast LOC/complexity counts per language | Go binary; code-modernization uses it for size |
| [skylos](https://github.com/duriantaco/skylos) | Apache-2.0 | 2026-10-12 / 4.48.0 | 100+ (**2**) | dead code + security + "AI-code mistakes" PR scanner | overlaps vulture; two authors, very high release cadence: watch, do not adopt yet (BELIEVED) |
| [rope](https://github.com/python-rope/rope), [LibCST](https://github.com/Instagram/LibCST), [ast-grep](https://github.com/ast-grep/ast-grep) | LGPL-3 / MIT(+PSF) / MIT | all pushed 2026 | | **executors**, not analyzers: rename/move across a project, codemods, structural rewrite | let the agent make mechanical moves deterministically; [Bowler](https://github.com/facebookincubator/Bowler) is archived |

Excluded: [qlty](https://github.com/qltysh/qlty) is **Business Source License 1.1** whose use grant excludes use "to
produce ... an Artificial Intelligence Coding Service" (SOURCED, its LICENSE.md); not open source and the clause is
a risk for an agent harness. [deadcode](https://github.com/albertas/deadcode) is AGPL-3.0 with no release since
2024-08. [hercules](https://github.com/src-d/hercules) and [git-of-theseus](https://github.com/erikbern/git-of-theseus)
last pushed 2023.

**F3. Trial on the sibling repos: the tools are fast, and their "duplication %" disagree 4-14x** (pyscn/jscpd: 13x, 5.9x, 4.4x; pyscn/symilar on repo-R: 14x). VERIFIED (commands
in the Appendix, run 2026-10-12 against the working trees).

| repo | tracked .py lines | pyscn dup % (clone groups) | jscpd dup % (clones, min 50 tokens) | symilar % (min 6 lines) | vulture conf>=80 / >=60 | ruff C901+PLR09xx | pyscn cycles | pyscn health |
|---|---|---|---|---|---|---|---|---|
| agent-harness | 14,208 | 2.6 (5) | 0.2 (4) | not run | 0 / 9 | 49 | 0 | 87 B |
| repo-R | 44,824 | 10.7 (147) | 1.8 (74) | 0.76 | 33 / 451 | 220 | 2 modules | 70 C |
| repo-L | 305,478 | 5.7 (400) | 1.3 (413) | not run | 84 / 961 | 705 | 3 modules | 84 B |

pyscn analysed the 305k-line repo in about 3 s wall, jscpd in about 2 s, vulture in under 9 s for two passes.
repo-E has no package code (its 73 .py files are under `docs/research` and `tests`), and pyscn exits with "no Python
files found", so it excludes those paths by default. Consequences for #113 (BELIEVED):
- A duplication trigger must name the tool and its settings; pyscn counts near-miss and semantic (type 3-4) clones
  ([rule docs](https://github.com/ludo-technologies/pyscn/tree/main/website/docs/rules)), jscpd and symilar count
  exact token/line runs. Ratchet one tool's number against its own history, never compare across tools.
- pyscn's health score is size-normalised: the 305k-line repo scores higher (84) than the 45k-line one (70). No tool
  here answers "this is 100k that could be 20k"; absolute duplicated lines, unused definitions and the size trend
  per feature are the closest proxies.
- pyscn "dead code" means unreachable code after return/break/raise (its rule list), so it found 0 everywhere while
  vulture found 33-84 high-confidence unused definitions. Pair them; they measure different things.

**F4. Claude Code plugins and agents.** SOURCED (files fetched from each repo 2026-10-12).
- [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official) (Apache-2.0, pushed
  2026-10-12) `code-simplifier`: one agent, [prompt](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/code-simplifier/agents/code-simplifier.md)
  scoped to "recently modified code", runs "proactively", and hard-codes JS/React standards (ES modules, `function`
  keyword, React Props). Diff-scoped: it cannot consolidate across a repo, and its standards need replacing for
  Python. The same idea ships as the `pr-review-toolkit` `code-simplifier` agent and as this Claude Code build's
  built-in `/simplify` skill ("Review the changed code for reuse, simplification, efficiency..."; VERIFIED in this
  session's skill list).
- Same repo, [`code-modernization`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization)
  (Apache-2.0): the closest prior art for the *protocol*. Steps: preflight, assess (inventory, complexity, debt),
  map (dependencies, entry points), extract-rules (business rules as Given/When/Then cards with `file:line`
  citations, each re-checked by a second agent), human review, brief (approved phased plan), build
  (uplift/transform/reimagine), verify, harden. Its verify step is script-computed, not model opinion: tests re-run
  from clean (zero tests run = fail), old and new run on the same inputs with byte compare, at least ten invented
  inputs, a canary one-line break that must turn tests red, every critical rule named by a test that ran, source
  untouched; verdict PROVEN / PARTLY PROVEN / NOT PROVEN from `scripts/proof_pack.py`. Its README reports pilots on
  systems of about 19k-44k lines (beets, osCommerce, AWStats) and warns that steps fan out many agents and that two
  extraction runs can find different rules. Built for legacy-to-new-stack moves, not same-stack consolidation, and
  heavy (TypeScript hooks, early-access function hooks for its pane). Reuse its design (and possibly its Python
  scripts, Apache-2.0) rather than install it into the gate (BELIEVED).
- `feature-dev` `code-architect` designs *new* features from existing patterns; not consolidation.
- [wshobson/agents](https://github.com/wshobson/agents) (MIT, 40.5k stars, pushed 2026-10-12): `code-refactoring`
  (`refactor-clean`, `tech-debt`, `legacy-modernizer`), `codebase-cleanup`, `comprehensive-review/architect-review`.
  Prompt-only: `refactor-clean.md` is 933 lines of generic advice with unsourced thresholds (functions >20 lines,
  classes >200 lines); none of the three files invokes vulture, radon, jscpd, import-linter or tach (grep, VERIFIED).
  Useful as a checklist source, not as a mechanism.
- [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) (MIT):
  `refactoring-specialist`, `architect-reviewer`, `legacy-modernizer`, and `codebase-orchestrator` ("Safe Refactor
  Protocol": map, propose, preview diffs, wait for human approval). Prompt-only; its tool list names MCP servers
  that a plain install does not have.
- [agent-sh/agentsys](https://github.com/agent-sh/agentsys) (MIT, about 1k stars): `/deslop` (remove AI-slop
  patterns), `/drift-detect` (plan vs implementation), run as pre-review gates alongside `/simplify`. Per-PR, not
  repo-wide.
- [hesreallyhim/awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code): no entry for repo-wide
  consolidation or architecture metrics (grep of README for refactor/architect/duplicate/dead code/tech debt).
- pyscn's own [Claude Code plugin](https://github.com/ludo-technologies/pyscn/tree/main/.claude-plugin/pyscn-mcp)
  (`health-check`, `refactoring`, `architecture-review`, `cli-analysis` skills + `pyscn-mcp`): the only plugin found
  that ties an agent skill to measured structure. Not read in full; quality unassessed.

**F5. Agentic refactoring evidence: agents refactor locally, fail at multi-file/compound work, and do better with
explicit state, planner/tester splits and tool verification.** SOURCED (abstracts only; models are of their date and
results are not comparable to current models).
- [Agentic Refactoring (arXiv 2511.04824)](https://arxiv.org/abs/2511.04824): 15,451 refactorings in 12,256 agent
  PRs (Java, AIDev dataset). Agents target refactoring in 26.1% of commits, dominated by low-level edits (Change
  Variable Type 11.8%, Rename Parameter 10.4%, Rename Variable 8.5%) rather than the high-level design changes
  humans make; quality gains small but significant (Class LOC median delta -15.25).
- [RefactorBench (arXiv 2503.07832)](https://arxiv.org/abs/2503.07832): 100 multi-file refactoring tasks; LM agents
  solved 22% vs a time-limited human 87%; conditioning on a state representation improved solving by 43.9%.
- [SWE-Refactor (arXiv 2602.03712)](https://arxiv.org/abs/2602.03712): 1,099 Java refactorings; a Codex agent
  solved 39.4% of compound instances. [SWE-Bench ProMax (arXiv 2608.09802)](https://arxiv.org/abs/2608.09802): 170
  multi-language tasks averaging 11.4 files and 261.6 lines; best model 41.2%.
  [SmellBench (arXiv 2606.05574)](https://arxiv.org/abs/2606.05574): best agent+model (Qwen Code + Claude Sonnet 4.5)
  scored 50.34 on smell elimination, attributed to local focus and weak cross-file understanding.
- [RefAgent (ICSE 2026, doi:10.1145/3744916.3773153)](https://doi.org/10.1145/3744916.3773153): planner/executor/
  tester/refiner agents on 8 Java projects; median test pass 90%, code smells -52.5%; vs single agent, test pass
  +64.7% and compilation +40.1%. [MANTRA (arXiv 2503.14340)](https://arxiv.org/abs/2503.14340): 82.8% (582/703) of
  method-level refactorings compile and pass tests vs 8.7% for a raw-LLM baseline.
- ["Refactoring Runaway" (arXiv 2605.22526)](https://arxiv.org/abs/2605.22526): refactorings tangled into issue fixes
  are associated with reduced compilability (3,691 patches, 12 LLMs); a refactoring-aware refinement raised
  compilability 19.34% to 38.33%. Supports keeping consolidation PRs separate from feature PRs (BELIEVED inference).
- [Quality and Security Signals in AI-Generated Python Refactoring PRs (doi:10.1145/3805760.3814886)](https://doi.org/10.1145/3805760.3814886):
  measures Python agent refactoring PRs with PyQu, Pylint and Bandit; the fetched abstract gives no results.

## Recommendation sketch for #113 (BELIEVED, for the synthesis round)
1. Gate-side (deterministic, cheap, every PR): ruff `C901`/`PLR09xx` (no new dependency), vulture, import-linter or
   tach for declared boundaries, deptry; ratchet counts in `.devkit/ratchets.toml` style rather than absolute
   thresholds. complexipy snapshot is a ready-made ratchet if cognitive complexity is wanted.
2. Periodic consolidation report (`uv run dev` command): `uvx pyscn analyze --json` + vulture + a git-log hotspot pass
   (PyDriller or plain `git log --numstat`), trended over time. About 3 s on 300k lines, so it can run in the gate
   or on a schedule.
3. Agent/skill: when the report trips, run a code-modernization-style loop on one module at a time: inventory
   behaviours with citations, pin them with tests, rewrite, script-computed verdict, separate PR. Use rope/LibCST/
   ast-grep for mechanical moves. Do not adopt the prompt-only marketplace agents.

## Gaps seen, not closed
- import-linter, tach and deptry were not run on the sibling repos (each needs a per-repo config); their noise level
  there is unknown.
- pyscn clone precision/false-positive rate is unmeasured; its type-4 "semantic" clones may inflate the 10.7%.
- Paper full texts not read (arXiv 429); effect intervals not available from abstracts.
- CodeScene (free for open source, proprietary) and Sourcery were not checked; neither is free/open-source for a
  private repo as far as known (BELIEVED).
- pyscn's skills and jscpd's MCP server were not exercised.
- code-modernization's Python scripts (`proof_pack.py`, `compare.py`) were listed, not read; reuse needs a read.

## Sources fetched
GitHub API repo records and PyPI/npm JSON for every tool in F2 and every repo in F4; files: code-simplifier agent,
code-modernization README and agent headers, feature-dev code-architect, pr-review-toolkit code-simplifier,
wshobson `refactor-clean.md`, `tech-debt.md`, `architect-review.md`, VoltAgent `codebase-orchestrator.md`,
awesome-claude-code and agentsys READMEs, pyscn README and rule list, tach README, qlty and LibCST licences,
code-maat README, import-linter docs tree. Papers via `uv run dev lit get` (OpenAlex): arXiv 2511.04824, 2503.07832,
2602.03712, 2606.05574, 2605.22526, 2503.14340, 2608.09802; doi:10.1145/3744916.3773153;
doi:10.1145/3805760.3814886.

## Appendix: commands behind F3 (2026-10-12)
```bash
for d in agent-harness repo-R repo-L; do p=~/GitHub/$d
  uvx pyscn@1.32.4 analyze --json --no-open --output "$d.json" "$p"          # summary.* fields
  npx -y jscpd@5.3.2 "$p" --format python --min-tokens 50 --reporters json --output "jscpd/$d" \
      --ignore "**/.venv/**,**/node_modules/**,**/.git/**"                     # statistics.total
  (cd "$p" && uvx vulture@2.16 . --min-confidence 80 --exclude .venv,node_modules | wc -l)
  (cd "$p" && ruff check --isolated --select C901,PLR0911,PLR0912,PLR0915 --statistics --exit-zero .)  # ruff 0.17.0
done
(cd repo-R && git ls-files '*.py' | \
  xargs uvx --from pylint@4.1.3 symilar --ignore-comments --ignore-docstrings --ignore-imports -d 6 | tail -1)
```
