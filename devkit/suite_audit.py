"""Suite audit: a rare, size-tiered review of the whole test suite (#114). harness-ops:tester writes tests for one
change; this measures the suite against its size tier and files what is missing.

- `status` is the trigger `dev orient` shows: 100 first-parent commits since the last audit, the test count up 25% and
  by 50, or a tier boundary crossed. Orient also shows red CI on main and a stale test floor on every run.
- `report` runs the nine checks with the tier's thresholds, and lists the judgement items the read-only
  harness-ops:suite-auditor subagent takes (vacuous oracles, unmarked end-to-end tests). It never edits a test.
- `report --file` keeps one issue per failing check under a stable title ("Suite health: <check>"), updated rather than
  duplicated and closed when clean, and records the audit (commit, counts, tier) in the footer of the closed
  "Suite health" issue, so no state is committed.

Design, tiers and thresholds: docs/research/test-suite-design.md §9-§10 and its round file r2-suite-health.md F8; every
threshold there is BELIEVED, to recalibrate after 10 audits. Reads git objects, the gate's test history and `gh`; the
only runs are the gate suite's own, k seeded reruns in a throwaway `git clone --local` of HEAD and one in the working
tree, under the gate lock.
"""

import ast
import contextlib
import importlib.util
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from devkit import board, checks, config, gate
from devkit.paths import default_branch, state_dir

TRACKER = "Suite health"  # the closed issue whose footer records the last audit; check issues are "Suite health: x"
FOOTER = re.compile(r"<!-- suite-audit: (\{.*?\}) -->")
REF = "agent-harness docs/research/test-suite-design.md §9-§10 (size-tiered suite audit, #114)"
# Trigger (note §10 item 2), replayed over four repositories' first-parent histories.
FP_COMMITS = 100
GROWTH, GROWTH_MIN = 0.25, 50
# Tiers (note §9): the higher of collected tests and gate wall time; production SLOC when there are no tests.
TIER_TESTS, TIER_GATE_S, TIER_SLOC = (1000, 5000), (120, 600), (5000, 50000)
# Flake detection (note §9): tier S reruns the whole gate suite with 5 seeds in a fresh clone; M and L run it once
# there (for parity) and rerun only the history's suspects, 10 seeds.
RERUNS = {"S": 5, "M": 10, "L": 10}
MAX_SUSPECTS = 50
GATE_PATHS = ["tests"]  # what the gate's test stage runs (gate.stages); the full collection reads testpaths
QUARANTINE_MIN, QUARANTINE_SHARE = 3, 0.005  # cap: max(3, 0.5% of tests)
QUARANTINE_DAYS = {"S": 14, "M": 14, "L": 7}
RED_RUNS, RED_HOURS, CI_STALE_DAYS = 3, 24, 1
SLOW_TEST_S, ANY_TIER_SLOW_S = {"S": 5.0, "M": 60.0, "L": 60.0}, 60.0  # a test not marked slow, median seconds
GATE_BUDGET_S = {"S": 120, "M": 600, "L": 600}
OUTLIER_MIN = 8  # gate wall times needed before the Q3 + 1.5 IQR outlier rule applies
RATIO_FLOOR = {"S": 0.3, "M": 0.3, "L": 0.5}  # test:prod SLOC
DRIFT, ORACLE_PP = 0.10, 5.0  # a relative fall in ratio or oracles per test; a rise in the single-oracle share (pp)
FIX_SHARE = 0.8  # fix commits that touch a test
SHOWN = 20  # items per check in an issue body; the rest is a count
LINE_MAX = 500  # characters per item in an issue body
BODY_CAP = 60_000  # GitHub refuses bodies over 65,536 characters (r2-suite-health F2: a maintenance job died on it)
ORIENT_BUDGET_S = 10.0  # `dev orient` runs at SessionStart under a 45 s hook timeout
RUN_TIMEOUT_S = 1800

TEST_PATH = re.compile(r"(^|/)tests?/.*\.py$|(^|/)test_[^/]*\.py$|_test\.py$")
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def test", re.M)
NOT_CODE = ("docs/", ".scratch/")  # research scripts and scratch files are neither product nor suite
FIX_SUBJECT = re.compile(r"(^fix(\(|:|\b))|\bfix(es|ed)?\b|\bbug\b|\bregression\b|\bhotfix\b", re.I)
E2E_MARKERS = {"e2e", "live", "soak", "system", "acceptance", "smoke", "scale", "golden", "journey"}
SLOW_MARKERS = {"slow", "perf", "benchmark", "scale", "soak", "live"}
ORACLE_CALLS = {"raises", "warns", "deprecated_call"}
COLLECT_PLUGIN = (
    "import os\n\n\ndef pytest_collection_finish(session):\n"
    "    with open(os.environ['SUITE_AUDIT_IDS'], 'w') as f:\n"
    "        f.write('\\n'.join(i.nodeid for i in session.items))\n"
)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def tier_of(tests: int, gate_s: float | None = None, prod_sloc: int = 0) -> str:
    """S, M or L: the higher of the test count's and the gate wall time's tier; production SLOC when no tests."""
    if tests == 0:
        return "SML"[_band(prod_sloc, TIER_SLOC)]
    return "SML"[max(_band(tests, TIER_TESTS), _band(gate_s, TIER_GATE_S) if gate_s is not None else 0)]


def _band(x: float, bounds: tuple[int, int]) -> int:
    """0 under the lower bound, 1 from it up to the upper one, 2 over the upper one."""
    return (x >= bounds[0]) + (x > bounds[1])


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH.search(path)) and not path.startswith(NOT_CODE)


def sloc(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def py_files(repo: Path, rev: str, tests_only: bool = False) -> list[tuple[str, str]]:
    """(path, text) of every tracked .py file at `rev` outside NOT_CODE, from git objects (one `cat-file --batch`)."""
    names = [p for p in _git(repo, "ls-tree", "-r", "-z", "--name-only", rev).split("\0") if p.endswith(".py")]
    names = [p for p in names if not p.startswith(NOT_CODE) and "\n" not in p and (is_test_path(p) or not tests_only)]
    if not names:
        return []
    batch = "".join(f"{rev}:{p}\n" for p in names).encode()
    out = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"], input=batch, capture_output=True, check=True
    ).stdout
    files, i = [], 0
    for p in names:
        nl = out.index(b"\n", i)
        header, i = out[i:nl].split(), nl + 1
        if header[-1] == b"missing" or len(header) < 3:
            continue
        size = int(header[2])
        if header[1] == b"blob":
            files.append((p, out[i : i + size].decode("utf-8", "replace")))
        i += size + 1
    return files


def test_count(repo: Path, rev: str) -> int:
    """`def test` functions in test files at `rev`: the trigger's proxy for collected tests (parametrize collects
    more), cheap enough for every `dev orient` and the same at both ends of a comparison."""
    return sum(len(TEST_DEF.findall(text)) for _, text in py_files(repo, rev, tests_only=True))


# ---------------------------------------------------------------- the static scan


@dataclass
class Static:
    """What the test files say without running them, at one commit. Ids are JUnit-style (`tests.test_x::test_y`,
    parameters dropped), as the gate's test history records them."""

    tests: int = 0
    test_sloc: int = 0
    prod_sloc: int = 0
    oracles: int = 0  # asserts, pytest.raises/warns and assert* calls inside test functions
    single: int = 0  # tests with exactly one oracle
    no_oracle: list[str] = field(default_factory=list)
    e2e: int = 0
    slow: set[str] = field(default_factory=set)
    flaky: list[tuple[str, list[int]]] = field(default_factory=list)  # quarantined id, issues its marker names
    xfail: int = 0
    unparsed: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.test_sloc / self.prod_sloc if self.prod_sloc else 0.0

    @property
    def per_test(self) -> float:
        return self.oracles / self.tests if self.tests else 0.0

    @property
    def single_share(self) -> float:
        return 100 * self.single / self.tests if self.tests else 0.0

    def summary(self) -> dict:
        return {
            "tests": self.tests,
            "ratio": round(self.ratio, 2),
            "oracles_per_test": round(self.per_test, 2),
            "single_oracle_pct": round(self.single_share, 1),
        }


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _marks(nodes: Iterable[ast.expr], lines: list[str]) -> list[tuple[str, str]]:
    """(marker name, its source line) of `pytest.mark.x` / `mark.x` expressions, called or not."""
    out = []
    for d in nodes:
        name = _dotted(d.func if isinstance(d, ast.Call) else d)
        if name.startswith(("pytest.mark.", "mark.")):
            line = lines[d.lineno - 1] if 0 < d.lineno <= len(lines) else ""
            out.append((name.rsplit(".", 1)[-1], line))
    return out


def _pytestmark(body: list[ast.stmt], lines: list[str]) -> list[tuple[str, str]]:
    out = []
    for s in body:
        if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in s.targets):
            value = s.value
            out += _marks(value.elts if isinstance(value, ast.List | ast.Tuple) else [value], lines)
    return out


def oracles(fn: ast.AST) -> int:
    n = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            n += 1
        elif isinstance(node, ast.Call):
            name = _dotted(node.func).rsplit(".", 1)[-1]
            n += name in ORACLE_CALLS or name.lstrip("_").startswith("assert")
    return n


def scan(files: Iterable[tuple[str, str]]) -> Static:
    s = Static()
    for path, src in files:
        if not is_test_path(path):
            s.prod_sloc += sloc(src)
            continue
        s.test_sloc += sloc(src)
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            s.unparsed.append(path)
            s.tests += len(TEST_DEF.findall(src))
            continue
        lines = src.splitlines()
        module = path[:-3].replace("/", ".")
        inherited = _pytestmark(tree.body, lines)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
                _visit(s, node, module, inherited, lines)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                marks = inherited + _marks(node.decorator_list, lines) + _pytestmark(node.body, lines)
                for f in node.body:
                    if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef) and f.name.startswith("test"):
                        _visit(s, f, f"{module}.{node.name}", marks, lines)
    return s


def _visit(s: Static, fn: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str, inherited: list, lines: list) -> None:
    tid = f"{prefix}::{fn.name}"
    marks = inherited + _marks(fn.decorator_list, lines)
    names = {m for m, _ in marks}
    n = oracles(fn)
    s.tests += 1
    s.oracles += n
    s.single += n == 1
    if n == 0:
        s.no_oracle.append(tid)
    s.e2e += bool(names & E2E_MARKERS)
    if names & SLOW_MARKERS:
        s.slow.add(tid)
    s.xfail += "xfail" in names
    if "flaky" in names:
        where = " ".join(line for m, line in marks if m == "flaky")
        s.flaky.append((tid, [int(x) for x in re.findall(r"#(\d+)", where)]))


# ---------------------------------------------------------------- the checks (pure)


@dataclass
class Check:
    key: str
    title: str  # the issue is "Suite health: <title>"
    done: str  # the issue's DONE WHEN
    problems: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    skipped: str = ""  # why the check could not run (nothing measured is not a pass)


def base_id(tid: str) -> str:
    return tid.split("[", 1)[0]


def mixed(runs: list[dict]) -> list[str]:
    """Ids that both passed and failed (or errored) across these runs of one tree: flaky."""
    seen: dict[str, set[str]] = {}
    for run in runs:
        for tid, (outcome, _) in run.get("tests", {}).items():
            seen.setdefault(tid, set()).add("bad" if outcome in ("failed", "error") else outcome)
    return sorted(t for t, o in seen.items() if {"passed", "bad"} <= o)


def outcomes(run: dict) -> dict[str, str]:
    return {t: o for t, (o, _) in run.get("tests", {}).items()}


def check_flaky(clone_runs: list[dict], ledger: list[dict]) -> Check:
    c = Check("flaky", "flaky tests", "0 mixed outcomes in the seeded fresh-clone reruns of the next audit")
    if clone_runs:
        c.problems = [f"{t}: passed and failed in seeded runs of one tree" for t in mixed(clone_runs)]
    else:
        c.skipped = "no reruns (--quick)"
    if suspects := [t for t in mixed(ledger) if t not in set(mixed(clone_runs))]:
        c.info.append(f"{len(suspects)} id(s) passed and failed across the gate history (any tree): suspects")
        c.info += suspects[:SHOWN]
    return c


def check_parity(tree: dict[str, str] | None, clone: dict[str, str] | None, flaky: set[str]) -> Check:
    c = Check("parity", "fresh-clone parity", "every gate test has the same outcome in a fresh clone and the tree")
    if tree is None or clone is None:
        c.skipped = "no runs (--quick)"
        return c
    for t in sorted(set(tree) | set(clone)):
        a, b = tree.get(t, "absent"), clone.get(t, "absent")
        if a != b and t not in flaky and "skipped" not in (a, b):
            c.problems.append(f"{t}: {a} in the working tree, {b} in a fresh clone")
    return c


def check_quarantine(s: Static, tier: str, issues: dict[int, tuple[str, str]], now: float) -> Check:
    """`issues`: number -> (state, createdAt ISO) of the issues the `flaky` markers name."""
    cap = max(QUARANTINE_MIN, int(QUARANTINE_SHARE * s.tests))
    days = QUARANTINE_DAYS[tier]
    c = Check("quarantine", "quarantine", f"at most {cap} `flaky` markers, each naming an open issue under {days} days")
    if len(s.flaky) > cap:
        c.problems.append(f"{len(s.flaky)} quarantined tests, over the cap of {cap}")
    for tid, refs in s.flaky:
        if not refs:
            c.problems.append(f"{tid}: `flaky` marker names no issue (#N)")
        for n in refs:
            state, created = issues.get(n, ("unknown", ""))
            if state.upper() != "OPEN":
                c.problems.append(f"{tid}: its issue #{n} is {state.lower()}")
            elif created and (age := (now - datetime.fromisoformat(created).timestamp()) / 86400) > days:
                c.problems.append(f"{tid}: quarantined {age:.0f} days (#{n}), over {days}")
    c.info.append(f"{len(s.flaky)} quarantined")
    return c


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def check_ci(runs: list[dict] | None, newest_commit: float | None, now: float, has_workflows: bool) -> Check:
    """Red streaks and staleness on the default branch, from `gh run list` rows (r2-suite-health F5)."""
    c = Check("ci", "red CI on main", "no workflow on main red 3 runs or 24 h, no scheduled job red, CI current")
    if runs is None:
        c.skipped = "gh run list unavailable"
        return c
    done = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") in ("success", "failure")]
    if has_workflows and not done:
        c.problems.append("no completed CI run on main: are Actions disabled?")
        return c
    by: dict[str, list[dict]] = {}
    for r in sorted(done, key=lambda r: r["createdAt"]):
        by.setdefault(r["workflowName"], []).append(r)
    for name, rs in sorted(by.items()):
        streak = 0
        for r in reversed(rs):
            if r["conclusion"] != "failure":
                break
            streak += 1
        hours = (now - _ts(rs[-streak]["createdAt"])) / 3600 if streak else 0.0
        if streak >= RED_RUNS or hours > RED_HOURS:
            c.problems.append(f"{name}: red for the last {streak} run(s), {hours:.0f} h")
        elif (sched := [r for r in rs if r.get("event") == "schedule"]) and sched[-1]["conclusion"] == "failure":
            c.problems.append(f"{name}: its last scheduled run is red")
        c.info.append(f"{name}: {len(rs)} run(s), {sum(r['conclusion'] == 'failure' for r in rs)} red")
    if done and newest_commit is not None:
        last = max(_ts(r["createdAt"]) for r in done)
        if newest_commit - last > CI_STALE_DAYS * 86400:
            c.problems.append(f"the last CI run on main is {(newest_commit - last) / 86400:.1f} days older than main")
    return c


def check_never_run(
    collected: set[str],
    ran: set[str],
    outside: list[str],
    xfail: int,
    strict: bool,
    ci_skipped: list[str],
    was: int | None,
) -> Check:
    """`was`: tests skipped in CI at the last audit (None: no audit recorded, so no rise can be seen yet)."""
    c = Check(
        "never_run", "never-run tests", "every collected test runs in the gate or a CI job; none outside testpaths"
    )
    c.problems += [f"{t}: deselected by the gate and run by no CI job" for t in sorted(collected - ran)]
    c.problems += [f"{p}: a test file outside testpaths (never collected)" for p in outside]
    if xfail and not strict:
        c.problems.append(f"{xfail} xfail test(s) with xfail_strict off: an unexpected pass is silent")
    if ci_skipped:
        c.info.append(f"{len(ci_skipped)} test(s) skipped in the latest CI history but passing here")
        c.info += ci_skipped[:SHOWN]
        if was is not None and len(ci_skipped) > was:
            c.problems.append(f"tests skipped in CI rose from {was} to {len(ci_skipped)}")
    return c


def median_seconds(runs: list[dict]) -> dict[str, float]:
    per: dict[str, list[float]] = {}
    for run in runs:
        for tid, (outcome, secs) in run.get("tests", {}).items():
            if outcome == "passed":
                per.setdefault(tid, []).append(float(secs))
    return {t: statistics.median(xs) for t, xs in per.items()}


def check_slow(runs: list[dict], slow: set[str], tier: str, gate_s: list[float]) -> Check:
    limit = SLOW_TEST_S[tier]
    c = Check("slow", "slow tests", f"no test outside the slow markers over {limit:g} s; the gate within its budget")
    if not runs and not gate_s:
        c.skipped = "no test history and no gate record on this machine"
        return c
    med = median_seconds(runs)
    for t, secs in sorted(med.items(), key=lambda kv: -kv[1]):
        if base_id(t) not in slow and secs > min(limit, ANY_TIER_SLOW_S):
            c.problems.append(f"{t}: median {secs:.1f} s, not marked slow (limit {limit:g} s)")
    if gate_s:
        if gate_s[-1] > GATE_BUDGET_S[tier]:
            c.problems.append(f"gate took {gate_s[-1]:.0f} s, over the tier {tier} budget of {GATE_BUDGET_S[tier]} s")
        if len(gate_s) > OUTLIER_MIN:
            q1, _, q3 = statistics.quantiles(gate_s[:-1], n=4)
            if gate_s[-1] > q3 + 1.5 * (q3 - q1):
                c.problems.append(
                    f"gate took {gate_s[-1]:.0f} s, an outlier over Q3 + 1.5 IQR ({q3 + 1.5 * (q3 - q1):.0f} s)"
                )
        c.info.append(f"gate wall time: last {gate_s[-1]:.0f} s over {len(gate_s)} record(s)")
    if med:
        top = sorted(med.items(), key=lambda kv: -kv[1])[:5]
        c.info.append("slowest: " + ", ".join(f"{t} {s:.1f} s" for t, s in top))
    return c


def check_drift(then: Static | None, now: Static, tier: str) -> Check:
    floor = RATIO_FLOOR[tier]
    c = Check(
        "drift", "ratio and oracle drift", f"test:prod SLOC at least {floor} and no fall over 10% since the audit"
    )
    if now.prod_sloc and now.ratio < floor:
        c.problems.append(f"test:prod SLOC {now.ratio:.2f}, below the tier {tier} floor of {floor}")
    if then is not None and then.tests and now.tests:
        if then.ratio and now.ratio < then.ratio * (1 - DRIFT):
            c.problems.append(f"test:prod SLOC fell {then.ratio:.2f} -> {now.ratio:.2f} since the last audit")
        if then.per_test and now.per_test < then.per_test * (1 - DRIFT):
            c.problems.append(f"oracles per test fell {then.per_test:.2f} -> {now.per_test:.2f} since the last audit")
        if now.single_share - then.single_share > ORACLE_PP:
            c.problems.append(
                f"single-oracle share rose {then.single_share:.1f}% -> {now.single_share:.1f}% since the last audit"
            )
    c.info.append(f"now: {now.summary()}" + (f"; at the last audit: {then.summary()}" if then else ""))
    if now.unparsed:
        c.info.append(f"{len(now.unparsed)} test file(s) did not parse: {', '.join(now.unparsed[:5])}")
    return c


def check_floor(passed: int | None, floors: dict) -> Check:
    c = Check("floor", "stale test floor", "min_tests is at least 90% of the passed count")
    if passed is None or "min_tests" not in floors:
        c.skipped = "no passed count" if passed is None else "no min_tests floor"
        return c
    if passed * gate.TEST_FLOOR_STALE > floors["min_tests"]:
        c.problems.append(f"raise min_tests to {int(passed * gate.TEST_FLOOR_SLACK)} ({passed} passed)")
    return c


def check_fixes(commits: list[tuple[str, str, list[str]]]) -> Check:
    """Fix commits (by subject) that change code: at least FIX_SHARE of them touch a test."""
    c = Check("fixes", "fixes without a test", f"at least {FIX_SHARE:.0%} of code fixes since the audit touch a test")
    fixes = [x for x in commits if FIX_SUBJECT.search(x[1]) and any(_is_code(f) for f in x[2])]
    bare = [x for x in fixes if not any(is_test_path(f) for f in x[2])]
    if fixes and (len(fixes) - len(bare)) / len(fixes) < FIX_SHARE:
        c.problems += [f"{sha[:9]} {subject[:90]}" for sha, subject, _ in bare]
    c.info.append(f"{len(fixes) - len(bare)} of {len(fixes)} code fix commit(s) touch a test")
    return c


def _is_code(path: str) -> bool:
    return path.endswith(".py") and not is_test_path(path) and not path.startswith(NOT_CODE)


def judgement(s: Static) -> list[str]:
    """What only reading the tests can settle; the suite-auditor turns it into at most 5 issues."""
    out = []
    if s.no_oracle:
        out.append(f"{len(s.no_oracle)} test(s) with no assert in their body (a helper asserts, or a vacuous oracle):")
        out += [f"  {t}" for t in s.no_oracle[:SHOWN]]
    if s.tests and not s.e2e:
        out.append(
            "no test carries an end-to-end marker: is the necessary end-to-end set (note §5) unmarked or missing?"
        )
    return out


# ---------------------------------------------------------------- measuring (I/O)


def collect(repo: Path, marks: str | None, paths: list[str]) -> set[str]:
    """Node ids pytest collects (after -m deselection), through a throwaway plugin: independent of -q levels."""
    with tempfile.TemporaryDirectory(prefix="suite-audit-collect-") as tmp:
        Path(tmp, "_suite_audit_ids.py").write_text(COLLECT_PLUGIN)
        ids = Path(tmp, "ids")
        env = os.environ | {
            "SUITE_AUDIT_IDS": str(ids),
            "PYTHONPATH": os.pathsep.join([tmp, os.environ.get("PYTHONPATH", "")]),
        }
        cmd = [sys.executable, "-m", "pytest", "--collect-only", "-p", "_suite_audit_ids", "-p", "no:randomly"]
        cmd += [*(["-m", marks] if marks else []), *paths]
        out = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, check=False, timeout=600)
        if out.returncode not in (0, 5) or not ids.exists():
            raise RuntimeError(f"pytest --collect-only failed ({out.returncode}): {(out.stdout + out.stderr)[-300:]}")
        return {i for i in ids.read_text().splitlines() if i}


def gate_marks() -> str:
    """The gate's -m expression (the last -m: the first is `python -m pytest`)."""
    cmd = gate.pytest_cmd(["tests"], n=1)
    return cmd[max(i for i, a in enumerate(cmd) if a == "-m") + 1]


def ci_pytest_selections(workflows: dict[str, str]) -> tuple[bool, list[tuple[str | None, list[str]]]]:
    """(does CI run the full suite, [(-m expression, paths)] of each pytest command in the workflows)."""
    full = any(re.search(r"dev (check|test)\b[^\n]*--(full|slow)", text) for text in workflows.values())
    picks = []
    for text in workflows.values():
        for m in re.finditer(r"(?:^|[\s;&|])pytest\b([^\n]*)", text):
            try:
                args = shlex.split(m.group(1))
            except ValueError:
                continue
            marks = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "-m"), None)
            paths = [a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] != "-m")]
            picks.append((marks, paths or ["tests"]))
    return full, picks


def outside_testpaths(repo: Path, testpaths: list[str]) -> list[str]:
    """Tracked test files pytest never collects: outside testpaths, and not inside a nested project (a directory with
    its own pyproject.toml, or a Copier template's pyproject.toml.jinja), whose suite is its own."""
    files = [f for f in _git(repo, "ls-files", "-z").split("\0") if f]
    nested = {str(Path(f).parent) for f in files if Path(f).name in ("pyproject.toml", "pyproject.toml.jinja")} - {"."}
    roots = tuple(p.rstrip("/") + "/" for p in testpaths)
    return sorted(
        f
        for f in files
        if re.search(r"(^|/)test_[^/]*\.py$|_test\.py$", f)
        and not f.startswith((*roots, *NOT_CODE))
        and not any(f.startswith(n + "/") for n in nested)
    )


def read_ledger(path: Path) -> list[dict]:
    runs = []
    with contextlib.suppress(OSError):
        for line in path.read_text().splitlines():
            with contextlib.suppress(ValueError):
                if isinstance(run := json.loads(line), dict) and isinstance(run.get("tests"), dict):
                    runs.append(run)
    return runs


def gate_seconds() -> list[float]:
    """Wall times of this machine's gate passes, oldest first (the gate's cache records)."""
    out = []
    for p in sorted(state_dir("gate").glob("*.json"), key=lambda p: p.stat().st_mtime):
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            out.append(float(json.loads(p.read_text())["seconds"]))
    return out


def run_suite(
    cwd: Path, seed: int, ledger: Path, prefix: list[str], env: dict | None = None, ids: list[str] | None = None
) -> None:
    """One gate-suite run (the gate's pytest command, random order from `seed`; only `ids` when given), appended to
    `ledger`."""
    cmd = gate.pytest_cmd(ids or GATE_PATHS)
    cmd[:1] = prefix
    if importlib.util.find_spec("pytest_randomly"):
        cmd.append(f"--randomly-seed={seed}")
    with tempfile.TemporaryDirectory(prefix="suite-audit-junit-") as tmp:
        junit = Path(tmp) / "junit.xml"
        subprocess.run(
            [*cmd, f"--junitxml={junit}"], cwd=cwd, env=env, capture_output=True, check=False, timeout=RUN_TIMEOUT_S
        )
        gate.record_history(junit, ledger, at=seed, keep=10_000)


def suspects(ledger: list[dict]) -> list[str]:
    """Ids that failed or errored in any run of the gate history (any tree), newest first, at most MAX_SUSPECTS."""
    seen: dict[str, None] = {}
    for run in reversed(ledger):
        for tid, (outcome, _) in run.get("tests", {}).items():
            if outcome in ("failed", "error"):
                seen.setdefault(tid)
    return list(seen)[:MAX_SUSPECTS]


def node_id(repo: Path, tid: str) -> str | None:
    """The pytest node id of a JUnit-style id (`tests.test_x.TestC::t[1]` -> `tests/test_x.py::TestC::t[1]`), found
    by the longest dotted prefix that is a file in `repo`; None when none is (the test is gone)."""
    cls, _, name = tid.partition("::")
    parts = cls.split(".")
    for i in range(len(parts), 0, -1):
        if (repo / ("/".join(parts[:i]) + ".py")).is_file():
            return "::".join(["/".join(parts[:i]) + ".py", *parts[i:], name])
    return None


def rerun(
    repo: Path, seeds: list[int], suspect_ids: list[str] | None = None
) -> tuple[list[dict], dict[str, str] | None]:
    """(the runs in a fresh `git clone --local` of HEAD, its origin removed; the working tree's run with the first
    seed). Without `suspect_ids` (tier S) the whole gate suite runs once per seed; with them (tier M and L, note §9)
    the whole suite runs once, for parity, and only the suspects run again with each seed."""
    sha = _git(repo, "rev-parse", "HEAD").strip()
    with tempfile.TemporaryDirectory(prefix="suite-audit-") as tmp:
        clone, ledger = Path(tmp) / "clone", Path(tmp) / "clone.jsonl"
        _git(Path(tmp), "clone", "--local", "-q", "--no-checkout", str(repo), str(clone))
        _git(clone, "remote", "remove", "origin")  # nothing the suite runs can reach the real checkout through it
        _git(clone, "checkout", "-q", "--detach", sha)
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        uv = (clone / "uv.lock").exists()
        prefix = ["uv", "run", "--locked", "--quiet", "python"] if uv else [sys.executable]
        if suspect_ids is None:
            for seed in seeds:
                run_suite(clone, seed, ledger, prefix, env)
        else:
            run_suite(clone, seeds[0], ledger, prefix, env)
            if ids := [n for t in suspect_ids if (n := node_id(clone, t))]:
                for seed in seeds:
                    run_suite(clone, seed, ledger, prefix, env, ids)
        runs = read_ledger(ledger)
        tree_ledger = Path(tmp) / "tree.jsonl"
        run_suite(repo, seeds[0], tree_ledger, [sys.executable])
        tree = read_ledger(tree_ledger)
    return runs, (outcomes(tree[-1]) if tree else None)


def last_snapshot() -> tuple[int | None, dict | None]:
    """(the tracker issue's number, the footer of the last audit), or (None, None) when none is recorded."""
    search = ["--search", f'in:title "{TRACKER}"', "--json", "number,title,body", "--limit", "50"]
    rows = json.loads(board.gh("issue", "list", "-R", board.repo(), "--state", "all", *search) or "[]")
    for row in rows:
        if row["title"].strip() == TRACKER:
            m = FOOTER.search(row.get("body") or "")
            with contextlib.suppress(ValueError):
                return row["number"], (json.loads(m.group(1)) if m else None)
            return row["number"], None
    return None, None


def ci_runs() -> list[dict]:
    fields = "conclusion,createdAt,event,status,workflowName,databaseId"
    branch = default_branch()
    out = board.gh("run", "list", "-R", board.repo(), "--branch", branch, "--limit", "100", "--json", fields)
    return json.loads(out or "[]")


def ci_ledger(runs: list[dict]) -> list[dict]:
    """The test history CI uploads (artifact `test-history`) of the newest main push run that has one."""
    for r in sorted(runs, key=lambda r: r["createdAt"], reverse=True)[:3]:
        if r.get("event") != "push" or r.get("status") != "completed":
            continue
        with tempfile.TemporaryDirectory(prefix="suite-audit-ci-") as tmp:
            try:
                board.gh("run", "download", str(r["databaseId"]), "-R", board.repo(), "-n", "test-history", "-D", tmp)
            except board.BoardError:
                continue
            for p in Path(tmp).rglob("*.jsonl"):
                return read_ledger(p)
    return []


def _base(repo: Path, snap: dict | None) -> str | None:
    sha = (snap or {}).get("commit")
    if (
        sha
        and subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True
        ).returncode
        == 0
    ):
        return sha
    return None


def due(repo: Path, snap: dict | None, now: float | None = None) -> dict:
    """Is an audit due since the snapshot of the last one (note §10 item 2)? Days are shown, never a reason."""
    now = time.time() if now is None else now
    proxy = test_count(repo, "HEAD")
    why = []
    base = _base(repo, snap)
    if snap is None:
        why += ["no audit recorded"] if proxy else []
    elif base is None:
        why.append(f"the last audit's commit {str(snap.get('commit'))[:9]} is not in this history")
    else:
        n = int(_git(repo, "rev-list", "--first-parent", "--count", f"{base}..HEAD").strip())
        if n >= FP_COMMITS:
            why.append(f"{n} first-parent commits since the last audit")
        last = int(snap.get("proxy", 0))
        if proxy - last >= GROWTH_MIN and proxy >= last * (1 + GROWTH):
            why.append(f"tests grew {last} -> {proxy}")
        if tier_of(proxy) != tier_of(last):
            why.append(f"tier {tier_of(last)} -> {tier_of(proxy)}")
    days = round((now - float(snap["at"])) / 86400, 1) if snap and "at" in snap else None
    return {"due": bool(why), "why": why, "proxy": proxy, "base": (base or "")[:9], "days_since": days}


def orient_lines(repo: Path, budget_s: float = ORIENT_BUDGET_S) -> list[str]:
    """`dev orient`'s suite-audit lines, only when something fires: an audit is due, CI on main is red or stale, or
    the test floor is stale. Runs in a daemon thread for at most `budget_s`; never raises (orient is a convenience)."""
    probe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True)
    if probe.returncode != 0:
        return []
    box: dict = {}

    def work() -> None:
        try:
            box["lines"] = _orient(repo)
        except Exception as exc:  # reported below, in orient's thread
            box["err"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(budget_s)
    if t.is_alive():
        return [f"suite audit trigger skipped: over {budget_s:g} s (`uv run dev suite-audit status`)"]
    if "err" in box:
        return [f"suite audit trigger failed: {box['err']}"[:200]]
    return box["lines"]


def _orient(repo: Path) -> list[str]:
    lines = []
    _, snap = last_snapshot()
    if (d := due(repo, snap))["due"]:
        lines.append(
            f"## Suite audit due ({'; '.join(d['why'])})"[:200]
            + ": lead runs `uv run dev suite-audit report --file`, then harness-ops:suite-auditor on its judgement items"
        )
    newest = float(_git(repo, "log", "-1", "--format=%ct", "HEAD").strip() or 0)
    ci = check_ci(ci_runs(), newest, time.time(), bool(_workflows(repo)))
    lines += [f"CI on main: {p}" for p in ci.problems[:3]]
    runs = read_ledger(state_dir() / gate.HISTORY_FILE)
    passed = sum(o == "passed" for o in outcomes(runs[-1]).values()) if runs else None
    lines += [f"test floor: {p}" for p in check_floor(passed, _floors(repo)).problems]
    return lines


def _workflows(repo: Path) -> dict[str, str]:
    return {p.name: p.read_text(errors="replace") for p in sorted((repo / ".github/workflows").glob("*.y*ml"))}


def _floors(repo: Path) -> dict:
    try:
        return checks.test_floors((repo / config.get().ratchets).read_text())
    except (OSError, ValueError):
        return {}


def _pytest_ini(repo: Path) -> dict:
    with contextlib.suppress(OSError, ValueError):
        return (
            tomllib.loads((repo / "pyproject.toml").read_text())
            .get("tool", {})
            .get("pytest", {})
            .get("ini_options", {})
        )
    return {}


def _commits(repo: Path, base: str | None) -> list[tuple[str, str, list[str]]]:
    rng = [f"{base}..HEAD"] if base else ["-n", "200", "HEAD"]
    out = _git(repo, "log", "--no-merges", "--format=%x1e%H%x1f%s", "--name-only", *rng)
    commits = []
    for chunk in out.split("\x1e")[1:]:
        head, _, files = chunk.partition("\n")
        sha, _, subject = head.partition("\x1f")
        commits.append((sha, subject, [f for f in files.splitlines() if f]))
    return commits


def report(repo: Path, reruns: int | None = None, quick: bool = False, now: float | None = None) -> dict:
    """Measure the suite and run the nine checks. `reruns`: seeds for the fresh-clone reruns (default: the tier's;
    the whole suite at tier S, the history's suspects at M and L);
    `quick`: no runs at all (the history, git and `gh` only)."""
    t0, now = time.perf_counter(), time.time() if now is None else now
    sha = _git(repo, "rev-parse", "HEAD").strip()
    notes: list[str] = []
    try:
        tracker, snap = last_snapshot()
    except (board.BoardError, ValueError) as exc:
        tracker, snap = None, None
        notes.append(f"no snapshot read: {exc}")
    base = _base(repo, snap)
    head = scan(py_files(repo, "HEAD"))
    then = scan(py_files(repo, base)) if base else None
    marks, ini = gate_marks(), _pytest_ini(repo)
    testpaths = list(ini.get("testpaths", ["tests"]))
    collected, gate_ids = collect(repo, None, testpaths), collect(repo, marks, GATE_PATHS)
    gate_s = gate_seconds()
    tier = tier_of(len(collected), gate_s[-1] if gate_s else None, head.prod_sloc)
    ledger = read_ledger(state_dir() / gate.HISTORY_FILE)
    k = RERUNS[tier] if reruns is None else reruns
    clone_runs: list[dict] = []
    tree: dict[str, str] | None = None
    if not quick and k > 0:
        with gate.gate_lock(state_dir() / "gate.lock", on_wait=lambda: print("suite audit: waiting for a gate ...")):
            clone_runs, tree = rerun(repo, list(range(1, k + 1)), None if tier == "S" else suspects(ledger))
    try:
        runs = ci_runs()
    except (board.BoardError, ValueError) as exc:
        runs = None
        notes.append(f"no CI runs read: {exc}")
    workflows = _workflows(repo)
    full, picks = ci_pytest_selections(workflows)
    ran = set(gate_ids)
    if full:
        ran |= collected
    for m, paths in picks:
        with contextlib.suppress(RuntimeError, subprocess.TimeoutExpired):
            ran |= collect(repo, m, paths)
    local = tree or (outcomes(ledger[-1]) if ledger else {})
    ci_hist = ci_ledger(runs) if runs else []
    ci_skipped = sorted(
        t for t, o in (outcomes(ci_hist[-1]) if ci_hist else {}).items() if o == "skipped" and local.get(t) == "passed"
    )
    issues = {}
    for n in sorted({n for _, refs in head.flaky for n in refs}):
        with contextlib.suppress(board.BoardError, ValueError):
            row = json.loads(board.gh("issue", "view", str(n), "-R", board.repo(), "--json", "state,createdAt"))
            issues[n] = (row["state"], row["createdAt"].replace("Z", "+00:00"))
    newest = float(_git(repo, "log", "-1", "--format=%ct", "HEAD").strip() or 0)
    passed = sum(o == "passed" for o in local.values()) if local else None
    flaky = check_flaky(clone_runs, ledger)
    checks_ = [
        flaky,
        check_quarantine(head, tier, issues, now),
        check_ci(runs, newest, now, bool(workflows)),
        check_parity(tree, outcomes(clone_runs[0]) if clone_runs else None, set(mixed(clone_runs))),
        check_never_run(
            collected,
            ran,
            outside_testpaths(repo, testpaths),
            head.xfail,
            bool(ini.get("xfail_strict")),
            ci_skipped,
            (snap or {}).get("ci_skipped"),
        ),
        check_slow([*ledger, *clone_runs], head.slow, tier, gate_s),
        check_drift(then, head, tier),
        check_floor(passed, _floors(repo)),
        check_fixes(_commits(repo, base)),
    ]
    return {
        "commit": sha[:9],
        "tier": tier,
        "collected": len(collected),
        "gate_selected": len(gate_ids),
        "prod_sloc": head.prod_sloc,
        "gate_s": gate_s[-1] if gate_s else None,
        "reruns": len(clone_runs),
        "base": (base or "")[:9],
        "tracker": tracker,
        "checks": [asdict(c) for c in checks_],
        "judgement": judgement(head),
        "notes": notes,
        "snapshot": {
            "commit": sha,
            "proxy": test_count(repo, "HEAD"),
            "collected": len(collected),
            "tier": tier,
            "ci_skipped": len(ci_skipped),
            "at": int(now),
        },
        "seconds": round(time.perf_counter() - t0, 1),
    }


# ---------------------------------------------------------------- output


def render(r: dict) -> str:
    gate_s = f"gate {r['gate_s']:.0f} s" if r["gate_s"] is not None else "no gate record"
    out = [
        f"suite audit at {r['commit']}: tier {r['tier']} ({r['collected']} collected, {r['gate_selected']} in the gate, "
        f"{gate_s}, {r['prod_sloc']:,} prod SLOC); {r['reruns']} fresh-clone rerun(s); "
        f"last audit {r['base'] or 'none'}; {r['seconds']} s",
        *(f"note: {n}" for n in r["notes"]),
    ]
    for c in r["checks"]:
        mark = "FAIL" if c["problems"] else "skip" if c["skipped"] else "ok"
        out.append(
            f"[{mark:4}] {c['title']}"
            + (f": {len(c['problems'])}" if c["problems"] else "")
            + (f" ({c['skipped']})" if c["skipped"] else "")
        )
        out += [f"       - {p}" for p in c["problems"][:SHOWN]]
        if len(c["problems"]) > SHOWN:
            out.append(f"       ... and {len(c['problems']) - SHOWN} more")
        out += [f"       {i}" for i in c["info"][:SHOWN]]
    if r["judgement"]:
        out.append(
            f"judgement items for harness-ops:suite-auditor ({sum(not j.startswith('  ') for j in r['judgement'])}):"
        )
        out += [f"  {j}" for j in r["judgement"]]
    else:
        out.append("judgement items: none (no suite-auditor needed)")
    return "\n".join(out)


def cap(text: str, limit: int = BODY_CAP) -> str:
    if len(text) <= limit:
        return text
    note = "\n\n(cut to fit GitHub's body limit; run `uv run dev suite-audit report` for all of it)\n"
    return text[: limit - len(note)].rsplit("\n", 1)[0] + note


def issue_body(c: dict, commit: str) -> str:
    """Top SHOWN problems, each cut to LINE_MAX, then the DONE WHEN: far under the cap, and the cap never cuts the
    DONE WHEN (an item without one breaks the board's conventions)."""
    lines = [f"The suite audit at {commit} found {len(c['problems'])} problem(s) in **{c['title']}**:"]
    lines += [f"- {p[:LINE_MAX]}" for p in c["problems"][:SHOWN]]
    if len(c["problems"]) > SHOWN:
        lines.append(f"- ... and {len(c['problems']) - SHOWN} more (`uv run dev suite-audit report`)")
    lines += [f"\n{i[:LINE_MAX]}" for i in c["info"][:3]]
    lines.append("\nUpdated by every audit, closed by the first clean one. Never weaken or delete a test to close it.")
    return board.body_from(cap("\n".join(lines), BODY_CAP - 2000), [c["done"]], [], [], [REF], None)


def file_report(r: dict) -> list[str]:
    """Keep one open issue per failing check (stable title, edited in place), close the clean ones, and record the
    audit: its report as a comment and its snapshot in the footer of the closed tracker issue."""
    search = ["--search", f'in:title "{TRACKER}:"', "--json", "number,title", "--limit", "100"]
    rows = json.loads(board.gh("issue", "list", "-R", board.repo(), "--state", "open", *search) or "[]")
    by_title = {row["title"].strip(): row["number"] for row in rows}
    out = []
    for c in r["checks"]:
        title = f"{TRACKER}: {c['title']}"
        n = by_title.get(title)
        if c["problems"]:
            body = issue_body(c, r["commit"])
            if n:
                board.gh("issue", "edit", str(n), "-R", board.repo(), "--body-file", "-", input=body)
                out.append(f"#{n} {title}: updated")
            else:
                ctx = board.sections(body).get("Context", "")
                out.append(f"{board.new(title, 'test', 'P2', ctx, [c['done']], [], [], [REF], None)} {title}: opened")
        elif n and not c["skipped"]:
            board.gh(
                "issue", "close", str(n), "-R", board.repo(), "--comment", f"Clean in the suite audit at {r['commit']}."
            )
            out.append(f"#{n} {title}: closed (clean)")
    footer = f"<!-- suite-audit: {json.dumps(r['snapshot'], separators=(',', ':'))} -->"
    summary = (
        "The record of this repository's suite audits (`uv run dev suite-audit`): each audit comments its report, and "
        "the footer below holds the last audit's snapshot, which the trigger in `dev orient` counts from. Kept closed: "
        f"it is a record, not work. The work is in the open `{TRACKER}: <check>` issues.\n\n"
        f"Last audit: {r['commit']}, tier {r['tier']}, {r['collected']} tests collected.\n\n{footer}\n"
    )
    n = r.get("tracker")
    if n:
        board.gh("issue", "edit", str(n), "-R", board.repo(), "--body-file", "-", input=summary)
    else:
        url = board.gh("issue", "create", "-R", board.repo(), "--title", TRACKER, "--body-file", "-", input=summary)
        n = int(url.strip().rstrip("/").rsplit("/", 1)[-1])
    board.gh(
        "issue", "comment", str(n), "-R", board.repo(), "--body-file", "-", input=cap("```\n" + render(r) + "\n```")
    )
    with contextlib.suppress(board.BoardError):  # already closed
        board.gh("issue", "close", str(n), "-R", board.repo(), "--reason", "not planned")
    out.append(f"#{n} {TRACKER}: audit recorded at {r['commit']}")
    return out
