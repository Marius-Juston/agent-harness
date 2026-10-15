# Machine setup

`uv run dev setup` does what can be automated; `uv run dev doctor` reports what this machine still needs. This page
covers the steps it cannot do for you.

## Research API keys
`uv run dev lit` searches OpenAlex and Semantic Scholar first. Without a key, every request goes to a shared pool
that throttles hard: a few parallel researchers get 429s, the pool can hold a source off for hours, and discovery
falls back to the weaker indexes. Both keys are free:

- OpenAlex: sign in at [openalex.org](https://openalex.org) and create an API key in your account settings.
- Semantic Scholar: request one with the [API key form](https://www.semanticscholar.org/product/api#api-key-form).
  A key allows 1 request per second across all endpoints, counted per key: `dev lit` keeps every process of every
  repository on one machine below that, but another machine or script using the same key shares the budget and gets 429s.

Store them once per machine in `~/.config/api-keys.env`, mode 600, outside any repository. `read -rsp` keeps the key
off the screen and out of shell history, and `printf` is a shell builtin, so the key never shows in a process list.
Never paste a key into a Claude chat or a file in a repository.
```bash
mkdir -p ~/.config && (umask 077 && touch ~/.config/api-keys.env) && chmod 600 ~/.config/api-keys.env
read -rsp "OpenAlex key: " k && printf 'OPENALEX_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
read -rsp "Semantic Scholar key: " k && printf 'S2_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
```
Do not export the keys in your shell profile (`~/.bashrc`, `~/.zshrc`, `~/.profile`), and do not source the file
there: an exported key is in every agent's environment, where one `env` or `echo` puts it into a transcript.
`uv run dev lit` reads the file itself, only for the keys its environment lacks (`DEVKIT_SECRETS_FILE` names
another file). It reads only a regular file owned by you that nobody else can access (mode 600; never a symlink),
and it never prints, logs or caches a key. Agents cannot read the file either: the plugin's Bash guard blocks it and
the project settings deny `Read` on it. Check with `uv run dev doctor` (each key found or missing, never its value)
and `uv run dev lit status` (`(key)` next to each keyed source).

## Weekly quota cap
The spawn guard refuses new agents at `[tool.devkit] quota_cap`; `! uv run dev quota-cap 90` overrides it on this
machine only (`.git/devkit/quota_cap`, no PR), `--clear` drops the override, and agents are denied both.

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
