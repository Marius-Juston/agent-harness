"""A small bash lexer for the harness-ops hooks: pipelines of simple commands, with quoting, heredocs,
substitutions and wrappers (sudo, env, timeout, uv run, ...) read the way bash reads them. Stdlib only."""

import itertools
import os
import re

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

EXPANDS = re.compile(r"\$\{?[#!]?([A-Za-z_]\w*)")  # a variable a word expands (single-quoted text removed first)

SINGLE_QUOTED = re.compile(r"\$?'[^']*'")

MAX_DEPTH = 8


class Word:  # plain classes, not dataclasses: importing dataclasses costs every Bash call 5 ms
    __slots__ = ("brace", "glob", "quoted", "value")

    def __init__(self, value: str, glob: bool = False, quoted: bool = False, brace: bool = False):
        self.value = value
        self.glob = glob  # an unquoted * ? or [ that bash would expand
        self.quoted = quoted
        self.brace = brace  # an unquoted {, so maybe a brace expansion


class Simple:
    __slots__ = ("expands", "reads", "stdin", "words", "writes")

    def __init__(self, words: list[Word] | None = None):
        self.words: list[Word] = words if words is not None else []
        self.stdin: list[tuple[str, bool]] = []  # heredoc / here-string text, and "is a heredoc"
        self.reads: list[str] = []  # input redirection targets
        self.writes: list[str] = []  # output redirection targets
        self.expands: list[str] = []  # the variables its words and unquoted heredocs expand


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
                owner.expands += EXPANDS.findall(body)
                scan_substitutions(body, self.out)  # an unquoted heredoc still runs $(...) and `...`
        self.pending = []
        return i

    def word(self, i: int) -> tuple[Word, int]:
        w, j = self.word_at(i)
        if "$" in self.s[i:j]:
            self.pipe[-1].expands += EXPANDS.findall(SINGLE_QUOTED.sub("", self.s[i:j]))
        return w, j

    def word_at(self, i: int) -> tuple[Word, int]:
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


SKIP_VALUE = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-t"},
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-a", "-E", "-s", "-i", "--max-args", "--max-procs", "--delimiter"},
    "stdbuf": {"-i", "-o", "-e"},
    "flock": {"-w", "-E", "--timeout"},
    "env": {"-u", "--unset", "-C", "--chdir"},  # strip_starters only: unwrap handles env itself
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


def within(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


def short_flag(args: list[str], letters: str, longs: tuple[str, ...]) -> bool:
    """A flag given as one of `letters` (alone or in a cluster such as -fn) or one of the long options."""
    for a in args:
        if a == "--":
            return False
        if a.startswith("--"):
            if a.split("=", 1)[0] in longs:
                return True
        elif a.startswith("-") and any(ch in letters for ch in a[1:]):
            return True
    return False


GREPS = {"grep", "egrep", "fgrep", "rg"}
