"""`uv run dev mutants`: advisory mutation testing of a change's own lines, for hand-back and review (#134).

The design is docs/research/test-suite-design.md §7 and its round file r2-mutation-diff.md (F3, F6):
- the changed non-test lines since the merge base with `--base`, of the working tree or of `--rev`;
- "arid" lines are skipped (imports, docstrings, logging and print calls, argparse wiring, decorators, signatures):
  Google's suppression, which took the median from 77 mutants per change to 7;
- one pytest-cov run with `--cov-context=test` over the change's tests maps each line to the tests that run it;
- one mutant per line from cosmic-ray's operators (MIT; the optional extra `agent-harness[mutants]`), the family in
  the order LCR, ROR, AOR, UOI, CRP, LOOP, the operator by a stable hash of `file:line`;
- at most CAP mutants and BUDGET_S seconds, each run against only its covering tests, fastest first, `-x`;
- every run happens in throwaway clones (`git clone --shared`, plus the working tree's edits); the working tree is
  never written.
Survivors print as `file:line [family] before -> after`, then the changed lines no test runs. It is a review aid and
never fails: the exit code is 0 whatever survives, 2 only when it could not run.
"""

import ast
import hashlib
import io
import os
import queue
import re
import subprocess
import tempfile
import time
import tokenize
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from devkit import diffparse, gate
from devkit.paths import git, root

CAP = 40  # mutants per run: L's 40 took about 110 s on a 264-line branch (r2-mutation-diff F3)
BUDGET_S = 300.0  # the whole run, coverage pass included
WORKERS = 2  # clones that run mutants side by side
TEST_FILES = 12  # the coverage run's test files: the change's own tests first, then the files importing it most
SHOWN = 10  # survivors printed without --all, weakest first
IDS_PER_MUTANT = 50  # covering tests a mutant runs against: the change's own tests, then the fastest
FAMILIES = ("LCR", "ROR", "AOR", "UOI", "CRP", "LOOP")  # Google's survivability order, then constants and loops
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
ARGPARSE = {
    "add_argument",
    "add_parser",
    "add_subparsers",
    "add_argument_group",
    "add_mutually_exclusive_group",
    "set_defaults",
}
INSTALL_HINT = "dev mutants needs cosmic-ray: require `agent-harness[mutants]` (the optional extra), then `uv sync`"


@dataclass(frozen=True)
class Mutant:
    path: str
    line: int
    family: str
    operator: str
    occurrence: int
    before: str
    after: str
    mutated: str  # the whole mutated file

    def show(self) -> str:
        return f"{self.path}:{self.line} [{self.family}] {self.before} -> {self.after}"


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def family(operator: str) -> str | None:
    """The mutation family of a cosmic-ray operator name, or None for those left out: exception and decorator removal
    and the variable operators (which need arguments), and ROR's `is` targets (`==` -> `is` is mostly equivalent)."""
    op = operator.rsplit("/", 1)[-1]
    if op in ("ReplaceAndWithOr", "ReplaceOrWithAnd"):
        return "LCR"
    if op.startswith("ReplaceComparisonOperator_"):
        src, dst = op.removeprefix("ReplaceComparisonOperator_").split("_")
        return None if dst in ("Is", "IsNot") and src not in ("Is", "IsNot") else "ROR"
    if op.startswith("ReplaceBinaryOperator_"):
        return "AOR"
    if op == "AddNot" or op.startswith("ReplaceUnaryOperator_"):
        return "UOI"
    if op in ("ReplaceTrueWithFalse", "ReplaceFalseWithTrue", "NumberReplacer"):
        return "CRP"
    if op in ("ZeroIterationForLoop", "ReplaceBreakWithContinue", "ReplaceContinueWithBreak"):
        return "LOOP"
    return None


def _callee(node: ast.AST) -> tuple[str, str]:
    """(receiver text, called name) of a call: `log.info(...)` -> ("log", "info"), `print(...)` -> ("", "print")."""
    if not isinstance(node, ast.Call):
        return "", ""
    if isinstance(node.func, ast.Name):
        return "", node.func.id
    if isinstance(node.func, ast.Attribute):
        return ast.unparse(node.func.value), node.func.attr
    return "", ""


def _arid_simple(node: ast.stmt) -> bool:
    if isinstance(node, ast.Import | ast.ImportFrom | ast.Pass | ast.Global | ast.Nonlocal):
        return True
    value = node.value if isinstance(node, ast.Expr | ast.Assign | ast.AnnAssign) else None
    if isinstance(node, ast.Expr) and isinstance(value, ast.Constant):
        return True  # docstrings and `...`
    receiver, name = _callee(value) if value is not None else ("", "")
    if name == "print" and not receiver:
        return True
    if name in LOG_METHODS and re.search(r"log|warnings", receiver, re.IGNORECASE):
        return True
    return name in ARGPARSE


def units(source: str) -> dict[int, tuple[int, int, bool]]:
    """Each line inside a statement -> (first line, last line, arid) of the innermost statement holding it. A compound
    statement owns its header only (a def's or class's from its first decorator); def and class headers are arid."""
    out: dict[int, tuple[int, int, bool]] = {}
    for node in ast.walk(ast.parse(source)):  # breadth first: an inner statement overwrites its parent's lines
        if not isinstance(node, ast.stmt | ast.excepthandler):
            continue
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
            end = max(node.lineno, body[0].lineno - 1)
            arid = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        else:
            start, end = node.lineno, node.end_lineno or node.lineno
            arid = isinstance(node, ast.stmt) and _arid_simple(node)
        for n in range(start, end + 1):
            out[n] = (start, end, arid)
    return out


def arid_lines(source: str) -> set[int]:
    return {n for n, (_, _, arid) in units(source).items() if arid}


def _stable(key: str) -> int:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")


def _code(line: str) -> str:
    """A source line without its indent and trailing comment (kept whole when it does not tokenize alone)."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line.strip() + "\n").readline))
    except (tokenize.TokenError, SyntaxError):
        return line.strip()
    comment = next((t for t in tokens if t.type == tokenize.COMMENT), None)
    return line.strip()[: comment.start[1]].rstrip() if comment else line.strip()


def equivalent(node) -> bool:
    """A number whose +-1 is a tuning choice no test should pin, so its mutant is noise: a slice bound (`x[:10]`, a
    truncation) or a module-level UPPER_CASE constant (`CAP = 40`). 6 of the 11 survivors on #110's diff were these."""
    if node.type != "number":
        return False
    if node.parent.type == "subscript":
        return True
    stmt = node.parent
    target = stmt.children[0] if stmt.type == "expr_stmt" else None
    top = stmt.parent is not None and stmt.parent.parent is not None and stmt.parent.parent.type == "file_input"
    return bool(top and target is not None and target.type == "name" and target.value.isupper())


def candidates(path: str, source: str, lines: Iterable[int]) -> list[Mutant]:
    """One mutant per line of `lines` that cosmic-ray can mutate there: the first family in FAMILIES with an operator
    on that line, and among its operators the one a stable hash of `path:line` picks."""
    from cosmic_ray.ast import ast_nodes, get_ast  # the optional extra: imported only when a run needs it
    from cosmic_ray.mutating import mutate_code
    from cosmic_ray.plugins import get_operator, operator_names

    wanted = set(lines)
    ops = {name: get_operator(name)() for name in operator_names() if family(name)}
    found: dict[int, list[tuple[str, int]]] = {}  # line -> (operator, occurrence)
    count = dict.fromkeys(ops, 0)
    for node in ast_nodes(get_ast(source)):  # the order cosmic-ray's MutationVisitor counts occurrences in
        skip = node.type == "fstring" or node.search_ancestor("fstring") is not None or equivalent(node)
        for name, op in ops.items():
            for (line, _), _end in op.mutation_positions(node):
                if line in wanted and not skip:  # an f-string's fields are message text: arid
                    found.setdefault(line, []).append((name, count[name]))
                count[name] += 1
    original = source.splitlines()
    out = []
    for line in sorted(found):
        for fam in FAMILIES:
            pool = sorted(c for c in found[line] if family(c[0]) == fam)
            if not pool:
                continue
            name, occurrence = pool[_stable(f"{path}:{line}") % len(pool)]
            mutated = mutate_code(source, ops[name], occurrence)
            if mutated is None:
                continue
            try:
                compile(mutated, path, "exec")
            except SyntaxError:
                continue
            after = mutated.splitlines()
            shown = _code(after[line - 1]) if len(after) == len(original) else "(lines removed)"
            out.append(Mutant(path, line, fam, name, occurrence, _code(original[line - 1]), shown, mutated))
            break
    return out


def pick(mutants: list[Mutant], cap: int = CAP) -> tuple[list[Mutant], int]:
    """At most `cap` mutants, chosen by a stable hash of `file:line` so a capped run samples every file; the rest
    are counted, not run. Returned in file and line order."""
    ranked = sorted(mutants, key=lambda m: _stable(f"{m.path}:{m.line}"))
    chosen = sorted(ranked[:cap], key=lambda m: (m.path, m.line))
    return chosen, len(ranked) - len(chosen)


def changed_lines(diff: str, untracked: dict[str, int] | None = None) -> dict[str, set[int]]:
    """Added lines per changed non-test `.py` file of a `git diff -U0`; `untracked` maps new files to their line
    count (every line is new)."""
    out: dict[str, set[int]] = {}
    for f in diffparse.parse(diff):
        if f.new.endswith(".py") and not is_test(f.new) and f.added:
            out.setdefault(f.new, set()).update(line.number for line in f.added)
    for path, n in (untracked or {}).items():
        if path.endswith(".py") and not is_test(path) and n:
            out.setdefault(path, set()).update(range(1, n + 1))
    return out


def select_tests(sandbox: Path, changed: Iterable[str], changed_tests: Iterable[str]) -> list[str]:
    """The coverage run's test files: the change's own tests, then the test files that import the most changed
    modules (gate.affected_tests' import pattern), at most TEST_FILES."""
    own = sorted(t for t in changed_tests if t.startswith("tests/") and (sandbox / t).is_file())
    modules = [c[:-3].replace("/", ".") for c in changed]
    patterns = [
        re.compile(
            rf"(import|from) {re.escape(m)}\b|from {re.escape(m.rsplit('.', 1)[0])} import .*\b{m.rsplit('.', 1)[-1]}\b"
        )
        for m in modules
    ]
    scored = []
    for f in sorted((sandbox / "tests").rglob("test_*.py")) if (sandbox / "tests").is_dir() else []:
        rel = f.relative_to(sandbox).as_posix()
        if rel in own:
            continue
        text = f.read_text(errors="replace")
        if hits := sum(1 for p in patterns if p.search(text)):
            scored.append((-hits, rel))
    return (own + [rel for _, rel in sorted(scored)])[:TEST_FILES]


PASSED = re.compile(r"^PASSED (\S.*)$")
DURATION = re.compile(r"^([\d.]+)s (?:setup|call|teardown)\s+(\S.*)$")


def parse_run(stdout: str) -> tuple[set[str], dict[str, float]]:
    """(test ids that passed, seconds per test id) from pytest's `-rp --durations=0 --durations-min=0` output."""
    passed: set[str] = set()
    took: dict[str, float] = {}
    for raw in stdout.splitlines():
        if m := PASSED.match(raw.strip()):
            passed.add(m.group(1).strip())
        elif m := DURATION.match(raw.strip()):
            took[m.group(2).strip()] = took.get(m.group(2).strip(), 0.0) + float(m.group(1))
    return passed, took


def covering(cov_file: Path, sandbox: Path) -> dict[str, dict[int, set[str]]]:
    """path -> line -> the test ids whose run executed it ("" for lines run only at import)."""
    from coverage import CoverageData

    data = CoverageData(basename=str(cov_file))
    data.read()
    out: dict[str, dict[int, set[str]]] = {}
    for measured in data.measured_files():
        try:
            rel = Path(measured).resolve().relative_to(sandbox.resolve()).as_posix()
        except ValueError:
            continue
        by_line = data.contexts_by_lineno(measured)
        out[rel] = {n: {c.rsplit("|", 1)[0] for c in ctx} for n, ctx in by_line.items()}
    return out


def tests_for(line_map: dict[int, set[str]], unit: tuple[int, int, bool], passed: set[str]) -> set[str] | None:
    """The passing tests that run any line of `unit`; all passing tests for a line run only at import; None when no
    passing test runs it."""
    ctx: set[str] = set()
    for n in range(unit[0], unit[1] + 1):
        ctx |= line_map.get(n, set())
    if not ctx:
        return None
    named = ctx & passed
    return named or (set(passed) if "" in ctx and passed else None)


def order_ids(ids: Iterable[str], took: dict[str, float], own: set[str]) -> list[str]:
    """The first IDS_PER_MUTANT of `ids`: those in the change's own test files first (a module-level line is "run" by
    every test, and its own tests are the ones likeliest to check it), then the fastest."""
    return sorted(ids, key=lambda i: (i.split("::", 1)[0] not in own, took.get(i, 0.0), i))[:IDS_PER_MUTANT]


def _env(sandbox: Path) -> dict[str, str]:
    env, _ = gate.hermetic_env(os.environ)
    # the clone's code shadows the editable install; no bytecode, so a same-size mutant never meets a stale .pyc
    return env | {"PYTHONPATH": str(sandbox), "PYTHONDONTWRITEBYTECODE": "1", "COVERAGE_FILE": str(sandbox / ".cov")}


def make_sandbox(dest: Path, sha: str, overlay: dict[str, bytes | None]) -> None:
    """A clone of this repository at `sha` with `overlay` applied (path -> content, None deletes)."""
    git("clone", "--quiet", "--shared", "--no-checkout", str(root()), str(dest))
    git("-C", str(dest), "checkout", "--quiet", "--detach", sha)
    for rel, content in overlay.items():
        target = dest / rel
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def working_overlay() -> dict[str, bytes | None]:
    """The working tree's edits against HEAD, staged or not, and its untracked files."""
    paths = git("diff", "--name-only", "--no-renames", "HEAD", check=False).splitlines()
    paths += git("ls-files", "--others", "--exclude-standard").splitlines()
    return {p: (root() / p).read_bytes() if (root() / p).is_file() else None for p in paths if p}


def merge_base(base: str | None, tip: str) -> tuple[str, str] | None:
    """(base ref, merge-base sha) of `tip` with `base`, or with origin/main then main."""
    for ref in [base] if base else ["origin/main", "main"]:
        sha = git("merge-base", tip, ref, check=False)
        if sha:
            return ref, sha
    return None


@dataclass
class Outcome:
    mutant: Mutant
    status: str  # "killed", "survived", "timeout", "not run"
    seconds: float = 0.0


def run_mutant(m: Mutant, ids: list[str], sandbox: Path, timeout: float) -> Outcome:
    target = sandbox / m.path
    original = target.read_bytes()
    start = time.monotonic()
    try:
        target.write_text(m.mutated)
        cmd = [*gate.pytest_cmd(ids, n=1), "-x", "-q", "-p", "no:randomly", "-p", "no:cacheprovider"]
        code = subprocess.run(
            cmd, cwd=sandbox, env=_env(sandbox), capture_output=True, text=True, check=False, timeout=timeout
        ).returncode
        status = "survived" if code == 0 else "killed"
    except subprocess.TimeoutExpired:
        status = "timeout"
    finally:
        target.write_bytes(original)
    return Outcome(m, status, time.monotonic() - start)


def _ranges(lines: Iterable[int]) -> str:
    spans: list[list[int]] = []
    for n in sorted(lines):
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def run(
    base: str | None = None,
    rev: str | None = None,
    shown: int | None = SHOWN,
    cap: int = CAP,
    budget: float = BUDGET_S,
    workers: int = WORKERS,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    started = clock()
    try:
        import cosmic_ray.plugins  # noqa: F401  (the optional extra)
    except ImportError:
        print(INSTALL_HINT)
        return 2
    tip = git("rev-parse", "--verify", f"{rev or 'HEAD'}^{{commit}}", check=False)
    found = merge_base(base, tip) if tip else None
    if not found:
        print(f"dev mutants: no merge base of {rev or 'HEAD'} with {base or 'origin/main or main'}")
        return 2
    ref, mb = found
    overlay = {} if rev else working_overlay()
    diff_args = ["diff", "-U0", "--no-renames", "--no-color", mb, *([tip] if rev else []), "--", "*.py"]
    diff = git(*diff_args, check=False)
    untracked = {p: len((c or b"").decode(errors="replace").splitlines()) for p, c in overlay.items()}
    untracked = {p: n for p, n in untracked.items() if not git("ls-files", "--", p)}
    changed = changed_lines(diff, untracked)
    changed_tests = [f.new for f in diffparse.parse(diff) if is_test(f.new)] + [p for p in untracked if is_test(p)]
    head = f"dev mutants (advisory, never a gate failure): {rev or 'working tree'} against {ref} ({mb[:9]})"
    if not changed:
        print(f"{head}\nno changed non-test Python lines")
        return 0
    with tempfile.TemporaryDirectory(prefix="dev-mutants-") as tmp:
        sandboxes = [Path(tmp) / f"s{i}" for i in range(max(1, workers))]
        for sb in sandboxes:
            make_sandbox(sb, tip, overlay)
        main_sb = sandboxes[0]
        sources = {p: (main_sb / p).read_text(errors="replace") for p in changed if (main_sb / p).is_file()}
        unit_maps: dict[str, dict[int, tuple[int, int, bool]]] = {}
        for p, src in sources.items():
            try:
                unit_maps[p] = units(src)
            except SyntaxError:
                print(f"skipped {p}: does not parse")
        live = {p: {n for n in changed[p] if n in unit_maps[p] and not unit_maps[p][n][2]} for p in unit_maps}
        arid = sum(len({n for n in changed[p] if n in unit_maps[p]}) - len(live[p]) for p in unit_maps)
        tests = select_tests(main_sb, changed, changed_tests)
        print(head)
        lines = [f"{len(changed)} files, {sum(map(len, changed.values()))} changed lines, {arid} of them arid"]
        line_maps: dict[str, dict[int, set[str]]] = {}
        passed: set[str] = set()
        took: dict[str, float] = {}
        if tests:
            dirs = sorted({str(main_sb / p).rsplit("/", 1)[0] for p in unit_maps})
            cmd = [*gate.pytest_cmd(tests), *(f"--cov={d}" for d in dirs), "--cov-context=test", "--cov-report="]
            cmd += ["-p", "no:randomly", "-rp", "--durations=0", "--durations-min=0"]
            t0 = clock()
            try:
                out = subprocess.run(
                    cmd, cwd=main_sb, env=_env(main_sb), capture_output=True, text=True, check=False, timeout=budget / 2
                )
                passed, took = parse_run(out.stdout)
                if (main_sb / ".cov").exists():
                    line_maps = covering(main_sb / ".cov", main_sb)
            except subprocess.TimeoutExpired:
                lines.append(f"coverage run passed half the budget ({budget / 2:.0f} s): no mutants run")
            lines.append(f"coverage run: {len(tests)} test files, {len(passed)} passing tests, {clock() - t0:.0f} s")
        else:
            lines.append("no test file is changed or imports a changed module: nothing covers the change")
        uncovered: dict[str, list[int]] = {}
        pool: list[Mutant] = []
        ids_of: dict[tuple[str, int], list[str]] = {}
        weakness: dict[tuple[str, int], tuple[bool, int]] = {}
        for p in sorted(live):
            covered = []
            for n in sorted(live[p]):
                ids = tests_for(line_maps.get(p, {}), unit_maps[p][n], passed)
                if ids is None:
                    uncovered.setdefault(p, []).append(n)
                else:
                    covered.append(n)
                    ids_of[(p, n)] = order_ids(ids, took, set(changed_tests))
                    own = any(i.split("::", 1)[0] in changed_tests for i in ids)
                    weakness[(p, n)] = (own, len(ids))
            pool += candidates(p, sources[p], covered)
        chosen, over = pick(pool, cap)
        lines.append(
            f"{len(pool)} mutable covered lines; {len(chosen)} mutants" + (f" ({over} over the cap)" if over else "")
        )
        outcomes = _run_all(chosen, ids_of, took, sandboxes, started + budget, clock)
        print("\n".join(lines))
        print(report(outcomes, uncovered, clock() - started, weakness, shown))
    return 0


def _run_all(
    chosen: list[Mutant],
    ids_of: dict[tuple[str, int], list[str]],
    took: dict[str, float],
    sandboxes: list[Path],
    deadline: float,
    clock: Callable[[], float],
) -> list[Outcome]:
    free: queue.Queue[Path] = queue.Queue()
    for sb in sandboxes:
        free.put(sb)

    def one(m: Mutant) -> Outcome:
        sb = free.get()
        try:
            left = deadline - clock()
            ids = ids_of[(m.path, m.line)]
            if left < 5:
                return Outcome(m, "not run")
            return run_mutant(m, ids, sb, timeout=min(left, 30 + 3 * sum(took.get(i, 1.0) for i in ids)))
        finally:
            free.put(sb)

    with ThreadPoolExecutor(max_workers=len(sandboxes)) as pool:
        return list(pool.map(one, chosen))


def report(
    outcomes: list[Outcome],
    uncovered: dict[str, list[int]],
    seconds: float,
    weakness: dict[tuple[str, int], tuple[bool, int]] | None = None,
    shown: int | None = SHOWN,
) -> str:
    """The summary, then at most `shown` survivors (None: all), weakest first: on a line none of the change's own
    tests runs, then by fewest covering tests; then the changed lines no test runs."""
    by = {s: [o for o in outcomes if o.status == s] for s in ("killed", "timeout", "survived", "not run")}
    ran = len(outcomes) - len(by["not run"])
    out = [
        f"mutants: {ran} run, {len(by['killed'])} killed, {len(by['timeout'])} timed out, "
        f"{len(by['survived'])} survived" + (f", {len(by['not run'])} not run (time budget)" if by["not run"] else "")
    ]
    if by["survived"]:
        weak = weakness or {}
        ranked = sorted(
            by["survived"],
            key=lambda o: (*weak.get((o.mutant.path, o.mutant.line), (False, 0)), o.mutant.path, o.mutant.line),
        )
        out.append("survived, weakest tested first (kill each with a test, or say in the PR why it is equivalent):")
        out += [f"  {o.mutant.show()}" for o in ranked[:shown]]
        if shown is not None and len(ranked) > shown:
            out.append(f"  ... {len(ranked) - shown} more (--all)")
    if uncovered:
        out.append(f"changed lines no test runs ({sum(map(len, uncovered.values()))}):")
        out += [f"  {p}:{_ranges(ns)}" for p, ns in sorted(uncovered.items())]
    out.append(f"took {seconds:.0f} s")
    return "\n".join(out)
