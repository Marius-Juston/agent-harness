#!/usr/bin/env python3
"""PreToolUse(Bash): refuse commands that break project rules, each with the fix in the message (exit 2).

The command is tokenized the way bash reads it: quoted strings and heredoc bodies are data (commit messages, issue
text, file contents), while every simple command is checked, including those inside $(...), `...`, <(...),
`bash -c`, `eval`, `find -exec`, wrappers (sudo, env, time, timeout, xargs, uv run, python -m) and text fed on stdin
to an interpreter (bash, sh, python) by a heredoc, a here-string or a pipe. Stdlib only. Settings deny rules are
prefix-only and this hook is best effort; server-side branch protection is the only complete guard for main.
"""

import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

PIP = "Python is managed with uv only: `uv add <pkg>` (or `uv add --dev`), `uv run ...`. Never pip."
PKILL = '`pkill -f` matches the shell running it. Collect PIDs with `pgrep -f "[p]attern"` in one call, kill them in another.'
ENV = "Never read secrets: .env stays unread. Commands that need it load it themselves."
SECRETS = (
    "Never read the research API keys file (~/.config/api-keys.env or $DEVKIT_SECRETS_FILE): `uv run dev lit` "
    "loads it itself, and `uv run dev doctor` says whether each key is found, never its value."
)
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
    "branch, check `gh pr view <branch>` first and leave the deletion to `uv run dev integrate`."
)
WHOLE_TREE = {".", "./", ":", ":/", ":/*", "*"}  # pathspecs that name every file
HOOK_VAR = re.compile(r"^(SKIP|PRE_COMMIT\w*|GIT_CONFIG\w*)(=|$)")
C_RUNNERS = {"flock", "su", "runuser", "script"}  # their -c / --command value is a shell command
GREPS = {"grep", "egrep", "fgrep", "rg"}
GREP_VALUE = {"-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--context", "-g", "--glob", "-t", "--type"}

OPS = sorted(
    [
        "&&",
        "||",
        ";;&",
        ";;",
        ";&",
        ";",
        "|&",
        "|",
        "&>>",
        "&>",
        "&",
        "<<<",
        "<<-",
        "<<",
        "<>",
        "<&",
        "<",
        ">>",
        ">&",
        ">|",
        ">",
    ],
    key=len,
    reverse=True,
)
BREAK = set(" \t\n;&|<>()")
ASSIGN = re.compile(r"^[A-Za-z_]\w*\+?=")
PY = re.compile(r"^python[0-9.]*$")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "mksh", "busybox"}
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!", "{", "}", "time", "case", "esac"}
ENV_SAFE = {".env.example", ".env.sample", ".env.template", ".env.dist"}
ENV_OK_CMDS = {"ls", "stat", "test", "[", "[[", "echo", "printf", "touch", "rm"}
MESSAGE_OPTS = {"-m", "--message", "--title", "--body", "-b", "-t", "--subject"}  # their values are text, not paths
MAX_DEPTH = 8
# `git config` options that only read; any other flag (--add, --unset, -e, ...) makes the call a possible write
CONFIG_READ_ACTIONS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list"}
CONFIG_READ_FLAGS = {"--global", "--system", "--local", "--worktree", "--show-origin", "--show-scope", "--includes"}
CONFIG_READ_FLAGS |= {"--no-includes", "-z", "--null", "--name-only", "--all", "--regexp", "--fixed-value"}
CONFIG_READ_FLAGS |= {"--bool", "--int", "--bool-or-int", "--path", "--expiry-date", "--no-type"}
CONFIG_VALUE_OPTS = {"-f", "--file", "--blob", "--type", "--default", "--url", "--value"}  # their value is not a name


@dataclass
class Word:
    value: str
    glob: bool = False  # an unquoted * ? or [ that bash would expand
    quoted: bool = False
    brace: bool = False  # an unquoted {, so maybe a brace expansion


@dataclass
class Simple:
    words: list[Word] = field(default_factory=list)
    stdin: list[tuple[str, bool]] = field(default_factory=list)  # heredoc / here-string text, and "is a heredoc"
    reads: list[str] = field(default_factory=list)  # input redirection targets
    writes: list[str] = field(default_factory=list)  # output redirection targets


class Lexer:
    """A small bash lexer: pipelines of simple commands; substitutions become pipelines of their own."""

    def __init__(self, s: str, out: list[list[Simple]]):
        self.s, self.n, self.out = s, len(s), out
        self.pipe: list[Simple] = [Simple()]
        self.out.append(self.pipe)
        self.pending: list[tuple[str, bool, bool, Simple]] = []

    def end_pipeline(self) -> None:
        if any(c.words or c.stdin or c.reads or c.writes for c in self.pipe):
            self.pipe = [Simple()]
            self.out.append(self.pipe)

    def sub(self, i: int, stop: str) -> int:
        """Lex a substitution starting at i into its own pipelines; return the index after its end."""
        return Lexer(self.s, self.out).run(i, stop)

    def run(self, i: int = 0, stop: str = "") -> int:
        s, depth = self.s, 0
        while i < self.n:
            c = s[i]
            if c in " \t":
                i += 1
            elif c == "\\" and s.startswith("\n", i + 1):
                i += 2
            elif c == "\n":
                i = self.heredocs(i + 1)
                self.end_pipeline()
            elif c == "#":
                j = s.find("\n", i)
                i = self.n if j < 0 else j
            elif c == ")":
                i += 1
                if stop == ")" and depth == 0:
                    return i
                depth = max(depth - 1, 0)
                self.end_pipeline()
            elif c == "(":
                depth += 1
                i += 1
                self.end_pipeline()
            elif c in "<>" and s.startswith("(", i + 1):
                i = self.word_into(i)
            else:
                op = next((o for o in OPS if s.startswith(o, i)), None)
                i = self.word_into(i) if op is None else self.operator(op, i + len(op))
        return i

    def word_into(self, i: int) -> int:
        w, i = self.word(i)
        cur = self.pipe[-1]
        if w.value.isdigit() and not w.quoted and i < self.n and self.s[i] in "<>":
            return i  # a file descriptor number in front of a redirection
        if w.brace:  # p{i,}p {pip,x}: check every word bash would produce
            cur.words += [Word(v, w.glob, w.quoted) for v in expand_braces(w.value)]
        else:
            cur.words.append(w)
        return i

    def operator(self, op: str, i: int) -> int:
        if op in ("|", "|&"):
            self.pipe.append(Simple())
            return i
        if op in ("&&", "||", ";;&", ";;", ";&", ";", "&"):
            self.end_pipeline()
            return i
        while i < self.n and self.s[i] in " \t":
            i += 1
        target, i = self.word(i)
        cur = self.pipe[-1]
        if op in ("<<", "<<-"):
            self.pending.append((target.value, op == "<<-", target.quoted, cur))
        elif op == "<<<":
            cur.stdin.append((target.value, False))
        elif op in ("<", "<>", "<&"):
            cur.reads.append(target.value)
        if op in (">", ">>", ">|", ">&", "&>", "&>>", "<>"):
            cur.writes.append(target.value)
        return i

    def heredocs(self, i: int) -> int:
        """At a newline: read the bodies of the heredocs opened on the line just ended."""
        for delim, _tabs, quoted, owner in self.pending:
            lines, s = [], self.s
            while i < self.n:
                j = s.find("\n", i)
                line = s[i : self.n if j < 0 else j]
                i = self.n if j < 0 else j + 1
                if line.strip() == delim:  # looser than bash, so a body never swallows the commands after it
                    break
                lines.append(line)
            body = "\n".join(lines)
            owner.stdin.append((body, True))
            if not quoted:
                scan_substitutions(body, self.out)  # an unquoted heredoc still runs $(...) and `...`
        self.pending = []
        return i

    def word(self, i: int) -> tuple[Word, int]:
        s, buf, glob, quoted, brace = self.s, [], False, False, False
        while i < self.n:
            c = s[i]
            if c in BREAK and not (c in "<>" and s.startswith("(", i + 1) and not buf):
                break
            if c in "<>":  # <(...) or >(...) process substitution
                i = self.sub(i + 2, ")")
                buf.append("\0")
            elif c == "\\":
                if s.startswith("\n", i + 1):
                    i += 2
                    continue
                buf.append(s[i + 1 : i + 2])
                quoted, i = True, i + 2
            elif c == "'":
                j = s.find("'", i + 1)
                j = self.n if j < 0 else j
                buf.append(s[i + 1 : j])
                quoted, i = True, j + 1
            elif c == "$" and s.startswith("'", i + 1):
                j = i + 2
                while j < self.n and s[j] != "'":
                    j += 2 if s[j] == "\\" else 1
                buf.append(ansi_c(s[i + 2 : j]))
                quoted, i = True, j + 1
            elif c == '"' or (c == "$" and s.startswith('"', i + 1)):
                i = i + 1 if c == '"' else i + 2
                while i < self.n and s[i] != '"':
                    if s[i] == "\\" and i + 1 < self.n:
                        buf.append(s[i + 1] if s[i + 1] in '$`"\\\n' else s[i : i + 2])
                        i += 2
                    elif s.startswith("$(", i):
                        i = self.sub(i + 2, ")")
                        buf.append("\0")
                    elif s[i] == "`":
                        i = self.backtick(i)
                        buf.append("\0")
                    else:
                        buf.append(s[i])
                        i += 1
                quoted, i = True, i + 1
            elif s.startswith("$(", i):
                i = self.sub(i + 2, ")")
                buf.append("\0")
            elif c == "`":
                i = self.backtick(i)
                buf.append("\0")
            elif s.startswith("${", i):
                j = s.find("}", i)
                j = self.n if j < 0 else j + 1
                buf.append(s[i:j])
                i = j
            else:
                glob = glob or c in "*?["
                brace = brace or c == "{"
                buf.append(c)
                i += 1
        return Word("".join(buf), glob, quoted, brace), i

    def backtick(self, i: int) -> int:
        j = i + 1
        while j < self.n and self.s[j] != "`":
            j += 2 if self.s[j] == "\\" else 1
        inner = re.sub(r"\\([`$\\])", r"\1", self.s[i + 1 : j])
        Lexer(inner, self.out).run()
        return j + 1


def expand_braces(word: str, cap: int = 256) -> list[str]:
    """Bash brace expansion of {a,b,...} (nested too; no {1..3} ranges), at most cap words."""
    depth, start, commas = 0, -1, []
    for i, ch in enumerate(word):
        if ch == "{":
            depth += 1
            if depth == 1:
                start, commas = i, []
        elif ch == "," and depth == 1:
            commas.append(i)
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and commas:
                cuts = [start, *commas, i]
                pre, post = word[:start], word[i + 1 :]
                out: list[str] = []
                for a, b in itertools.pairwise(cuts):
                    for tail in expand_braces(post, cap):
                        out += expand_braces(pre + word[a + 1 : b] + tail, cap)
                        if len(out) >= cap:
                            return out[:cap]
                return out
    return [word]


ANSI_C = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "v": "\v"}


def ansi_c(s: str) -> str:
    """Decode the body of a $'...' string: \\n, \\t, \\xHH, \\NNN (octal), \\uHHHH; anything else stands for itself."""

    def one(m: re.Match) -> str:
        e = m.group(1)
        if e[0] in "xuU":
            return chr(min(int(e[1:], 16), 0x10FFFF)) if len(e) > 1 else e
        if e[0] in "01234567":
            return chr(int(e, 8))
        return ANSI_C.get(e, e)

    return re.sub(r"\\(x[0-9a-fA-F]{0,2}|u[0-9a-fA-F]{0,4}|U[0-9a-fA-F]{0,8}|[0-7]{1,3}|.)", one, s, flags=re.S)


def scan_substitutions(text: str, out: list[list[Simple]]) -> None:
    lx, i = Lexer(text, out), 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text.startswith("$(", i):
            i = lx.sub(i + 2, ")")
        elif text[i] == "`":
            i = lx.backtick(i)
        else:
            i += 1


def lex(text: str) -> list[list[Simple]]:
    out: list[list[Simple]] = []
    lx = Lexer(text, out)
    lx.run()
    lx.heredocs(lx.n)
    return out


def base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def long_opt(arg: str, name: str, shortest: int) -> bool:
    """git accepts any unambiguous prefix of a long option."""
    arg = arg.split("=", 1)[0]
    return len(arg) >= shortest and name.startswith(arg)


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


SKIP_VALUE = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-t"},
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-a", "-E", "-s", "-i", "--max-args", "--max-procs", "--delimiter"},
    "stdbuf": {"-i", "-o", "-e"},
    "flock": {"-w", "-E", "--timeout"},
}
WRAPPERS = {"sudo", "doas", "nice", "ionice", "timeout", "xargs", "stdbuf", "nohup", "exec", "command", "builtin"}
WRAPPERS |= {"setsid", "chronic", "unbuffer", "flock", "time"}
UV_RUN_VALUE = {"--with", "-w", "--python", "-p", "--package", "--env-file", "--directory", "--project", "--extra"}
UV_RUN_VALUE |= {"--group", "--index", "--with-requirements", "--with-editable", "--only-group", "--no-group"}


def unwrap(argv: list[str], assigns: list[str]) -> list[str]:
    """Strip assignments and wrappers (sudo, env, time, timeout, xargs, uv run, python -m ...) to the real command."""
    argv = list(argv)
    while argv:
        while argv and ASSIGN.match(argv[0]):
            assigns.append(argv.pop(0))
        if not argv:
            break
        name = base(argv[0])
        if name in KEYWORDS:
            argv.pop(0)
        elif name == "env":
            argv.pop(0)
            while argv and (argv[0].startswith("-") or ASSIGN.match(argv[0])):
                a = argv.pop(0)
                if ASSIGN.match(a):
                    assigns.append(a)
                elif a in ("-S", "--split-string") and argv:
                    argv = argv.pop(0).split() + argv
                elif a in ("-u", "--unset", "-C", "--chdir") and argv:
                    argv.pop(0)
        elif name in WRAPPERS:
            argv.pop(0)
            while argv and argv[0].startswith("-"):
                a = argv.pop(0)
                if a == "--":
                    break
                if a in SKIP_VALUE.get(name, ()) and argv:
                    argv.pop(0)
            if name == "timeout" and argv:
                argv.pop(0)  # the duration
            if name == "flock" and argv:
                argv.pop(0)  # the lock file
        elif name == "uv" and "run" in argv[1:3]:
            argv = argv[argv.index("run") + 1 :]
            while argv and argv[0].startswith("-"):
                a = argv.pop(0)
                if a == "--":
                    break
                if a in UV_RUN_VALUE and argv:
                    argv.pop(0)
        elif PY.match(name):
            i = 1
            while i < len(argv) and argv[i].startswith("-") and argv[i] not in ("-", "-c") and argv[i][:2] != "-m":
                i += 2 if argv[i] in ("-X", "-W") else 1
            if i < len(argv) and argv[i] == "-m" and i + 1 < len(argv):
                argv = argv[i + 1 :]
            elif i < len(argv) and argv[i].startswith("-m") and len(argv[i]) > 2:
                argv = [argv[i][2:], *argv[i + 1 :]]
            else:
                break
        else:
            break
    return argv


class Checker:
    def __init__(self, cwd: str, read_only: str = ""):
        self.read_only = read_only  # a read-only agent's own `dev` report (READ_ONLY_AGENTS); "": not read-only
        self.cwd = cwd
        self.start = cwd  # the session's worktree; a `cd ..` inside the command does not move it
        self.in_python = 0  # > 0 while checking a Python string literal as a shell command

    def text(self, text: str, depth: int = 0) -> str | None:
        """Check a shell command string; return the reason it is blocked, or None."""
        if depth > MAX_DEPTH:
            return DEEP
        for pipe in lex(text):
            cmds = [c for c in pipe if c.words or c.stdin or c.reads or c.writes]
            for k, c in enumerate(cmds):
                if why := self.simple(c, depth):
                    return why
                if self.stdin_lang(c) and k:  # earlier stages of the pipeline feed this interpreter
                    lang = self.stdin_lang(c)
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
        self.in_python += 1
        try:
            for s in strings:
                if is_env(Word(s)):
                    return ENV
                if is_secrets(Word(s)):
                    return SECRETS
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
            return SECRETS
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
        if name == "pkill" and any(a == "--full" or (re.match(r"-[^-]", a) and "f" in a) for a in args):
            return PKILL
        if name == "git":
            return self.git(args, assigns)
        if name == "rm":
            return self.rm(args)
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
        if sub == "config" and any("hookspath" in a.lower() for a in rest) and not config_reads_only(rest):
            return HOOKS
        if any(long_opt(a, "--no-verify", 9) for a in rest):
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
                if long_opt(opt, "--mirror", 4) or long_opt(opt, "--all", 4) or long_opt(opt, "--branches", 4):
                    return MAIN
                tags_only = tags_only or opt == "--tags"
                skip = "=" not in a and opt in ("--repo", "--push-option", "--receive-pack", "--exec")
            elif a.startswith("-") and len(a) > 1:
                if "f" in a.split("o", 1)[0]:
                    return FORCE
                skip = a.endswith("o")
            else:
                pos.append(a)
        refspecs = pos[1:]
        for r in refspecs:
            if r.startswith("+"):
                return FORCE
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
    """git commit -n is --no-verify; -m, -F, -c, -C and -t take a value, as the rest of the cluster or the next arg."""
    skip = False
    for a in rest:
        if skip:
            skip = False
            continue
        if a == "--":
            break
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


def within(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


def worktree_root(cwd: str) -> str:
    """The top of the worktree holding cwd, or cwd itself outside a repository."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return os.path.realpath(out or cwd)


def current_branch(where: str) -> str | None:
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


def main() -> int:
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
    except (json.JSONDecodeError, AttributeError):
        return 0
    if not isinstance(cmd, str):
        return 0
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    agent = data.get("agent_type") if isinstance(data.get("agent_type"), str) else ""  # set inside a subagent
    try:
        why = Checker(cwd, read_only=READ_ONLY_AGENTS.get(agent.rsplit(":", 1)[-1], "")).text(cmd)
    except RecursionError:  # hundreds of nested substitutions: nobody writes that by hand
        why = DEEP
    if why:
        print(f"Blocked by project rule: {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
