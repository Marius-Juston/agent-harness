#!/usr/bin/env python3
"""PreToolUse(Bash): refuse commands that break project rules, each with the fix in the message (exit 2).
PreToolUse(Read|Grep): refuse the same secrets (.env, the research keys file, gh's credential) to the file tools.

The command is tokenized the way bash reads it: quoted strings and heredoc bodies are data (commit messages, issue
text, file contents), while every simple command is checked, including those inside $(...), `...`, <(...),
`bash -c`, `eval`, `find -exec`, wrappers (sudo, env, time, timeout, xargs, uv run, python -m) and text fed on stdin
to an interpreter (bash, sh, python) by a heredoc, a here-string or a pipe. Stdlib only. Settings deny rules are
prefix-only and this hook is best effort; server-side branch protection is the only complete guard for main.

GitHub writes that bypass integrate or widen access (gh pr merge, repository settings, refs, releases, gists) are
refused to every session; issue housekeeping is the lead's, and PR reviews and `Verdict:` lines belong to the reviewer
agents (#528), told apart by the input's `agent_type`.

A command allowed through that can wait forever (a poll loop, `tail -f`, `watch`, `wait`, ...) is rewritten
(updatedInput) to run under `timeout <[tool.devkit] wait_bound_s>`; see bound_waits.

Defense in depth, not a security boundary. Every agent runs as the owner, holding the owner's GitHub token and SSH
keys, so the GitHub and credential rules (#528, #601) close the common and the obvious paths only: code in a script
file, an encoding this lexer does not undo, or a tool it does not know still reaches what the owner can. The real
boundary is credentials scoped per agent, which no hook provides.
"""

import itertools
import json
import os
import re
import sys
from fnmatch import fnmatchcase

# The shared modules live beside this script; a test or another hook may load it by path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Read through guard_bash by the tests and by uv_add_guard.
from hooklib.github import COMMIT_API as COMMIT_API
from hooklib.github import (
    CRED_OK,
    GH_CRED,
    GREP_ABOVE,
    OPAQUE,
    REF_DELETE,
    TOKEN_NAMES,
    TRANSFORMERS,
    GitHubRules,
    assigned,
    cred_path,
    holds_cred,
)
from hooklib.github import GH_ADMIN as GH_ADMIN
from hooklib.github import GH_DIRECT as GH_DIRECT
from hooklib.github import GH_MERGE as GH_MERGE
from hooklib.github import GH_UNKNOWN as GH_UNKNOWN
from hooklib.github import ISSUE_WRITE as ISSUE_WRITE
from hooklib.github import OWNER_MARK as OWNER_MARK
from hooklib.github import REVIEW as REVIEW
from hooklib.github import REVIEW_VAR as REVIEW_VAR
from hooklib.github import VERDICT_LINE as VERDICT_LINE
from hooklib.github import WEAKENING_OK as WEAKENING_OK
from hooklib.github import cred_dirs as cred_dirs
from hooklib.shell import (
    ASSIGN,
    GREPS,
    KEYWORDS,
    MAX_DEPTH,
    PY,
    SHELLS,
    Simple,
    Word,
    base,
    lex,
    long_opt,
    unwrap,
    within,
)
from hooklib.waits import WAIT_BOUND as WAIT_BOUND
from hooklib.waits import bound_waits

# Hooks run as `#!/usr/bin/env python3`, the host's interpreter, not the project's: every script must run on this
# (Ubuntu 22.04 LTS ships 3.10). tests/test_plugin.py runs each one under it; devkit.setup.HOOK_PYTHON must agree.
MIN_PYTHON = (3, 10)

PIP = "Python is managed with uv only: `uv add <pkg>` (or `uv add --dev`), `uv run ...`. Never pip."
PKILL = '`pkill -f` matches the shell running it. Collect PIDs with `pgrep -f "[p]attern"` in one call, kill them in another.'
ENV = "Never read secrets: .env stays unread. Commands that need it load it themselves."
KEYS_FILE = (
    "Never read the research API keys file (~/.config/api-keys.env or $DEVKIT_SECRETS_FILE): `uv run dev lit` "
    "loads it itself, and `uv run dev doctor` says whether each key is found, never its value."
)
# The older name. Code returns and prints KEYS_FILE: CodeQL treats a value read from a variable named like a
# secret as one (py/clear-text-logging-sensitive-data on #603), though this is a fixed refusal message.
SECRETS = KEYS_FILE
SECRETS_NAME = "api-keys.env"
MAIN = "Never push to main: push your issue branch and open a PR (`uv run dev pr <issue>`); the lead integrates."
FORCE = "Force-push rewrites shared history. Use --force-with-lease on your own issue branch only, never on main."
HOOKS = "--no-verify, -n, SKIP= and core.hooksPath skip the checks every agent is held to. Fix what the hook reports instead."
DEEP = "the command nests too deeply to check. Split it into simpler commands."
# Destructive local commands: each loses uncommitted work, and sibling worktrees share one repository.
RESET = (
    "`git reset --hard` / `--merge` throw away uncommitted work. Commit it first, then `git reset --keep <ref>`, "
    "which refuses to lose changes."
)
DISCARD = (
    "this discards uncommitted changes across the tree. Commit what you want to keep (a WIP commit is fine), revert one "
    "file with `git restore <file>`, or unstage with `git restore --staged .`."
)
CLEAN = "`git clean -f` deletes untracked files for good. Preview with `git clean -n`, then `rm` the files it lists."
RM = (
    "recursive rm outside your worktree (or of the worktree or its .git) can delete another worker's files. "
    "`rm -r` paths inside your worktree, and remove a worktree with `git worktree remove <path>`."
)
STASH = (
    "the stash is shared by every worktree of the repository, so `git stash drop` / `clear` can delete another "
    "worker's changes. Keep work in a WIP commit and leave stash entries to the owner."
)
WORKTREE = (
    "`git worktree remove --force` deletes unsaved work. Commit and push its branch, then `git worktree remove <path>`."
)
BRANCH_D = (
    "`git branch -D` deletes an unmerged branch, maybe another worker's. Use `git branch -d`; for a squash-merged "
    "branch, run `uv run dev worktree prune` (integrate already does for the PR it merges): it "
    "deletes only a branch whose merged PR head contains its tip."
)
QUOTA = (
    "the quota cap is the owner's: agents never change it. `uv run dev quota-cap` prints it; ask the owner to run "
    "`! uv run dev quota-cap <pct>` (or to raise [tool.devkit] quota_cap). Stop spawning; commit and hand off."
)
LIT = (
    "call the literature APIs through `uv run dev lit` (search, get, refs, cited-by, status): it shares one rate "
    "limit, cache and key set across every worktree. Working on devkit/lit.py itself? Prefix the fetch with "
    "DEVKIT_LIT_RAW=1."
)
# The endpoints devkit/lit.py wraps (#227; docs/research/decision-records-and-drift.md check 9). Europe PMC stops
# at rest/search: its full-text downloads (rest/PMC.../fullTextXML) are not in dev lit and stay allowed.
LIT_API = re.compile(
    r"(?<![\w.-])(?:https?://)?(?:(?:api\.openalex\.org|api\.semanticscholar\.org|api\.crossref\.org)[/?]"
    r"|www\.ebi\.ac\.uk/europepmc/webservices/rest/search|export\.arxiv\.org/api/)",
    re.I,
)
LIT_VAR = re.compile(r"\$\{?(\w+)")  # a fetched argument that starts with a variable: $u, ${u}...
LIT_RAW = "DEVKIT_LIT_RAW"
# Environment the guards read (#191). The first two switch a guard off, so they count only from the environment
# Claude Code started in (owner_env); edit_guard refuses agent edits that put any of them in a settings file.
OWNER_ENV = ("DEVKIT_ALLOW_COPYLEFT", LIT_RAW, "DEVKIT_SECRETS_FILE", REVIEW_VAR)  # REVIEW_VAR widens a guard (#662)
# Names that move where settings files are found: a settings `env` setting one could hide the file that sets an
# override, so owner_env ignores every override while one is set, and edit_guard refuses adding them as well.
LOCATORS = ("HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR")
FETCHERS = {"curl", "wget"}
SLOTS = (
    "the gate slot count is the owner's: agents never change it. `uv run dev gate-slots` prints it; ask the owner to "
    "run `! uv run dev gate-slots <n>` (or to change [tool.devkit] gate_slots)."
)
# The owner's machine-local overrides under the git common dir: the `dev` command that sets each, its file, the denial.
OWNER_SETTINGS = (("quota-cap", "devkit/quota_cap", QUOTA), ("gate-slots", "devkit/gate_slots", SLOTS))
CAP_READERS = {"cat", "head", "tail", "ls", "stat", "wc", "test", "[", "[[", "file"}
WHOLE_TREE = {".", "./", ":", ":/", ":/*", "*"}  # pathspecs that name every file
HOOK_VAR = re.compile(r"^(SKIP|PRE_COMMIT\w*|GIT_CONFIG\w*)(=|$)")
C_RUNNERS = {"flock", "su", "runuser", "script"}  # their -c / --command value is a shell command
GREP_VALUE = {"-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--context", "-g", "--glob", "-t", "--type"}

ENV_SAFE = {".env.example", ".env.sample", ".env.template", ".env.dist"}
ENV_OK_CMDS = {"ls", "stat", "test", "[", "[[", "echo", "printf", "touch", "rm"}
MESSAGE_OPTS = {"-m", "--message", "--title", "--body", "-b", "-t", "--subject"}  # their values are text, not paths
# `git config` options that only read; any other flag (--add, --unset, -e, ...) makes the call a possible write
CONFIG_READ_ACTIONS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list"}
CONFIG_READ_FLAGS = {"--global", "--system", "--local", "--worktree", "--show-origin", "--show-scope", "--includes"}
CONFIG_READ_FLAGS |= {"--no-includes", "-z", "--null", "--name-only", "--all", "--regexp", "--fixed-value"}
CONFIG_READ_FLAGS |= {"--bool", "--int", "--bool-or-int", "--path", "--expiry-date", "--no-type"}
CONFIG_VALUE_OPTS = {"-f", "--file", "--blob", "--type", "--default", "--url", "--value"}  # their value is not a name


def settings_files(cwd: str) -> list[str]:
    """Every Claude Code settings file that could feed this session's `env`: .claude/settings{,.local}.json in each
    directory from the cwd, the git checkout (and its main checkout), the user's home and the project directory up
    to /, and in $CLAUDE_CONFIG_DIR. Home and checkout are found without the environment as well, since a settings
    `env` block could point HOME or CLAUDE_PROJECT_DIR elsewhere."""
    starts = [cwd, os.getcwd(), os.environ.get("CLAUDE_PROJECT_DIR", ""), os.path.expanduser("~")]
    try:
        import pwd  # POSIX only

        starts.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        pass
    import subprocess  # here, not at the top: most calls never spawn anything, and the import costs every call

    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        lines = out.stdout.splitlines() if out.returncode == 0 else []
        starts += lines[:1] + [os.path.dirname(x) for x in lines[1:2]]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    dirs: list[str] = []
    for start in starts:
        d = os.path.abspath(start) if start else ""
        while d and d not in dirs:
            dirs.append(d)
            d = os.path.dirname(d) if os.path.dirname(d) != d else ""
    files = [os.path.join(d, ".claude", f) for d in dirs for f in ("settings.json", "settings.local.json")]
    if config := os.environ.get("CLAUDE_CONFIG_DIR"):
        files += [os.path.join(config, f) for f in ("settings.json", "settings.local.json")]
    return files


JSON_KEY = re.compile(r'"((?:[^"\\]|\\.)*)"\s*:')


def unescape(raw: str) -> str:
    """A JSON string body decoded, so `DEVKIT_LIT_RAW` reads as the name it is; undecodable text as written."""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def env_keys(text: str) -> set[str]:
    """The names a settings file's `env` block sets. Text that is not a JSON object fails closed: every key-like
    string in it counts, since Claude Code's reader may be more lenient than json."""
    try:
        data = json.loads(text)
    except ValueError:
        return {unescape(m.group(1)) for m in JSON_KEY.finditer(text)}
    env = data.get("env") if isinstance(data, dict) else None
    return {k for k in env if isinstance(k, str)} if isinstance(env, dict) else set()


def owner_env(name: str, cwd: str) -> str:
    """An override that switches a guard off, read only where an agent's tools cannot set it (#191).

    Claude Code hands a settings file's `env` block to hooks, ahead of the shell it started in (VERIFIED,
    docs/research/agent-harness.md §12c), and an agent can write those files. So the name reads as unset when any
    settings `env` sets it, or sets one of LOCATORS (which could hide the file that does): the owner sets it in the
    shell that starts Claude Code. Unset in the environment, nothing is read, so the usual call costs nothing."""
    value = os.environ.get(name, "")
    if not value:
        return ""
    for path in settings_files(cwd):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if env_keys(text) & {name, *LOCATORS}:
            return ""
    return value


def lit_vars(pipes: list[list[Simple]]) -> set[str]:
    """Names assigned a literature-API URL by a command: `u=<url>`, `export u=<url>`, `for u in ... <url> ...`."""
    names = set()
    for c in itertools.chain.from_iterable(pipes):
        words = [w.value for w in c.words]
        while words and words[0] in KEYWORDS:
            words.pop(0)
        if words[:1] == ["for"] and words[2:3] == ["in"] and any(LIT_API.match(v) for v in words[3:]):
            names.add(words[1])
        if words[:1] and words[0] in ("export", "declare", "typeset", "local", "readonly"):
            words.pop(0)
        for w in itertools.takewhile(lambda w: ASSIGN.match(w) or w.startswith("-"), words):
            name, _, value = w.partition("=")
            if value and LIT_API.match(value):
                names.add(name.rstrip("+"))
    return names


def is_env(w: Word, code: bool = False) -> bool:
    """`code`: the word is from a Python string literal, where `.*` and `(.*?)` are regexes far more often than globs,
    so a glob counts only if it spells the start of the name (`.e*`, `.env*`)."""
    if any(ch.isspace() for ch in w.value):  # prose (a commit or issue body), not a path
        return False
    name = base(re.split(r"[=:]", w.value)[-1])
    if w.glob:  # a glob reaches a dotfile only if its own name starts with "." (no dotglob)
        if code and name[1:2] in ("", "*", "?", "["):
            return False
        return name.startswith(".") and any(fnmatchcase(f, name) for f in (".env", ".env.local"))
    return name not in ENV_SAFE and (name == ".env" or name.startswith(".env."))


def is_secrets(w: Word) -> bool:
    """The research API keys file: its default name, $DEVKIT_SECRETS_FILE, or the path that variable names here."""
    if any(ch.isspace() for ch in w.value):  # prose, not a path
        return False
    if "DEVKIT_SECRETS_FILE" in w.value and "$" in w.value:
        return True
    path = re.split(r"[=:]", w.value)[-1]
    name = base(path)
    if w.glob:  # `api-*`, `*.env`, or anything in ~/.config that would expand to it
        literal = re.sub(r"[*?\[\]]", "", name)
        near = path.rsplit("/", 1)[0].endswith(".config") if "/" in path else False
        return fnmatchcase(SECRETS_NAME, name) and (len(literal) >= 3 or near)
    custom = os.environ.get("DEVKIT_SECRETS_FILE", "")
    return name == SECRETS_NAME or (bool(custom) and ((name == base(custom) and name != "") or path == custom))


class Checker(GitHubRules):
    def __init__(self, cwd: str, read_only: str = "", agent: str | None = None):
        # #528: the subagent's type, plugin prefix dropped; None in the main (lead) session. VERIFIED on a real
        # PreToolUse input: a subagent's carries agent_type "<plugin>:<name>", the main session's has none.
        self.agent = agent
        self.fed: list[str] = []  # the text earlier stages of the current pipeline write to this command's stdin
        self.later: list[str] = []  # the command names of the current pipeline's later stages
        self.vars: dict[str, str] = {}  # literal values the command assigns, expanded where words use them
        self.transforms = False  # some stage rewrites text (base64 -d, rev, sed, ...): code built from it is opaque
        self.read_only = read_only  # a read-only agent's own `dev` report (READ_ONLY_AGENTS); "": not read-only
        self.cwd = cwd
        self.start = cwd  # the session's worktree; a `cd ..` inside the command does not move it
        self.in_python = 0  # > 0 while checking a Python string literal as a shell command
        self.lit_vars: set[str] = set()  # variables the command sets to a literature-API URL
        self.lit_raw = owner_env(LIT_RAW, cwd) == "1"

    def review_env(self) -> str:
        """#662: DEVKIT_REVIEW_AGENTS as the owner set it; "" when a settings `env` block (an agent can write one)
        sets it. Read only when a review or verdict post is checked."""
        return owner_env(REVIEW_VAR, self.start)

    def text(self, text: str, depth: int = 0) -> str | None:
        """Check a shell command string; return the reason it is blocked, or None."""
        if depth > MAX_DEPTH:
            return DEEP
        pipes = lex(text)
        self.lit_vars |= lit_vars(pipes)
        self.vars.update(assigned(pipes))
        if TOKEN_NAMES & set(self.vars.values()):  # x=GH_TOKEN; echo ${!x}
            return GH_CRED
        names = [[base(a[0]) for a in [unwrap([w.value for w in c.words], [])] if a] for p in pipes for c in p]
        self.transforms = self.transforms or any(n and n[0] in TRANSFORMERS for n in names)
        for pipe in pipes:
            cmds = [c for c in pipe if c.words or c.stdin or c.reads or c.writes]
            for k, c in enumerate(cmds):
                self.fed = [t for p in cmds[:k] for t in [b for b, _ in p.stdin] + [w.value for w in p.words]]
                self.later = [base(p.words[0].value) for p in cmds[k + 1 :] if p.words]
                if k and c.words and base(c.words[0].value) == "xargs" and self.feeds_cred_paths(c, cmds[:k]):
                    return GH_CRED
                if why := self.simple(c, depth):
                    return why
                if (lang := self.stdin_lang(c)) and k:  # earlier stages of the pipeline feed this interpreter
                    if any(base(w.value) in TRANSFORMERS for prev in cmds[:k] for w in prev.words[:1]):
                        return OPAQUE
                    for prev in cmds[:k]:
                        for chunk in [b for b, _ in prev.stdin] + [w.value for w in prev.words[1:]]:
                            if why := self.code(chunk, lang, depth):
                                return why
        return None

    def code(self, chunk: str, lang: str, depth: int) -> str | None:
        if lang == "sh":
            return self.text(chunk, depth + 1)
        return self.python(chunk, depth)

    def python(self, src: str, depth: int) -> str | None:
        """Python source fed to an interpreter: its string literals may be shell commands or paths."""
        literals = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", src)
        strings = [a or b for a, b in literals]
        for group in re.findall(r"\[([^\]]*)\]", src):  # list-form argv: ["pip", "install", "x"]
            parts = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", group)
            strings.append(" ".join(a or b for a, b in parts))
        if why := self.code_reaches(src, "py"):
            return why
        self.in_python += 1
        try:
            for s in strings:
                if is_env(Word(s)):
                    return ENV
                if is_secrets(Word(s)):
                    return KEYS_FILE
                if s in TOKEN_NAMES or cred_path(s, self.cwd):  # os.environ["GH_TOKEN"], open(".../hosts.yml")
                    return GH_CRED
                if why := self.text(s, depth + 1):
                    return why
            return None
        finally:
            self.in_python -= 1

    def stdin_lang(self, c: Simple) -> str | None:
        """'sh' or 'py' when this command runs code read from stdin."""
        argv = unwrap([w.value for w in c.words], [])
        if not argv:
            return None
        name, args = base(argv[0]), argv[1:]
        if name in SHELLS:
            i = 0
            while i < len(args) and args[i].startswith(("-", "+")):
                if "c" in args[i].lstrip("-+") and not args[i].startswith("--"):
                    return None
                i += 2 if args[i] in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file") else 1
            return "sh" if i == len(args) or args[i] == "-s" else None
        if PY.match(name):
            rest = [a for a in args if not a.startswith("-")]
            return "py" if (not rest or args[-1] == "-") and "-c" not in args else None
        if name in ("source", ".") and args and args[0] in ("/dev/stdin", "-"):
            return "sh"
        return None

    def simple(self, c: Simple, depth: int) -> str | None:
        fed, self.fed = self.fed, []
        if self.vars and any("$" in w.value for w in c.words):  # b=main; git push origin $b
            c = self.expanded(c)
        assigns: list[str] = []
        argv = unwrap([w.value for w in c.words], assigns)
        name = base(argv[0]) if argv else ""
        text_opts = MESSAGE_OPTS if name in ("git", "gh") else set()  # grep -b is a flag, not a message
        paths = [w for k, w in enumerate(c.words) if not (k and c.words[k - 1].value in text_opts)]
        if name in GREPS and (k := grep_pattern(argv)) is not None:
            pattern = len(c.words) - len(argv) + k  # unwrap only strips words from the front
            paths = [w for j, w in enumerate(c.words) if j != pattern]
        env_ok = name in ENV_OK_CMDS or (name == "git" and "check-ignore" in argv[1:4])
        in_py = self.in_python > 0
        if (any(is_env(w, in_py) for w in paths) and not env_ok) or any(is_env(Word(r), in_py) for r in c.reads):
            return ENV
        if (any(is_secrets(w) for w in paths) and not env_ok) or any(is_secrets(Word(r)) for r in c.reads):
            return KEYS_FILE
        if why := self.gh_cred(c, argv, paths, name in CRED_OK) or self.reaches(c, argv, fed):
            return why
        exported = argv[1:] if name in ("export", "declare", "typeset", "local", "readonly") else []
        if any(HOOK_VAR.match(a) or "hookspath" in a.lower() for a in exported + (assigns if not argv else [])):
            return HOOKS
        raw = [w.value for w in c.words]
        for k, v in enumerate(raw):
            if base(v) in C_RUNNERS:
                for j in range(k + 1, len(raw)):
                    code = raw[j].split("=", 1)[1] if raw[j].startswith("--command=") else None
                    if code is None and raw[j] in ("-c", "--command") and j + 1 < len(raw):
                        code = raw[j + 1]
                    if code is not None and (why := self.text(code, depth + 1)):
                        return why
        if lang := self.stdin_lang(c):
            for body, _ in c.stdin:
                if why := self.code(body, lang, depth):
                    return why
        if why := self.owner_setting(argv, c.writes, paths):
            return why
        if self.read_only and (why := read_only(argv, c.writes, self.read_only, tuple(assigns))):
            return why
        if not argv:
            return None
        args = argv[1:]
        if name in ("cd", "pushd") and args:
            self.cwd = os.path.join(self.cwd, os.path.expanduser(args[-1]))
        if name == "trap" and args:
            code = next((a for a in args if not a.startswith("-")), "")
            if why := self.text(code, depth + 1):
                return why
        if name in SHELLS:
            for i, a in enumerate(args):
                if a.startswith("-") and not a.startswith("--") and "c" in a and i + 1 < len(args):
                    return self.text(args[i + 1], depth + 1)
        if name == "eval":
            return self.text(" ".join(args), depth + 1)
        if name == "watch":
            return self.text(" ".join(a for a in args if not a.startswith("-")), depth + 1)
        if PY.match(name) and "-c" in args and args.index("-c") + 1 < len(args):
            return self.python(args[args.index("-c") + 1], depth)
        if name == "find":
            for i, a in enumerate(args):
                if a in ("-exec", "-execdir", "-ok", "-okdir"):
                    inner = []
                    for b in args[i + 1 :]:
                        if b in (";", "+"):
                            break
                        inner.append(Word(b))
                    if why := self.simple(Simple(inner), depth + 1):
                        return why
        if re.fullmatch(r"pip[0-9.]*", name) and {"install", "uninstall"} & set(args):
            return PIP
        if name == "uv":
            rest = [a for a in args if not a.startswith("-")]
            if rest[:1] == ["pip"] and {"install", "uninstall", "sync"} & set(rest):
                return PIP
        if name in FETCHERS and f"{LIT_RAW}=1" not in assigns and not self.lit_raw:
            for a in args:
                a = a.split("=", 1)[1] if a.startswith("--") and "=" in a else a  # --url=<url>
                if LIT_API.match(a) or ((m := LIT_VAR.match(a)) and m.group(1) in self.lit_vars):
                    return LIT
        if name == "pkill" and any(a == "--full" or (re.match(r"-[^-]", a) and "f" in a) for a in args):
            return PKILL
        if name == "gh":
            return self.gh(args, [b for b, _ in c.stdin] + fed)
        if name in ("dev", "devkit.cli") and args[:1] in (["board"], ["pr"]):
            return self.forged(args + [b for b, _ in c.stdin] + fed)
        if name == "git":
            return self.git(args, assigns)
        if name == "rm":
            return self.rm(args)
        return None

    def owner_setting(self, argv: list[str], writes: list[str], paths: list[Word]) -> str | None:
        """The denial when the command sets one of OWNER_SETTINGS: `dev quota-cap <pct>|--clear` (or gate-slots), or
        touches its file other than to read it (a `cd` earlier in the command counts, so `cd .git/devkit && echo 99 >
        quota_cap` too)."""
        name, args = (base(argv[0]), argv[1:]) if argv else ("", [])
        for command, path, why in OWNER_SETTINGS:
            if name in ("dev", "devkit.cli") and args[:1] == [command] and not set(args[1:]) <= {"-h", "--help"}:
                return why

            def owned(v: str, path: str = path) -> bool:
                return path in v or os.path.normpath(os.path.join(self.cwd, v)).endswith("/" + path)

            if any(owned(w) for w in writes):
                return why
            if name not in CAP_READERS and any(owned(w.value) for w in paths):
                return why
        return None

    def git(self, args: list[str], assigns: list[str]) -> str | None:
        configs, where, i = [*assigns], self.cwd, 0
        while i < len(args) and args[i].startswith("-"):
            a = args[i]
            if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env") and i + 1 < len(args):
                if a == "-C":
                    where = os.path.join(where, args[i + 1])
                configs.append(args[i + 1])
                i += 2
            else:
                configs.append(a)
                i += 1
        if any("hookspath" in x.lower() for x in configs) or any(HOOK_VAR.match(x) for x in assigns):
            return HOOKS
        if i >= len(args):
            return None
        sub, rest = args[i], args[i + 1 :]
        if sub == "credential" or sub.startswith("credential-"):  # `git credential fill` prints the stored token
            return GH_CRED
        no_index = sub == "grep" and "--no-index" in rest  # searches any directory, hidden files included
        if no_index and any(holds_cred(a, where) for a in rest if not a.startswith("-")):
            return GH_CRED
        if sub == "config" and any("hookspath" in a.lower() for a in rest) and not config_reads_only(rest):
            return HOOKS
        if sub != "commit" and any(long_opt(a, "--no-verify", 9) for a in rest):  # commit: values are data (#605)
            return HOOKS
        if sub == "commit" and commit_skips_hooks(rest):
            return HOOKS
        if sub == "rebase":  # -x / --exec run a shell command after each commit
            for k, a in enumerate(rest):
                code = a.split("=", 1)[1] if a.startswith("--exec=") else a[2:] if a.startswith("-x") else None
                if code == "" and k + 1 < len(rest):
                    code = rest[k + 1]
                if code is None and a == "--exec" and k + 1 < len(rest):
                    code = rest[k + 1]
                if code and (why := self.text(code, 1)):
                    return why
        if sub == "push":
            return self.push(rest, where)
        return destructive_git(sub, rest, where)

    def rm(self, args: list[str]) -> str | None:
        shorts, longs, pos = parse_opts(args)
        if not ({"r", "R"} & shorts or any(long_opt(o, "--recursive", 3) for o in longs)):
            return None
        import tempfile

        root = worktree_root(self.start)
        temp = os.path.realpath(tempfile.gettempdir())
        for t in pos:
            path = os.path.expandvars(os.path.expanduser(t))
            if "$" in path:  # a variable the shell sets: the target cannot be checked here
                continue
            full = os.path.realpath(os.path.join(self.cwd, path))
            if root not in ("/", os.path.realpath(os.path.expanduser("~"))) and within(full, root):
                if full != root and ".git" not in os.path.relpath(full, root).split(os.sep):
                    continue
            elif within(full, temp) and not within(root, temp):
                continue
            return RM
        return None

    def push(self, rest: list[str], where: str) -> str | None:
        pos, skip, tags_only = [], False, False
        for k, a in enumerate(rest):
            if skip:
                skip = False
            elif a == "--":
                pos += rest[k + 1 :]
                break
            elif a.startswith("--"):
                opt = a.split("=", 1)[0]
                if opt == "--force":
                    return FORCE
                if long_opt(opt, "--delete", 4) or opt == "--prune":
                    return REF_DELETE
                if long_opt(opt, "--mirror", 4) or long_opt(opt, "--all", 4) or long_opt(opt, "--branches", 4):
                    return MAIN
                tags_only = tags_only or opt == "--tags"
                skip = "=" not in a and opt in ("--repo", "--push-option", "--receive-pack", "--exec")
            elif a.startswith("-") and len(a) > 1:
                if "f" in a.split("o", 1)[0]:
                    return FORCE
                if "d" in a.split("o", 1)[0]:
                    return REF_DELETE
                skip = a.endswith("o")
            else:
                pos.append(a)
        refspecs = pos[1:]
        for r in refspecs:
            if r.startswith("+"):
                return FORCE
            if r.startswith(":"):  # an empty source deletes the destination
                return REF_DELETE
            dst = r.split(":")[-1].removeprefix("refs/").removeprefix("heads/")
            if dst in ("HEAD", "@"):
                dst = current_branch(where) or ""
            if dst in ("main", "master") or "*" in dst:  # a wildcard refspec can land on main
                return MAIN
        if not refspecs and not tags_only and current_branch(where) in ("main", "master"):
            return MAIN
        return None


def grep_pattern(argv: list[str]) -> int | None:
    """Index of grep's pattern argument (text, not a path), or None when -e / -f give the pattern instead."""
    k = 1
    while k < len(argv):
        a = argv[k]
        if a in ("-e", "-f", "--regexp", "--file") or a.startswith(("--regexp=", "--file=")):
            return None
        if a == "--":
            return k + 1 if k + 1 < len(argv) else None
        if not a.startswith("-") or a == "-":
            return k
        k += 2 if a in GREP_VALUE else 1
    return None


COMMIT_VALUE = {"--message", "--file", "--reuse-message", "--reedit-message", "--template", "--author", "--date"}
COMMIT_VALUE |= {"--fixup", "--squash", "--trailer", "--cleanup", "--pathspec-from-file"}


def config_reads_only(rest: list[str]) -> bool:
    """True when `git config <rest>` can only read: a get/list action, or a bare name. Unknown flags count as writes."""
    names, flags, i = [], set(), 0
    while i < len(rest):
        a = rest[i]
        opt = a.split("=", 1)[0]
        if opt in CONFIG_VALUE_OPTS:
            i += 1 if "=" in a else 2
            continue
        if a.startswith("-"):
            if opt not in CONFIG_READ_FLAGS | CONFIG_READ_ACTIONS:
                return False
            flags.add(opt)
        else:
            names.append(a)
        i += 1
    if names[:1] in (["get"], ["list"]):
        return True
    if names[:1] and names[0] in ("set", "unset", "rename-section", "remove-section", "edit"):
        return False
    return bool(flags & CONFIG_READ_ACTIONS) or len(names) == 1


def commit_skips_hooks(rest: list[str]) -> bool:
    """git commit -n is --no-verify; -m, -F, -c, -C and -t take a value, as the rest of the cluster or the next arg,
    so `-m --no-veri` is a message, not the option."""
    skip = False
    for a in rest:
        if skip:
            skip = False
            continue
        if a == "--":
            break
        if long_opt(a, "--no-verify", 9):
            return True
        if a in COMMIT_VALUE:  # --message -n: "-n" is the message
            skip = True
        elif a.startswith("-") and not a.startswith("--"):
            for j, ch in enumerate(a[1:]):
                if ch == "n":
                    return True
                if ch in "mFcCt":
                    skip = j == len(a) - 2
                    break
    return False


def parse_opts(args: list[str], valued: str = "") -> tuple[set[str], set[str], list[str]]:
    """Split args into short flag letters, long option names and positionals (all of them after `--`).
    `valued`: short options whose value is the rest of the cluster or the next word."""
    shorts: set[str] = set()
    longs: set[str] = set()
    pos: list[str] = []
    skip = False
    for k, a in enumerate(args):
        if skip:
            skip = False
        elif a == "--":
            pos += args[k + 1 :]
            break
        elif a.startswith("--"):
            longs.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                shorts.add(ch)
                if ch in valued:
                    skip = j == len(a) - 1
                    break
        else:
            pos.append(a)
    return shorts, longs, pos


def destructive_git(sub: str, rest: list[str], where: str) -> str | None:
    """The git subcommands that throw away uncommitted work, stashes, worktrees or unmerged branches."""
    shorts, longs, pos = parse_opts(rest, {"checkout": "bB", "switch": "cC", "restore": "s", "clean": "e"}.get(sub, ""))

    def has(letter: str, name: str, shortest: int) -> bool:
        return letter in shorts or any(long_opt(o, name, shortest) for o in longs)

    def whole_tree() -> bool:
        return any(p in WHOLE_TREE or os.path.isdir(os.path.join(where, p)) for p in pos)

    if sub == "reset" and any(long_opt(o, "--hard", 4) or long_opt(o, "--merge", 4) for o in longs):
        return RESET
    if sub == "checkout" and (has("f", "--force", 4) or whole_tree()):
        return DISCARD
    if sub == "switch" and (has("f", "--force", 4) or any(long_opt(o, "--discard-changes", 3) for o in longs)):
        return DISCARD
    if sub == "restore" and (has("W", "--worktree", 3) or not has("S", "--staged", 4)) and whole_tree():
        return DISCARD
    if sub == "clean" and has("f", "--force", 3) and not has("n", "--dry-run", 3):
        return CLEAN
    if sub == "stash" and pos[:1] in (["drop"], ["clear"]):
        return STASH
    if sub == "worktree" and pos[:1] == ["remove"] and has("f", "--force", 3):
        return WORKTREE
    if sub == "branch" and ("D" in shorts or (has("d", "--delete", 4) and has("f", "--force", 3))):
        return BRANCH_D
    return None


def worktree_root(cwd: str) -> str:
    """The top of the worktree holding cwd, or cwd itself outside a repository."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return os.path.realpath(out or cwd)


def current_branch(where: str) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", where, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


# Read-only agents (the PreToolUse input's `agent_type`, plugin prefix dropped), each with the one `dev` report it may
# run besides `dev board|code`: the consolidation review (#113) and the suite audit (#114) propose, builders execute, so
# a stray write can never delete progress or a test. Allowlist, not denylist: unknown means no. The suite-auditor gets
# `suite-audit status` and `report --quick` only: a full report runs the suite (also in the working tree), and
# `--file` edits and closes issues; both stay with the lead. `dev` refuses abbreviated options, so `--fi` is no way in.
READ_ONLY_AGENTS = {"architect": "consolidation", "suite-auditor": "suite-audit"}
READ_ONLY = (
    "this agent is read-only: it reads, measures and files issues. Allowed: read commands (cat, grep, ls, "
    "sed -n, ...), `git log|show|diff|status`, `gh issue view|list|create|comment`, `gh pr view|list|diff`, GET-only "
    "`gh api`, `uv run dev board|code`, and its own report (architect: `dev consolidation`; suite-auditor: "
    "`dev suite-audit status` or `dev suite-audit report --quick`). File an issue for a builder instead."
)


def quick_audit(args: list[str]) -> bool:
    """`suite-audit status`, or `report --quick` with no --file (nor a prefix of it): reruns nothing, files nothing."""
    if any(len(a) > 2 and "--file".startswith(a.split("=", 1)[0]) for a in args):
        return False
    return args[:1] == ["status"] or (args[:1] == ["report"] and "--quick" in args)


READ_CMDS = {"cat", "head", "tail", "wc", "ls", "grep", "egrep", "fgrep", "rg", "sort", "uniq", "cut", "tr", "jq"}
READ_CMDS |= {"echo", "printf", "pwd", "stat", "file", "diff", "cmp", "tree", "du", "df", "basename", "dirname"}
READ_CMDS |= {"realpath", "readlink", "test", "[", "[[", "true", "false", "column", "nl", "date", "which", "cd"}
READ_GIT = {"log", "show", "diff", "status", "blame", "grep", "ls-files", "ls-tree", "rev-parse", "cat-file"}
READ_GIT_GLOBAL = {"-C", "--no-pager", "-P", "--git-dir", "--work-tree"}  # no -c: core.fsmonitor, alias.x=!cmd run code
READ_GH = {"issue": {"view", "list", "create", "comment", "status"}, "pr": {"view", "list", "diff", "checks", "status"}}
READ_GH |= {"search": {"issues", "prs", "code", "repos", "commits"}}
READ_DEV = {"board", "code", "--help", "-h"}
# Options of allowed read commands that run a program or write a file (#145): (short letters that do, short letters
# that take a value and so end a cluster, long options; git-style prefixes of 3+ characters count).
RISKY_OPTS = {
    "sort": ("o", "ktST", {"--output", "--compress-program"}),
    "tree": ("o", "LPIHT", set()),
    "rg": ("", "", {"--pre"}),
    "file": ("C", "mPeFf", {"--compile"}),
    "date": ("s", "dfIr", {"--set"}),
    "git grep": ("O", "efABCm", {"--open-files-in-pager"}),
}
# Environment variables a read-only agent may set: the rest (GIT_CONFIG_*, GIT_EXTERNAL_DIFF, LD_PRELOAD, ...) can run
# code. A pager may only be turned off.
SAFE_VARS = {"NO_COLOR", "TERM", "COLUMNS", "LINES", "LANG", "LC_ALL", "TZ"}
SAFE_PAGERS = {"GIT_PAGER=", "GIT_PAGER=cat", "PAGER=", "PAGER=cat"}
SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "-"}


def risky(name: str, args: list[str]) -> bool:
    """An option in RISKY_OPTS, or a second file operand for uniq (its output file)."""
    if name == "uniq":
        valued, files, i = ("-f", "-s", "-w"), 0, 0
        while i < len(args):
            files += not args[i].startswith("-") or args[i] == "-"
            i += 2 if args[i] in valued else 1
        return files > 1
    letters, valued, longs = RISKY_OPTS.get(name, ("", "", set()))
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            if any(long_opt(a, opt, 3) for opt in longs):
                return True
        elif a.startswith("-"):
            for ch in a[1:]:
                if ch in letters:
                    return True
                if ch in valued:
                    break
    return False


def read_only(argv: list[str], writes: list[str], report: str = "", assigns: tuple[str, ...] = ()) -> str | None:
    """READ_ONLY unless the command only reads, files issues through `dev board` / `gh`, or runs the agent's own
    `dev <report>` (READ_ONLY_AGENTS)."""
    if any(not (w.isdigit() or w in SINKS) for w in writes):
        return READ_ONLY
    if any(a.split("=", 1)[0] not in SAFE_VARS and a not in SAFE_PAGERS for a in assigns):
        return READ_ONLY
    if not argv:
        return None
    name, args = base(argv[0]), argv[1:]
    if name == "sed":
        if any(a.startswith(("-i", "--in-place")) for a in args):
            return READ_ONLY
        scripts = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-e"] or [
            next((a for a in args if not a.startswith("-")), "")
        ]
        return None if all(re.fullmatch(r"[0-9,$;p ]*", s) for s in scripts) else READ_ONLY
    if name == "find":
        bad = {"-delete", "-fprint", "-fprint0", "-fprintf", "-fls", "-ok", "-okdir"}
        return READ_ONLY if bad & set(args) else None  # -exec's command is checked on its own
    if name in READ_CMDS:
        return READ_ONLY if risky(name, args) else None
    if name == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] not in READ_GIT_GLOBAL:
                return READ_ONLY
            i += 2 if args[i] in ("-C", "--git-dir", "--work-tree") else 1
        rest = args[i:]
        ok = rest and rest[0] in READ_GIT and not any(a.startswith(("--output", "-o")) for a in rest[1:])
        ok = ok and not (rest[0] == "grep" and risky("git grep", rest[1:]))
        return None if ok else READ_ONLY
    if name == "gh":
        if args[:1] == ["api"]:
            write = {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
            return READ_ONLY if any(a.split("=", 1)[0] in write for a in args) else None
        return None if len(args) > 1 and args[1] in READ_GH.get(args[0], ()) else READ_ONLY
    if name == "dev" and args[:1] and args[0] in READ_DEV:
        return None
    if name == "dev" and args[:1] == [report] and (report != "suite-audit" or quick_audit(args[1:])):
        return None
    return READ_ONLY


FILE_RULES = {"env": ENV, "keys": KEYS_FILE, "cred": GH_CRED, "above": GREP_ABOVE}  # file_tool_rule -> message


def file_tool_rule(data: dict, cwd: str) -> str:
    """Which FILE_RULES refusal a Read or Grep call gets, or "": by its path, its Grep glob, or a Grep of a folder
    above a credential store."""
    given = data.get("tool_input")
    inp: dict = given if isinstance(given, dict) else {}
    path = inp.get("file_path") or inp.get("path") or ""
    path = path if isinstance(path, str) else ""
    if path:
        w = Word(path, glob=any(ch in path for ch in "*?["))
        if is_env(w):
            return "env"
        if is_secrets(w):
            return "keys"
        if cred_path(path, cwd):
            return "cred"
    glob = inp.get("glob")
    hint = glob if isinstance(glob, str) else ""
    if cred_path(hint, cwd, glob=True) or "hosts.yml" in hint or ".ssh/" in hint:
        return "cred"
    if data.get("tool_name") == "Grep" and holds_cred(path or cwd, cwd):
        return "above"  # Grep searches hidden files: a folder above the store reaches it
    return ""


def main(raw: str | None = None) -> int:
    """`raw`: the hook input file_guard.py already read from stdin."""
    try:
        data = json.loads(raw) if raw is not None else json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
        url = (data.get("tool_input") or {}).get("url", "") if data.get("tool_name") == "WebFetch" else ""
    except (json.JSONDecodeError, AttributeError):
        return 0
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    if isinstance(url, str) and LIT_API.match(url.strip()) and owner_env(LIT_RAW, cwd) != "1":
        print(f"Blocked by project rule: {LIT}", file=sys.stderr)
        return 2
    if data.get("tool_name") in ("Read", "Grep"):  # #601: the file tools reach the same secrets
        rule = file_tool_rule(data, cwd)
        if rule:
            print("Blocked by project rule: " + FILE_RULES[rule], file=sys.stderr)
            return 2
        return 0
    if not isinstance(cmd, str):
        return 0
    agent = data.get("agent_type") if isinstance(data.get("agent_type"), str) else ""  # set inside a subagent
    try:
        kind = agent.rsplit(":", 1)[-1]
        why = Checker(cwd, read_only=READ_ONLY_AGENTS.get(kind, ""), agent=kind if agent else None).text(cmd)
    except RecursionError:  # hundreds of nested substitutions: nobody writes that by hand
        why = DEEP
    if why:
        print(f"Blocked by project rule: {why}", file=sys.stderr)
        return 2
    if data.get("tool_name", "Bash") == "Bash" and (bounded := bound_waits(cmd, cwd)):
        new, note = bounded
        inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        out = {"hookEventName": "PreToolUse", "updatedInput": {**inp, "command": new}, "additionalContext": note}
        print(json.dumps({"hookSpecificOutput": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
