# agent-harness

A harness for repositories that many Claude Code agents work on at once, on one or several machines: one lead
session per machine plans and integrates, worker subagents each take one GitHub issue in their own worktree and hand
back a gated pull request. It is one source of truth in three parts, so projects that use it do not drift apart:

| Part | What | How a project gets it |
|---|---|---|
| `devkit/` | The `uv run dev` command: the gate (`dev check`), the board on GitHub issues, symbol navigation, PR and integrate, research checks (`cite-check`, `lit`), setup | a pinned dependency (git tag) |
| `plugins/harness-ops/` | Claude Code plugin: agents (builder, tester, researcher, research-auditor, reviewer), skills (lead, research, harness), guard hooks, a status band with `/ops` | the marketplace in this repository, pinned to the same tag |
| `template/` | Copier template: settings, CI, pre-commit, `.gitattributes`, CLAUDE.md and docs skeletons, budgets | `copier copy`, then `copier update` (three-way) |

The design and its evidence: [docs/research/agent-harness.md](docs/research/agent-harness.md). The protocol agents
follow: [docs/specs/multi-agent.md](docs/specs/multi-agent.md).

## Quickstart
```bash
uvx copier copy gh:Marius-Juston/agent-harness my-project
cd my-project
uv run dev setup      # git init, uv sync, generated docs, git config, prek hooks, the Claude Code plugins
```
Then commit, create the GitHub repository (`gh repo create`), run `uv run dev board init` once, and start `claude`.

Prerequisites: git, [uv](https://docs.astral.sh/uv/), [gh](https://cli.github.com) (authenticated), jq, Node.js 20+
and [Claude Code](https://code.claude.com/docs).

The plugin alone, in any project:
```text
/plugin marketplace add Marius-Juston/agent-harness
/plugin install harness-ops@agent-harness
```
Its agents and skills are namespaced: `harness-ops:builder`, `/harness-ops:lead`. Most hooks call `uv run dev`, so
the plugin is meant for projects made from the template.

## Research API keys
`uv run dev lit` (the shared literature search) works without keys, but OpenAlex's and Semantic Scholar's shared
pools throttle parallel researchers hard and degrade discovery. Get both free keys and store them in
`~/.config/api-keys.env` (mode 600, outside any repository) with `read -rsp`, so they are never echoed, kept in shell
history or pasted into a Claude chat. Do not export them in your shell profile: an exported key sits in every
agent's environment. `dev lit` reads the file itself, and the plugin's Bash guard and the project's `Read` deny rule
keep agents from reading it. The steps: [docs/SETUP.md](docs/SETUP.md#research-api-keys), which every project made
from the template carries too; `uv run dev doctor` reports each key as found or missing.

## Configuration
What differs between projects lives in the project: `[tool.devkit]` in its pyproject.toml (the keys and defaults
are in [devkit/config.py](devkit/config.py)), word budgets in `.devkit/ratchets.toml`, the area-label map in
`.github/labeler.yml`, and its own CLAUDE.md. Everything generic is changed here, released as a tag, and pulled into
a project with `uvx copier update` (which also moves the devkit and plugin pins).

## Updating a project
```bash
uvx copier check-update   # is a newer release out? (`uv run dev doctor` warns too, for the template and plugins)
uvx copier update         # three-way: your edits to template files are kept
uv sync && uv run dev docs && uv run dev check
```

## Working on this repository
It runs its own harness on itself (`[tool.devkit]` in [pyproject.toml](pyproject.toml)): `uv sync`, then
`uv run dev check`. CI also renders the template into a fresh project and gates it
([scripts/render_check.sh](scripts/render_check.sh)), and runs `claude plugin validate --strict`. A test scans every
file for strings that must not be public ([tests/test_scrub.py](tests/test_scrub.py)).

## License
MIT, see [LICENSE](LICENSE).
