# Machine setup

`uv run dev setup` does what can be automated; `uv run dev doctor` reports what this machine still needs. This page
covers the steps it cannot do for you.

## Research API keys
`uv run dev lit` works without any of these, but each one it lacks costs reach. All four are free, and the owner sets
them once per machine; an agent that meets a missing one reports it to the lead and never asks for the key:

| name | what it does in `dev lit` | how to get it |
|---|---|---|
| `OPENALEX_API_KEY` | searches OpenAlex on its own budget instead of a shared pool | sign in at [openalex.org](https://openalex.org) and create an API key in your account settings |
| `S2_API_KEY` | searches Semantic Scholar on its own budget instead of a shared pool | the [API key form](https://www.semanticscholar.org/product/api#api-key-form) |
| `CORE_API_KEY` | `dev lit read` and cite-check get full texts from CORE's repository copies (without a key CORE returns none) | register at [core.ac.uk/services/api](https://core.ac.uk/services/api) |
| `UNPAYWALL_EMAIL` | `dev lit read` and cite-check ask Unpaywall for open-access copies (never asked without it) | any address the owner reads; no registration. Unpaywall asks for it on every call |

Without the two search keys, every request goes to a shared pool that throttles hard: a few parallel researchers get
429s, the pool can hold a source off for hours, and discovery falls back to the weaker indexes. A Semantic Scholar
key allows 1 request per second across all endpoints, counted per key: `dev lit` keeps every process of every
repository on one machine below that, but another machine or script using the same key shares the budget and gets
429s. The contact email is not a secret in the strict sense, but it is personal and never committed, so it goes in the
same file (a project may instead set `[tool.devkit] contact_email`, which is committed).

Store them once per machine in `~/.config/api-keys.env`, mode 600, outside any repository. `read -rsp` keeps the key
off the screen and out of shell history, and `printf` is a shell builtin, so the key never shows in a process list.
Never paste a key into a Claude chat or a file in a repository. Run only the lines for the keys you have:
```bash
mkdir -p ~/.config && (umask 077 && touch ~/.config/api-keys.env) && chmod 600 ~/.config/api-keys.env
read -rsp "OpenAlex key: " k && printf 'OPENALEX_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
read -rsp "Semantic Scholar key: " k && printf 'S2_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
read -rsp "CORE key: " k && printf 'CORE_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
read -rsp "Unpaywall contact email: " k && printf 'UNPAYWALL_EMAIL=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
```
Do not export the keys in your shell profile (`~/.bashrc`, `~/.zshrc`, `~/.profile`), and do not source the file
there: an exported key is in every agent's environment, where one `env` or `echo` puts it into a transcript.
`uv run dev lit` reads the file itself, only for the keys its environment lacks (`DEVKIT_SECRETS_FILE` names
another file). It reads only a regular file owned by you that nobody else can access (mode 600; never a symlink),
and it never prints, logs or caches a key. Agents cannot read the file either: the plugin's Bash guard blocks it and
the project settings deny `Read` on it. To replace a key, run its line again (the last line for a name wins) and drop
the old line in your own editor, never through an agent. Check with `uv run dev doctor` (each of the four found or
missing, and from where, never its value) and `uv run dev lit status` (its `keys:` line names each keyed source and
any missing key).

## Host Python environment (ROS, conda, an old system python3)
The harness runs in the environment the project declares, not the one your shell happens to have:
- **Hook scripts** run on the machine's `python3` (they start `#!/usr/bin/env python3`), which must be 3.10 or
  later. `uv run dev doctor` checks it.
- **The gate and `dev test`** ignore `PYTHONPATH`, `PYTHONHOME`, `PYTHONOPTIMIZE`, `PYTHONWARNINGS`,
  `PYTEST_PLUGINS` and `PYTEST_ADDOPTS` and say so. A sourced ROS sets `PYTHONPATH` to its own Python's packages, and
  pytest would load their plugins into the venv's interpreter. `PYTHONOPTIMIZE` strips the asserts of the code under
  test, and `PYTHONWARNINGS` changes which warnings fail. A path or warning filter the tests really need belongs in
  pytest's `pythonpath` or `filterwarnings` option in `pyproject.toml`.
- **Everything else** (`uv run python`, a bare `pytest`) still sees those variables. On such a machine, clear them for
  agents in the gitignored `settings.local.json` under `.claude/`, so the change applies to this machine only:
  ```json
  { "env": { "PYTHONPATH": "" } }
  ```
  In your own shell, use `env -u PYTHONPATH <command>`, or a shell that has not sourced ROS. `dev doctor` warns while
  one of these variables is set.
- **Test dependencies go in the `dev` group.** `uv sync` (and CI's `uv sync --locked`) installs no optional extras
  and removes any you installed by hand. If the tests import an extra, list it in the dev group, e.g.
  `dev = ["<your-package>[<extra>, ...]", ...]`.

## Weekly quota cap
The spawn guard refuses new agents at `[tool.devkit] quota_cap`; `! uv run dev quota-cap 90` overrides it on this
machine only (`.git/devkit/quota_cap`, no PR), `--clear` drops the override, and agents are denied both.

## Gate slots
At most `[tool.devkit] gate_slots` gates run at once on a machine; `! uv run dev gate-slots 4` overrides it on this
machine only (`.git/devkit/gate_slots`, no PR), `--clear` drops the override, `uv run dev status` shows the effective
count and its source, and agents are denied setting it.

## Removing devkit's local files
devkit keeps no state outside these places, all plain files owned by you:
- `<repository>/.git/devkit/`: per-repository caches, logs and locks (never committed); deleting the repository
  deletes it.
- `${XDG_STATE_HOME:-~/.local/state}/devkit/`: the rate limits `dev lit` shares across repositories, since an API
  counts a key or an IP, not a repository.
- `~/.config/api-keys.env`: your research API keys, if you stored them.
- `~/<project>-claude-memory.tgz`: only if you ran `scripts/claude-memory.sh export` without naming a file.

After uninstalling the plugin, `rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/devkit"` removes the shared state;
delete the keys file only if nothing else on the machine uses it.
