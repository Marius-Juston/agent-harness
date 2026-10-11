# Machine setup

`uv run dev setup` does what can be automated; `uv run dev doctor` reports what this machine still needs. This page
covers the steps it cannot do for you.

## Research API keys
`uv run dev lit` searches OpenAlex and Semantic Scholar first. Without a key, every request goes to a shared pool
that throttles hard: a few parallel researchers get 429s, the pool can hold a source off for hours, and discovery
falls back to the weaker indexes. Both keys are free:

- OpenAlex: sign in at [openalex.org](https://openalex.org) and create an API key in your account settings.
- Semantic Scholar: request one with the [API key form](https://www.semanticscholar.org/product/api#api-key-form).

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
