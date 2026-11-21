"""guard_bash's PATH rules (#673): an agent never writes, moves, links or chmods a file into a directory on the
session's PATH outside its worktree (~/.local/bin, ~/.cargo/bin, ...), never lands a tree on a directory holding one,
and, in a subagent, never installs a tool onto PATH. Stdlib only."""

import functools
import itertools
import os
import re
from collections.abc import Callable

from hooklib.shell import parse_opts, within

SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "-"}
# #673: a file written into a directory on the owner's PATH (~/.local/bin, ~/.cargo/bin, ...) shadows gh, git, uv or
# python in the owner's shells, every later session and the hooks themselves.
PATH_DIR = (
    "{dir} is on the session's PATH outside your worktree: an executable written there shadows the owner's tools (gh, "
    "git, uv, python) in every later shell, session and hook. Keep scripts in the worktree and run them by path; "
    "installing a tool onto PATH is the lead's or the owner's."
)
PATH_HOLDS = (
    "{target} holds {dir}, a directory on the session's PATH: copying, moving or extracting a tree onto it can put "
    "executables there that shadow the owner's tools (gh, git, uv, python). Copy into the worktree or the temp dir; "
    "installing a tool onto PATH is the lead's or the owner's."
)
PATH_UNKNOWN = (
    "a settings file's env block sets PATH (or HOME, CLAUDE_CONFIG_DIR, CLAUDE_PROJECT_DIR), so the guard cannot "
    "tell which directories are on the owner's PATH: writes outside your worktree, the temp dir and the project's "
    "auto-memory are refused until the owner removes that setting."
)
GUARD_ERROR = (
    "guard error, refused: the guard failed on this call ({kind}), so it is refused rather than let through. Split it "
    "into simpler commands, or tell the lead."
)
INSTALL = (
    "`{cmd}` puts executables in a directory on the owner's PATH (~/.local/bin, ~/.cargo/bin, ...), where they shadow "
    "the owner's tools: installing a tool is the lead's. Run it without installing (`uvx <tool>`, `uv run`), or ask "
    "the lead."
)
# Commands whose every path operand is written (or has its mode or owner changed); cp-like ones write their last
# operand and their -t / --target-directory value; mv also empties its sources. A recursive copy, a move, or an
# extraction must not land on a directory that holds a PATH directory either (`cp -r stage/. ~/.local/`).
PATH_WRITERS = {"tee", "chmod", "chown", "chgrp", "touch", "truncate", "mkdir", "setfacl", "chattr"}
PATH_COPIERS = {"cp", "ln", "link", "install", "rsync", "scp", "mv", "ditto"}
TREE_COPIERS = {"rsync", "ditto", "mv"}  # always copy or move a whole tree; cp and scp only with -r / -a
# Per command: the short letters and long options whose value is a file or directory written. A short letter takes
# the rest of its cluster or the next word (`-fsSLo <path>`, `-o<path>`); a long one `=value` or the next word.
OUT_OPTS = {
    "curl": ("o", {"--output", "--output-dir"}),
    "wget": ("oOPa", {"--output-document", "--output-file", "--append-output", "--directory-prefix"}),
    "tar": ("Cf", {"--directory", "--file"}),
    "unzip": ("d", set()),
}
# Code that writes a file, per interpreter: the string literals on a line that matches are checked as write targets.
PY_WRITE = re.compile(
    r"\b(?:write_text|write_bytes|copy\w*|move|rename|replace|symlink\w*|link\w*|chmod|mkdir|makedirs|touch|"
    r"urlretrieve)\s*\(|\bopen\s*\([^)]*['\"][rbt]*[wax+][rbt+]*['\"]"  # open() only with a writing mode
)
CODE_WRITE = {
    "pl": re.compile(r"\bopen\b[^;]*['\"]\+?>|\b(?:rename|symlink|link|chmod|copy|move|mkdir)\b\s*\(?"),
    "js": re.compile(
        r"\b(?:writeFile|appendFile|copyFile|cp|symlink|link|rename|chmod|mkdir|createWriteStream|open)(?:Sync)?\s*\("
    ),
    "rb": re.compile(
        r"\b(?:File\.(?:write|symlink|link|rename|chmod)|IO\.write|FileUtils\.\w+)|\bopen\b[^;]*['\"][wa]"
    ),
}
CODE_LANGS = {"perl": "pl", "node": "js", "nodejs": "js", "bun": "js", "ruby": "rb"}
# Package managers' install commands that put executables on PATH: (command, the words that install). A word set
# matches when the command's first non-option words start with it (a `+toolchain` word skipped); `-g`, `--global`
# or `--location=global` marks the node ones.
INSTALLERS = {
    "uv": [("tool", "install"), ("tool", "upgrade"), ("python", "install"), ("python", "upgrade")],
    "pipx": [
        (s,) for s in ("install", "install-all", "inject", "upgrade", "upgrade-all", "reinstall", "reinstall-all")
    ],
    "cargo": [("install",), ("binstall",)],
    "go": [("install",)],
    "gem": [("install",)],
    "brew": [("install",), ("reinstall",), ("upgrade",), ("link",)],
    "apt": [("install",), ("reinstall",)],
    "apt-get": [("install",), ("reinstall",)],
    "dnf": [("install",), ("reinstall",)],
    "yum": [("install",), ("reinstall",)],
}
GLOBAL_INSTALLERS = {
    "npm": {"install", "i", "add", "link"},
    "pnpm": {"add", "install", "i", "link"},
    "bun": {"add", "install", "i", "link"},
}


def path_list(value: str) -> list[str] | None:
    """PATH's absolute directories, each once; None for an empty value (the owner's PATH is unknown). Relative
    entries ("", ".") follow the shell's cwd and cannot be guarded."""
    if not value:
        return None
    return list(dict.fromkeys(os.path.normpath(d) for d in value.split(os.pathsep) if os.path.isabs(d)))


@functools.lru_cache(maxsize=4)
def resolved(dirs: tuple[str, ...]) -> tuple[str, ...]:
    """Each PATH directory with its symlinks resolved. Threads, since a PATH reaching a network or WSL drive costs
    milliseconds a lookup (0.32 s serial against 0.07 s for 117 entries on WSL2, measured for #673)."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(16) as pool:
        return tuple(pool.map(os.path.realpath, dirs))


def owner_home() -> str:
    """The owner's home from the password database (a settings `env` can move HOME), else ~."""
    try:
        import pwd  # POSIX only

        return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        return os.path.realpath(os.path.expanduser("~"))


def memory_dirs(root: str) -> list[str]:
    """Claude Code's auto-memory directories for the worktree `root` and its main checkout, under the owner's home:
    ~/.claude/projects/<path with every other character as '-'>/memory."""
    import subprocess

    roots = [root]
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            roots.append(os.path.dirname(os.path.realpath(out.stdout.strip())))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    projects = os.path.join(owner_home(), ".claude", "projects")
    return [os.path.join(projects, re.sub(r"[^A-Za-z0-9]", "-", r), "memory") for r in dict.fromkeys(roots)]


def path_refusal(
    target: str, cwd: str, root: str, dirs: Callable[[], list[str] | None], holds: bool = False
) -> str | None:
    """The refusal when writing `target` (relative to `cwd`) lands in a directory on PATH (`dirs()`, read only for a
    target outside the worktree `root`) outside the worktree (#673), or, with `holds` (a tree copied, moved or
    extracted onto it), when the target holds one. When `dirs()` is None (PATH unknown) it fails closed: anything
    outside the worktree, the temp dir and the project's auto-memory is refused. A path that starts with a variable
    the guard cannot expand counts only when its literal tail names a PATH directory under the owner's home by two
    components or more (`$X/.local/bin/gh`), so `$tmp/bin/gh` is no write to /bin."""
    if not target or target.isdigit() or target in SINKS or "://" in target:
        return None
    # the lexer leaves a NUL where a $(...) or <(...) was: its output is as unknown as an unset variable's
    path = os.path.expandvars(os.path.expanduser(target.replace("\0", "$()")))
    if "$" in path:
        if (known := dirs()) is None:
            return PATH_UNKNOWN
        tail = os.path.dirname(re.split(r"\$\(\)|\$\{?\w*\}?|\$\(", path)[-1]).rstrip("/")
        if tail.count("/") < 2 or not tail.startswith("/"):
            return None
        homes = (owner_home(), os.path.realpath(os.path.expanduser("~")))  # HOME counts: no settings `env` moves it
        hit = next((d for d in known if d.endswith(tail) and any(within(d, h) for h in homes)), None)
        return PATH_DIR.format(dir=hit) if hit else None
    full = os.path.normpath(os.path.join(cwd, path))
    real = os.path.realpath(full)
    if root not in ("/", owner_home(), os.path.realpath(os.path.expanduser("~"))) and within(real, root):
        return None  # the worktree's own .venv/bin and the like
    import tempfile

    temp = os.path.realpath(tempfile.gettempdir())
    if (known := dirs()) is None:
        free = [temp, *memory_dirs(root)]
        return None if any(within(real, d) for d in free) else PATH_UNKNOWN
    if within(real, temp) and not any(within(d, temp) for d in known):
        return None  # the scratchpad and /tmp: no PATH entry to resolve when none is spelled under the temp dir
    for d in itertools.chain(known, resolved(tuple(known))):
        if within(full, d) or within(real, d):
            return PATH_DIR.format(dir=d)
        if holds and (within(d, full) or within(d, real)):
            return PATH_HOLDS.format(target=target, dir=d)
    return None


def installs(name: str, args: list[str]) -> bool:
    """A package manager's install that puts executables on PATH (INSTALLERS, GLOBAL_INSTALLERS, yarn's global form,
    make's install target, cmake's install step)."""
    words = [a for a in args if not a.startswith(("-", "+"))]
    if any(tuple(words[: len(w)]) == w for w in INSTALLERS.get(name, ())):
        return True
    if name in GLOBAL_INSTALLERS and words[:1] and words[0] in GLOBAL_INSTALLERS[name]:
        pairs = itertools.pairwise([*args, ""])
        if any(a == "--location=global" or (a == "--location" and b == "global") for a, b in pairs):
            return True
        return any(a in ("-g", "--global") or (a[:1] == "-" and a[1:2] != "-" and "g" in a) for a in args)
    if name == "make":
        return any(w == "install" or w.startswith("install-") for w in words if "=" not in w)
    if name == "cmake":
        return "--install" in args
    return name == "yarn" and words[:2] == ["global", "add"]


def option_values(args: list[str], letters: str, longs: set[str]) -> list[str]:
    """The values of the options in `letters` (short: the rest of the cluster, else the next word) and `longs`
    (`--opt=value`, else the next word), up to `--`."""
    out, k = [], 0
    while k < len(args):
        a = args[k]
        if a == "--":
            break
        if a.startswith("--"):
            opt, eq, value = a.partition("=")
            if opt in longs:
                if eq:
                    out.append(value)
                elif k + 1 < len(args):
                    out.append(args[k + 1])
                    k += 1
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                if ch in letters:
                    if a[j + 1 :]:
                        out.append(a[j + 1 :])
                    elif k + 1 < len(args):
                        out.append(args[k + 1])
                        k += 1
                    break
        k += 1
    return out


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


class PathRules:
    """The PATH checks guard_bash's Checker runs; it sets start, cwd, agent, root and dirs and defines owner_path."""

    start: str
    cwd: str
    agent: str | None
    root: str | None
    dirs: tuple[list[str] | None] | None

    def on_path(self, target: str, holds: bool = False) -> str | None:
        """path_refusal for a write target, the worktree and PATH read once per command."""
        if self.root is None:
            self.root = worktree_root(self.start)
        return path_refusal(target, self.cwd, self.root, self.path_dirs, holds)

    def path_dirs(self) -> list[str] | None:
        if self.dirs is None:
            self.dirs = (self.owner_path(),)
        return self.dirs[0]

    def owner_path(self) -> list[str] | None:
        """PATH's directories as the owner set it: guard_bash.path_dirs."""
        raise NotImplementedError

    def path_write(self, name: str, args: list[str], writes: list[str]) -> str | None:
        """#673: the refusal when the command writes, moves, links or chmods a file into a PATH directory outside the
        worktree, copies, moves or extracts a tree onto a directory holding one, or (in a subagent) installs a tool."""
        if self.agent is not None and installs(name, args):
            return INSTALL.format(cmd=" ".join([name, *[a for a in args if not a.startswith(("-", "+"))][:2]]))
        shorts, longs, pos = parse_opts(args)
        targets = [(w, False) for w in writes]  # (target, a tree lands on it)
        in_place = any(a.startswith("--in-place") or (a[:1] == "-" and a[1:2] != "-" and "i" in a) for a in args)
        if name in PATH_WRITERS or (name in ("sed", "perl") and in_place):
            targets += [(p, False) for p in pos]
        elif name in PATH_COPIERS:
            recursive = {"r", "R", "a"} & shorts or {"--recursive", "--archive"} & longs
            tree = name in TREE_COPIERS or (name in ("cp", "scp") and bool(recursive))
            dests = option_values(args, "t", {"--target-directory"}) + pos[-1:]
            targets += [(p, tree) for p in (pos if name == "mv" else dests)]
        elif name == "dd":
            targets += [(a[3:], False) for a in args if a.startswith("of=")]
        elif name in OUT_OPTS:
            letters, opts = OUT_OPTS[name]
            argv = ["-" + args[0], *args[1:]] if name == "tar" and args and not args[0].startswith("-") else args
            dirs = option_values(argv, letters.replace("f", ""), opts - {"--file"})
            targets += [(v, name in ("tar", "unzip")) for v in dirs]
            if name == "tar":
                targets += [(v, False) for v in option_values(argv, "f", {"--file"})]
            extracts = name == "unzip" or any(
                a in ("--extract", "--get") or (a[:1] == "-" and a[1:2] != "-" and "x" in a) for a in argv
            )
            if extracts and not dirs:
                targets.append((".", True))  # into the cwd
        return next((why for t, holds in targets if (why := self.on_path(t, holds))), None)

    def code_writes(self, src: str, pattern: re.Pattern[str]) -> str | None:
        """The refusal for a string literal on a line of `src` that matches `pattern` (writes a file) naming a path
        on PATH; perl's `">path"` counts with its mode stripped."""
        for line in src.splitlines() if pattern.search(src) else ():
            if pattern.search(line):
                found = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", line)
                paths = [(a or b).lstrip("+<> ") for a, b in found]
                paths = [s for s in paths if "/" in s and not any(ch.isspace() for ch in s)]
                if why := next((w for s in paths if (w := self.on_path(s))), None):
                    return why
        return None
