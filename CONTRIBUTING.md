# Contributing

agent-harness is the harness other projects pin, so every change here reaches every project at its next update.
The rules that agents and people follow alike are in [CLAUDE.md](CLAUDE.md); this page is the path from a clone to
a merged pull request.

## From a clone to a green gate
1. Install the prerequisites listed in the [README](README.md#quickstart).
2. Clone the repository, then in the clone run `uv sync` and `uv run dev check`. The gate ends with
   `RESULT: passed`. If it does not on a fresh clone of `main`, that is a bug: report it.
3. Machine steps that cannot be scripted, such as the research API keys: [docs/SETUP.md](docs/SETUP.md).
   `uv run dev doctor` reports what this machine still lacks.

Every command and what it does: `uv run dev --help` (and `--help` on each command). Nothing here copies that list.

## From an issue to a pull request
1. Work starts from an issue with a measurable DONE WHEN. Open one with the issue forms, or comment on an existing one.
2. Branch `<issue>-<slug>` from `main`. Write the failing test first; iterate with `uv run dev test --affected`.
3. A change to `template/` or `plugins/` also needs a rendered project's gate:
   [scripts/render_check.sh](scripts/render_check.sh) with a new directory.
4. Run `uv run dev check` once on the committed tip, then open the pull request. Fill in its template: what changed,
   the evidence, and a verdict for each DONE WHEN item.
5. A design change needs evidence first, in [docs/research/agent-harness.md](docs/research/agent-harness.md).

## Conduct, security, license
- Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md).
- Vulnerabilities and leaked secrets are reported privately: [SECURITY.md](SECURITY.md).
- Contributions are licensed under the repository's [MIT license](LICENSE).
