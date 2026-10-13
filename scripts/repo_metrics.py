#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Read-only architecture metrics for a git repository (agent-harness#111).

FROZEN (#141): superseded by `uv run dev consolidation`; do not extend. Kept unchanged as the research artifact that
the architecture-consolidation note's VERIFIED numbers rest on.

Reads only git objects (``git ls-tree`` / ``git cat-file``) and, for the optional external tools, an extracted
``git archive`` copy in a temporary directory, so the measured repository is never modified.

Metrics per repository:
  growth      code and doc lines by top-level dir at N commits sampled evenly in time along first-parent history
  sizes       file-size distribution of code files at the tip, and the largest files
  hotspots    change frequency (non-merge commits touching the file, renames followed) x current size
  dup         duplicated code lines: windows of K normalised lines that occur more than once (stdlib, jscpd-like)
  imports     Python import graph: cycles (Tarjan SCC), package-level layering, edges by package
  reach       modules reachable from entry points ([project.scripts], ``__main__`` guards) and from tests
  cli         CLI subcommands found in argparse code, and how often each was run in Claude Code transcripts
  vulture     unused-code findings (``uvx vulture``) on the archive copy, optional

Usage:
  uv run scripts/repo_metrics.py REPO [REPO ...] [--samples 12] [--json out.json] [--vulture]
        [--transcripts GLOB ...] [--cross]

Line counting: a code line is a non-blank line that is not only a comment (docstrings count as code).
Excluded everywhere: lock files, vendored/third-party trees, generated trees, minified bundles, binary files.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import glob
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

CODE_EXT = {
    ".py": "#",
    ".pyi": "#",
    ".sh": "#",
    ".bash": "#",
    ".js": "//",
    ".mjs": "//",
    ".cjs": "//",
    ".ts": "//",
    ".tsx": "//",
    ".jsx": "//",
    ".css": "/*",
    ".html": "<!--",
    ".sql": "--",
    ".rs": "//",
    ".go": "//",
    ".c": "//",
    ".h": "//",
    ".cpp": "//",
    ".hpp": "//",
    ".jinja": "{#",
    ".pyx": "#",
}
DOC_EXT = {".md", ".rst", ".txt"}
EXCLUDE_GLOBS = [
    "*.lock",
    "*/package-lock.json",
    "package-lock.json",
    "*.min.js",
    "*.min.css",
    "third_party/*",
    "*/third_party/*",
    "vendor/*",
    "*/vendor/*",
    "vendored/*",
    "*/vendored/*",
    "_vendor/*",
    "*/_vendor/*",
    "node_modules/*",
    "*/node_modules/*",
    "generated/*",
    "*/generated/*",
    "*/_generated/*",
    "dist/*",
    "build/*",
    "*/__pycache__/*",
]
TEST_RE = re.compile(
    r"(^|/)(tests?|tests_[a-z0-9_]+|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$|\.test\.(ts|js)$"
)


def git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace") if text else out.stdout


def excluded(path: str, extra: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in EXCLUDE_GLOBS + extra)


def kind_of(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in CODE_EXT:
        return "code"
    if ext in DOC_EXT:
        return "doc"
    return None


def is_test(path: str) -> bool:
    return bool(TEST_RE.search(path))


class BlobReader:
    """One long-lived ``git cat-file --batch``; blob contents are content-addressed so results are cached."""

    def __init__(self, repo: Path):
        self.proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        self.loc_cache: dict[tuple[str, str], int] = {}

    def read(self, sha: str) -> bytes:
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("git cat-file is not running")
        self.proc.stdin.write(sha.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3 or header[1] == b"missing":
            return b""
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data

    def text(self, sha: str) -> str | None:
        data = self.read(sha)
        if b"\0" in data[:8000]:
            return None
        return data.decode("utf-8", "replace")

    def loc(self, sha: str, path: str) -> int:
        ext = os.path.splitext(path)[1].lower()
        key = (sha, ext)
        if key not in self.loc_cache:
            src = self.text(sha)
            self.loc_cache[key] = 0 if src is None else count_loc(src, ext)
        return self.loc_cache[key]

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()


def count_loc(src: str, ext: str) -> int:
    prefix = CODE_EXT.get(ext)
    n = 0
    for line in src.splitlines():
        s = line.strip()
        if not s:
            continue
        if prefix and s.startswith(prefix) and not (ext in {".py", ".sh"} and s.startswith("#!")):
            continue
        n += 1
    return n


def tree(repo: Path, rev: str, extra: list[str]) -> list[tuple[str, str]]:
    """(sha, path) of regular tracked files at ``rev`` that are code or docs and not excluded."""
    raw = git(repo, "ls-tree", "-r", "-z", "--full-tree", rev, text=False)
    out = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, typ, sha = meta.split()
        p = path.decode("utf-8", "replace")
        if typ != b"blob" or mode == b"120000" or excluded(p, extra) or kind_of(p) is None:
            continue
        out.append((sha.decode(), p))
    return out


def top(path: str, depth: int = 1) -> str:
    parts = path.split("/")
    if len(parts) <= depth:
        return "(root)" if depth == 1 else "/".join(parts[:-1]) or "(root)"
    return "/".join(parts[:depth])


# ---------------------------------------------------------------- growth
def growth(repo: Path, reader: BlobReader, samples: int, extra: list[str]) -> list[dict]:
    lines = git(repo, "rev-list", "--first-parent", "--format=%ct", "HEAD").splitlines()
    commits = []
    for i in range(0, len(lines), 2):
        commits.append((lines[i].split()[1], int(lines[i + 1])))
    commits.reverse()  # oldest first
    if not commits:
        return []
    # end-of-day state for every active day, plus points evenly spaced by first-parent commit index (bursts of
    # agent activity make time-spaced samples collapse onto the same commit)
    chosen: dict[str, tuple[str, int]] = {}
    for c in commits:
        chosen[_date(c[1])] = c
    idx = {round(k * (len(commits) - 1) / max(samples - 1, 1)) for k in range(samples)}
    keep = {c[0] for c in chosen.values()} | {commits[i][0] for i in idx}
    picks = [c for c in commits if c[0] in keep]
    order = {c[0]: i for i, c in enumerate(commits)}
    out = []
    for sha, ts in picks:
        code_by_dir: Counter[str] = Counter()
        doc_lines = 0
        test_lines = 0
        nfiles = 0
        for blob, path in tree(repo, sha, extra):
            n = reader.loc(blob, path)
            if kind_of(path) == "code":
                code_by_dir[top(path)] += n
                nfiles += 1
                if is_test(path):
                    test_lines += n
            else:
                doc_lines += n
        out.append(
            {
                "commit": sha[:9],
                "date": _date(ts),
                "fp_index": order[sha],
                "code": sum(code_by_dir.values()),
                "test_code": test_lines,
                "code_files": nfiles,
                "doc_lines": doc_lines,
                "by_dir": dict(code_by_dir.most_common()),
            }
        )
    return out


def _date(ts: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts, _dt.UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- sizes
def pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(q * len(s)) - 1)]


def sizes(files: dict[str, int]) -> dict:
    v = list(files.values())
    return {
        "files": len(v),
        "p50": pct(v, 0.5),
        "p90": pct(v, 0.9),
        "p99": pct(v, 0.99),
        "max": max(v, default=0),
        "over_300": sum(x > 300 for x in v),
        "over_500": sum(x > 500 for x in v),
        "over_1000": sum(x > 1000 for x in v),
        "lines_in_files_over_500": sum(x for x in v if x > 500),
        "total": sum(v),
        "largest": sorted(files.items(), key=lambda kv: -kv[1])[:12],
    }


# ---------------------------------------------------------------- hotspots
RENAME_RE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


def _rename(path: str) -> tuple[str, str] | None:
    m = RENAME_RE.match(path)
    if m:
        a, old, new, b = m.groups()
        return (a + old + b).replace("//", "/"), (a + new + b).replace("//", "/")
    if " => " in path:
        old, new = path.split(" => ", 1)
        return old, new
    return None


def hotspots(repo: Path, current: dict[str, int], extra: list[str]) -> dict:
    log = git(repo, "log", "--no-merges", "-M", "--numstat", "--format=@%H", "HEAD")
    final: dict[str, str] = {}
    freq: Counter[str] = Counter()
    churn: Counter[str] = Counter()
    commits_total = 0
    added = deleted = 0
    for line in log.splitlines():
        if line.startswith("@"):
            commits_total += 1
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        ren = _rename(path)
        if ren:
            old, new = ren
            final[old] = final.get(new, new)
            path = new
        name = final.get(path, path)
        if add != "-" and kind_of(name) == "code" and not excluded(name, extra):
            added += int(add)
            deleted += int(dele)
        if name not in current:
            continue
        freq[name] += 1
        if add != "-":
            churn[name] += int(add) + int(dele)
    rows = []
    for name, loc in current.items():
        rows.append({"path": name, "commits": freq[name], "loc": loc, "churn": churn[name], "score": freq[name] * loc})
    rows.sort(key=lambda r: -r["score"])
    total_churn = sum(churn.values()) or 1
    by_churn = sorted(current, key=lambda p: -churn[p])
    top10 = by_churn[: max(1, len(by_churn) // 10)]
    return {
        "non_merge_commits": commits_total,
        "top": rows[:15],
        "code_lines_added": added,
        "code_lines_deleted": deleted,
        "deleted_per_added": round(deleted / (added or 1), 3),
        "churn_share_top10pct_files": round(sum(churn[p] for p in top10) / total_churn, 3),
        "churn_to_size": round(total_churn / (sum(current.values()) or 1), 2),
    }


# ---------------------------------------------------------------- duplication
TRIVIAL = re.compile(r"^[\)\]\}\(\[\{,;:]*$|^(pass|else:|try:|finally:|return|break|continue|\"\"\"|''')$")


PY_KEYWORDS = set(__import__("keyword").kwlist) | {"self", "cls", "None", "True", "False"}
TOKEN_RE = re.compile(r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')|\b(\d[\w.]*)\b|\b([A-Za-z_]\w*)\b")


def _rename_tokens(line: str) -> str:
    def sub(m: re.Match) -> str:
        if m.group(1):
            return "S"
        if m.group(2):
            return "0"
        return m.group(3) if m.group(3) in PY_KEYWORDS else "N"

    return TOKEN_RE.sub(sub, line)


def normalise(src: str, ext: str, renamed: bool = False) -> list[tuple[int, str, str]]:
    """(line number, hash key, whitespace-collapsed text). ``renamed`` replaces identifiers and literals in the key,
    so type-2 clones (same shape, different names) match too."""
    prefix = CODE_EXT.get(ext, "#")
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        s = " ".join(line.split())
        if not s or s.startswith(prefix) or TRIVIAL.match(s):
            continue
        if ext == ".py" and (s.startswith("import ") or s.startswith("from ")):
            continue
        out.append((i, _rename_tokens(s) if renamed else s, s))
    return out


def dup_index(files: dict[str, str], window: int, renamed: bool = False) -> dict[str, list[tuple[str, int]]]:
    """hash -> [(path, start index into normalised lines)] for every window with enough content."""
    idx: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for path, src in files.items():
        norm = normalise(src, os.path.splitext(path)[1].lower(), renamed)
        for j in range(len(norm) - window + 1):
            chunk = [k for _, k, _ in norm[j : j + window]]
            if (
                sum(len(raw) for _, _, raw in norm[j : j + window]) < 20 * window
            ):  # skip low-content windows (closing brackets, short calls)
                continue
            h = hashlib.blake2b("\n".join(chunk).encode(), digest_size=10).hexdigest()
            idx[h].append((path, j))
    return idx


def duplication(files: dict[str, str], window: int, renamed: bool = False) -> dict:
    idx = dup_index(files, window, renamed)
    dup_lines: dict[str, set[int]] = defaultdict(set)
    pair: Counter[tuple[str, str]] = Counter()
    norm_len = {p: len(normalise(s, os.path.splitext(p)[1].lower())) for p, s in files.items()}
    for occ in idx.values():
        if len(occ) < 2:
            continue
        for path, j in occ:
            dup_lines[path].update(range(j, j + window))
        paths = sorted({p for p, _ in occ})
        for a in range(len(paths)):
            for b in range(a + 1, len(paths)):
                pair[(paths[a], paths[b])] += 1
        if len(paths) == 1:
            pair[(paths[0], paths[0])] += 1
    total = sum(norm_len.values()) or 1
    duped = sum(len(v) for v in dup_lines.values())
    by_dir: Counter[str] = Counter()
    for p, v in dup_lines.items():
        by_dir[top(p)] += len(v)
    return {
        "window": window,
        "renamed": renamed,
        "normalised_lines": total,
        "duplicated_lines": duped,
        "pct": round(100 * duped / total, 1),
        "by_dir": dict(by_dir.most_common(8)),
        "top_files": sorted(((p, len(v), norm_len[p]) for p, v in dup_lines.items()), key=lambda r: -r[1])[:10],
        "top_pairs": [(a, b, n) for (a, b), n in pair.most_common(10)],
    }


# ---------------------------------------------------------------- python import graph
def module_name(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    if p.startswith("src/"):
        p = p[4:]
    parts = p.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class PyInfo:
    imports: list[tuple[str, bool]] = field(default_factory=list)  # (target, module_level)
    main_guard: bool = False
    dynamic_import: bool = False
    parse_error: bool = False
    strings: set[str] = field(default_factory=set)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def analyse_py(src: str, mod: str, is_pkg: bool) -> PyInfo:
    info = PyInfo()
    try:
        t = ast.parse(src)
    except SyntaxError:
        info.parse_error = True
        return info

    def visit(node: ast.AST, level0: bool) -> None:
        for sub in ast.iter_child_nodes(node):
            if isinstance(sub, ast.If) and _is_type_checking(sub.test):
                continue
            if (
                isinstance(sub, ast.If)
                and isinstance(sub.test, ast.Compare)
                and isinstance(sub.test.left, ast.Name)
                and sub.test.left.id == "__name__"
            ):
                info.main_guard = True
            if isinstance(sub, ast.Import):
                info.imports += [(a.name, level0) for a in sub.names]
            elif isinstance(sub, ast.ImportFrom):
                base = sub.module or ""
                if sub.level:
                    pkg = mod.split(".") if is_pkg else mod.split(".")[:-1]
                    pkg = pkg[: len(pkg) - (sub.level - 1)] if sub.level > 1 else pkg
                    base = ".".join([*pkg, base] if base else pkg)
                for a in sub.names:
                    info.imports.append((f"{base}.{a.name}" if a.name != "*" else base, level0))
                    info.imports.append((base, level0))
            elif isinstance(sub, ast.Call):
                f = sub.func
                name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
                if name in {"import_module", "__import__", "run_module", "spec_from_file_location"}:
                    info.dynamic_import = True
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) < 80:
                info.strings.add(sub.value)
            inner = level0 and not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            visit(sub, inner)

    visit(t, True)
    return info


def resolve(target: str, known: set[str]) -> str | None:
    parts = target.split(".")
    for k in range(len(parts), 0, -1):
        cand = ".".join(parts[:k])
        if cand in known:
            return cand
    return None


def tarjan(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in graph:
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(graph.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return out


def reach(graph: dict[str, set[str]], roots: set[str]) -> set[str]:
    seen = set(roots)
    todo = list(roots)
    while todo:
        for w in graph.get(todo.pop(), ()):
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return seen


def pkg_of(mod: str, depth: int) -> str:
    return ".".join(mod.split(".")[:depth])


def _by_dir(ms: set[str], mods: dict[str, str], loc: dict[str, int]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for m in ms:
        c[top(mods[m])] += loc.get(mods[m], 0)
    return dict(c.most_common(12))


def python_graph(py: dict[str, str], loc: dict[str, int], scripts: dict[str, str], pkg_depth: int) -> dict:
    mods = {module_name(p): p for p in py}
    known = set(mods)
    infos = {m: analyse_py(py[p], m, p.endswith("__init__.py")) for m, p in mods.items()}
    g_all: dict[str, set[str]] = {m: set() for m in mods}
    g_top: dict[str, set[str]] = {m: set() for m in mods}
    for m, info in infos.items():
        for target, level0 in info.imports:
            r = resolve(target, known)
            if r and r != m:
                g_all[m].add(r)
                if level0:
                    g_top[m].add(r)
        # importing a.b.c executes a and a.b
        parts = m.split(".")
        for k in range(1, len(parts)):
            parent = ".".join(parts[:k])
            if parent in known:
                g_all[m].add(parent)
    cycles_all = tarjan(g_all)
    cycles_top = tarjan(g_top)
    # package layering
    pedges: Counter[tuple[str, str]] = Counter()
    for m, ws in g_top.items():
        for w in ws:
            a, b = pkg_of(m, pkg_depth), pkg_of(w, pkg_depth)
            if a != b:
                pedges[(a, b)] += 1
    pgraph: dict[str, set[str]] = defaultdict(set)
    for a, b in pedges:
        pgraph[a].add(b)
        pgraph.setdefault(b, set())
    pkg_cycles = tarjan(dict(pgraph))
    pgraph_all: dict[str, set[str]] = defaultdict(set)
    lazy_edges = 0
    for m, ws in g_all.items():
        lazy_edges += len(ws - g_top[m] - {".".join(m.split(".")[:k]) for k in range(1, len(m.split(".")))})
        for w in ws:
            a, b = pkg_of(m, pkg_depth), pkg_of(w, pkg_depth)
            if a != b:
                pgraph_all[a].add(b)
                pgraph_all.setdefault(b, set())
    pkg_cycles_all = tarjan(dict(pgraph_all))
    # reachability
    tests = {m for m, p in mods.items() if is_test(p)}
    script_roots = {m for m in mods if m in {s.split(":")[0] for s in scripts.values()}}
    main_roots = {m for m, i in infos.items() if i.main_guard or m.endswith("__main__")} - tests
    r_entry = reach(g_all, script_roots)
    r_main = reach(g_all, script_roots | main_roots)
    r_tests = reach(g_all, tests)
    src = {m for m in mods if m not in tests}

    def lines(ms: set[str]) -> int:
        return sum(loc.get(mods[m], 0) for m in ms)

    unreached = src - r_main - r_tests
    by_dir: Counter[str] = Counter()
    for m in unreached:
        by_dir[top(mods[m])] += loc.get(mods[m], 0)
    return {
        "modules": len(mods),
        "test_modules": len(tests),
        "parse_errors": sorted(mods[m] for m, i in infos.items() if i.parse_error),
        "dynamic_import_modules": sorted(mods[m] for m, i in infos.items() if i.dynamic_import),
        "edges": sum(len(v) for v in g_all.values()),
        "cycles_all": {
            "count": len(cycles_all),
            "largest": max((len(c) for c in cycles_all), default=0),
            "modules_in_cycles": sum(len(c) for c in cycles_all),
            "examples": [c[:8] for c in sorted(cycles_all, key=len, reverse=True)[:3]],
        },
        "cycles_module_level": {
            "count": len(cycles_top),
            "largest": max((len(c) for c in cycles_top), default=0),
            "modules_in_cycles": sum(len(c) for c in cycles_top),
            "examples": [c[:8] for c in sorted(cycles_top, key=len, reverse=True)[:3]],
        },
        "package_depth": pkg_depth,
        "package_edges": [(a, b, n) for (a, b), n in pedges.most_common(40)],
        "package_cycles": pkg_cycles,
        "package_cycles_incl_function_level": pkg_cycles_all,
        "function_level_internal_edges": lazy_edges,
        "entry_scripts": sorted(script_roots),
        "main_guard_modules": len(main_roots),
        "src_loc": lines(src),
        "reach_from_project_scripts_loc": lines(src & r_entry),
        "reach_from_entry_or_main_loc": lines(src & r_main),
        "reach_from_tests_loc": lines(src & r_tests),
        "reach_from_tests_only_loc": lines(src & (r_tests - r_main)),
        "not_from_project_scripts_by_dir": _by_dir(src - r_entry, mods, loc) if script_roots else {},
        "unreached_loc": lines(unreached),
        "unreached_by_dir": dict(by_dir.most_common(10)),
        "unreached_largest": sorted(((mods[m], loc.get(mods[m], 0)) for m in unreached), key=lambda r: -r[1])[:12],
        "_infos": infos,
        "_mods": mods,
        "_main": sorted(main_roots),
    }


# ---------------------------------------------------------------- CLI inventory and usage
def cli_commands(src: str) -> list[str]:
    """Subcommand paths from argparse code: ``x.add_parser("name")`` chained under named subparser groups."""
    try:
        t = ast.parse(src)
    except SyntaxError:
        return []
    found: list[str] = []

    def lit_first_strings(node: ast.expr) -> list[str]:
        vals = []
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for e in node.elts:
                if isinstance(e, ast.Constant) and isinstance(e.value, str):
                    vals.append(e.value)
                elif isinstance(e, (ast.Tuple, ast.List)) and e.elts and isinstance(e.elts[0], ast.Constant):
                    vals.append(str(e.elts[0].value))
        elif isinstance(node, ast.Dict):
            vals = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        return vals

    consts: dict[str, ast.expr] = {}
    for node in t.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for tgt in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(tgt, ast.Name):
                    consts[tgt.id] = node.value

    def iterable(node: ast.expr) -> ast.expr:
        """``NAME``, ``NAME.items()`` and ``NAME.keys()`` resolve to the module-level literal bound to NAME."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"items", "keys"}:
            node = node.func.value
        return consts.get(node.id, node) if isinstance(node, ast.Name) else node

    for node in ast.walk(t):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append(node.args[0].value)
        if isinstance(node, ast.For):
            uses = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_parser"
                and n.args
                and isinstance(n.args[0], ast.Name)
                for n in ast.walk(node)
            )
            if uses:
                found += lit_first_strings(iterable(node.iter))
    return sorted(set(found))


def _bash_commands(files: list[str]):
    """Each distinct Bash tool call's command string in Claude Code transcripts (tool_use ids de-duplicated)."""
    seen: set[str] = set()
    for f in files:
        try:
            fh = open(f, encoding="utf-8", errors="replace")  # noqa: SIM115 - closed by the with below
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Bash"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if (
                        isinstance(c, dict)
                        and c.get("type") == "tool_use"
                        and c.get("name") == "Bash"
                        and c.get("id") not in seen
                    ):
                        seen.add(c.get("id"))
                        yield (c.get("input") or {}).get("command") or ""


def transcript_usage(
    globs: list[str], progs: list[str], commands: list[str], repo_names: frozenset[str] = frozenset()
) -> dict:
    """Count Bash tool calls in Claude Code transcripts that run ``<prog> [group] <command>`` or ``python -m mod``."""
    files: list[str] = []
    for g in globs:
        files += glob.glob(os.path.expanduser(g), recursive=True)
    cmdset = set(commands)
    use: Counter[str] = Counter()
    pym: Counter[str] = Counter()
    calls = 0
    prog_re = (
        re.compile(
            r"(?:^|[\s;&|(])(?:uv run (?:--[^\s]+ )*)?("
            + "|".join(map(re.escape, progs))
            + r")\s+((?:[a-z][a-z0-9_-]*\s*){1,3})"
        )
        if progs
        else None
    )
    pym_re = re.compile(r"python[0-9.]*\s+(?:-[A-Za-z]+\s+)*-m\s+([A-Za-z_][\w.]*)")
    pyf_re = re.compile(r"python[0-9.]*\s+(?:-[A-Za-z]+\s+)*([\w./-]+\.py)\b")
    for cmd in _bash_commands(sorted(set(files))):
        calls += 1
        if prog_re:
            for m in prog_re.finditer(cmd):
                run = []
                for w in m.group(2).split():
                    if w not in cmdset:
                        break
                    run.append(w)
                if run:
                    use[" ".join(run)] += 1
        for m in pym_re.finditer(cmd):
            pym[m.group(1)] += 1
        for m in pyf_re.finditer(cmd):
            path = m.group(1)
            for name in repo_names:  # absolute path into one of the measured repos: keep the repo-relative part
                if f"/{name}/" in path:
                    path = path.split(f"/{name}/", 1)[1]
                    break
            pym[module_name(path.lstrip("./"))] += 1
    return {
        "transcript_files": len(set(files)),
        "bash_calls": calls,
        "commands": dict(use.most_common()),
        "never_run": sorted(cmdset - {w for path in use for w in path.split()}),
        "python_m": dict(pym.most_common()),
    }


# ---------------------------------------------------------------- external tools on an archive copy
def archive_copy(repo: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="repo_metrics_"))
    data = git(repo, "archive", "--format=tar", "HEAD", text=False)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(tmp, filter="data")
    return tmp


def run_vulture(copy: Path, paths: list[str]) -> dict:
    if not paths or not shutil.which("uvx"):
        return {}
    out = subprocess.run(
        ["uvx", "vulture==2.16", "--min-confidence", "60", *paths], cwd=copy, capture_output=True, text=True
    )
    kinds: Counter[str] = Counter()
    conf100: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    by_dir: Counter[str] = Counter()
    for line in out.stdout.splitlines():
        m = re.match(r"(.+?):\d+: (unused \w+|unreachable code|redundant if-condition)[^(]*\((\d+)% confidence", line)
        if not m:
            continue
        kinds[m.group(2)] += 1
        by_file[m.group(1)] += 1
        by_dir[top(m.group(1))] += 1
        if int(m.group(3)) >= 90:
            conf100[m.group(2)] += 1
    return {
        "findings": sum(kinds.values()),
        "by_kind": dict(kinds),
        "high_confidence": dict(conf100),
        "by_dir": dict(by_dir.most_common(10)),
        "top_files": by_file.most_common(8),
    }


# ---------------------------------------------------------------- per-repo driver
def project_scripts(repo: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(git(repo, "show", "HEAD:pyproject.toml"))
    except (subprocess.CalledProcessError, tomllib.TOMLDecodeError):
        return {}
    return data.get("project", {}).get("scripts", {})


def measure(repo: Path, args: argparse.Namespace) -> dict:
    reader = BlobReader(repo)
    extra = args.exclude
    head = tree(repo, "HEAD", extra)
    code_loc: dict[str, int] = {}
    py_src: dict[str, str] = {}
    code_src: dict[str, str] = {}
    doc_lines = 0
    for sha, path in head:
        if kind_of(path) == "code":
            src = reader.text(sha)
            if src is None:
                continue
            code_loc[path] = count_loc(src, os.path.splitext(path)[1].lower())
            code_src[path] = src
            if path.endswith(".py"):
                py_src[path] = src
        else:
            doc_lines += reader.loc(sha, path)
    by_dir2: Counter[str] = Counter()
    by_dir1: Counter[str] = Counter()
    for p, n in code_loc.items():
        by_dir1[top(p)] += n
        by_dir2[top(p, 2)] += n
    test_loc = sum(n for p, n in code_loc.items() if is_test(p))
    scripts = project_scripts(repo)
    res: dict = {
        "repo": repo.name,
        "head": git(repo, "rev-parse", "--short=9", "HEAD").strip(),
        "commits": int(git(repo, "rev-list", "--count", "HEAD")),
        "merges": int(git(repo, "rev-list", "--merges", "--count", "HEAD")),
        "first_commit": git(repo, "log", "--reverse", "--format=%ad", "--date=short").split("\n", 1)[0],
        "code_loc": sum(code_loc.values()),
        "test_loc": test_loc,
        "doc_lines": doc_lines,
        "by_dir": dict(by_dir1.most_common()),
        "by_dir2": dict(by_dir2.most_common(25)),
        "project_scripts": scripts,
    }
    res["sizes"] = sizes(code_loc)
    res["sizes_nontest"] = sizes({p: n for p, n in code_loc.items() if not is_test(p)})
    print(f"[{repo.name}] growth", file=sys.stderr)
    res["growth"] = growth(repo, reader, args.samples, extra)
    print(f"[{repo.name}] hotspots", file=sys.stderr)
    res["hotspots"] = hotspots(repo, code_loc, extra)
    print(f"[{repo.name}] duplication", file=sys.stderr)
    res["dup"] = duplication(code_src, args.window)
    nontest_src = {p: s for p, s in code_src.items() if not is_test(p)}
    res["dup_nontest"] = duplication(nontest_src, args.window)
    res["dup_renamed"] = duplication(code_src, args.window, renamed=True)
    res["dup_renamed_nontest"] = duplication(nontest_src, args.window, renamed=True)
    print(f"[{repo.name}] imports", file=sys.stderr)
    depth = args.pkg_depth
    if depth == 0:  # auto: one level deeper when one top-level package holds most of the Python code
        py_top = Counter(top(p) for p in py_src)
        depth = 2 if py_top and py_top.most_common(1)[0][1] > 0.5 * len(py_src) else 1
    g = python_graph(py_src, code_loc, scripts, depth)
    infos, mods, main_mods = g.pop("_infos"), g.pop("_mods"), g.pop("_main")
    res["imports"] = g
    cmds: list[str] = []
    for target in scripts.values():
        mod = target.split(":")[0]
        if mod in mods:
            cmds += cli_commands(py_src[mods[mod]])
    res["cli_commands"] = sorted(set(cmds))
    test_strings: set[str] = set()
    for m, info in infos.items():
        if is_test(mods[m]):
            test_strings |= info.strings
    res["cli_commands_named_in_tests"] = sorted(c for c in res["cli_commands"] if c in test_strings)
    tglobs = [
        t.split("=", 1)[1] if "=" in t else t
        for t in args.transcripts
        if "=" not in t or t.split("=", 1)[0] == repo.name
    ]
    if tglobs:
        print(f"[{repo.name}] transcripts", file=sys.stderr)
        res["usage"] = u = transcript_usage(
            tglobs, list(scripts), res["cli_commands"], frozenset(r.name for r in args.repos)
        )
        ran = [m for m in main_mods if u["python_m"].get(m)]
        never = [m for m in main_mods if not u["python_m"].get(m)]
        res["main_modules_run"] = {
            "run": len(ran),
            "never": len(never),
            "run_loc": sum(code_loc[mods[m]] for m in ran),
            "never_loc": sum(code_loc[mods[m]] for m in never),
            "never_by_dir": _by_dir(set(never), mods, code_loc),
            "run_counts": {m: u["python_m"][m] for m in ran},
        }
    if args.vulture:
        print(f"[{repo.name}] vulture", file=sys.stderr)
        copy = archive_copy(repo)
        try:
            nontest = [p for p in py_src if not is_test(p)]
            res["vulture"] = run_vulture(copy, nontest)
        finally:
            shutil.rmtree(copy, ignore_errors=True)
    reader.close()
    res["_code_src"] = code_src
    return res


def cross_repo(results: list[dict], window: int) -> list[tuple[str, str, int]]:
    """Normalised windows shared between repositories: copy-paste between siblings."""
    owners: dict[str, set[str]] = defaultdict(set)
    for r in results:
        for h in dup_index({p: s for p, s in r["_code_src"].items() if not is_test(p)}, window):
            owners[h].add(r["repo"])
    pair: Counter[tuple[str, str]] = Counter()
    for repos in owners.values():
        rs = sorted(repos)
        for a in range(len(rs)):
            for b in range(a + 1, len(rs)):
                pair[(rs[a], rs[b])] += 1
    return [(a, b, n) for (a, b), n in pair.most_common()]


# ---------------------------------------------------------------- trigger replay and fast trigger (#111 round 2)
INDENTED_IMPORT_RE = re.compile(r"^[ \t]+(?:import [A-Za-z_]|from [.A-Za-z_][\w.]* import )", re.M)
ANY_IMPORT_RE = re.compile(r"^[ \t]*(?:import [A-Za-z_]|from [.A-Za-z_][\w.]* import )", re.M)
CHURNY = ((1000, 30), (800, 20), (500, 15))  # (LOC above, non-merge commits at least)
DORMANT_K = (30, 50, 100)  # first-parent commits without a touch


def unit_of(path: str) -> str:
    """Dormancy unit: the first two directory levels; files at the root are their own unit."""
    parts = path.split("/")
    return path if len(parts) == 1 else "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def regex_imports(src: str) -> tuple[int, int]:
    """(indented import statements, all import statements): a parse-free proxy for function-level imports."""
    return len(INDENTED_IMPORT_RE.findall(src)), len(ANY_IMPORT_RE.findall(src))


def _first_parent(repo: Path, rev: str = "HEAD") -> list[tuple[str, int, list[str]]]:
    rows = []
    for line in git(repo, "log", "--first-parent", "--format=%H %ct %P", rev).splitlines():
        sha, ts, *parents = line.split()
        rows.append((sha, int(ts), parents))
    rows.reverse()
    return rows


def _introduced(repo: Path, fp: list[tuple[str, int, list[str]]]) -> list[list[str]]:
    """Non-merge commits that each first-parent commit brings in (its own, or its merged branch's)."""
    parents: dict[str, list[str]] = {}
    ctime: dict[str, int] = {}
    for line in git(repo, "log", "--format=%H %ct %P", fp[-1][0]).splitlines():
        sha, ts, *ps = line.split()
        parents[sha] = ps
        ctime[sha] = int(ts)
    seen: set[str] = set()
    out = []
    for sha, _, _ in fp:
        todo, got = [sha], []
        while todo:
            c = todo.pop()
            if c in seen or c not in parents:
                continue
            seen.add(c)
            if len(parents[c]) <= 1:
                got.append(c)
            todo.extend(parents[c])
        out.append(sorted(got, key=lambda c: ctime[c]))
    return out


def _name_status(repo: Path) -> dict[str, list[tuple[str, str, str | None]]]:
    """Per non-merge commit: (status, path, new_path) with renames detected."""
    out: dict[str, list[tuple[str, str, str | None]]] = {}
    cur: list = []
    for line in git(repo, "log", "--no-merges", "-M", "--name-status", "--format=@%H", "HEAD").splitlines():
        if line.startswith("@"):
            cur = out.setdefault(line[1:], [])
        elif line:
            parts = line.split("\t")
            if parts[0].startswith(("R", "C")) and len(parts) == 3:
                cur.append((parts[0][0], parts[1], parts[2]))
            elif len(parts) == 2:
                cur.append((parts[0][0], parts[1], None))
    return out


def _fp_touched(repo: Path) -> dict[str, list[str]]:
    """Files each first-parent commit changes relative to its first parent (merges included)."""
    out: dict[str, list[str]] = {}
    cur: list[str] = []
    for line in git(
        repo, "log", "--first-parent", "--diff-merges=first-parent", "--name-only", "--format=@%H", "HEAD"
    ).splitlines():
        if line.startswith("@"):
            cur = out.setdefault(line[1:], [])
        elif line:
            cur.append(line)
    return out


def replay(repo: Path, reader: BlobReader, extra: list[str], stride: int = 1) -> list[dict]:
    """Cheap trigger signals at every ``stride``-th first-parent commit, oldest first (read-only)."""
    fp = _first_parent(repo)
    intro = _introduced(repo, fp)
    status = _name_status(repo)
    touched = _fp_touched(repo)
    touches: Counter[str] = Counter()  # non-merge commits per current path, renames followed
    last_touch: dict[str, int] = {}
    py_cache: dict[tuple[str, str], PyInfo] = {}
    rx_cache: dict[str, tuple[int, int]] = {}
    rows = []
    merges = 0
    for i, (sha, ts, parents) in enumerate(fp):
        merges += len(parents) > 1
        for c in intro[i]:
            for st, path, new in status.get(c, []):
                if st == "R" and new:
                    touches[new] = touches.pop(path, 0) + 1
                elif st == "D":
                    touches.pop(path, None)
                else:
                    touches[new or path] += 1
        for path in touched.get(sha, []):
            last_touch[unit_of(path)] = i
        if i % stride and i != len(fp) - 1:
            continue
        files = tree(repo, sha, extra)
        loc: dict[str, int] = {}
        py: dict[str, str] = {}
        lazy_rx = all_rx = 0
        for blob, path in files:
            if kind_of(path) != "code":
                continue
            loc[path] = reader.loc(blob, path)
            if path.endswith(".py"):
                py[path] = blob
                if not is_test(path):
                    if blob not in rx_cache:
                        rx_cache[blob] = regex_imports(reader.text(blob) or "")
                    a, b = rx_cache[blob]
                    lazy_rx += a
                    all_rx += b
        nontest = {p: n for p, n in loc.items() if not is_test(p)}
        # python graph (cached per blob)
        mods = {module_name(p): p for p in py}
        known = set(mods)
        infos = {}
        for m, p in mods.items():
            key = (py[p], p)
            if key not in py_cache:
                py_cache[key] = analyse_py(reader.text(py[p]) or "", m, p.endswith("__init__.py"))
            infos[m] = py_cache[key]
        g_all: dict[str, set[str]] = {m: set() for m in mods}
        lazy = edges = 0
        for m, info in infos.items():
            top_edges: set[str] = set()
            for target, level0 in info.imports:
                r = resolve(target, known)
                if r and r != m:
                    g_all[m].add(r)
                    if level0:
                        top_edges.add(r)
            lazy += len(g_all[m] - top_edges)
            edges += len(g_all[m])
        importers: Counter[str] = Counter(w for ws in g_all.values() for w in ws)
        scc = max((len(c) for c in tarjan(g_all)), default=0)
        leaf_main = sum(
            loc[mods[m]] for m, info in infos.items() if info.main_guard and not is_test(mods[m]) and not importers[m]
        )
        # reachability from [project.scripts] at this commit, for the dormant-and-unreached variant
        scripts: dict[str, str] = {}
        ls = git(repo, "ls-tree", sha, "pyproject.toml").split()
        pp_sha = ls[2] if len(ls) >= 3 else None
        if pp_sha:
            try:
                scripts = tomllib.loads(reader.text(pp_sha) or "").get("project", {}).get("scripts", {})
            except tomllib.TOMLDecodeError:
                scripts = {}
        roots = {s.split(":")[0] for s in scripts.values()} & known
        reached = {mods[m] for m in reach(g_all, roots)} if roots else set(py)
        unit_loc: Counter[str] = Counter()
        for p, n in loc.items():
            unit_loc[unit_of(p)] += n
        # a test unit that imports live code is still run against it, so it is never orphaned
        test_units = {u for u in unit_loc if all(is_test(p) for p in loc if unit_of(p) == u)}
        uses: dict[str, set[str]] = defaultdict(set)
        for m, ws in g_all.items():
            uses[unit_of(mods[m])] |= {unit_of(mods[w]) for w in ws}
        row = {
            "i": i,
            "sha": sha[:9],
            "date": _date(ts),
            "merge": len(parents) > 1,
            "merges": merges,
            "code": sum(loc.values()),
            "test": sum(n for p, n in loc.items() if is_test(p)),
            "nontest": sum(nontest.values()),
            "lazy_edges": lazy,
            "edges": edges,
            "scc_max": scc,
            "lazy_rx": lazy_rx,
            "imports_rx": all_rx,
            "leaf_main_loc": leaf_main,
        }
        d2: Counter[str] = Counter()
        for p, n in loc.items():
            d2[top(p, 2)] += n
        row["by_dir2"] = dict(d2)
        for k in DORMANT_K:
            dormant = {u for u in unit_loc if i - last_touch.get(u, i) >= k}
            row[f"dormant{k}_loc"] = sum(unit_loc[u] for u in dormant)
            row[f"dormant{k}_unreached_loc"] = sum(
                n for p, n in loc.items() if unit_of(p) in dormant and (not p.endswith(".py") or p not in reached)
            )
            row[f"dormant{k}_units"] = sorted(dormant, key=lambda u: -unit_loc[u])[:6]
            # orphaned: a dormant unit that no module of an active unit imports (Python only; other files count
            # with their unit)
            used = {unit_of(mods[w]) for m, ws in g_all.items() if unit_of(mods[m]) not in dormant for w in ws}
            orphan = {u for u in dormant - used if u not in test_units or not uses[u] - dormant}
            row[f"dormant{k}_orphan_loc"] = sum(unit_loc[u] for u in orphan)
            row[f"dormant{k}_orphan_units"] = sorted(orphan, key=lambda u: -unit_loc[u])[:6]
        for lim, cnt in CHURNY:
            hit = [p for p, n in nontest.items() if n > lim and touches.get(p, 0) >= cnt]
            row[f"churny_{lim}_{cnt}"] = len(hit)
        rows.append(row)
    return rows


def replay_tools(repo: Path, fp_shas: list[str]) -> list[dict]:
    """pyscn summary and vulture counts on a ``git archive`` copy of each given commit (slow; few samples)."""
    out = []
    for sha in fp_shas:
        tmp = Path(tempfile.mkdtemp(prefix="repo_metrics_replay_"))
        try:
            with tarfile.open(fileobj=io.BytesIO(git(repo, "archive", "--format=tar", sha, text=False))) as tf:
                tf.extractall(tmp, filter="data")
            row: dict = {"sha": sha[:9]}
            js = tmp / "_pyscn.json"
            subprocess.run(
                ["uvx", "pyscn@1.32.4", "analyze", "--json", "--no-open", "--output", str(js), str(tmp)],
                capture_output=True,
                text=True,
            )
            if js.exists():
                s = json.loads(js.read_text()).get("summary", {})
                row["pyscn"] = {
                    k: s.get(k)
                    for k in (
                        "health_score",
                        "grade",
                        "code_duplication_percentage",
                        "clone_groups",
                        "deps_modules_in_cycles",
                        "analyzed_files",
                    )
                }
            py = [
                p
                for p in (str(x.relative_to(tmp)) for x in tmp.rglob("*.py"))
                if not is_test(p) and not excluded(p, [])
            ]
            row["vulture"] = run_vulture(tmp, py) if py else {}
            out.append(row)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


IMPORT_LINE_RE = re.compile(
    r"^[ \t]*(?:from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+\(?[ \t]*([\w., \t]*)|import[ \t]+([\w., \t]+))", re.M
)
REVIEW_TRAILER = "Consolidation-Review:"
# calibrated on four repositories' histories (#111 round 2): see the r2-trigger-calibration scratch note
TRIGGER = {
    "growth": 1.0,
    "growth_min_loc": 5000,
    "fp_commits": 100,
    "dormant_k": 30,
    "dormant_share": 0.05,
    "churny_loc": 1000,
    "churny_commits": 30,
    "lazy_cycle": 10,
    "lazy_share": 0.10,
    "lazy_min_imports": 50,
}


def regex_import_targets(src: str, mod: str, is_pkg: bool) -> list[str]:
    """Absolute dotted targets of every import statement, found by a regex (no parse; for speed)."""
    out = []
    for dots, base, names, plain in IMPORT_LINE_RE.findall(src):
        if plain:
            out += [n.split()[0] for n in plain.split(",") if n.strip()]
            continue
        if dots:
            pkg = mod.split(".") if is_pkg else mod.split(".")[:-1]
            pkg = pkg[: len(pkg) - (len(dots) - 1)] if len(dots) > 1 else pkg
            base = ".".join([*pkg, base] if base else pkg)
        out.append(base)
        out += [f"{base}.{n.split()[0]}" for n in names.split(",") if n.strip() and n.strip() != "*"]
    return [t for t in out if t]


def trigger_signals(repo: Path, rev: str, reader: BlobReader, cfg: dict = TRIGGER) -> dict:
    """Size and structural smells at ``rev`` from git objects and regexes only."""
    loc: dict[str, int] = {}
    srcs: dict[str, str] = {}
    lazy = imps = 0
    for blob, path in tree(repo, rev, []):
        if kind_of(path) != "code":
            continue
        src = reader.text(blob) or ""
        loc[path] = count_loc(src, os.path.splitext(path)[1].lower())
        if path.endswith(".py"):
            srcs[path] = src
            if not is_test(path):
                a, b = regex_imports(src)
                lazy, imps = lazy + a, imps + b
    total = sum(loc.values())
    mods = {module_name(p): p for p in srcs}
    known = set(mods)
    graph: dict[str, set[str]] = {m: set() for m in mods}
    for m, p in mods.items():
        for t in regex_import_targets(srcs[p], m, p.endswith("__init__.py")):
            r = resolve(t, known)
            if r and r != m:
                graph[m].add(r)
    k = cfg["dormant_k"]
    nfp = int(git(repo, "rev-list", "--first-parent", "--count", rev))
    recent = git(
        repo, "log", "--first-parent", "--diff-merges=first-parent", "--name-only", "--format=", f"-{k}", rev
    ).split("\n")
    active = {unit_of(p) for p in recent if p}
    unit_loc: Counter[str] = Counter()
    for p, n in loc.items():
        unit_loc[unit_of(p)] += n
    dormant = {u for u in unit_loc if u not in active} if nfp > k else set()
    used, uses = set(), defaultdict(set)
    for m, ws in graph.items():
        u = unit_of(mods[m])
        uses[u] |= {unit_of(mods[w]) for w in ws}
        if u not in dormant:
            used |= {unit_of(mods[w]) for w in ws}
    tests = {u for u in unit_loc if all(is_test(p) for p in loc if unit_of(p) == u)}
    orphan = {u for u in dormant - used if u not in tests or not uses[u] - dormant}
    commits: Counter[str] = Counter(
        p for p in git(repo, "log", "--no-merges", "--name-only", "--format=", rev).split("\n") if p
    )
    churny = sorted(
        p for p, n in loc.items() if not is_test(p) and n > cfg["churny_loc"] and commits[p] >= cfg["churny_commits"]
    )
    cycle = max((len(c) for c in tarjan(graph)), default=0)
    share = round(sum(unit_loc[u] for u in orphan) / (total or 1), 3)
    smells = {
        "dormant": share >= cfg["dormant_share"],
        "lazy-imports": imps >= cfg["lazy_min_imports"] and lazy >= cfg["lazy_share"] * imps,
        "import-cycle": cycle >= cfg["lazy_cycle"],
        "churny": bool(churny),
    }
    return {
        "code_loc": total,
        "fp_commits": nfp,
        "orphaned_dormant_share": share,
        "orphaned_dormant_units": sorted(orphan, key=lambda u: -unit_loc[u])[:5],
        "indented_import_share": round(lazy / (imps or 1), 3),
        "largest_import_cycle": cycle,
        "churny_files": churny,
        "smells": [s for s, on in smells.items() if on],
    }


def fast_trigger(repo: Path, since: str | None, cfg: dict = TRIGGER) -> dict:
    """Composite consolidation trigger for ``dev orient`` (git objects and regexes only; target < 2 s).

    Due when the code doubled (and grew by >= 5k LOC) or 100 first-parent commits landed since the last review,
    or when a smell is present now that was absent at the last review (or a large churny file was added). The last
    review is ``since``, else the newest commit whose message has a ``Consolidation-Review:`` trailer,
    else the root commit (and then every present smell counts as new).
    """
    import time

    t0 = time.perf_counter()
    if not since:
        since = git(repo, "log", "-1", "--format=%H", f"--grep=^{REVIEW_TRAILER}", "HEAD").strip()
    base = since or git(repo, "rev-list", "--first-parent", "--max-parents=0", "HEAD").split()[-1]
    reader = BlobReader(repo)
    try:
        now = trigger_signals(repo, "HEAD", reader, cfg)
        then = trigger_signals(repo, base, reader, cfg)
    finally:
        reader.close()
    if not since:  # never reviewed: every standing smell counts as new, so the first review sees them all
        then["smells"] = []
    grown = now["code_loc"] - then["code_loc"]
    why = []
    if grown >= cfg["growth_min_loc"] and now["code_loc"] >= (1 + cfg["growth"]) * then["code_loc"]:
        why.append(f"code grew {then['code_loc']} -> {now['code_loc']} LOC")
    if now["fp_commits"] - then["fp_commits"] >= cfg["fp_commits"]:
        why.append(f"{now['fp_commits'] - then['fp_commits']} first-parent commits")
    why += [f"new smell: {s}" for s in now["smells"] if s not in then["smells"]]
    if then["churny_files"] and len(now["churny_files"]) > len(then["churny_files"]):
        why.append("more large churny files")
    return {
        "since": base[:9],
        "review_due": bool(why),
        "why": why,
        "now": now,
        "at_last_review": {k: then[k] for k in ("code_loc", "fp_commits", "smells")},
        "seconds": round(time.perf_counter() - t0, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repos", nargs="+", type=Path)
    ap.add_argument("--samples", type=int, default=12, help="history samples for the growth curve")
    ap.add_argument("--window", type=int, default=6, help="normalised lines per duplication window")
    ap.add_argument("--pkg-depth", type=int, default=0, help="package depth for layering (0: auto)")
    ap.add_argument("--exclude", action="append", default=[], help="extra fnmatch glob to exclude (repeatable)")
    ap.add_argument(
        "--transcripts",
        action="append",
        default=[],
        help="[REPO_NAME=]glob of Claude Code *.jsonl transcripts (repeatable; no prefix: every repo)",
    )
    ap.add_argument("--vulture", action="store_true", help="run `uvx vulture` on a git-archive copy")
    ap.add_argument("--cross", action="store_true", help="count normalised windows shared between the repos")
    ap.add_argument("--json", type=Path, help="write full results here")
    ap.add_argument(
        "--replay",
        type=int,
        metavar="STRIDE",
        default=0,
        help="instead of the tip metrics: cheap trigger signals at every STRIDE-th first-parent commit",
    )
    ap.add_argument(
        "--replay-tools",
        type=int,
        metavar="N",
        default=0,
        help="with --replay: also run pyscn and vulture at N evenly spaced first-parent commits",
    )
    ap.add_argument("--trigger", action="store_true", help="instead of the tip metrics: the fast composite trigger")
    ap.add_argument("--since", help="with --trigger: commit of the last consolidation review (default: root)")
    args = ap.parse_args()
    if args.trigger or args.replay:
        out = {}
        for r in args.repos:
            r = r.resolve()
            if args.trigger:
                out[r.name] = fast_trigger(r, args.since)
                continue
            print(f"[{r.name}] replay", file=sys.stderr)
            reader = BlobReader(r)
            rows = replay(r, reader, args.exclude, args.replay)
            reader.close()
            out[r.name] = {"rows": rows}
            if args.replay_tools:
                n = args.replay_tools
                idx = sorted({round(k * (len(rows) - 1) / max(n - 1, 1)) for k in range(n)})
                full = {x["sha"]: x for x in rows}
                shas = [git(r, "rev-parse", rows[j]["sha"]).strip() for j in idx]
                print(f"[{r.name}] replay tools at {len(shas)} commits", file=sys.stderr)
                out[r.name]["tools"] = [
                    dict(t, i=full[t["sha"]]["i"], date=full[t["sha"]]["date"]) for t in replay_tools(r, shas)
                ]
        text = json.dumps(out, indent=1, default=list)
        if args.json:
            args.json.write_text(text)
        else:
            print(text)
        return 0
    results = [measure(r.resolve(), args) for r in args.repos]
    out: dict = {"repos": results}
    if args.cross and len(results) > 1:
        out["cross_repo_shared_windows"] = cross_repo(results, args.window)
    for r in results:
        r.pop("_code_src", None)
    text = json.dumps(out, indent=1, default=list)
    if args.json:
        args.json.write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
