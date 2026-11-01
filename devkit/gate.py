"""The gate: `uv run dev check` is the one verification entry point (CI runs the same command).

- One gate per machine at a time (a lock in the shared git dir), so parallel agents queue instead of thrashing memory;
  integrate's gate goes ahead of the queued worker gates.
- A pass is cached by tree_key: re-gating an unchanged clean tree is free; a dirty tree is never cached.
- Each stage prints one line; a failing stage prints at most FAIL_LINES of its output and saves the rest.
- A pytest run that passed nothing fails (nothing collected, everything skipped or deselected): it is not a pass.
- The [tests] floors of ratchets.toml hold the run to a passed-test count and a line coverage; diff coverage is
  printed, never gated (docs/research/test-floors-and-coverage.md).
- Each gate's test run appends per-test outcome and duration to a bounded history in the machine's state dir (CI
  uploads it as the test-history artifact), so an audit can find flaky and slow tests across runs.
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
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from pathlib import Path, PurePosixPath
from typing import IO
from xml.etree import ElementTree

from devkit import board, checks, config, docs, rules, weaken
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


def workers(slots: int | None = None, cores: int | None = None, gb: float | None = None) -> int:
    """pytest-xdist workers: half the cores, at most one per 2 GB free, shared among the gate slots (#461), so
    `slots` gates running at once take no more than one did; at least 1, at most 16."""
    slots = gate_slots() if slots is None else slots
    cores = (os.cpu_count() or 2) if cores is None else cores
    gb = free_gb() if gb is None else gb
    return max(1, min(cores // 2 // slots, int(gb // 2) // slots, 16))


# pytest's final line, e.g. "== 3 passed, 1 skipped in 0.52s ==" or "3 passed in 65.1s (0:01:05)" under -q.
SUMMARY = re.compile(r"^=*\s*(?:\d+ \w+|no tests ran).* in [\d.]+s(?: \([^)]*\))?\s*=*$")


def passed_count(stdout: str, junit: Path) -> int:
    """Tests that passed, by both pytest's final summary line and the JUnit report; the smaller wins, and anything
    unreadable counts 0 (which fails). Text elsewhere in the output (a header saying "7 passed") never counts."""
    summary = [line for line in stdout.splitlines() if SUMMARY.match(line.strip())]
    by_line = re.search(r"(\d+) passed", summary[-1]) if summary else None
    try:
        cases = ElementTree.parse(junit).getroot().iter("testcase")  # noqa: S314 - the gate's own pytest wrote it
        by_junit = sum(1 for c in cases if not any(x.tag in ("skipped", "failure", "error") for x in c))
    except (OSError, ElementTree.ParseError):
        by_junit = 0
    return min(int(by_line.group(1)) if by_line else 0, by_junit)


HISTORY_FILE = "test-history.jsonl"  # in state_dir(): machine-local, shared by worktrees, never committed
HISTORY_RUNS = 50  # runs kept: a test failing 1 run in 20 shows up within 50 runs with ~92% chance (1 - 0.95**50)
HISTORY_CLOCK = time.time
OUTCOMES = (("error", "error"), ("failure", "failed"), ("skipped", "skipped"))  # JUnit tag inside a testcase -> outcome


def record_history(junit: Path, history: Path, at: int, keep: int = HISTORY_RUNS) -> None:
    """Append one run (`at`, and per test id its outcome and seconds) from a JUnit report to a JSON-lines history,
    keeping the last `keep` runs. An unreadable report records nothing: the history is evidence, never a gate."""
    try:
        cases = list(ElementTree.parse(junit).getroot().iter("testcase"))  # noqa: S314 - the gate's own pytest wrote it
    except (OSError, ElementTree.ParseError):
        return
    tests = {}
    for case in cases:
        tags = {x.tag for x in case}
        outcome = next((o for tag, o in OUTCOMES if tag in tags), "passed")
        try:
            seconds = round(float(case.get("time", 0)), 3)
        except ValueError:
            seconds = 0.0
        tests[f"{case.get('classname', '')}::{case.get('name', '')}".lstrip(":")] = [outcome, seconds]
    lines = history.read_text().splitlines() if history.exists() else []
    lines = [*lines, json.dumps({"at": at, "tests": tests}, separators=(",", ":"))][-keep:]
    history.parent.mkdir(parents=True, exist_ok=True)
    tmp = history.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(history)


def _run(
    name: str,
    cmd: list[str],
    allow_empty: bool = False,
    cwd: Path | None = None,
    checked: int | None = None,
    env: dict[str, str] | None = None,
    history: Path | None = None,
) -> Ran:
    ran = Ran(name, allow_empty=allow_empty)
    with tempfile.TemporaryDirectory(prefix="gate-junit-") as tmp:
        junit = Path(tmp) / "junit.xml"
        full = [*cmd, f"--junitxml={junit}"] if "pytest" in cmd else cmd
        out = subprocess.run(full, cwd=cwd or root(), capture_output=True, text=True, check=False, env=env)
        # count what ran; an unreadable summary counts 0, which fails (never a silent pass)
        if "pytest" in cmd:
            ran.checked = passed_count(out.stdout, junit)
            if history is not None:
                record_history(junit, history, at=int(HISTORY_CLOCK()))
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


TEST_FLOOR_SLACK = 0.95  # suggested min_tests: this share of the passed count (one dropped test file still fails)
TEST_FLOOR_STALE = 0.90  # suggest it once the floor is below this share (the gap keeps the notice from nagging)
COVERAGE_SLACK = 2  # suggest raising min_coverage once the measured line % is this many points above it


def floor_problems(passed: int, percent: float | None, floors: dict) -> tuple[list[str], list[str]]:
    """(problems, notes) of the [tests] floors: below a floor fails; a floor the suite has outgrown gets a notice."""
    where = config.get().ratchets
    problems: list[str] = []
    notes: list[str] = []
    if (floor := floors.get("min_tests")) is not None:
        if passed < floor:
            problems.append(
                f"{passed} tests passed, below the floor min_tests = {floor} ({where}): did a test file drop out?"
            )
        elif floor < passed * TEST_FLOOR_STALE:
            notes.append(f"raise min_tests to {int(passed * TEST_FLOOR_SLACK)} ({passed} passed)")
    if (floor := floors.get("min_coverage")) is not None:
        if percent is None:
            problems.append(f"no coverage data, but {where} sets min_coverage = {floor}")
        elif percent < floor:
            problems.append(f"line coverage {percent:.1f}% is below the floor min_coverage = {floor} ({where})")
        elif percent >= floor + COVERAGE_SLACK:
            notes.append(f"raise min_coverage to {int(percent)} ({percent:.1f}% measured)")
    return problems, notes


def _ranges(lines: list[int]) -> str:
    """[3, 4, 5, 9] -> "3-5,9"."""
    spans: list[list[int]] = []
    for n in sorted(lines):
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def diff_coverage(xml: Path, base: str, cwd: Path | None = None, shown: int = 10) -> list[str]:
    """Report-only diff coverage (diff-cover): the share of this change's executable lines that a test ran, and
    where the uncovered ones are. Never a problem: a hard per-change gate buys tests written for the number."""
    binary = tool("diff-cover")
    if binary is None:
        return ["diff coverage: diff-cover not found (run `uv sync`)"]
    report = xml.parent / "diff-cover.json"
    cmd = [binary, str(xml), f"--compare-branch={base}", "--include-untracked", "-q", f"--format=json:{report}"]
    out = subprocess.run(cmd, cwd=cwd or root(), capture_output=True, text=True, check=False)
    try:
        data = json.loads(report.read_text())
    except (OSError, ValueError):
        return [f"diff coverage: not measured {(out.stderr or out.stdout).strip().splitlines()[-1:]}"]
    if not data.get("total_num_lines"):
        return ["diff coverage (report-only): no changed executable lines"]
    uncovered = [f"{f}:{_ranges(s['violation_lines'])}" for f, s in data["src_stats"].items() if s["violation_lines"]]
    line = f"diff coverage (report-only): {data['total_percent_covered']}% of {data['total_num_lines']} changed lines"
    more = f" (+{len(uncovered) - shown} files)" if len(uncovered) > shown else ""
    return [line + (f"; uncovered: {' '.join(uncovered[:shown])}{more}" if uncovered else "")]


def tests_stage(
    cmd: list[str], floors: dict, base: str | None, cwd: Path | None = None, history: Path | None = None
) -> Ran:
    """The test run, held to the [tests] floors of ratchets.toml. A min_coverage floor turns on coverage (pytest-cov,
    data and reports in a temporary directory) and the report-only diff coverage against `base`. With `history`, the
    run's per-test outcomes and durations are appended to it (record_history)."""
    with tempfile.TemporaryDirectory(prefix="gate-cov-") as tmp:
        report, xml = Path(tmp) / "coverage.json", Path(tmp) / "coverage.xml"
        percent = None
        if "min_coverage" in floors:
            cov = ["--cov", "--cov-report=", f"--cov-report=json:{report}", f"--cov-report=xml:{xml}"]
            env = os.environ | {"COVERAGE_FILE": str(Path(tmp) / ".coverage")}
            ran = _run("tests", [*cmd, *cov], cwd=cwd, env=env, history=history)
            with contextlib.suppress(OSError, ValueError, KeyError):
                percent = json.loads(report.read_text())["totals"]["percent_covered"]
        else:
            ran = _run("tests", cmd, cwd=cwd, history=history)
        if ran.problems:  # the run itself failed: its output says why, a floor message would only add noise
            return ran
        problems, ran.notes = floor_problems(ran.checked, percent, floors)
        ran.problems += problems
        if percent is not None:
            ran.notes.insert(0, f"line coverage: {percent:.1f}% (floor {floors['min_coverage']})")
            if base and xml.exists():
                ran.notes += diff_coverage(xml, base, cwd)
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
    if timeout := config.get().test_timeout_s:  # a hung test fails with pytest-timeout's message, not a hung gate
        cmd.append(f"--timeout={timeout}")
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


PRIORITY_ENV = "DEVKIT_GATE_PRIORITY"  # set by integrate: its gate takes the lock ahead of worker gates (#385)
HELD_ENV = "DEVKIT_GATE_HELD"  # a file the gate creates once it holds the lock: integrate's deadline starts there


def lock_role(env: Mapping[str, str]) -> dict:
    """gate_lock's `priority` and `held`, as integrate's environment sets them for the gate it runs."""
    return {"priority": bool(env.get(PRIORITY_ENV)), "held": Path(env[HELD_ENV]) if env.get(HELD_ENV) else None}


def take_lock_role(env: MutableMapping[str, str]) -> dict:
    """lock_role, removing both variables from `env`: the gate's stages (pytest, a nested `dev check` in a test) must
    neither inherit integrate's priority nor touch its held file."""
    role = lock_role(env)
    env.pop(PRIORITY_ENV, None)
    env.pop(HELD_ENV, None)
    return role


SLOT_POLL_S = 0.2  # with several slots, how often a waiting gate tries them all again


def gate_slots() -> int:
    """How many gates may run at once on this machine: `[tool.devkit] gate_slots` (#461)."""
    return config.get().gate_slots


def slot_paths(path: Path, slots: int) -> list[Path]:
    """The slots' lock files: `path` itself, then `<path>.1` ... `<path>.<slots-1>`."""
    return [path, *(path.with_name(f"{path.name}.{i}") for i in range(1, slots))]


def holders(path: Path) -> list[str]:
    """The pid and root of each gate holding a slot of `path` now, in slot order (any slot count, so a changed
    gate_slots still shows a gate started under the old one)."""
    found = []
    for slot in sorted(path.parent.glob(f"{path.name}*"), key=lambda p: (p != path, len(p.name), p.name)):
        if slot != path and not slot.name.removeprefix(path.name + ".").isdigit():
            continue
        with open(slot, "a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.seek(0)
                found.append(f.read().strip())
    return found


@contextlib.contextmanager
def gate_lock(
    path: Path,
    on_wait: Callable[[], None] = lambda: None,
    priority: bool = False,
    held: Path | None = None,
    slots: int = 1,
) -> Iterator[None]:
    """At most `slots` gates per machine, one per lock file of slot_paths (#461). Opened with "a+" (never truncating)
    so a waiting gate does not erase a holder's pid.
    A gate tries every slot without blocking. Worker gates that find none free queue on `<path>.wait`; its holder
    waits for a slot (blocking on the one lock file, else trying all every SLOT_POLL_S), so a newcomer cannot take a
    slot freed for a gate that queued before it. flock grants a lock in no fixed order, so a priority gate
    (integrate's) skips that queue and holds a shared lock on `<path>.priority` while it waits, and a worker gate that
    gets a slot meanwhile hands it back and waits until no priority gate does (#385): integrate takes the next free
    slot. By design, worker gates starve while integrate gates arrive back to back: a merge outranks a check.
    Once a slot is held, `held` is created: integrate's deadline counts from there, not the wait."""
    with contextlib.ExitStack() as stack:
        locks = [stack.enter_context(open(p, "a+")) for p in slot_paths(path, slots)]
        queue = stack.enter_context(open(path.with_name(path.name + ".priority"), "a+"))
        turn = None if priority else stack.enter_context(open(path.with_name(path.name + ".wait"), "a+"))
        said = False

        def say() -> None:
            nonlocal said
            if not said:
                on_wait()
                said = True

        if priority:
            fcntl.flock(queue, fcntl.LOCK_SH)
        if turn and not _try(turn):
            say()
            fcntl.flock(turn, fcntl.LOCK_EX)
        while True:
            lock = next((f for f in locks if _try(f)), None)
            if lock is None:
                say()
                lock = _wait_slot(locks)
            if priority or _no_priority_waiter(queue):
                break
            fcntl.flock(lock, fcntl.LOCK_UN)
            say()
            fcntl.flock(queue, fcntl.LOCK_EX)  # granted once no priority gate waits
            fcntl.flock(queue, fcntl.LOCK_UN)
        fcntl.flock(turn or queue, fcntl.LOCK_UN)  # the next queued worker gate's turn; or no longer a priority waiter
        lock.seek(0)
        lock.truncate()
        lock.write(f"{os.getpid()} {root()}\n")
        lock.flush()
        if held:
            held.touch()
        yield


def _try(f: IO[str]) -> bool:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _wait_slot(locks: list[IO[str]]) -> IO[str]:
    """Block until a slot is held: on the lock itself when there is one, else by trying them all every SLOT_POLL_S
    (flock cannot wait on several files at once)."""
    if len(locks) == 1:
        fcntl.flock(locks[0], fcntl.LOCK_EX)
        return locks[0]
    while True:
        time.sleep(SLOT_POLL_S)
        if lock := next((f for f in locks if _try(f)), None):
            return lock


def _no_priority_waiter(queue: IO[str]) -> bool:
    try:
        fcntl.flock(queue, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    fcntl.flock(queue, fcntl.LOCK_UN)
    return True


def stages(
    full: bool, commands: dict[str, set[str]], base: str | None, help_rows: Sequence[tuple[str, str]] = ()
) -> list[Callable[[], Ran]]:
    return [
        checks.check_surface,
        checks.check_settings,
        checks.check_links,
        lambda: checks.check_dev_commands(commands),
        checks.check_research_notes,
        checks.check_decisions,
        lambda: rules.check_rules(base),
        board.check_board,
        lambda: checks.check_ratchets(base),
        lambda: weaken.check_weakening(base),
        lambda: checks.check_installs(base),
        lambda: checks.check_generated(docs.render(help_rows)),
        lambda: _run("ruff lint", ["ruff", "check", "--quiet", "--output-format", "concise", "."]),
        lambda: _run("ruff format", ["ruff", "format", "--check", "--quiet", "--output-format", "concise", "."]),
        lock_check,
        actionlint,
        zizmor,
        lambda: tests_stage(pytest_cmd(["tests"], slow=full), _floors(), base, history=state_dir() / HISTORY_FILE),
    ]


def _floors() -> dict:
    """The [tests] floors, or none when ratchets.toml is missing or bad (the ratchets stage reports a bad one)."""
    try:
        return checks.test_floors((root() / config.get().ratchets).read_text())
    except (OSError, ValueError):
        return {}


CLEAN_FLAG = "DEVKIT_GATE_PYCACHE"
# Variables that point Python or pytest away from the project's .venv. A sourced ROS sets PYTHONPATH to its own
# site-packages, which come before the venv's, so pytest loads their entry-point plugins and fails before a test runs.
# PYTHONOPTIMIZE strips the asserts of the code under test, PYTHONWARNINGS changes the warning filters (#376).
# CI sets none of them. What a project needs belongs in pytest's `pythonpath` and `filterwarnings` options, which are
# versioned.
HOST_LEAKS = ("PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE", "PYTHONWARNINGS", "PYTEST_PLUGINS", "PYTEST_ADDOPTS")


def hermetic_env(env: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """(env without HOST_LEAKS, the ones that were set to something): what the gate and `dev test` run under."""
    return {k: v for k, v in env.items() if k not in HOST_LEAKS}, [k for k in HOST_LEAKS if env.get(k)]


def rerun_env(env: Mapping[str, str], prefix: str) -> tuple[dict[str, str], list[str]]:
    """The re-run gate's environment: hermetic_env, and bytecode only under the fresh per-run `prefix`."""
    clean, dropped = hermetic_env(env)
    return clean | {"PYTHONPYCACHEPREFIX": prefix, CLEAN_FLAG: prefix}, dropped


def _say_dropped(dropped: list[str]) -> None:
    if dropped:
        print(
            f"gate: ignoring {', '.join(dropped)} from the shell (set by ROS or similar); tests use only .venv, as in "
            "CI. Import paths the tests need go in pytest's `pythonpath` option",
            flush=True,
        )


def clean_bytecode() -> bool:
    """True when this process imports bytecode only from its own fresh per-run prefix (set up by check)."""
    return bool(sys.pycache_prefix) and os.environ.get(CLEAN_FLAG) == sys.pycache_prefix


def check(
    commands: dict[str, set[str]], help_rows: Sequence[tuple[str, str]] = (), full: bool = False, use_cache: bool = True
) -> int:
    if not clean_bytecode():
        # This process has already imported devkit, possibly from a planted __pycache__. Re-run the gate in a fresh
        # interpreter whose bytecode (and every test subprocess's) lives in a new empty dir, never in the tree.
        # The same re-run leaves out what the shell set to redirect Python (HOST_LEAKS).
        with tempfile.TemporaryDirectory(prefix="gate-pycache-") as prefix:
            env, dropped = rerun_env(os.environ, prefix)
            _say_dropped(dropped)
            cmd = [sys.executable, "-c", "from devkit.cli import main; main()", *sys.argv[1:]]
            return subprocess.run(cmd, cwd=root(), env=env, check=False).returncode
    role = take_lock_role(os.environ)  # the re-run above kept them, the stages below must not see them
    base = checks.merge_base()
    key = tree_key(base)
    record = state_dir("gate") / f"{key}{'-full' if full else ''}.json" if key else None
    if use_cache and record and record.exists():
        print(f"RESULT: passed (cached for key {key[:10]}, {json.loads(record.read_text())['when']})")
        return 0

    def waiting() -> None:
        print("gate: every gate slot on this machine is busy; waiting for one ...", flush=True)

    with gate_lock(state_dir() / "gate.lock", on_wait=waiting, slots=gate_slots(), **role):
        start = time.time()
        failed = 0
        for stage in stages(full, commands, base, help_rows):
            ran = stage()
            print(ran.line(), flush=True)
            for p in ran.problems[:FAIL_LINES]:
                print(f"     {p}")
            for w in ran.warnings[:10]:
                print(f"     warning: {w}")
            for n in ran.notes:
                print(f"     {n}")
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
    env, dropped = hermetic_env(os.environ)
    _say_dropped(dropped)
    code = subprocess.run(cmd, cwd=root(), env=env, check=False).returncode
    if code == 5:
        print("pytest collected no tests (exit 5): check the paths/markers", file=sys.stderr)
        return 1
    return code
