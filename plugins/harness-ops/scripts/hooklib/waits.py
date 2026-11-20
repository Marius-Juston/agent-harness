"""guard_bash's bound on commands that can wait forever (#381): a poll loop, `tail -f`, `watch`, `wait`, ... runs
as `timeout <[tool.devkit] wait_bound_s> bash -c '<command>'`. Stdlib only."""

import os
import re
import shlex

from hooklib.shell import ASSIGN, KEYWORDS, MAX_DEPTH, SHELLS, SKIP_VALUE, base, lex, short_flag

# #381: commands that can wait forever. An agent's poll loop whose file is never written, or whose `pgrep -f` matches
# its own shell, otherwise outlives the agent as a stale shell. Such a command is rewritten, not refused, to run under
# `timeout <wait_bound_s>`. WAIT_HINT is the cheap test every command takes; only a hit is lexed.
WAIT_HINT = re.compile(r"\b(?:while|until|tail|watch|sleep|wait|inotifywait|flock|follow|journalctl|logs)\b")
WAIT_BOUND = 3600  # [tool.devkit] wait_bound_s; devkit/config.py documents it
BOUND_KEY = re.compile(r"(?m)^\s*wait_bound_s\s*=\s*(\d+)")
FOLLOWERS = {"docker", "podman", "kubectl", "oc", "nerdctl"}  # `<tool> [compose] logs -f`
STARTERS = {"nohup", "nice", "ionice", "setsid", "stdbuf", "exec", "command", "builtin", "time", "sudo", "doas", "env"}
SLEEP_ARG = re.compile(r"^(\d+(?:\.\d*)?|\.\d+)([smhd]?)$")
UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
PGREP_F = "`pgrep -f` may match this shell's own command line, so the loop never ends; prefer `pgrep -x` or a pid."


def sleep_s(args: list[str]) -> float | None:
    total = 0.0
    for a in args:
        if a.startswith("inf"):
            return float("inf")
        m = SLEEP_ARG.match(a)
        if not m:
            return None  # $N, $((...)): not knowable here
        total += float(m.group(1)) * UNIT[m.group(2)]
    return total


def waits_forever(argv: list[str], bound: int) -> str | None:
    """What makes this one command (wrappers already stripped) wait without end, or None."""
    name, args = base(argv[0]), argv[1:]
    if name == "tail" and short_flag(args, "fF", ("--follow", "--retry")):
        return "tail -f"
    if name == "watch":
        return "watch"
    if name == "sleep" and (s := sleep_s(args)) is not None and s > bound:
        return f"sleep {' '.join(args)}"
    if name == "wait":
        return "wait"
    if name == "inotifywait" and (short_flag(args, "m", ("--monitor",)) or not short_flag(args, "t", ("--timeout",))):
        return "inotifywait without -t"
    if name == "flock" and not short_flag(args, "wnu", ("--wait", "--timeout", "--nonblock", "--unlock")):
        return "flock without -w"
    if name == "gh" and (args[:2] == ["run", "watch"] or (args[:2] == ["pr", "checks"] and "--watch" in args)):
        return f"gh {' '.join(args[:2])}"
    if name in FOLLOWERS and "logs" in args[:3] and short_flag(args, "f", ("--follow",)):
        return f"{name} logs -f"
    if name == "journalctl" and short_flag(args, "f", ("--follow",)):
        return "journalctl -f"
    return None


def strip_starters(argv: list[str]) -> list[str]:
    """Drop assignments and the wrappers that run a command without bounding it (nohup, nice, env, sudo ...)."""
    while argv and (base(argv[0]) in STARTERS or ASSIGN.match(argv[0])):
        name, argv = base(argv[0]), argv[1:]
        while name in STARTERS and argv and argv[0].startswith("-"):
            a, argv = argv[0], argv[1:]
            if a == "--":
                break
            if a in SKIP_VALUE.get(name, ()) and argv:
                argv = argv[1:]
    return argv


def unbounded(text: str, bound: int, depth: int = 0) -> tuple[list[str], bool]:
    """The waits in a shell command that nothing bounds, and whether it polls with `pgrep -f`.

    A wait under `timeout` is bounded. A `while read` loop ends with its input. Shell code passed with -c (or flock -c)
    or fed to a shell by a heredoc is searched too, unless a timeout wraps that shell."""
    found: list[str] = []
    pgrep_f = False
    if depth > MAX_DEPTH or not WAIT_HINT.search(text):
        return found, pgrep_f
    for pipe in lex(text):
        for c in pipe:
            words = list(c.words)
            loop = False
            while words and ((not words[0].quoted and words[0].value in KEYWORDS) or ASSIGN.match(words[0].value)):
                loop = loop or (words[0].value in ("while", "until") and not words[0].quoted)
                words.pop(0)
            argv = [w.value for w in words]
            argv = strip_starters(argv)
            if argv and base(argv[0]) == "pgrep" and short_flag(argv[1:], "f", ("--full",)):
                pgrep_f = True
            if loop and not (argv and base(argv[0]) == "read"):
                found.append("until/while loop")
            if not argv or base(argv[0]) == "timeout":
                continue
            if why := waits_forever(argv, bound):
                found.append(why)
            if base(argv[0]) not in SHELLS | {"flock"}:
                continue
            code = [body for body, _ in c.stdin] if base(argv[0]) in SHELLS else []  # bash <<'EOF' ... EOF
            for k, a in enumerate(argv[1:-1], 1):
                if a in ("-c", "--command") or (a.startswith("-") and not a.startswith("--") and "c" in a):
                    code = [argv[k + 1]]
                    break
            for chunk in code:
                more, pf = unbounded(chunk, bound, depth + 1)
                found += more
                pgrep_f = pgrep_f or pf
    return found, pgrep_f


def wait_bound(cwd: str) -> int:
    """[tool.devkit] wait_bound_s from the nearest pyproject.toml with a [tool.devkit] table above cwd (the worktree's
    own config), else above $CLAUDE_PROJECT_DIR, else the default.

    Read only for a command WAIT_HINT matches. A plain scan, not tomllib: hooks run on the host's python3 (3.10)."""
    for start in (cwd, os.environ.get("CLAUDE_PROJECT_DIR", "")):
        d = os.path.abspath(start) if start else ""
        while d:
            try:
                with open(os.path.join(d, "pyproject.toml"), encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                text = ""
            if (at := text.find("[tool.devkit]")) >= 0:
                table = re.split(r"(?m)^\s*\[", text[at + len("[tool.devkit]") :], maxsplit=1)[0]
                m = BOUND_KEY.search(table)
                return int(m.group(1)) if m else WAIT_BOUND
            d = "" if os.path.dirname(d) == d else os.path.dirname(d)
    return WAIT_BOUND


def bound_waits(cmd: str, cwd: str) -> tuple[str, str] | None:
    """(the rewritten command, the note for the agent) when the command waits without a bound, else None."""
    if not WAIT_HINT.search(cmd) or (bound := wait_bound(cwd)) <= 0:
        return None
    found, pgrep_f = unbounded(cmd, bound)
    if not found:
        return None
    body, tail = cmd.strip(), ""
    if body.endswith("&") and not body.endswith(("&&", "\\&", ">&", "<&")):
        body, tail = body[:-1].rstrip(), " &"  # a backgrounded wait: bound it in the background
    new = f"timeout {bound} bash -c {shlex.quote(body)}{tail}"
    what = ", ".join(dict.fromkeys(found))
    note = (
        f"guard-bash: this command can wait forever ({what}), so it runs as `timeout {bound} bash -c '...'` "
        f"([tool.devkit] wait_bound_s). Exit 124 means the bound ran out. Put a shorter `timeout <s>` in front "
        f"yourself to choose the bound."
    )
    return new, note + (" " + PGREP_F if pgrep_f else "")
