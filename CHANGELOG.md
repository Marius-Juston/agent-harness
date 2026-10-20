# Changelog

Notable changes of each release (a `vX.Y.Z` tag), newest first. A project moves to a release with
`uvx copier update`; read the entries between its pinned tag and the new one first. v0.1.0 to v0.1.4 predate this
file: see [their GitHub releases](https://github.com/Marius-Juston/agent-harness/releases).

## Unreleased

### Changed
- `dev integrate` waits (bounded by `checks_wait_s`) for checks still running on the PR's own head when they are its
  only unfinished feedback, then judges them; a failing check, requested change or open thread still refuses at
  once, and a timeout ends in `waiting`, which a rerun resumes. Not with `--override`, `--local-ci` or `--dry-run`.
  The lead skill no longer asks the lead to wait for green checks by hand. (#321, #322)

## v0.1.10 (2026-10-19)

Research-driven harness changes (#254, #282): shorter always-loaded rules, one hand-back and review contract,
explicit effort per agent, plugin skew detection, squash-only integrate, and a hard limit on hung tests.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.10`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **Plugin skew fails `doctor`.** `uv run dev doctor` exits 1 when Claude Code loaded a harness-ops build whose
  version or content digest differs from the pinned tag's (was a warning). `uv run dev setup` re-pins the plugin to
  the tag; `dev setup --repin-all` is the last resort and uninstalls the plugin from every other project. The
  SessionStart and SubagentStart hooks print the skew line. (#296, #310)
- **integrate squash-merges.** The squash commit is the PR title and body only, so put the trailers a commit needs
  in the PR body. Open PRs based on the merged branch are retargeted to main, with the `git rebase --onto` to run.
  In a public repository `dev pr` and `integrate` refuse a Claude session link or `Claude-Session:` trailer in the
  title, body or commit messages. The repository must allow squash merges; agent-harness itself is now
  squash-only. (#292, #295)
- **Compaction at 400k.** The template's `.claude/settings.json` sets `"autoCompactWindow": 400000`. (#258, #304)
- **Workers withhold tools.** builder sets `disallowedTools: Agent, Artifact`; tester and researcher also withhold
  `Skill`. The template leaves `enableArtifact` unset, so the lead keeps Artifacts. (#256, #311)
- **Effort per agent.** Every agent sets `effort` in its frontmatter (builder, reviewer, suite-auditor `medium`; the
  others `high`); the lead skill escalates per dispatch after a failure. (#259, #306)
- **One hand-back contract.** Every agent hands back at most 25 lines: Blocked on me, Changed, Found. (#263, #293)
- **Review verdict contract.** Findings are tagged Blocking, Nit, Pre-existing or Follow-up, and only Blocking
  returns a PR; from round 3 any Blocking finding means reconsider the approach. Each Blocking finding names a
  runnable probe that the builder commits as a test and reruns before each fix push. (#260, #309, #257, #315)
- **`dev lit search` coverage line.** Its first line is `discovery YYYY-MM-DD: OK|OK, partial|DEGRADED (...)` and it
  exits 1 on DEGRADED; the separate `dev lit status` step is gone from the research agents. (#266, #274)
- **Test timeout and gate deadline.** `pytest-timeout` is a dependency; `[tool.devkit] test_timeout_s = 300` fails a
  hung test (mark a slow one `@pytest.mark.timeout(n)`), and `gate_deadline_s = 3600` stops integrate's gate and
  kills its process group. (#43, #290)

### Added
- `dev agent-stats --cost`, `--reviews` and `--misses`: cost per merged issue reconciled with the cost records,
  review rounds per role, and candidate post-merge defects. (#255, #280)
- `dev eval --model/--effort/--agent/--label` arms with a check that the intended model ran, and a paired A/B
  report. (#82, #288)
- guard_bash denies `curl`, `wget` and WebFetch to the literature APIs `dev lit` wraps (`DEVKIT_LIT_RAW=1`
  overrides). (#227, #285)
- integrate refuses a merge that deletes a path another open issue owns; board lint reports deleted Owns as errors.
  (#230, #286)

### Changed
- HARNESS.md drops lines a hook enforces or another file repeats (543 -> 310 words, budget 320) and builder.md
  states that Edit and Write need a prior Read. (#267, #278)
- One-time owner-approved budget raise: lead `SKILL.md` 650 -> 720 words, `builder.md` 600 -> 650. (#267, #317)
- `dev pr` prints the PR URL and one status line; builders send the body with nothing piped after it. (#261, #273)
- read_guard windows Reads of persisted `tool-results/*.txt` outputs over 300 lines or 40 KB. (#265, #272)

## v0.1.9 (2026-10-15)

The owner can change the weekly quota cap on one machine without a PR: `! uv run dev quota-cap <0-100>`.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.9`, then `uv sync && uv run dev docs && uv run dev check`.
- **Machine-local quota cap.** `uv run dev quota-cap` prints the effective cap and its source; `<pct>` writes an
  override to `<git-common-dir>/devkit/quota_cap` (never committed, shared by all worktrees), `--clear` drops it.
  The spawn guard, `orient` and `status` use it over `[tool.devkit] quota_cap`. The Bash and Edit guards deny agents
  setting it. (#247)

## v0.1.8 (2026-10-14)

Research tools share one rate limit per user: sessions in different repositories on one machine no longer overrun
an API key together. The `Reuse:` line asks whether each new dependency does the job, and through which API.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.8`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **Rate limits move to a per-user directory.** `dev lit` and `dev reuse` keep their token buckets and shared
  cooldowns in `$XDG_STATE_HOME/devkit/lit` (default `~/.local/state/devkit/lit`), shared by every repository on the
  machine; the response cache stays in `<git-common-dir>/devkit/lit`. A project still on an older release keeps its
  own buckets, so update every project that searches with the same keys. SETUP.md lists every file devkit keeps
  outside a repository and how to remove it. (#241)
- **New fit clause in the `Reuse:` line.** Each new dependency needs `fit: <package> does <need> via <API>` (or
  `fit: unsure`) in a `Reuse:` line; `Reuse: none: harness adoption` on this update needs none. (#196)

### Added
- The `Reuse:` completion check refuses a new dependency without a fit clause, and the builder asks the fit question
  before `uv add`; the method is picked by a pre-registered eval in `software-discovery.md` §11. (#196)
- README cites The Semantic Scholar Open Data Platform (arXiv:2301.10140), as the Semantic Scholar API license asks.
  (#241)
- Research notes: recall of `dev lit search` against WebSearch and the arxiv MCP (#192), a pre-registered rerun of
  the discovery benchmark (#176), decision records and drift (#220), and builders' reuse trigger rate (#183).

### Changed
- Semantic Scholar is asked at 0.8 requests/s (was 1/s), below a key's 1/s across all endpoints. (#241)
- `dev reuse --from` builds the need from the symbol's name, its docstring's first sentence and its third-party
  imports only. (#175)

### Fixed
- `dev reuse check --json a b` and other option orders: bare words after an option join the need. (#175)
- The licence gate (G5) matches SPDX ids case-insensitively: `mit` and `apache-2.0` were HOLD. (#215)

## v0.1.7 (2026-10-13)

Reuse before build becomes a checked step: agents verify candidate libraries before they write code, `uv add` runs
the same gates, and the PR names what was weighed. The literature commands gain fields and citation chasing.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.7`, then `uv sync && uv run dev docs && uv run dev check`. These can
need attention:
- **New `uv add` guard (PreToolUse hook).** Each package named in `uv add` goes through `uv run dev reuse check`
  first. A name PyPI does not know, a malicious-package advisory or strong or network copyleft is refused; other
  findings are warnings, and an unreachable registry fails open with a warning. To allow a copyleft package, the
  owner lists it in `DEVKIT_ALLOW_COPYLEFT` (comma-separated) in the environment Claude Code runs in; an assignment
  inside the command does not count. (#172)
- **New `Reuse:` completion check.** A branch that adds a dependency to pyproject.toml or a new non-test module
  needs one `Reuse:` line in its PR summary: candidates with their `reuse check` results, the research note that
  weighed them, or `none: <reason>` (`Reuse: none: harness adoption` for this update). (#173)
- **Optional extra `semantic`.** `uv add 'agent-harness[semantic]'` installs model2vec, which `dev reuse` search
  uses to re-rank by meaning; without it the re-rank is BM25, and the output says which ran. (#29)

### Added
- `uv run dev reuse "<need>"` searches, gates and ranks maintained libraries and repositories, and
  `uv run dev reuse check <name>...` verifies named candidates (existence, malicious advisories, age, maintenance,
  licence, open advisories). The builder and researcher prompts name candidates first, then check them. Design and
  evidence in the research note `software-discovery.md`. (#29, #169, #179)
- `uv run dev lit get` returns authors, the open-access link and the full abstract, takes several ids, and batches
  OpenAlex lookups. (#174, #30)
- `uv run dev lit refs` and `uv run dev lit cited-by` chase citations backward and forward through OpenAlex. (#193)
- `uv run dev lit search --also` searches paraphrases too and merges the results by rank. (#194)
- `uv run dev cite-check` flags DOIs that Crossref marks retracted, withdrawn or removed; an expression of concern
  is a note. (#195)
- Ten pre-registered reuse-before-build trigger scenarios with their grader under evals/. (#183)
- The lead skill documents dispatching a builder onto another repository's issue. (#170)

### Fixed
- The licence gate reads a dual-licensed package as either licence (OR) and prefers the registry's SPDX
  expression, and it flags an undeclared licence list that mixes in strong copyleft. (#180, #182)

## v0.1.6 (2026-10-13)

A fix found adopting v0.1.5.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.6`, then `uv sync && uv run dev docs && uv run dev check`.

### Fixed
- `uv run dev cite-check` with no paths skips the research README.md, which the template ships since v0.1.5, as
  well as INDEX.md. Before, the first PR that brought the README in failed with `0 citations in 1 note(s)`. (#165)

## v0.1.5 (2026-10-13)

Round-2 hardening. The gate catches more ways to pass without doing the work, merging works without Actions
minutes, and the template now ships the generic files that projects had been writing by hand.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.5`, then `uv sync && uv run dev docs && uv run dev check`. Without
the flag the update keeps the stored v0.1.4 answer, and the devkit and plugin stay pinned there. These can need
attention:
- **New gate checks can fail a tree that passed under v0.1.4.**
  - Agent and skill frontmatter: a key outside the documented tables fails the gate, and a skill's `version:` key
    is now an error. Values outside the documented options fail too (`effort`, `isolation`, `memory`, `color`,
    `maxTurns`, `context`, `shell`). (#153)
  - Skill bash injection: an inline ``!`cmd` `` or the last line of a ```` ```! ```` block must end in `|| true` or
    in a `| head`/`| tail` pipeline, and it needs an allow rule. An unguarded one fails. (#153)
  - Test weakening: the gate flags removed tests or asserts and new skip/xfail (#55). It now also flags loosened or
    trivial asserts, a swallowed `AssertionError`, dropped parametrize cases, conftest and pytest/coverage config
    edits, golden-file edits, and patches of a name an assert calls (#127). The issue must record owner approval
    (`weakening-ok:`) for any of these.
  - Test floors: `[tests] min_tests` in `.devkit/ratchets.toml` (template default 1) and an optional
    `min_coverage`. Lowering either needs an owner-approved raise entry (#58). `xfail_strict = true` is on, so an
    xfail test that starts passing fails the run (#136).
  - `guard_bash` blocks destructive git and filesystem commands (#52), and read-only agents may not pass `git -c`
    (#145). Reading `core.hooksPath` with `git config --get` is now allowed; writing it is still refused (#45).
- **Files the template now ships that a project may already have locally.** `copier update` can report a conflict
  for each one. Keep your version or take the template's, and remove any conflict markers or `.rej` files:
  .devkit/HARNESS.md, docs/PROMPTS.md, docs/specs/multi-agent.md, scripts/claude-memory.sh, evals/README.md and
  evals/scenarios/, CONTRIBUTING.md, SECURITY.md and .editorconfig (sources under [template/](template)). `CHANGELOG.md`,
  `CLAUDE.md` and `README.md` are project-owned, and the update never touches them.
- **CLAUDE.md split.** Replace the generic sections of your CLAUDE.md with the line `@.devkit/HARNESS.md`, once,
  by hand ("Updating a project" in [README.md](README.md)). (#22)
- **Dependency pin.** The dev group now requires `agent-harness @ git+…@<tag>`, a direct reference, so a
  `--no-sources` resolve cannot pick the unrelated `agent-harness` on PyPI. (#61)

### Gate and checks
- Test-weakening check, extended to 11 kinds (#55, #127). Test-count and coverage floors, with report-only diff
  coverage (#58). `xfail_strict` on and higher floors in this repository (#136). Per-test JUnit outcome and
  duration history for audits (#121).
- Frontmatter allowlists match the documented agent and skill fields, with value and injection checks (#153).
- The word budget counts `.claude/rules` and `.claude/commands` (#44).
- Free secret and code scanning: a gitleaks hook and ruff `S` rules (#15).
- cite-check no longer reads DOI fragments as arXiv ids and resolves DataCite (Zenodo) DOIs (#35).

### Board, integrate and CI
- Integrate blocks on failing checks, unresolved review threads and requested changes (#39). Merge hygiene:
  stale branches are updated automatically, conflicts go back to the builder, and union merge applies only to
  append-only files (#38). After a push, integrate waits for every check on the new tip (#72).
- Merging works with no Actions minutes: the local gate on the merged tree posts the required check contexts (#76).
- The board uses GitHub's native sub-issues and blocked-by relationships (#64). `board lint` checks issue
  conventions and fixes the mechanical ones with `--fix` (#74).
- Template `gate.yml`: one concurrency group per PR or SHA, and citations folded into check (#57).
  `maintenance.yml` no longer fails on an issue body over 65,536 characters (#120).
- `spawn_guard` covers workflow agents, and a jq error can no longer block every spawn (#85).

### Plugin
- Agents and skills use the documented frontmatter: `maxTurns` and `color` on every agent, `isolation: worktree`
  for architect and suite-auditor, `when_to_use` on skills, `$ARGUMENTS` for research, and a `dev status`
  preload for lead (#153).
- New suite-auditor agent for a size-tiered test-suite audit, held read-only by `guard_bash` (#114, #146).
- Builder and tester prompts forbid special-casing test inputs (#143). A fix-now rule for small finds, and
  follow-up triage before integrate (#154).
- agent-stats no longer undercounts output tokens (it was 4 to 13 times low) (#53).

### Template
- CLAUDE.md is split into a project-owned CLAUDE.md and the template-owned .devkit/HARNESS.md (#22).
- Standard repository files: CONTRIBUTING, SECURITY, `.editorconfig` and CHANGELOG (#81). Routine prompts, the
  memory transfer script, the multi-agent spec, agent-surface evals and more read-only allow rules (#115).

### Research and internals
- New notes: [test-suite design](docs/research/test-suite-design.md) (#112),
  [architecture consolidation](docs/research/architecture-consolidation.md) (#111, #138),
  [fix now or defer](docs/research/fix-now-vs-defer.md) (#149),
  [test floors and coverage](docs/research/test-floors-and-coverage.md) (#58) and
  [free security scanning](docs/research/free-security-scanning.md) (#15).
- devkit: shared HTTP-get and atomic-write helpers (#139), and the import cycle through `cli` is gone (#140).
