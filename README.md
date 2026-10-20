# agent-harness

A harness for repositories that many Claude Code agents work on at once, on one or several machines: one lead
session per machine plans and integrates, worker subagents each take one GitHub issue in their own worktree and hand
back a gated pull request. It is one source of truth in three parts, so projects that use it do not drift apart:

| Part | What | How a project gets it |
|---|---|---|
| `devkit/` | The `uv run dev` command: the gate (`dev check`), the board on GitHub issues, symbol navigation, PR and integrate, research checks (`cite-check`, `lit`), setup | a pinned dependency (git tag) |
| `plugins/harness-ops/` | Claude Code plugin: agents (builder, tester, researcher, research-auditor, reviewer, architect, suite-auditor), skills (lead, research, harness), guard hooks, a status band with `/ops` | the marketplace in this repository, pinned to the same tag |
| `template/` | Copier template: settings, CI, pre-commit, `.gitattributes`, CLAUDE.md and docs skeletons, budgets | `copier copy`, then `copier update` (three-way) |

The design and its evidence: [docs/research/agent-harness.md](docs/research/agent-harness.md). The protocol agents
follow: [docs/specs/multi-agent.md](docs/specs/multi-agent.md).

<p align="center">
  <img src="docs/assets/demo-new-project.svg" width="100%" alt="A terminal walkthrough in six scenes: copier copy creates my-project; uv run dev setup prints ok for each step; the repository goes on GitHub and uv run dev board new files issue #1 with a DONE WHEN; claude starts and the /goal prompt is pasted; the lead dispatches harness-ops:builder, which opens PR #2, harness-ops:reviewer approves it and uv run dev integrate re-gates and merges it; gh issue list shows #1 closed.">
</p>
<p align="center"><sub>A condensed replay of a real session: the shell output is captured, the Claude Code part is shortened.
The commands are the quickstart below; an existing project and an update have their own, further down.</sub></p>

## Quickstart
```bash
uvx copier copy gh:Marius-Juston/agent-harness my-project
cd my-project
uv run dev setup      # git init, uv sync, generated docs, git config, prek hooks, the Claude Code plugins
```
Then commit, create the GitHub repository (`gh repo create`), run `uv run dev board init` once, and file work with
`uv run dev board new` (each item gets a measurable `--done`). Start `claude` and paste the `/goal` prompt from the
new project's [docs/SESSION.md](template/docs/SESSION.md), or type `continue` for one pass that stops at the first
decision. The session is the lead: it gives each startable issue to a worker in its own worktree, and every PR is
re-gated and merged only by `uv run dev integrate`. It stops when the board needs you.

Prerequisites: git, [uv](https://docs.astral.sh/uv/), [gh](https://cli.github.com) (authenticated), jq, Node.js 20+
and [Claude Code](https://code.claude.com/docs).

The plugin alone, in any project:
```text
/plugin marketplace add Marius-Juston/agent-harness
/plugin install harness-ops@agent-harness
```
Its agents and skills are namespaced: `harness-ops:builder`, `/harness-ops:lead`. Most hooks call `uv run dev`, so
the plugin is meant for projects made from the template.

## Adding it to an existing project
<p align="center">
  <img src="docs/assets/demo-existing-project.svg" width="100%" alt="A terminal walkthrough in five scenes: in an existing app with a clean git tree, copier copy asks to overwrite pyproject.toml and .gitignore and skips CLAUDE.md and README.md; git diff shows the replaced version and dependencies, which are put back with an editor and uv add; @.devkit/HARNESS.md is appended to CLAUDE.md; uv run dev setup, a commit and uv run dev check pass.">
</p>

```bash
cd my-app                                          # a clean git tree, so git shows what the template changes
uvx copier copy gh:Marius-Juston/agent-harness .   # yes to overwrite pyproject.toml and .gitignore
git diff                                           # put back your [project] fields (`uv add` your dependencies) and ignores
echo '@.devkit/HARNESS.md' >> CLAUDE.md            # your CLAUDE.md and README.md are kept: import the agent rules
uv run dev setup && git add -A && git commit -m "Adopt agent-harness" && uv run dev check
```
Commit before the first `uv run dev check`: its test-weakening check reads the template's pytest settings as an
edit until they are on the branch. The template's lint and test settings now cover your code, so the first gate may
find things to fix; fix them or set the rules in pyproject.toml, never by weakening a check. Then continue as in the
quickstart: `uv run dev board init`, `uv run dev board new`, `claude`.

## Research API keys
`uv run dev lit` (the shared literature search) works without keys, but OpenAlex's and Semantic Scholar's shared
pools throttle parallel researchers hard and degrade discovery. Get both free keys and store them in
`~/.config/api-keys.env` (mode 600, outside any repository) with `read -rsp`, so they are never echoed, kept in shell
history or pasted into a Claude chat. Do not export them in your shell profile: an exported key sits in every
agent's environment. `dev lit` reads the file itself, and the plugin's Bash guard and the project's `Read` deny rule
keep agents from reading it. The steps: [docs/SETUP.md](docs/SETUP.md#research-api-keys), which every project made
from the template carries too; `uv run dev doctor` reports each key as found or missing.

Results from Semantic Scholar come from the Semantic Scholar Academic Graph; work that publishes them cites
Kinney et al., [The Semantic Scholar Open Data Platform](https://arxiv.org/abs/2301.10140) (2023), as the
[API license](https://www.semanticscholar.org/product/api/license) asks.

## Configuration
What differs between projects lives in the project: `[tool.devkit]` in its pyproject.toml (the keys and defaults
are in [devkit/config.py](devkit/config.py)), word budgets and test floors in `.devkit/ratchets.toml`, the area-label map in
`.github/labeler.yml`, and its own CLAUDE.md. Everything generic is changed here, released as a tag, and pulled into
a project with `uvx copier update --data harness_ref=<tag>`, which also moves the devkit and plugin pins.

## Updating a project
<p align="center">
  <img src="docs/assets/demo-update.svg" width="100%" alt="A terminal walkthrough in four scenes: copier check-update finds v0.1.13; on a new branch copier update changes five files and git diff shows the devkit, source and plugin pins moving from v0.1.12 to v0.1.13; uv sync, dev docs and dev check pass; uv run dev setup moves the plugin to the new pin and the branch is pushed.">
</p>

```bash
uvx copier check-update   # is a newer release out? (`uv run dev doctor` warns too, for the template and plugins)
uvx copier update --data harness_ref=<tag>   # three-way: your edits are kept; the flag moves the pins
uv sync && uv run dev docs && uv run dev check
uv run dev setup          # moves the installed plugin to the new pin
```
`copier update` never touches a project's CLAUDE.md or README.md (`_skip_if_exists`); the generic agent rules are the
template-owned .devkit/HARNESS.md ([source](template/.devkit/HARNESS.md.jinja)), which CLAUDE.md @-imports. A project made before v0.1.5 replaces the generic
sections of its CLAUDE.md with the line `@.devkit/HARNESS.md` once, by hand, after its first such update.

## Working on this repository
It runs its own harness on itself (`[tool.devkit]` in [pyproject.toml](pyproject.toml)): `uv sync`, then
`uv run dev check`. CI also renders the template into a fresh project and gates it
([scripts/render_check.sh](scripts/render_check.sh)), and runs `claude plugin validate --strict`. A test scans every
file for strings that must not be public ([tests/test_scrub.py](tests/test_scrub.py)). From a clone to a pull
request: [CONTRIBUTING.md](CONTRIBUTING.md); vulnerabilities: [SECURITY.md](SECURITY.md).

## License
MIT, see [LICENSE](LICENSE).
