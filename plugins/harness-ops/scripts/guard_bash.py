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
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

PIP = "Python is managed with uv only: `uv add <pkg>` (or `uv add --dev`), `uv run ...`. Never pip."
PKILL = '`pkill -f` matches the shell running it. Collect PIDs with `pgrep -f "[p]attern"` in one call, kill them in another.'
ENV = "Never read secrets: .env stays unread. Commands that need it load it themselves."
MAIN = "Never push to main: push your issue branch and open a PR (`uv run dev pr <issue>`); the lead integrates."
FORCE = "Force-push rewrites shared history. Use --force-with-lease on your own issue branch only, never on main."
HOOKS = "--no-verify, -n, SKIP= and core.hooksPath skip the checks every agent is held to. Fix what the hook reports instead."
DEEP = "the command nests too deeply to check. Split it into simpler commands."
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


class Lexer:
    """A small bash lexer: pipelines of simple commands; substitutions become pipelines of their own."""

    def __init__(self, s: str, out: list[list[Simple]]):
        self.s, self.n, self.out = s, len(s), out
        self.pipe: list[Simple] = [Simple()]
        self.out.append(self.pipe)
        self.pending: list[tuple[str, bool, bool, Simple]] = []

    def end_pipeline(self) -> None:
        if any(c.words or c.stdin or c.reads for c in self.pipe):
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


def is_env(w: Word) -> bool:
    if any(ch.isspace() for ch in w.value):  # prose (a commit or issue body), not a path
        return False
    name = base(re.split(r"[=:]", w.value)[-1])
    if w.glob:  # a glob reaches a dotfile only if its own name starts with "." (no dotglob)
        return name.startswith(".") and any(fnmatchcase(f, name) for f in (".env", ".env.local"))
    return name not in ENV_SAFE and (name == ".env" or name.startswith(".env."))


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
    def __init__(self, cwd: str):
        self.cwd = cwd

    def text(self, text: str, depth: int = 0) -> str | None:
        """Check a shell command string; return the reason it is blocked, or None."""
        if depth > MAX_DEPTH:
            return DEEP
        for pipe in lex(text):
            cmds = [c for c in pipe if c.words or c.stdin or c.reads]
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
        for s in strings:
            if is_env(Word(s)):
                return ENV
            if why := self.text(s, depth + 1):
                return why
        return None

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
        if (any(is_env(w) for w in paths) and not env_ok) or any(is_env(Word(r)) for r in c.reads):
            return ENV
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
        if sub == "config" and any("hookspath" in a.lower() for a in rest):
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


def main() -> int:
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
    except (json.JSONDecodeError, AttributeError):
        return 0
    if not isinstance(cmd, str):
        return 0
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    try:
        why = Checker(cwd).text(cmd)
    except RecursionError:  # hundreds of nested substitutions: nobody writes that by hand
        why = DEEP
    if why:
        print(f"Blocked by project rule: {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
