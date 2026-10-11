"""The gate: `uv run dev check` is the one verification entry point (CI runs the same command).

- One gate per machine at a time (a lock in the shared git dir), so parallel agents queue instead of thrashing memory.
- A pass is cached by tree_key: re-gating an unchanged clean tree is free; a dirty tree is never cached.
- Each stage prints one line; a failing stage prints at most FAIL_LINES of its output and saves the rest.
- A pytest run that passed nothing fails (nothing collected, everything skipped or deselected): it is not a pass.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from devkit import checks, docs
from devkit.paths import git, root, state_dir
from devkit.ran import Ran

FAIL_LINES = 40


def free_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return 8.0


def workers() -> int:
    """pytest-xdist workers: half the cores, at most one per 2 GB free, at least 1."""
    return max(1, min((os.cpu_count() or 2) // 2, int(free_gb() // 2), 16))


# pytest's final line, e.g. "== 3 passed, 1 skipped in 0.52s ==" or "3 passed in 65.1s (0:01:05)" under -q.
SUMMARY = re.compile(r"^=*\s*(?:\d+ \w+|no tests ran).* in [\d.]+s(?: \([^)]*\))?\s*=*$")


def passed_count(stdout: str, junit: Path) -> int:
    """Tests that passed, by both pytest's final summary line and the JUnit report; the smaller wins, and anything
    unreadable counts 0 (which fails). Text elsewhere in the output (a header saying "7 passed") never counts."""
    summary = [line for line in stdout.splitlines() if SUMMARY.match(line.strip())]
    by_line = re.search(r"(\d+) passed", summary[-1]) if summary else None
    try:
        cases = ElementTree.parse(junit).getroot().iter("testcase")
        by_junit = sum(1 for c in cases if not any(x.tag in ("skipped", "failure", "error") for x in c))
    except (OSError, ElementTree.ParseError):
        by_junit = 0
    return min(int(by_line.group(1)) if by_line else 0, by_junit)


def _run(
    name: str, cmd: list[str], allow_empty: bool = False, cwd: Path | None = None, checked: int | None = None
) -> Ran:
    ran = Ran(name, allow_empty=allow_empty)
    with tempfile.TemporaryDirectory(prefix="gate-junit-") as tmp:
        junit = Path(tmp) / "junit.xml"
        full = [*cmd, f"--junitxml={junit}"] if "pytest" in cmd else cmd
        out = subprocess.run(full, cwd=cwd or root(), capture_output=True, text=True, check=False)
        # count what ran; an unreadable summary counts 0, which fails (never a silent pass)
        if "pytest" in cmd:
            ran.checked = passed_count(out.stdout, junit)
        else:
            ran.checked = len(checks.tracked("*.py")) if checked is None else checked
    text = (out.stdout + out.stderr).strip()
    if out.returncode == 5 and "pytest" in cmd:
        ran.problems.append("pytest collected no tests (exit 5): a gate that ran nothing does not pass")
    elif out.returncode == 0 and "pytest" in cmd and not ran.checked:
        ran.problems.append(f"pytest exited 0 with 0 passed (all skipped or deselected?): {text.splitlines()[-1:]}")
    elif out.returncode != 0:
        log = state_dir("logs") / f"{name.split()[0]}-{int(time.time())}.log"
        log.write_text(text)
        lines = text.splitlines()
        ran.problems += lines[-FAIL_LINES:] + ([f"(full output: {log})"] if len(lines) > FAIL_LINES else [])
    return ran


def tool(name: str) -> str | None:
    """A tool's binary: the project venv's (next to this interpreter, so `uv sync` pins it), else $UV for uv, else PATH."""
    venv = Path(sys.executable).parent / name
    if venv.is_file() and os.access(venv, os.X_OK):
        return str(venv)
    return (os.environ.get("UV") if name == "uv" else None) or shutil.which(name)


def _tool_stage(name: str, binary: str, args: list[str], cwd: Path | None, checked: int) -> Ran:
    """Run a tool stage. A tool that is not installed is skipped with a notice locally, but fails in CI, where
    `uv sync --locked` must have installed it (a skipped check never passes silently where it counts)."""
    path = tool(binary)
    if path is None:
        ran = Ran(name, allow_empty=True)
        notice = f"{binary} not found (run `uv sync`); {name} skipped"
        (ran.problems if os.environ.get("CI") else ran.warnings).append(notice)
        return ran
    return _run(name, [path, *args], cwd=cwd, checked=checked)


def _workflow_files(cwd: Path | None) -> list[str]:
    base = cwd or root()
    return sorted(str(p.relative_to(base)) for p in (base / ".github/workflows").glob("*.y*ml"))


def lock_check(cwd: Path | None = None) -> Ran:
    """uv.lock matches pyproject.toml. Offline: a current lock needs no network, and a stale one fails either way."""
    return _tool_stage("uv lock --check", "uv", ["lock", "--check", "--offline"], cwd, checked=1)


def actionlint(cwd: Path | None = None) -> Ran:
    """Workflow syntax, expressions and job graph. shellcheck and pyflakes integrations are off so the result does not
    depend on what else the machine has installed (CI runners ship shellcheck, laptops may not)."""
    files = _workflow_files(cwd)
    return _tool_stage("actionlint", "actionlint", ["-shellcheck=", "-pyflakes=", *files], cwd, checked=len(files))


def zizmor(cwd: Path | None = None) -> Ran:
    """Workflow security audits (unpinned actions, credential persistence, excessive permissions, injection).
    Offline, so the result is deterministic and needs no token."""
    files = _workflow_files(cwd)
    args = ["--offline", "--no-progress", "--format", "plain", *files]
    return _tool_stage("zizmor", "zizmor", args, cwd, checked=len(files))


def pytest_cmd(paths: list[str], slow: bool = False, n: int | None = None) -> list[str]:
    n = workers() if n is None else n
    cmd = [sys.executable, "-m", "pytest", *paths]
    if n > 1:
        cmd += ["-n", str(n)]
    if not slow:
        cmd += ["-m", "not slow and not live"]
    return cmd


def affected_tests(base: str | None) -> list[str]:
    """Test files to run for the change: changed tests, and tests importing a changed module (by name)."""
    changed = set(git("diff", "--name-only", base or "HEAD", check=False).splitlines())
    changed |= set(git("ls-files", "--others", "--exclude-standard").splitlines())
    tests = {c for c in changed if c.startswith("tests/") and c.endswith(".py") and (root() / c).exists()}
    for c in changed:
        if c.endswith(".py") and not c.startswith("tests/"):
            module = c[:-3].replace("/", ".")
            hits = git(
                "grep",
                "-l",
                "-E",
                rf"(import|from) {module.replace('.', r'\.')}\b|from {module.rsplit('.', 1)[0]} import .*\b{module.rsplit('.', 1)[-1]}\b",
                "--",
                "tests",
                check=False,
            )
            tests |= set(hits.splitlines())
    if any(c in {"pyproject.toml", "uv.lock"} or c.startswith("tests/conftest") for c in changed):
        return ["tests"]
    return sorted(tests)


# Files outside the tree that Python can import. __pycache__ is left out: check() re-runs itself under a fresh per-run
# PYTHONPYCACHEPREFIX, so neither the gate nor its tests read any __pycache__ in the tree.
IMPORTABLE = ("*.py", "*.pyc", "*.pyo", "*.pth", "*.so")


def tree_key(base: str | None, repo: Path | None = None) -> str | None:
    """What a cached pass depends on: the committed tree, the ratchet base, the Python version and every ignored
    importable file outside .venv (by content). None when the worktree is dirty, untracked files included."""
    if git("status", "--porcelain", "--untracked-files=all", cwd=repo):
        return None
    top = Path(git("rev-parse", "--show-toplevel", cwd=repo))
    ignored = git(
        "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", *IMPORTABLE, ":(exclude).venv", cwd=repo
    )
    extra = sorted(
        (f, hashlib.sha256((top / f).read_bytes()).hexdigest())
        for f in ignored.split("\0")
        if f and "__pycache__" not in PurePosixPath(f).parts and (top / f).is_file()
    )
    parts = [git("rev-parse", "HEAD^{tree}", cwd=repo), base, sys.version, extra]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:40]


@contextlib.contextmanager
def gate_lock(path: Path, on_wait: Callable[[], None] = lambda: None) -> Iterator[None]:
    """One gate per machine. Opened with "a+" (never truncating) so a waiting gate does not erase the holder's pid."""
    with open(path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            on_wait()
            fcntl.flock(lock, fcntl.LOCK_EX)
        lock.seek(0)
        lock.truncate()
        lock.write(f"{os.getpid()} {root()}\n")
        lock.flush()
        yield


def stages(full: bool, commands: dict[str, set[str]], base: str | None) -> list[Callable[[], Ran]]:
    return [
        checks.check_surface,
        checks.check_settings,
        checks.check_links,
        lambda: checks.check_dev_commands(commands),
        checks.check_research_notes,
        lambda: checks.check_ratchets(base),
        lambda: checks.check_generated(docs.render()),
        lambda: _run("ruff lint", ["ruff", "check", "--quiet", "--output-format", "concise", "."]),
        lambda: _run("ruff format", ["ruff", "format", "--check", "--quiet", "--output-format", "concise", "."]),
        lock_check,
        actionlint,
        zizmor,
        lambda: _run("tests", pytest_cmd(["tests"], slow=full)),
    ]


CLEAN_FLAG = "DEVKIT_GATE_PYCACHE"


def clean_bytecode() -> bool:
    """True when this process imports bytecode only from its own fresh per-run prefix (set up by check)."""
    return bool(sys.pycache_prefix) and os.environ.get(CLEAN_FLAG) == sys.pycache_prefix


def check(commands: dict[str, set[str]], full: bool = False, use_cache: bool = True) -> int:
    if not clean_bytecode():
        # This process has already imported devkit, possibly from a planted __pycache__. Re-run the gate in a fresh
        # interpreter whose bytecode (and every test subprocess's) lives in a new empty dir, never in the tree.
        with tempfile.TemporaryDirectory(prefix="gate-pycache-") as prefix:
            env = os.environ | {"PYTHONPYCACHEPREFIX": prefix, CLEAN_FLAG: prefix}
            cmd = [sys.executable, "-c", "from devkit.cli import main; main()", *sys.argv[1:]]
            return subprocess.run(cmd, cwd=root(), env=env, check=False).returncode
    base = checks.merge_base()
    key = tree_key(base)
    record = state_dir("gate") / f"{key}{'-full' if full else ''}.json" if key else None
    if use_cache and record and record.exists():
        print(f"RESULT: passed (cached for key {key[:10]}, {json.loads(record.read_text())['when']})")
        return 0

    def waiting() -> None:
        print("gate: another gate runs on this machine; waiting for it ...", flush=True)

    with gate_lock(state_dir() / "gate.lock", on_wait=waiting):
        start = time.time()
        failed = 0
        for stage in stages(full, commands, base):
            ran = stage()
            print(ran.line(), flush=True)
            for p in ran.problems[:FAIL_LINES]:
                print(f"     {p}")
            for w in ran.warnings[:10]:
                print(f"     warning: {w}")
            failed += not ran.ok
        took = time.time() - start
    if failed:
        print(f"RESULT: failed ({failed} stage(s), {took:.0f} s)")
        return 1
    if record:
        record.write_text(
            json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(took), "root": str(root())})
        )
    print(f"RESULT: passed ({took:.0f} s)")
    return 0


def test(paths: list[str], affected: bool, slow: bool, n: int | None) -> int:
    if affected:
        paths = affected_tests(checks.merge_base())
        if not paths:
            print("no affected tests for this change (nothing to run); the gate still runs everything")
            return 0
        print(f"affected: {' '.join(paths)}")
    cmd = pytest_cmd(paths or ["tests"], slow=slow, n=n)
    code = subprocess.run(cmd, cwd=root(), check=False).returncode
    if code == 5:
        print("pytest collected no tests (exit 5): check the paths/markers", file=sys.stderr)
        return 1
    return code
