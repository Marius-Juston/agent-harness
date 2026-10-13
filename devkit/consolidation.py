"""Consolidation review: when one is due (the trigger `dev orient` shows), and the deterministic report the
`harness-ops:architect` subagent reads before it proposes anything. Both are read-only: they read git objects
(`git ls-tree`, one `git cat-file --batch`) and never touch the working tree. The review itself proposes issues;
builders execute them, so nothing here deletes progress.

Design, thresholds and their calibration: docs/research/architecture-consolidation.md §5, §6 and §8. Ported from
that note's scripts/repo_metrics.py (fast_trigger, trigger_signals). The trigger uses regexes only, so it does not
depend on the Python version; the report's AST figures do, so it lists the files that fail to parse instead of
dropping them (#113).
"""

import ast
import fnmatch
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from devkit import config

REVIEW_TRAILER = "Consolidation-Review:"
ADOPTION_FILE = ".copier-answers.yml"  # the Copier answers file: its first commit is when the harness was adopted
PYSCN = "pyscn@1.32.4"  # pinned: pyscn's own skills call @latest and keep no baseline (note §7)
VULTURE = "vulture==2.16"

COMMENT = {
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
EXCLUDE = [  # never code to review: locks, vendored, generated and built trees
    "*.lock",
    "*package-lock.json",
    "*.min.js",
    "*.min.css",
    *(f"{p}{d}/*" for p in ("", "*/") for d in ("third_party", "vendor", "vendored", "_vendor", "node_modules")),
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
INDENTED_IMPORT_RE = re.compile(r"^[ \t]+(?:import [A-Za-z_]|from [.A-Za-z_][\w.]* import )", re.M)
ANY_IMPORT_RE = re.compile(r"^[ \t]*(?:import [A-Za-z_]|from [.A-Za-z_][\w.]* import )", re.M)
IMPORT_LINE_RE = re.compile(
    r"^[ \t]*(?:from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+\(?[ \t]*([\w., \t]*)|import[ \t]+([\w., \t]+))", re.M
)


class GitError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if out.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout.decode("utf-8", "replace")


class BlobReader:
    """One long-lived ``git cat-file --batch`` for every blob of both trees."""

    def __init__(self, repo: Path):
        self.proc = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def text(self, sha: str) -> str | None:
        """The blob's text, or None for a binary blob."""
        if self.proc.stdin is None or self.proc.stdout is None:
            raise GitError("git cat-file is not running")
        self.proc.stdin.write(sha.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3 or header[1] == b"missing":
            return ""
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return None if b"\0" in data[:8000] else data.decode("utf-8", "replace")

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()


def count_loc(src: str, ext: str) -> int:
    """Non-blank lines that are not only a comment (docstrings count as code)."""
    prefix = COMMENT.get(ext)
    n = 0
    for line in src.splitlines():
        s = line.strip()
        if s and not (prefix and s.startswith(prefix) and not (ext in {".py", ".sh"} and s.startswith("#!"))):
            n += 1
    return n


def is_test(path: str) -> bool:
    return bool(TEST_RE.search(path))


def code_files(repo: Path, rev: str, exclude: list[str]) -> list[tuple[str, str]]:
    """(blob sha, path) of tracked code files at ``rev``, symlinks and excluded globs left out."""
    out = []
    for entry in _git(repo, "ls-tree", "-r", "-z", "--full-tree", rev).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob" or mode == "120000" or os.path.splitext(path)[1].lower() not in COMMENT:
            continue
        if not any(fnmatch.fnmatch(path, g) for g in EXCLUDE + exclude):
            out.append((sha, path))
    return out


def module_name(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    parts = (p[4:] if p.startswith("src/") else p).split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def unit_of(path: str) -> str:
    """Dormancy unit: the first two directory levels; a file at the root is its own unit."""
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def top(path: str, depth: int = 1) -> str:
    parts = path.split("/")
    return "/".join(parts[: min(depth, len(parts) - 1)]) or "(root)"


def regex_import_targets(src: str, mod: str, is_pkg: bool) -> list[str]:
    """Absolute dotted targets of every import statement, found by a regex (no parse, so no Python version)."""
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


def resolve(target: str, known: set[str]) -> str | None:
    parts = target.split(".")
    for k in range(len(parts), 0, -1):
        if (cand := ".".join(parts[:k])) in known:
            return cand
    return None


def tarjan(graph: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one node (import cycles), iteratively."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    for start in graph:
        if start in index:
            continue
        index[start] = low[start] = len(index)
        stack.append(start)
        on.add(start)
        work = [(start, iter(sorted(graph[start])))]
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(graph.get(w, ())))))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while (w := stack.pop()) != v:
                        on.discard(w)
                        comp.append(w)
                    on.discard(v)
                    if comp:
                        out.append(sorted([*comp, v]))
    return out


def reach(graph: dict[str, set[str]], roots: set[str]) -> set[str]:
    seen, todo = set(roots), list(roots)
    while todo:
        for w in graph.get(todo.pop(), ()):
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return seen


def signals(repo: Path, rev: str, cfg: dict, reader: BlobReader | None = None) -> dict:
    """Size and structural smells at ``rev``, from git objects and regexes only."""
    own = reader is None
    reader = reader or BlobReader(repo)
    loc: dict[str, int] = {}
    srcs: dict[str, str] = {}
    lazy = imps = 0
    try:
        for blob, path in code_files(repo, rev, cfg["exclude"]):
            src = reader.text(blob) or ""
            loc[path] = count_loc(src, os.path.splitext(path)[1].lower())
            if path.endswith(".py"):
                srcs[path] = src
                if not is_test(path):
                    lazy += len(INDENTED_IMPORT_RE.findall(src))
                    imps += len(ANY_IMPORT_RE.findall(src))
    finally:
        if own:
            reader.close()
    total = sum(loc.values())
    mods = {module_name(p): p for p in srcs}
    graph: dict[str, set[str]] = {m: set() for m in mods}
    for m, p in mods.items():
        for t in regex_import_targets(srcs[p], m, p.endswith("__init__.py")):
            if (r := resolve(t, set(mods))) and r != m:
                graph[m].add(r)
    k = cfg["dormant_k"]
    nfp = int(_git(repo, "rev-list", "--first-parent", "--count", rev))
    recent = _git(repo, "log", "--first-parent", "--diff-merges=first-parent", "--name-only", "--format=", f"-{k}", rev)
    active = {unit_of(p) for p in recent.split("\n") if p}
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
    commits = Counter(p for p in _git(repo, "log", "--no-merges", "--name-only", "--format=", rev).split("\n") if p)
    churny = sorted(
        p for p, n in loc.items() if not is_test(p) and n > cfg["churny_loc"] and commits[p] >= cfg["churny_commits"]
    )
    cycle = max(tarjan(graph), key=len, default=[])
    share = round(sum(unit_loc[u] for u in orphan) / (total or 1), 3)
    smells = {
        "dormant": share >= cfg["dormant_share"],
        "lazy-imports": imps >= cfg["lazy_min_imports"] and lazy >= cfg["lazy_share"] * imps,
        "import-cycle": len(cycle) >= cfg["lazy_cycle"],
        "churny": bool(churny),
    }
    return {
        "code_loc": total,
        "loc": loc,
        "fp_commits": nfp,
        "orphaned_dormant_share": share,
        "orphaned_dormant_units": sorted(orphan, key=lambda u: (-unit_loc[u], u)),
        "unit_loc": {u: unit_loc[u] for u in orphan},
        "imports": imps,
        "indented_import_share": round(lazy / (imps or 1), 3),
        "largest_import_cycle": len(cycle),
        "import_cycle": cycle,
        "churny_files": churny,
        "smells": [s for s, on in smells.items() if on],
    }


def last_review(repo: Path, cfg: dict) -> tuple[str, str]:
    """(commit, source) the trigger counts from: the configured ``since``, else the newest commit with a
    ``Consolidation-Review:`` trailer line, else the commit that adopted the harness, else the root commit."""
    if cfg.get("since"):
        return _git(repo, "rev-parse", "--verify", f"{cfg['since']}^{{commit}}").strip(), "config"
    if sha := _git(repo, "log", "-1", "--format=%H", f"--grep=^{REVIEW_TRAILER}", "HEAD").strip():
        return sha, "review"
    if adopted := _git(repo, "log", "--diff-filter=A", "--format=%H", "HEAD", "--", ADOPTION_FILE).split():
        return adopted[-1], "adoption"
    return _git(repo, "rev-list", "--first-parent", "--max-parents=0", "HEAD").split()[-1], "root"


def _detail(smell: str, s: dict) -> str:
    return {
        "dormant": lambda: f"orphaned dormant {', '.join(s['orphaned_dormant_units'][:5])}",
        "lazy-imports": lambda: f"{s['indented_import_share']:.0%} of {s['imports']} imports inside functions",
        "import-cycle": lambda: f"{s['largest_import_cycle']} modules",
        "churny": lambda: ", ".join(s["churny_files"][:5]),
    }[smell]()


def due(repo: Path, cfg: dict | None = None, now: float | None = None) -> dict:
    """The composite trigger (note §6, §8 item 1): due when the code grew by ``growth`` times and by at least
    ``growth_min_loc`` LOC, or ``fp_commits`` first-parent commits landed since the last review, or a smell is
    present now that was absent then (or more large churny files). Never reviewed (adoption or root base): every
    standing smell counts as new, so the first review sees them all. Days since the review are information only."""
    cfg = cfg or config.get().consolidation
    t0 = time.perf_counter()
    base, source = last_review(repo, cfg)
    reader = BlobReader(repo)
    try:
        head = signals(repo, "HEAD", cfg, reader)
        then = signals(repo, base, cfg, reader)
    finally:
        reader.close()
    reviewed = source in ("config", "review")
    before = set(then["smells"]) if reviewed else set()
    why = []
    grown = head["code_loc"] - then["code_loc"]
    if grown >= cfg["growth_min_loc"] and head["code_loc"] >= cfg["growth"] * then["code_loc"]:
        why.append(f"code grew {then['code_loc']} -> {head['code_loc']} LOC")
    if (n := head["fp_commits"] - then["fp_commits"]) >= cfg["fp_commits"]:
        why.append(f"{n} first-parent commits since the last review")
    why += [f"new smell: {s} ({_detail(s, head)})" for s in head["smells"] if s not in before]
    if "churny" in before and (more := sorted(set(head["churny_files"]) - set(then["churny_files"]))):
        why.append(f"more large churny files: {', '.join(more[:5])}")
    stamp = int(_git(repo, "show", "-s", "--format=%ct", base).strip())
    return {
        "review_due": bool(why),
        "why": why,
        "base": base[:9],
        "base_source": source,
        "days_since_base": round(((time.time() if now is None else now) - stamp) / 86400, 1),
        "head": head,
        "at_base": then,
        "seconds": round(time.perf_counter() - t0, 2),
    }


def orient_line(repo: Path, cfg: dict | None = None) -> str | None:
    """One line for `dev orient` when a review is due, else None (also outside a repository with commits)."""
    probe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True)
    if probe.returncode != 0:
        return None
    r = due(repo, cfg)
    if not r["review_due"]:
        return None
    return (
        f"## Consolidation review due ({'; '.join(r['why'])})"[:300]
        + ": lead dispatches harness-ops:architect (`uv run dev consolidation report`)"
    )


# ---------------------------------------------------------------- the report
def cli_commands(tree: ast.AST) -> list[str]:
    """Subcommand names from argparse code: ``x.add_parser("name")`` calls with a literal first argument."""
    return sorted(
        {
            n.args[0].value
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add_parser"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)
        }
    )


def _own_package(repo: Path) -> str | None:
    """The installed devkit's directory relative to ``repo`` when the repository is the harness itself."""
    pkg = Path(__file__).resolve().parent
    try:
        return pkg.relative_to(repo.resolve()).as_posix() + "/"
    except ValueError:
        return None


def by_dir(loc: dict[str, int], depth: int) -> Counter[str]:
    c: Counter[str] = Counter()
    for p, n in loc.items():
        c[top(p, depth)] += n
    return c


def run_tools(repo: Path) -> dict:
    """pyscn (pinned) and vulture (pinned) on a ``git archive`` copy of HEAD; slow, so only on request."""
    if not shutil.which("uvx"):
        return {"skipped": "uvx not found"}
    tmp = Path(tempfile.mkdtemp(prefix="devkit_consolidation_"))
    try:
        data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "HEAD"], capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extractall(tmp, filter="data")
        out: dict = {}
        js = tmp / "_pyscn.json"
        subprocess.run(
            ["uvx", PYSCN, "analyze", "--json", "--no-open", "--output", str(js), str(tmp)], capture_output=True
        )
        if js.exists():
            s = json.loads(js.read_text()).get("summary", {})
            keys = ("health_score", "grade", "code_duplication_percentage", "clone_groups", "deps_modules_in_cycles")
            out["pyscn"] = {k: s.get(k) for k in keys}
        py = sorted(p.relative_to(tmp).as_posix() for p in tmp.rglob("*.py"))
        py = [p for p in py if not is_test(p) and not any(fnmatch.fnmatch(p, g) for g in EXCLUDE)]
        if py:
            res = subprocess.run(
                ["uvx", VULTURE, "--min-confidence", "60", *py], cwd=tmp, capture_output=True, text=True
            )
            hits = re.findall(r"^(.+?):\d+: (.+?) \((\d+)% confidence", res.stdout, re.M)
            out["vulture"] = {
                "findings": len(hits),
                "90_plus": sum(int(c) >= 90 for _, _, c in hits),
                "top_files": Counter(f for f, _, _ in hits).most_common(8),
            }
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def report(repo: Path, names: set[str], cfg: dict | None = None, tools: bool = False, now: float | None = None) -> dict:
    """What the architect reads first (note §8 item 2): the trigger's verdict, standing smells, LOC by directory
    and its growth since the review, argparse command names that repeat ``names`` (the installed tooling's), and
    files that do not parse."""
    cfg = cfg or config.get().consolidation
    r = due(repo, cfg, now)
    head, then = r.pop("head"), r.pop("at_base")
    now_dir, then_dir = by_dir(head["loc"], 2), by_dir(then["loc"], 2)
    growth = sorted(
        ([d, then_dir[d], now_dir[d], now_dir[d] - then_dir[d]] for d in set(now_dir) | set(then_dir)),
        key=lambda row: (-row[3], row[0]),
    )
    own = _own_package(repo)
    overlap: dict[str, list[str]] = {}
    broken: list[str] = []
    reader = BlobReader(repo)
    try:
        for blob, path in code_files(repo, "HEAD", cfg["exclude"]):
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(reader.text(blob) or "")
            except (SyntaxError, ValueError):
                broken.append(path)
                continue
            if own and path.startswith(own):
                continue
            if hits := sorted(set(cli_commands(tree)) & names):
                overlap[path] = hits
    finally:
        reader.close()
    return {
        **r,
        "code_loc": {"base": then["code_loc"], "head": head["code_loc"]},
        "fp_commits_since_base": head["fp_commits"] - then["fp_commits"],
        "loc_by_dir": by_dir(head["loc"], 1).most_common(),
        "growth_by_dir": growth[:10],
        "smells": head["smells"],
        "orphaned_dormant": [[u, head["unit_loc"][u]] for u in head["orphaned_dormant_units"]],
        "orphaned_dormant_share": head["orphaned_dormant_share"],
        "largest_import_cycle": head["import_cycle"],
        "indented_import_share": head["indented_import_share"],
        "imports": head["imports"],
        "churny_files": head["churny_files"],
        "command_overlap": overlap,
        "parse_errors": {"count": len(broken), "paths": broken},
        "tools": run_tools(repo) if tools else {},
        "thresholds": {k: v for k, v in cfg.items() if k != "since"},
    }


def render(r: dict) -> str:
    n = f"{r['code_loc']['head']:,}"
    out = [
        f"# Consolidation report (base {r['base']}: {r['base_source']}, {r['days_since_base']} days ago)",
        f"review due: {'yes: ' + '; '.join(r['why']) if r['review_due'] else 'no'}",
        f"code LOC {r['code_loc']['base']:,} -> {n}; {r['fp_commits_since_base']} first-parent commits since base",
        "",
        "## LOC by top-level directory",
        "  " + "; ".join(f"{d} {v:,}" for d, v in r["loc_by_dir"][:12]),
        "## Fastest-growing directories since base",
        *(f"  {d:<32} {a:>7,} -> {b:>7,} ({c:+,})" for d, a, b, c in r["growth_by_dir"] if c),
        "## Smells now (thresholds: [tool.devkit.consolidation])",
        f"  standing: {', '.join(r['smells']) or 'none'}",
        f"  orphaned dormant units ({r['orphaned_dormant_share']:.1%} of code): "
        + (", ".join(f"{u} ({v:,} LOC)" for u, v in r["orphaned_dormant"]) or "none"),
        f"  largest import cycle: {len(r['largest_import_cycle'])} modules"
        + (f": {', '.join(r['largest_import_cycle'][:12])}" if r["largest_import_cycle"] else ""),
        f"  imports inside functions: {r['indented_import_share']:.1%} of {r['imports']}",
        f"  large churny files: {', '.join(r['churny_files']) or 'none'}",
        "## Command names that repeat the installed `dev` tooling",
        *([f"  {p}: {', '.join(c)}" for p, c in r["command_overlap"].items()] or ["  none"]),
        "## Python files that fail to parse (their AST figures are missing)",
    ]
    pe = r["parse_errors"]
    if pe["count"]:
        out.append(
            f"  WARNING: {pe['count']} Python file(s) failed to parse under this Python: run the report "
            "under the project's Python before trusting the command overlap"
        )
        out += [f"  {p}" for p in pe["paths"][:20]] + ([f"  ... {pe['count'] - 20} more"] if pe["count"] > 20 else [])
    else:
        out.append("  none")
    if r["tools"]:
        out += ["## Tools (pinned)", *(f"  {k}: {json.dumps(v)}" for k, v in r["tools"].items())]
    out.append(f"({r['seconds']} s for the trigger)")
    return "\n".join(out)
