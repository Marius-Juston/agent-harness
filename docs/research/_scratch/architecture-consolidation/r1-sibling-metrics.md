r1: sibling-repo metrics (agent-harness#111, round 1)

Search coverage: no literature was searched; this round only measures local repos. `uv run dev lit status` on
2026-10-12: OK, partial (s2 down; openalex, crossref, europepmc, arxiv up). Tool metadata came from the GitHub and
PyPI APIs on 2026-10-12.

Measured at: repo-L `086fb686`, repo-E `3748c23`, repo-R `7e5c305`, agent-harness `bd9abb9` (all clean;
`git status` was empty before and after the run, and nothing was written to them).

Reproduce (about 20 s; reads git objects only, and vulture runs on a `git archive` copy in a temp dir):

```sh
P=$HOME/.claude/projects
uv run scripts/repo_metrics.py ~/GitHub/{repo-L,repo-E,repo-R,agent-harness} --vulture --cross \
  --transcripts "repo-L=$P/-home-marius-GitHub-repo-L*/**/*.jsonl" \
  --transcripts "repo-R=$P/-home-marius-GitHub-repo-R*/**/*.jsonl" \
  --transcripts "agent-harness=$P/-home-marius-GitHub-repo-E*/**/*.jsonl" \
  --transcripts "agent-harness=$P/-home-marius-GitHub-agent-harness*/**/*.jsonl" --json .scratch/all.json
```

Script: [scripts/repo_metrics.py](../../../../scripts/repo_metrics.py). It uses only the standard library, needs
Python 3.14 (PEP 723 header) and can call `uvx vulture` if asked. Unless a finding says otherwise, every number
below is **VERIFIED** with that script; the derived ratios come from `scripts/derive.py` (in this
folder; repo paths come from `scripts/codenames.py`). "LOC" means non-blank lines that are not just a comment; docstrings count as code. The script excludes
lock files, vendored/third_party trees, `generated/` trees, minified bundles and symlinks. "Test" means files under
`tests*/` plus `test_*.py`, `*_test.py`, `conftest.py` and `*.test.ts`.

## Findings

### F1. Overview: one large repo, three small ones (VERIFIED, repo_metrics.py)

| repo | code LOC | test share | doc lines | merges | code LOC per merge | deleted/added (code, all history) |
|---|---|---|---|---|---|---|
| repo-L | 259,163 | 34% | 57,184 | 520 | 498 | 0.074 (332,624 added, 24,529 deleted) |
| repo-R | 39,375 | 12% | 14,842 | 93 | 423 | 0.048 |
| agent-harness | 12,016 | 46% | 777 | 44 | 273 | 0.029 |
| repo-E | 3,947 | 0% | 18,073 | 68 | 58 | 0.614 (one extraction PR, F6) |

- In three of the four repos, agents mostly append. Lines deleted are 3 to 7% of lines added. The exception is
  repo-E, where most deletions come from a single PR (F6). Added and deleted are raw `git log --numstat` lines; LOC
  is non-blank lines. (VERIFIED, repo_metrics.py `hotspots`)
- Exact copy-paste is rare in all four repos: 0.0 to 1.6% of non-test normalised lines are duplicated. pylint
  `symilar` agrees (`-d 6 -i --ignore-docstrings --ignore-imports`, non-test `.py`): repo-L 0.31%, repo-R
  0.55%, agent-harness 0.10%. With identifiers and literals renamed before hashing (type-2), the share rises to
  12.6% in repo-L, 4.4% in repo-R, 1.8% in agent-harness and 7.7% in repo-E. (VERIFIED, repo_metrics.py
  `dup_*`, plus `uvx --from pylint==4.0.2 symilar` on archive copies)
- None of the repos has an import cycle at module level. Cycles exist only through imports placed inside
  functions: repo-L has 17 strongly connected components, the largest with 76 modules; repo-R has 4, the
  largest with 4; agent-harness has 1 with 5. (VERIFIED, repo_metrics.py `imports`)
- The LOC counter was checked against a `grep -cvE '^\s*(#[^!]|#$|$)'` count of every `.py` file. repo-L: 256,656
  vs 256,655. repo-R: 37,638 vs 37,638. agent-harness: 11,596 vs 11,596. (VERIFIED, `scripts/verify_loc.py`)

### F2. repo-L growth curve: about 229k lines in 22 days, with the old code frozen beside it (VERIFIED, repo_metrics.py `growth`)

| date (first-parent index) | code | of which app + tests_engine | v1 dirs (systems, core, agents, models, utils, tests) | root files (incl. bot.py) |
|---|---|---|---|---|
| 2026-04-18 (0) | 12,358 | 0 | 10,730 | 1,628 |
| 2026-05-04 (30) to 2026-09-13 (31) | 21,596 | 0 | 19,166 | 2,430 (dormant for four months) |
| 2026-09-14 (61) | 40,212 | 17,735 | 19,166 | 2,430 |
| 2026-09-27 (279) | 126,029 | 102,283 | 19,374 | 2,434 |
| 2026-10-03 (452) | 185,362 | 159,711 | 19,374 | 2,434 |
| 2026-10-05 (622) | 259,163 | 228,934 | 19,374 | 2,436 |

- The new package and its tests grew from 0 to 228,934 LOC over 591 first-parent commits, about 387 LOC per
  first-parent commit. Doc lines grew from 2,020 to 57,184 over the same period. Code at the 2026-04-18 commit is
  the v1 tree, not app. (VERIFIED)
- After the rewrite to `app/`, the v1 tree stayed in the repo: `bot.py`, `systems/`, `core/`, `agents/`,
  `models/`, `utils/` and `tests/`, totalling 21,359 LOC (8.2%). Evidence that nothing uses it:
  - `git grep` finds no import of it from `app/` or `tests_engine/`.
  - pytest `testpaths = ["tests_engine"]`, so the gate never runs the legacy tests.
  - Since 2026-05-04 one commit has touched it: `8044e2cb` on 2026-09-25, "updated documents".
  - `bot.py` (1,985 LOC) is the only `__main__` module in repo-L that agents never ran.

  (VERIFIED, git grep / git log / repo_metrics.py `not_from_project_scripts_by_dir`, `main_modules_run`)

### F3. repo-L: where the size goes, and what looks like accretion (VERIFIED unless labelled)

- **A second harness inside the product.** `app/dev` is 23,505 LOC across 61 files: 16.4% of `app` and
  4.7 times agent-harness's `devkit` (5,011). repo-L has 57 CLI subcommands. 14 of them have the same name as an
  agent-harness command: board, brief, check, claim, docs, doctor, integrate, lint, list, new, next, orient, show
  and status. Tests that import only `app.dev` add 6,042 LOC across 21 files; 94 test files (31,345 LOC)
  import it at all. (VERIFIED, repo_metrics.py `by_dir2`, `cli_commands`; test counts from `git grep`)
  How much of `app/dev` is generic is a judgement. Playtest, model-eval, agent-eval, prompt-budget, test-pg
  and scale are specific to the product. (BELIEVED)
- **Lazy imports hide cycles.** 1,067 of 7,588 internal import edges (14.1%) are inside functions. Counting those
  edges, `app.domain` forms a 76-module cycle, and the packages `app.domain`, `app.kernel` and
  `app.prompts` form a cycle at package level. Without them, the module graph is acyclic and the layering is
  clean: kernel→domain 512 edges, application→domain 218, application→kernel 192, adapters→application 49, with no
  reverse edges at module level. (VERIFIED)
- **Hotspots** (commits × LOC):
  - `app/adapters/discord.py`: 4,634 LOC, 71 commits, churn 6,915. 689 of its 3,999 normalised lines are
    type-2 duplicates within the file.
  - `app/dev/workflow.py`: 1,952 LOC, 65 commits.
  - `app/application/turns.py`: 1,069 LOC, 69 commits.
  - `app/application/gm_agent.py`: 1,079 LOC, 50 commits.

  The top 10% of files by churn carry 34.7% of all churn. 81 non-test files exceed 500 LOC and 14 exceed 1,000.
  (VERIFIED)
- **Superseded prompt versions.** 65 of 91 prompt `.md` files are not the active version in `ACTIVE.toml` (868
  non-blank lines). Its `reads` table still pins some older versions for replay, so not all of them are dead.
  (VERIFIED with a tomllib count; whether they are needed is BELIEVED)
- **Board growth.** The board holds 571 backlog tickets and 303 done. (VERIFIED, `git ls-files board/*`)
- **Signals that the code is essential.**
  - 93.7% of non-test Python (157,199 of 167,715 LOC) is reachable from `app.cli` or a `__main__` module
    through the import graph. Of the 5,745 LOC that is unreached, 2,986 are Alembic migrations loaded
    dynamically, so they are false positives.
  - vulture (at least 60% confidence) reports 1,052 items, with 662 in `app` and 180 in migrations. At 90%
    or more it reports only 18: 17 unused variables and 1 import. In a fixed 12-item sample of the 60%
    unused-function hits in `app`, 7 were framework callbacks (5 FastAPI routes in `adapters/web.py`, 2
    pytest hooks), so the 60% tier is mostly noise. (VERIFIED, `scripts/check_claims.py` + `uvx vulture`)
  - Agents ran 50 of the 57 subcommands directly, across 33,666 Bash calls in 407 transcripts. The other 7
    (discord, worker, inference-worker, the three `*-smoke` commands, scale) are launched by `dev up` or
    `dev/live.py`, so every command has a caller.

  (VERIFIED, repo_metrics.py `imports`, `vulture`, `usage`; callers found with `git grep`)
- **What a consolidation could remove without changing behaviour:** 45,895 LOC, or 17.7% of 259,163. That is the
  legacy tree (21,359), plus `app/dev` minus a devkit-sized replacement (23,505 − 5,011), plus tests that
  only cover dev (6,042). If every type-2 duplicate window outside tests were also merged (at most 18,902 lines),
  the total would be at most 25.0%. (VERIFIED arithmetic, `scripts/derive.py`)
  Getting repo-L to one fifth of its size, the owner's 100k-to-20k example, would mean redesigning the product
  code itself, for example rules as data rather than modules. These metrics can neither show nor rule that out.
  (BELIEVED)

### F4. repo-R: one-off experiments sit next to the core (VERIFIED, repo_metrics.py)

- **Size.** `sim/` is 20,355 LOC and `experiments/` 11,655 (29.6%) across 100 files of A/B and diagnostic
  scripts. Tests are 4,613 and `docs/research` 2,667, of which `report.html` is 1,615.
  - Growth: 3,976 LOC on 2026-10-07, 39,375 on 2026-10-11.
  - In that time `experiments/` grew from 610 to 11,655, faster in relative terms than `sim/` (3,118 to 20,355).
- **Usage.** repo-R has no `[project.scripts]`. All 93 entry points are `__main__` modules run as
  `python -m`.
  - 80 of them appear in agent transcripts (8,156 Bash calls in 85 files); `sim.board` was run 202 times.
  - 13 were never run (1,620 LOC), and 1,538 of those lines are in `experiments/`.
- **Duplication.** Type-2 duplicates concentrate in `experiments/`: 741 of 1,384 non-test duplicated lines. One
  example is the triplet `c6_ab.py`, `c6_holdback.py` and `c6_adaptive.py`. Two review-probe folders each contain
  a `swarm.py`, and the copies share 43 windows.
- **Imports.** 40.8% of import edges (452 of 1,107) are inside functions. Function-level import targets come from
  `experiments/` (759), `sim/` (612) and `tests/` (563), so the habit is repo-wide, not just experiments importing
  `sim` lazily. (VERIFIED, `scripts/check_claims.py`) One lazy import, from `sim/status.py:277` to `experiments.e7_levels`, closes a package cycle between
  `sim` and `experiments`.
- **Core quality.** The core looks essential: only 1,822 LOC is unreached, and vulture reports 7 findings at 90%
  confidence or more. The top hotspot is `sim/route.py` (1,211 LOC, 48 commits, churn 1,821).
- **Biggest cut.** Archiving experiments whose results are already recorded would remove up to 11,655 LOC (29.6%).
  Whether each experiment can be archived needs checking one by one. (BELIEVED)

### F5. agent-harness: lean but growing fastest in relative terms (VERIFIED, repo_metrics.py)

- **Size.** `devkit` is 5,011 LOC, `tests` 5,502 and `plugins` 1,239.
- **Quality.** No non-test duplication (symilar 0.10%), 2 vulture findings, no module-level cycles (one 5-module
  cycle through lazy imports in `cli`), and nothing unreached.
- **Usage.** Agents ran 38 of the 39 subcommands across 6.3k Bash calls in the repo-E and agent-harness
  transcripts; only `release` was never run. The count keeps moving because repo-E sessions are live.
- **Growth.** Code went from 6,826 to 12,016 LOC (+76%) between first-parent commits 3 and 30, about one day.
  Tests went from 2,410 to 5,522 over the same span, and deleted/added is 0.029.
- **Hotspots.** `devkit/board.py` is the top hotspot (1,120 LOC, 9 of 51 non-merge commits), followed by
  `devkit/workflow.py` (761 LOC) and `plugins/harness-ops/scripts/guard_bash.py` (783 LOC).

### F6. repo-E: no product code yet, and one real consolidation in its history (VERIFIED)

- **Code.** All 3,947 LOC are probe scripts under `docs/research/{_scratch,assets}`. They are one-off and not
  imported: 2,910 of 3,545 Python LOC are unreached. 7.7% are type-2 duplicates. repo-E also has 18,073 doc lines
  from 2 days. (repo_metrics.py)
- **Precedent.** PR #127 (`95-consume-agent-harness`, merge `ecfaa72`) replaced the in-repo `devkit/`, `tests/` and
  `.claude/hooks` with a dependency on agent-harness: 7,722 lines deleted, 96 added. Code fell from 6,740 to 1,220
  LOC between first-parent commits 24 and 28. This is the same move F3 suggests for repo-L's `app/dev`.
  (VERIFIED, `git diff --shortstat ecfaa72^1 ecfaa72`, repo_metrics.py `growth`)
- **Not copy-paste.** agent-harness does not copy repo-L's code. The two repos share only 1 normalised 6-line
  window, and repo-L shares 4 with repo-E. (VERIFIED, `--cross`)

### F7. What the data suggests for a consolidation trigger (#113) (BELIEVED, inferred from F1 to F6)

- **Weak triggers.** Exact duplication (at most 1.6%) and module-level cycles (zero) would never fire in these
  repos. LOC per merge (273 in agent-harness against 498 in repo-L) and the deleted/added ratio (0.029 against
  0.074) differ by less than 3x between the lean repo and the large one, while the repos differ 20x in size.
- **Signals that separated the repos:**
  1. A frozen subtree: a top-level directory of more than about 5% of code that has had no commits in N merges
     and is not reachable from the entry points. In repo-L that is 21.4k LOC.
  2. A parallel implementation of a capability that a shared dependency already provides (`app/dev`
     against devkit).
  3. A share of function-level imports above about 10%, or a cycle through lazy imports larger than 10 modules
     (repo-L 14.1% and 76 modules; repo-R 40.8% and 4).
  4. One-off script share: `__main__` modules outside the core that have gone unrun for N sessions, together with
     the size of `experiments/` (repo-R 29.6%).
  5. Hotspot files over 1,000 LOC with 30 or more commits (repo-L 4, repo-R 1, agent-harness 0; VERIFIED,
     `scripts/check_claims.py`).
- **Cheap to compute.** All five signals are computed in under 20 seconds by `repo_metrics.py`.

## Gaps not closed

- No runtime coverage. repo-L's suite needs PostgreSQL and local models, so "reached" means statically importable,
  and dynamic imports cause false "unreached" results (20 repo-L modules use importlib, and Alembic migrations load
  dynamically). Running `coverage` in the next round, on an archive copy of repo-R or agent-harness, would
  calibrate this.
- The type-2 measure works on line windows and gives an upper bound; it has not been validated against a
  token-level clone detector such as PMD CPD with `--ignore-identifiers`.
- Transcripts only show agent sessions on this machine. They miss the owner's own terminal and service managers.
- Nothing here maps features to modules, which is what a "5x smaller" estimate for repo-L's product code would need.
- Growth is measured on first-parent history only. repo-L was dormant from 2026-05-04 to 2026-09-13, so
  time-spaced samples collapse; the script uses end-of-day points plus points spaced by commit index.
- radon complexity (supplementary, non-test Python, `uvx radon==6.0.1 cc -j`):
  - agent-harness: 381 functions, mean CC 6.9, 21 above CC 20.
  - repo-R: 1,340 functions, mean CC 7.2, 93 above CC 20.
  - repo-L: 8,146 functions, mean CC 5.7, 326 above CC 20.

  These numbers are VERIFIED. They were not used in the findings, and radon's repo was last pushed on 2024-10-20.

## Sources and tools fetched

- [jendrikseipp/vulture](https://github.com/jendrikseipp/vulture): MIT, about 4.8k stars, pushed 2026-10-12;
  PyPI 2.16, used via `uvx vulture==2.16`.
- [pylint-dev/pylint](https://github.com/pylint-dev/pylint): GPL-2.0, about 5.7k stars, pushed 2026-10-12. Its
  `symilar` was used via `uvx --from pylint==4.0.2`. It is run only as a tool, never vendored.
- [rubik/radon](https://github.com/rubik/radon): MIT, about 2.0k stars, last pushed 2024-10-20 (stale); PyPI 6.0.1.
- Local git history of the four repos at the SHAs above, and Claude Code transcripts under `~/.claude/projects/`
  (read only).
