# agent-harness: agent guide

This repository is the harness other projects pin: the `devkit` package, the `harness-ops` Claude Code plugin and
the Copier template ([README.md](README.md)). Every change here reaches every project at its next update, so the bar
is the gate plus a rendered project's gate. `uv run dev --help` lists commands;
[docs/generated/agent-surface.md](docs/generated/agent-surface.md) is the plugin's surface.

## Rules
- Python is managed with **uv only** (`uv add`, `uv run`), never pip.
- Branch `<issue>-<slug>`; never commit to `main` directly. PRs need the `check`, `template` and `plugin` jobs green.
- Run `uv run dev test --affected` while iterating and `uv run dev check` once at the end. For template or plugin
  changes also run `scripts/render_check.sh <new dir>`. Never weaken a check, test or budget to pass.
- **Public repository.** Nothing private, and nothing specific to one project: project facts belong in that
  project's CLAUDE.md or `[tool.devkit]`. `tests/test_scrub.py` must pass.
- **Generic over specific.** A path or option one project needs becomes a `[tool.devkit]` key with a default that
  fits the template (`devkit/config.py`), never a hard-coded value.
- Plugin agents and skills are namespaced (`harness-ops:<name>`): name them so in skill text. Plugin agents ignore
  `permissionMode`, `hooks`, `mcpServers` and `initialPrompt`; the gate refuses them.
- A release is a tag `vX.Y.Z` with the same version in `pyproject.toml`, `plugins/harness-ops/.claude-plugin/plugin.json`
  and the `harness_ref` default in `copier.yml` (a test checks they agree).
- Design changes need evidence: extend [docs/research/agent-harness.md](docs/research/agent-harness.md), labelling
  claims VERIFIED, SOURCED, BELIEVED or ASSUMED, and run `uv run dev cite-check`.
- Never edit generated files (`docs/generated/`, `docs/research/INDEX.md`, `uv.lock`); run `uv run dev docs`.
