#!/usr/bin/env python3
"""PreToolUse(Bash): `uv add <name>...` runs the reuse gates on each named PyPI package before it is installed (#172).

The gates are `uv run dev reuse check --json` (devkit.reuse.check; docs/research/software-discovery.md §8, §10), run
from the project's own .venv, so there is one implementation. The owner's split:
- refuse (exit 2): NOT FOUND on PyPI (G1, a hallucinated name), a malicious-package advisory (G2 FAIL, OSV MAL-), or
  strong or network copyleft (G5 FAIL) unless the owner lists the package in DEVKIT_ALLOW_COPYLEFT (comma-separated,
  read from the hook's own environment: an assignment inside the command does not reach it);
- warn (additionalContext the agent sees, and a systemMessage): every other HOLD, FAIL or flag (G3 new, G4 dormant,
  deprecated or archived, an unknown or Lead licence, weak copyleft, G6 open advisories), and each argument that is
  not a PyPI name (a path, URL, direct reference, -r file, or a custom index);
- fail open with a visible warning: the registry gave no answer (UNVERIFIED), no devkit in the project, or the check
  timed out or crashed. A source being down must not stop all work; the warning names the command to rerun.

Commands are read with guard_bash's lexer and Checker, so `cd x && uv add y`, `bash -c '...'` and `uv run uv add` are
seen and quoted text is not. Stdlib only.
"""

import contextlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard_bash

TIMEOUT = 80  # seconds for `dev reuse check`; hooks.json gives the hook more
ALLOW_ENV = "DEVKIT_ALLOW_COPYLEFT"
TAG = "uv add guard"
# uv's options that take a value: before the subcommand (global) and after `add`.
UV_GLOBAL_VALUE = {"--directory", "--project", "--config-file", "--cache-dir", "-p", "--python", "--color"}
UV_GLOBAL_VALUE |= {"--allow-insecure-host"}
ADD_VALUE = UV_GLOBAL_VALUE | {"-r", "--requirements", "-c", "--constraints", "-m", "--marker", "--optional"}
ADD_VALUE |= {"--group", "--bounds", "--rev", "--tag", "--branch", "--extra", "--package", "--script"}
ADD_VALUE |= {"--no-install-package", "--index-strategy", "--keyring-provider", "-P", "--upgrade-package"}
ADD_VALUE |= {"--upgrade-group", "--resolution", "--prerelease", "--prerelease-package", "--fork-strategy"}
ADD_VALUE |= {"--exclude-newer", "--exclude-newer-package", "--no-sources-package", "--reinstall-package"}
ADD_VALUE |= {"--link-mode", "-C", "--config-setting", "--config-settings-package", "--no-build-isolation-package"}
ADD_VALUE |= {"--no-build-package", "--no-binary-package", "--refresh-package"}
INDEX = {"--index", "--default-index", "-i", "--index-url", "--extra-index-url", "-f", "--find-links"}
ADD_VALUE |= INDEX
REQ = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*(.*)", re.S)
SPEC = re.compile(r"[\s<>=!~*.,0-9A-Za-z+-]*")  # a version specifier, nothing that makes it a path or URL
ARCHIVE = (".whl", ".tar.gz", ".tgz", ".zip", ".tar.bz2")


@dataclass
class Add:
    cwd: str
    names: list[str] = field(default_factory=list)  # PEP 503-normalised, in order, once each
    notes: list[str] = field(default_factory=list)  # arguments the gates did not check, and why


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def add_args(argv: list[str]) -> list[str] | None:
    """The arguments after `add` when argv is `uv [global options] add ...`, else None."""
    if not argv or guard_bash.base(argv[0]) != "uv":
        return None
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in UV_GLOBAL_VALUE else 1
    return argv[i + 1 :] if argv[i : i + 1] == ["add"] else None


def parse(args: list[str], cwd: str) -> Add:
    a, positional, custom_index, i = Add(cwd), [], "", 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            positional += args[i + 1 :]
            break
        if arg.startswith("--") and "=" in arg:
            opt, value = arg.split("=", 1)
        elif arg in ADD_VALUE:
            opt, value = arg, args[i + 1] if i + 1 < len(args) else ""
            i += 1
        elif re.fullmatch(r"-[rcmPCif].+", arg):  # a short option with its value attached: -rreq.txt
            opt, value = arg[:2], arg[2:]
        elif arg.startswith("-"):
            opt, value = arg, ""
        else:
            positional.append(arg)
            i += 1
            continue
        if opt in ("-r", "--requirements"):
            a.notes.append(f"the names in {value} were not checked: `uv run dev reuse check <name>...` them")
        if opt in INDEX or opt == "--no-index":
            custom_index = opt
        i += 1
    for word in positional:
        name, why = requirement(word)
        if why:
            a.notes.append(f"{word!r} was not checked: {why}")
        elif custom_index:
            a.notes.append(f"{name} was not checked: {custom_index} names an index the PyPI gates do not cover")
        elif name not in a.names:
            a.names.append(name)
            if name.replace("-", "_") in getattr(sys, "stdlib_module_names", ()):
                a.notes.append(f"{name} is a standard-library module name: import it, nothing needs installing")
    return a


def requirement(word: str) -> tuple[str, str]:
    """(normalised name, '') for a PEP 508 requirement on a registry name, else ('', why it is not one)."""
    m = REQ.fullmatch(word.strip())
    if word.lower().endswith(ARCHIVE) or not m:
        return "", "a path, URL or archive, not a PyPI name"
    rest = m.group(2).split(";", 1)[0]
    if rest.lstrip().startswith("@"):
        return "", "a direct reference (name @ URL), not from PyPI"
    if not SPEC.fullmatch(rest):
        return "", "a path, URL or archive, not a PyPI name"
    return normalise(m.group(1)), ""


class Collector(guard_bash.Checker):
    """guard_bash's walk over every simple command (substitutions, bash -c, eval, stdin), collecting `uv add`s."""

    def __init__(self, cwd: str):
        super().__init__(cwd)
        self.adds: list[Add] = []

    def simple(self, c, depth: int) -> str | None:
        if (args := add_args(guard_bash.unwrap([w.value for w in c.words], []))) is not None:
            self.adds.append(parse(args, self.cwd))
        super().simple(c, depth)
        return None  # keep walking: guard_bash itself refuses what it refuses


def adds(command: str, cwd: str) -> list[Add]:
    col = Collector(cwd)
    with contextlib.suppress(RecursionError):
        col.text(command)
    return col.adds


class Unavailable(Exception):
    """The check could not run: the caller fails open with this reason."""


def dev_binary(cwd: str) -> str | None:
    d = cwd
    while d and not os.path.isdir(d) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    exe = os.path.join(guard_bash.worktree_root(d or "."), ".venv", "bin", "dev")
    return exe if os.access(exe, os.X_OK) else None


def execute(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str]:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    return out.returncode, out.stdout


def run_check(names: list[str], cwd: str) -> dict:
    """`dev reuse check --json` on the names: devkit.reuse.check's report."""
    exe = dev_binary(cwd)
    if not exe:
        raise Unavailable(f"no devkit in this project (.venv/bin/dev under {cwd})")
    d = cwd if os.path.isdir(cwd) else os.path.dirname(os.path.dirname(exe))
    try:
        rc, out = execute([exe, "reuse", "--json", "check", *names], d, TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"`dev reuse check` did not finish: {exc}") from exc
    try:
        report = json.loads(out)
        if len(report["candidates"]) != len(names):  # one candidate per name, in order
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise Unavailable(f"`dev reuse check` gave no JSON report (exit {rc})") from exc
    return report


def judge(report: dict, names: list[str], allowed: set[str]) -> tuple[list[str], list[str]]:
    """(refusals, warnings), each line `<name>: <gate> <outcome (why)>`."""
    refuse: list[str] = []
    warn: list[str] = []
    for name, c in zip(names, report["candidates"], strict=False):
        gates = c.get("gates") or {}
        if c.get("status") == "NOT FOUND":
            refuse.append(f"{name}: G1 {gates.get('G1', 'FAIL')}")
            continue
        if c.get("status") == "UNVERIFIED":
            warn.append(f"{name}: not verified ({gates.get('G1', '')}): run `uv run dev reuse check {name}` later")
            continue
        for g, v in gates.items():
            line, outcome = f"{name}: {g} {v}", v.split(" ")[0]
            if outcome == "FAIL" and g == "G2":
                refuse.append(line)
            elif outcome == "FAIL" and g == "G5":
                if name in allowed:
                    warn.append(f"{line}; allowed by the owner ({ALLOW_ENV})")
                else:
                    refuse.append(line)
            elif outcome in ("FAIL", "HOLD", "flag"):
                warn.append(line)
        warn += [f"{name}: {f}" for f in c.get("flags") or []]
    return refuse, warn


def warning(lines: list[str]) -> None:
    text = f"{TAG}: installed anyway, read before using it:\n" + "\n".join(f"- {x}" for x in lines)
    print(
        json.dumps(
            {
                "systemMessage": text,
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text},
            }
        )
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
    except (json.JSONDecodeError, AttributeError):
        return 0
    if not isinstance(cmd, str) or not re.search(r"\buv\b", cmd) or "add" not in cmd:
        return 0
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    allowed = {normalise(x) for x in re.split(r"[,\s]+", os.environ.get(ALLOW_ENV, "")) if x}
    refuse: list[str] = []
    warn: list[str] = []
    for a in adds(cmd, cwd):
        warn += a.notes
        if not a.names:
            continue
        try:
            r, w = judge(run_check(a.names, a.cwd), a.names, allowed)
        except Unavailable as exc:
            r, w = [], [f"{', '.join(a.names)} not checked: {exc}. Run `uv run dev reuse check {' '.join(a.names)}`"]
        refuse += r
        warn += w
    if refuse:
        print(
            f"Blocked by project rule: {TAG} (gates of `uv run dev reuse check`):\n"
            + "\n".join(f"- {x}" for x in refuse)
            + "\nName another package you know and check it with `uv run dev reuse check <name>...` first. "
            "A strong-copyleft licence needs the owner's approval: ask the lead.",
            file=sys.stderr,
        )
        return 2
    if warn:
        warning(warn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
