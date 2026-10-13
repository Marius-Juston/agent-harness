# Changelog

Notable changes of each release (a `vX.Y.Z` tag), newest first. A project moves to a release with
`uvx copier update`; read the entries between its pinned tag and the new one first. v0.1.0 to v0.1.4 predate this
file: see [their GitHub releases](https://github.com/Marius-Juston/agent-harness/releases).

## v0.1.6 (2026-10-13)

A fix found adopting v0.1.5.

### Upgrading a project
Run `uvx copier update --data harness_ref=v0.1.6`, then `uv sync && uv run dev docs && uv run dev check`.

### Fixed
- `uv run dev cite-check` with no paths skips `docs/research/README.md`, which the template ships since v0.1.5, as
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
