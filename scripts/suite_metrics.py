#!/usr/bin/env python3
"""Measure a Python repository's test suite without changing it (agent-harness #112).

Static part (stdlib only, reads tracked files through git):
  - test and code LOC (non-blank, non-comment lines of tracked .py files);
  - test functions, parametrize cases, markers, fixtures and I/O signals per test;
  - oracles per test: `assert` statements, pytest.raises/warns, mock `assert_*` calls, calls to helpers that assert;
  - tests with no oracle, tests whose only oracle is a mock-call check;
  - clone groups: type-1 (identical AST) and type-2 (identical AST shape, names and literals abstracted);
  - git history: fix commits, and whether each one touched or added tests.
Optional:
  --collect   `pytest --collect-only` in the repo's own environment (no sync, no cache, no bytecode);
  --run       the suite itself with --durations (only for suites you know are cheap and side-effect free);
  --ci        GitHub Actions history through `gh run list` (conclusions, durations, reruns, red main).

Usage: python scripts/suite_metrics.py REPO [--tests DIR ...] [--code DIR ...] [--collect] [--run] [--ci]
       [--json OUT]. Output is a JSON document on stdout (or OUT) plus a short text summary on stderr.
The I/O and kind classification is a heuristic over call names in the test, its fixtures and its helpers
(transitively, first definition wins on a name clash); read the counts as signals. "net" is noisy (fakes and mocked
openers match), so only markers make a test e2e/large. Hand-checked precision: see the r1 scratch note of #112.
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import hashlib
import itertools
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

MOCK_ASSERTS = {
    "assert_called",
    "assert_called_once",
    "assert_called_with",
    "assert_called_once_with",
    "assert_any_call",
    "assert_has_calls",
    "assert_not_called",
    "assert_awaited",
    "assert_awaited_once",
    "assert_awaited_with",
    "assert_awaited_once_with",
    "assert_any_await",
    "assert_has_awaits",
    "assert_not_awaited",
}
ORACLE_CALL_PREFIXES = ("assert", "_assert", "check", "_check", "expect", "_expect", "verify", "_verify")
DOUBLES = {"patch", "Mock", "MagicMock", "AsyncMock", "create_autospec", "PropertyMock", "setattr", "setitem"}
RAISES = {"raises", "warns", "deprecated_call", "fail"}
FS_FIXTURES = {"tmp_path", "tmpdir", "tmp_path_factory", "tmpdir_factory"}
FS_CALLS = {
    "open",
    "write_text",
    "read_text",
    "write_bytes",
    "read_bytes",
    "mkdir",
    "unlink",
    "rmtree",
    "copy",
    "copytree",
    "touch",
    "chdir",
}
PROC_CALLS = {
    "run",
    "check_output",
    "check_call",
    "Popen",
    "call",
    "system",
    "invoke",
    "create_subprocess_exec",
    "create_subprocess_shell",
}
NET_NAMES = {"requests", "httpx", "urllib", "socket", "aiohttp", "urlopen", "websockets"}
DB_HINTS = re.compile(r"(^|_)(db|database|session|engine|postgres|sqlite|conn|connection)($|_)", re.I)
FIX_SUBJECT = re.compile(r"(^fix(\(|:|\b))|\bfix(es|ed)?\b|\bbug\b|\bregression\b|\bhotfix\b", re.I)
E2E_MARKERS = {"e2e", "live", "soak", "system", "acceptance", "smoke", "scale"}
SLOW_MARKERS = {"slow", "perf", "benchmark", "scale", "soak"}
NEAR_DUP_JACCARD = 0.8  # same-file pairs above this read as copy-paste-edit; parametrize candidates
MIN_CLONE_NODES = 40
PYTEST = [
    "uv",
    "run",
    "--no-sync",
    "--frozen",
    "pytest",
]  # --python swaps in another environment's interpreter  # type-2 clones smaller than this are too short to mean anything


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def sloc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def default_testpaths(repo: Path) -> list[str]:
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text())
        return list(data["tool"]["pytest"]["ini_options"]["testpaths"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return ["tests"]


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


@dataclass
class TestInfo:
    file: str
    name: str
    line: int
    loc: int
    asserts: int = 0
    raises: int = 0
    mock_asserts: int = 0
    helper_oracles: int = 0
    cases: int = 1
    markers: list[str] = field(default_factory=list)
    io: list[str] = field(default_factory=list)
    flake_risk: list[str] = field(default_factory=list)
    shape: str = ""
    exact: str = ""
    nodes: int = 0
    toks: frozenset = frozenset()

    @property
    def oracles(self) -> int:
        return self.asserts + self.raises + self.mock_asserts + self.helper_oracles

    @property
    def kind(self) -> str:
        if set(self.markers) & E2E_MARKERS:  # names alone are too noisy for net (fakes, mocked openers)
            return "e2e/large"
        if "proc" in self.io:
            return "process"
        if {"fs", "db"} & set(self.io):
            return "io"
        return "pure"


class Abstract(ast.NodeTransformer):
    """Replace identifiers and literals so two tests with the same structure hash the same (type-2 clones)."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        node.attr = "_"
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = "_"
        node.annotation = None
        return node

    def visit_keyword(self, node: ast.keyword) -> ast.AST:
        self.generic_visit(node)
        node.arg = "_" if node.arg else None
        return node


def body_without_docstring(fn: ast.AST) -> list[ast.stmt]:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append(dotted(node.func) + "()")
    return ".".join(reversed(parts))


def marker_names(decorators: list[ast.expr]) -> tuple[list[str], int]:
    names, cases = [], 1
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        path = dotted(target)
        if ".mark." in path or path.startswith("mark."):
            mark = path.split(".mark.", 1)[-1].split(".")[0] if ".mark." in path else path.split(".")[1]
            names.append(mark)
            if mark == "parametrize" and isinstance(dec, ast.Call) and len(dec.args) >= 2:
                values = dec.args[1]
                if isinstance(values, (ast.List, ast.Tuple)):
                    cases *= max(1, len(values.elts))
        elif path.endswith("given"):
            names.append("hypothesis")
    return names, cases


def asserting_helpers(trees: dict[str, ast.Module]) -> set[str]:
    """Names of non-test functions (any test file, conftest, helper module) that assert, directly or via another."""
    defs: dict[str, ast.AST] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test"):
                defs.setdefault(node.name, node)
    found = {
        n
        for n, fn in defs.items()
        if any(isinstance(x, ast.Assert) for x in ast.walk(fn))
        or any(isinstance(x, ast.Raise) for x in ast.walk(fn))
        or n.startswith(ORACLE_CALL_PREFIXES)
    }
    changed = True
    while changed:
        changed = False
        for n, fn in defs.items():
            if n in found:
                continue
            for x in ast.walk(fn):
                if isinstance(x, ast.Call) and dotted(x.func).split(".")[-1] in found:
                    found.add(n)
                    changed = True
                    break
    return found


def call_effects(path: str) -> set[str]:
    """I/O signals of one call, by its dotted name: fs, proc (a subprocess), net, double (a mock or patch)."""
    last = path.split(".")[-1]
    head = path.split(".")[0]
    io: set[str] = set()
    if last in FS_CALLS:
        io.add("fs")
    if (
        (head == "subprocess" and last in PROC_CALLS)
        or last in {"Popen", "check_output", "check_call", "create_subprocess_exec", "create_subprocess_shell"}
        or path == "os.system"
        or (last == "invoke" and head in {"runner", "cli_runner", "CliRunner"})
    ):
        io.add("proc")
    if head in NET_NAMES or last in NET_NAMES:
        io.add("net")
    if last in DOUBLES or head == "mocker":
        io.add("double")
    return io


def helper_io(trees: dict[str, ast.Module]) -> dict[str, set[str]]:
    """I/O signals of every non-test function and fixture in the test tree, through the helpers it calls."""
    defs: dict[str, ast.AST] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not is_test_def(node):
                defs.setdefault(node.name, node)
    calls = {n: [dotted(x.func) for x in ast.walk(fn) if isinstance(x, ast.Call)] for n, fn in defs.items()}
    io = {n: set().union(*(call_effects(c) for c in cs)) if cs else set() for n, cs in calls.items()}
    for n, fn in defs.items():
        args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        if args & FS_FIXTURES:
            io[n].add("fs")
    changed = True
    while changed:
        changed = False
        for n, cs in calls.items():
            extra = set().union(*(io.get(c.split(".")[-1], set()) for c in cs)) if cs else set()
            args = {a.arg for a in defs[n].args.args + defs[n].args.kwonlyargs}
            extra |= set().union(*(io.get(a, set()) for a in args)) if args else set()
            if not extra <= io[n]:
                io[n] |= extra
                changed = True
    return io


def analyse_test(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    rel: str,
    inherited: list[str],
    oracle_helpers: set[str],
    source_lines: list[str],
    helper_io: dict[str, set[str]],
) -> TestInfo:
    end = getattr(fn, "end_lineno", fn.lineno)
    loc = sloc("\n".join(source_lines[fn.lineno - 1 : end]))
    marks, cases = marker_names(fn.decorator_list)
    info = TestInfo(
        file=rel, name=fn.name, line=fn.lineno, loc=loc, cases=cases, markers=sorted(set(marks + inherited))
    )
    io: set[str] = set()
    risk: set[str] = set()
    args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    if args & FS_FIXTURES:
        io.add("fs")
    if any(DB_HINTS.search(a) for a in args):
        io.add("db")
    if args & {"monkeypatch", "mocker"}:
        io.add("double")
    for a in args:  # a fixture's own I/O, through the helpers it calls
        io |= helper_io.get(a, set())
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            info.asserts += 1
        elif isinstance(node, ast.Call):
            path = dotted(node.func)
            last = path.split(".")[-1]
            head = path.split(".")[0]
            if last in RAISES and head in {"pytest", "raises", "warns", "fail"}:
                info.raises += 1
            elif last in MOCK_ASSERTS:
                info.mock_asserts += 1
            elif (
                last.startswith(ORACLE_CALL_PREFIXES)
                or last in oracle_helpers
                or (head == "self" and last.startswith("assert"))
            ):
                info.helper_oracles += 1
            io |= call_effects(path) | helper_io.get(last, set())
            if last == "sleep":
                risk.add("sleep")
            if path in {"datetime.now", "datetime.datetime.now", "time.time", "datetime.utcnow", "date.today"}:
                risk.add("wall-clock")
            if head == "random" and last not in {"seed", "Random"}:
                risk.add("unseeded-random")
        elif isinstance(node, ast.Raise) and node.exc is not None and "fail" in dotted(node.exc).lower():
            info.raises += 1
    info.io = sorted(io)
    info.flake_risk = sorted(risk)
    body = body_without_docstring(fn)
    module = ast.Module(body=body, type_ignores=[])
    info.exact = hashlib.sha256(ast.dump(module, annotate_fields=False).encode()).hexdigest()[:12]
    abstract = Abstract().visit(ast.parse(ast.unparse(module)))
    info.shape = hashlib.sha256(ast.dump(abstract, annotate_fields=False).encode()).hexdigest()[:12]
    info.nodes = sum(1 for _ in ast.walk(module))
    words = re.findall(r"\w+|[^\w\s]", ast.unparse(abstract))
    info.toks = frozenset(tuple(words[i : i + 4]) for i in range(max(0, len(words) - 3)))
    return info


def is_test_def(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A test function, not a fixture that happens to be named test_*."""
    return node.name.startswith("test") and not any(
        "fixture" in dotted(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list
    )


def near_duplicates(tests: list[TestInfo], threshold: float) -> list[tuple[TestInfo, TestInfo, float]]:
    """Pairs in one file whose abstracted 4-gram sets overlap by Jaccard >= threshold (copy-paste-edit tests)."""
    by_file: dict[str, list[TestInfo]] = collections.defaultdict(list)
    for t in tests:
        if t.nodes >= MIN_CLONE_NODES and t.toks:
            by_file[t.file].append(t)
    pairs = []
    for group in by_file.values():
        for a, b in itertools.combinations(group, 2):
            if a.shape == b.shape:
                continue
            j = len(a.toks & b.toks) / len(a.toks | b.toks)
            if j >= threshold:
                pairs.append((a, b, round(j, 2)))
    return pairs


def module_markers(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            vals = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            return marker_names(vals)[0]
    return []


def static(repo: Path, test_dirs: list[str], code_dirs: list[str] | None) -> dict:
    tracked = [p for p in git(repo, "ls-files", "*.py").splitlines() if (repo / p).is_file()]
    in_tests = [p for p in tracked if any(p == d or p.startswith(d.rstrip("/") + "/") for d in test_dirs)]
    test_files = [p for p in in_tests if is_test_file(p)]
    support = [p for p in in_tests if p not in test_files]
    if code_dirs:
        code = [p for p in tracked if any(p.startswith(d.rstrip("/") + "/") for d in code_dirs)]
    else:
        code = [
            p for p in tracked if p not in in_tests and not is_test_file(p) and p.rsplit("/", 1)[-1] != "conftest.py"
        ]
    stray = [p for p in tracked if is_test_file(p) and p not in in_tests]
    texts = {p: (repo / p).read_text(errors="replace") for p in set(in_tests) | set(code)}
    trees = {}
    for p in in_tests:
        with contextlib.suppress(SyntaxError):
            trees[p] = ast.parse(texts[p])
    helpers = asserting_helpers(trees)
    effects = helper_io(trees)
    tests: list[TestInfo] = []
    fixtures = 0
    for p in test_files + [s for s in support if s.endswith("conftest.py")]:
        tree = trees.get(p)
        if tree is None:
            continue
        lines = texts[p].splitlines()
        inherited = module_markers(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                "fixture" in dotted(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list
            ):
                fixtures += 1
        if p not in test_files:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_test_def(node):
                tests.append(analyse_test(node, p, inherited, helpers, lines, effects))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                cls_marks = inherited + marker_names(node.decorator_list)[0]
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_test_def(sub):
                        tests.append(analyse_test(sub, p, cls_marks, helpers, lines, effects))
    test_loc = sum(sloc(texts[p]) for p in in_tests)
    code_loc = sum(sloc(texts[p]) for p in code)
    by_dir = collections.Counter()
    for p in code:
        by_dir[p.split("/")[0] if "/" in p else "(root)"] += sloc(texts[p])
    n = len(tests)
    oracle_free = [t for t in tests if t.oracles == 0]
    mock_only = [t for t in tests if t.mock_asserts and not (t.asserts + t.raises + t.helper_oracles)]
    exact_groups = [g for g in _groups(tests, "exact") if g[0].nodes >= 10]
    shape_groups = [g for g in _groups(tests, "shape") if g[0].nodes >= MIN_CLONE_NODES]
    same_file_shape = [g for g in shape_groups if len({t.file for t in g}) == 1]
    near = near_duplicates(tests, NEAR_DUP_JACCARD)
    near_tests = {(t.file, t.name) for a, b, _ in near for t in (a, b)}
    markers = collections.Counter(m for t in tests for m in t.markers)
    kinds = collections.Counter(t.kind for t in tests)
    io = collections.Counter(i for t in tests for i in t.io)
    risk = collections.Counter(r for t in tests for r in t.flake_risk)
    oracles = [t.oracles for t in tests]
    return {
        "files": {
            "test_files": len(test_files),
            "support_files": len(support),
            "code_files": len(code),
            "stray_test_files_outside_testpaths": stray[:20],
            "stray_count": len(stray),
        },
        "loc": {
            "test": test_loc,
            "code": code_loc,
            "test_to_code": round(test_loc / code_loc, 2) if code_loc else None,
            "code_by_top_dir": dict(by_dir.most_common(12)),
        },
        "tests": {
            "functions": n,
            "cases_static_estimate": sum(t.cases for t in tests),
            "fixtures_defined": fixtures,
            "loc_per_test_median": statistics.median([t.loc for t in tests]) if tests else 0,
            "oracles_per_test": _dist(oracles),
            "asserts_per_test": _dist([t.asserts for t in tests]),
            "oracle_free": len(oracle_free),
            "oracle_free_pct": _pct(len(oracle_free), n),
            "oracle_free_examples": [f"{t.file}::{t.name}" for t in oracle_free[:12]],
            "mock_only": len(mock_only),
            "mock_only_pct": _pct(len(mock_only), n),
            "mock_only_examples": [f"{t.file}::{t.name}" for t in mock_only[:8]],
            "uses_mock_asserts": sum(1 for t in tests if t.mock_asserts),
            "uses_raises": sum(1 for t in tests if t.raises),
            "kinds_heuristic": dict(kinds),
            "io_signals": dict(io),
            "flake_risk_signals": dict(risk),
            "markers": dict(markers.most_common()),
            "clones_type1_groups": len(exact_groups),
            "clones_type1_tests": sum(len(g) for g in exact_groups),
            "clones_type1_examples": [[f"{t.file}::{t.name}" for t in g[:3]] for g in exact_groups[:5]],
            "clones_type2_groups": len(shape_groups),
            "clones_type2_tests": sum(len(g) for g in shape_groups),
            "clones_type2_tests_pct": _pct(sum(len(g) for g in shape_groups), n),
            "clones_type2_same_file_groups": len(same_file_shape),
            "near_dup_pairs_same_file": len(near),
            "near_dup_tests": len(near_tests),
            "near_dup_tests_pct": _pct(len(near_tests), n),
            "near_dup_examples": [f"{a.file}::{a.name} ~ {b.name} ({j})" for a, b, j in near[:6]],
            "uses_test_doubles": sum(1 for t in tests if "double" in t.io),
            "clones_type2_examples": [
                [f"{t.file}::{t.name}" for t in g[:3]] for g in sorted(shape_groups, key=len, reverse=True)[:5]
            ],
        },
        "_tests": [
            {k: v for k, v in asdict(t).items() if k != "toks"} | {"kind": t.kind, "oracles": t.oracles} for t in tests
        ],
    }


def _groups(tests: list[TestInfo], key: str) -> list[list[TestInfo]]:
    buckets: dict[str, list[TestInfo]] = collections.defaultdict(list)
    for t in tests:
        buckets[getattr(t, key)].append(t)
    return [g for g in buckets.values() if len(g) > 1]


def _dist(xs: list[int]) -> dict:
    if not xs:
        return {}
    s = sorted(xs)
    return {
        "mean": round(statistics.mean(s), 2),
        "median": statistics.median(s),
        "p90": s[int(0.9 * (len(s) - 1))],
        "max": s[-1],
        "zero": s.count(0),
        "one": s.count(1),
    }


def _pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def pytest_env() -> dict:
    return os.environ | {"PYTHONDONTWRITEBYTECODE": "1", "UV_NO_SYNC": "1"}


def collect(repo: Path, test_dirs: list[str]) -> dict:
    cmd = [
        *PYTEST,
        "--collect-only",
        "--verbosity=-1",
        "-p",
        "no:cacheprovider",
        *test_dirs,
    ]
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=pytest_env(), timeout=600)
    ids = [line for line in r.stdout.splitlines() if "::" in line]
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    return {
        "returncode": r.returncode,
        "collected_ids": len(ids),
        "summary": tail,
        "seconds": round(time.monotonic() - t0, 1),
        "stderr_tail": r.stderr.strip().splitlines()[-3:],
    }


def run(repo: Path, test_dirs: list[str], extra: list[str]) -> dict:
    cmd = [
        *PYTEST,
        "--verbosity=0",
        "-p",
        "no:cacheprovider",
        "--durations=0",
        "--durations-min=0",
        *extra,
        *test_dirs,
    ]
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=pytest_env(), timeout=3600)
    wall = time.monotonic() - t0
    durs = []
    for line in r.stdout.splitlines():
        m = re.match(r"^([\d.]+)s (call|setup|teardown)\s+(\S+)", line)
        if m:
            durs.append((float(m.group(1)), m.group(2), m.group(3)))
    calls = sorted([d for d in durs if d[1] == "call"], reverse=True)
    per_test = collections.Counter()
    for d, _, tid in durs:
        per_test[tid] += d
    totals = sorted(per_test.values(), reverse=True)
    return {
        "returncode": r.returncode,
        "wall_seconds": round(wall, 1),
        "summary": (r.stdout.strip().splitlines() or [""])[-1],
        "tests_timed": len(per_test),
        "sum_seconds": round(sum(totals), 1),
        "top10_share_pct": _pct(sum(totals[:10]), sum(totals)) if totals else 0,
        "median_test_seconds": round(statistics.median(totals), 4) if totals else 0,
        "slowest": [f"{d:.2f}s {tid}" for d, _, tid in calls[:8]],
    }


def history(repo: Path, test_dirs: list[str]) -> dict:
    log = git(repo, "log", "--no-merges", "--format=@@@%H%x09%s", "--name-only")
    commits = []
    for chunk in log.split("@@@")[1:]:
        head, *files = chunk.strip().splitlines()
        sha, _, subject = head.partition("\t")
        commits.append((sha, subject, [f for f in files if f]))
    added_tests: collections.Counter = collections.Counter()
    if test_dirs:
        patch = git(repo, "log", "--no-merges", "--format=@@@%H", "--unified=0", "--", *test_dirs)
        sha = None
        for line in patch.splitlines():
            if line.startswith("@@@"):
                sha = line[3:]
            elif sha and re.match(r"^\+\s*(async\s+)?def test", line):
                added_tests[sha] += 1

    def touches_tests(files: list[str]) -> bool:
        return any(any(f.startswith(d.rstrip("/") + "/") for d in test_dirs) or is_test_file(f) for f in files)

    def touches_code(files: list[str]) -> bool:
        return any(f.endswith(".py") and not touches_tests([f]) for f in files)

    fixes = [c for c in commits if FIX_SUBJECT.search(c[1])]
    code_fixes = [c for c in fixes if touches_code(c[2])]
    with_test = [c for c in code_fixes if touches_tests(c[2])]
    with_new_test = [c for c in code_fixes if added_tests.get(c[0])]
    code_commits = [c for c in commits if touches_code(c[2])]
    return {
        "commits_no_merges": len(commits),
        "commits_touching_code": len(code_commits),
        "code_commits_touching_tests_pct": _pct(sum(1 for c in code_commits if touches_tests(c[2])), len(code_commits)),
        "fix_commits": len(fixes),
        "fix_commits_touching_code": len(code_fixes),
        "code_fixes_touching_tests": len(with_test),
        "code_fixes_touching_tests_pct": _pct(len(with_test), len(code_fixes)),
        "code_fixes_adding_a_test_function": len(with_new_test),
        "code_fixes_adding_a_test_pct": _pct(len(with_new_test), len(code_fixes)),
        "code_fixes_without_test_examples": [f"{c[0][:9]} {c[1][:90]}" for c in code_fixes if c not in with_test][:12],
        "test_functions_added_total": sum(added_tests.values()),
    }


def ci(repo: Path, limit: int) -> dict:
    url = git(repo, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/](.+?)(\.git)?$", url)
    if not m:
        return {"error": f"not a GitHub remote: {url}"}
    fields = "attempt,conclusion,createdAt,databaseId,event,headBranch,headSha,startedAt,status,updatedAt,workflowName"
    r = subprocess.run(
        ["gh", "run", "list", "-R", m.group(1), "--limit", str(limit), "--json", fields], capture_output=True, text=True
    )
    if r.returncode:
        return {"error": r.stderr.strip()[:200]}
    runs = [x for x in json.loads(r.stdout) if x["status"] == "completed"]
    view = ["gh", "repo", "view", m.group(1), "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"]
    default = subprocess.run(view, capture_output=True, text=True).stdout.strip() or "main"
    out = {"repo": m.group(1), "runs_fetched": len(runs), "default_branch": default, "workflows": {}}

    def secs(x: dict) -> float:
        return (
            datetime.fromisoformat(x["updatedAt"].replace("Z", "+00:00"))
            - datetime.fromisoformat(x["startedAt"].replace("Z", "+00:00"))
        ).total_seconds()

    by_wf: dict[str, list[dict]] = collections.defaultdict(list)
    for x in runs:
        by_wf[x["workflowName"]].append(x)
    for wf, xs in by_wf.items():
        concl = collections.Counter(x["conclusion"] for x in xs)
        durations = [secs(x) for x in xs if x["conclusion"] in {"success", "failure"}]
        main = sorted(
            [x for x in xs if x["headBranch"] == default and x["event"] in {"push", "schedule"}],
            key=lambda x: x["createdAt"],
        )
        by_sha: dict[str, set] = collections.defaultdict(set)
        for x in xs:
            by_sha[x["headSha"]].add(x["conclusion"])
        mixed = sum(1 for v in by_sha.values() if {"success", "failure"} <= v)
        pr = sorted([x for x in xs if x["event"] == "pull_request"], key=lambda x: x["createdAt"])
        by_branch: dict[str, list[dict]] = collections.defaultdict(list)
        for x in pr:
            by_branch[x["headBranch"]].append(x)
        caught = sum(
            1
            for b in by_branch.values()
            for a, c in itertools.pairwise(b)
            if a["conclusion"] == "failure" and c["conclusion"] == "success" and a["headSha"] != c["headSha"]
        )
        out["workflows"][wf] = {
            "runs": len(xs),
            "conclusions": dict(concl),
            "failure_pct": _pct(concl.get("failure", 0), concl.get("failure", 0) + concl.get("success", 0)),
            "median_minutes": round(statistics.median(durations) / 60, 1) if durations else None,
            "p90_minutes": round(sorted(durations)[int(0.9 * (len(durations) - 1))] / 60, 1) if durations else None,
            "reruns_attempt_gt1": sum(1 for x in xs if x["attempt"] > 1),
            "reruns_that_passed": sum(1 for x in xs if x["attempt"] > 1 and x["conclusion"] == "success"),
            "shas_with_both_pass_and_fail": mixed,
            "default_branch_runs": len(main),
            "default_branch_failures": sum(1 for x in main if x["conclusion"] == "failure"),
            "pr_branches": len(by_branch),
            "pr_fail_then_fixed_by_new_commit": caught,
            "first": min(x["createdAt"] for x in xs)[:10],
            "last": max(x["createdAt"] for x in xs)[:10],
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--tests", nargs="*", help="test dirs (default: pyproject testpaths)")
    ap.add_argument("--code", nargs="*", help="code dirs (default: every tracked .py outside the test dirs)")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--pytest-args", default="", help="extra pytest arguments as one string, e.g. \"-m 'not live'\"")
    ap.add_argument("--python", help="run pytest with this interpreter (e.g. the original repo's venv, from a clone)")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--ci-limit", type=int, default=1000)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--keep-tests", action="store_true", help="keep the per-test rows in the JSON")
    a = ap.parse_args()
    repo = a.repo.resolve()
    if a.python:
        PYTEST[:] = [a.python, "-m", "pytest"]
    tests = a.tests or default_testpaths(repo)
    res = {
        "repo": repo.name,
        "head": git(repo, "rev-parse", "--short", "HEAD").strip(),
        "test_dirs": tests,
        "measured": datetime.now().isoformat(timespec="minutes"),
    }
    res |= static(repo, tests, a.code)
    if not a.keep_tests:
        res.pop("_tests")
    res["history"] = history(repo, tests)
    if a.collect:
        res["collect"] = collect(repo, tests)
    if a.run:
        res["run"] = run(repo, tests, shlex.split(a.pytest_args))
    if a.ci:
        res["ci"] = ci(repo, a.ci_limit)
    text = json.dumps(res, indent=1, default=str)
    if a.json:
        a.json.write_text(text)
    else:
        print(text)
    t = res["tests"]
    print(
        f"{repo.name}: {t['functions']} test functions, test/code LOC {res['loc']['test']}/{res['loc']['code']}, "
        f"oracle-free {t['oracle_free']}, mock-only {t['mock_only']}, type-2 clone tests {t['clones_type2_tests']}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
