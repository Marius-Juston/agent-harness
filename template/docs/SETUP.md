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
read -rsp "OpenAlex key: " k && printf 'export OPENALEX_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
read -rsp "Semantic Scholar key: " k && printf 'export S2_API_KEY=%s\n' "$k" >> ~/.config/api-keys.env; unset k; echo
```
Load it in every interactive shell with this line in `~/.bashrc` (or `~/.zshrc`):
```bash
[ -f ~/.config/api-keys.env ] && . ~/.config/api-keys.env
```
Subagents and Claude Code's Bash tool do not source the shell profile, so `dev lit` also reads the file itself when
a key is not in its environment (`DEVKIT_SECRETS_FILE` names another file). It refuses a file that group or others
can read, and it never prints, logs or caches a key. Check with `uv run dev doctor` (each key found or missing,
never its value) and `uv run dev lit status` (`(key)` next to each keyed source).
