"""`dev arch`: a deterministic architecture report of at most 8 lines, and the gate's arch stage (#527, #529).

Lines: declared import contracts ([tool.devkit.arch], checked on devkit's own import graph, so no tool to install),
the largest import cycle, the top 3 hotspots (first-parent commits touching a file since the last consolidation
review, at most the last 30, times its LOC) and the modules over the LOC ceiling. The gate stage fails on a broken
contract, on an import cycle larger than `.devkit/ratchets.toml` [arch] allows, and on a module over the ceiling
that is unpinned or above its pin; hotspots are a rank, so they are never gated. Design and replay evidence:
docs/research/architecture-grading.md §3-§7.

The import graph is parsed with `ast` (a file that does not parse falls back to consolidation's regex). Each import
statement is an edge to the module it names: `from pkg import mod` to `pkg.mod` when that is a module, else to
`pkg`; relative imports resolve against the importing module. Imports inside functions are edges (they run when
called; PR #417's `checks -> cli` was one). Imports under `if TYPE_CHECKING:` (or `typing.TYPE_CHECKING`) never run
and are left out unless `type_checking_imports` is true; their `else:` branch runs and counts. Not seen: imports by
string (`importlib.import_module`, `__import__`).
"""

import ast
import os
import re
import time
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from devkit import config, consolidation
from devkit.paths import git, root
from devkit.ran import Ran

WINDOW = 30  # hotspot churn: first-parent commits counted, at most (note §3: the window that ranked workflow.py)
TOP = 3
MAX_LINES = 8
SHOWN_EDGES = 3  # edges to cut printed per broken contract (note §4: the chains themselves were 26 lines)
SHOWN_CONTRACT_LINES = 2
# `.devkit/ratchets.toml` [arch]: values that only tighten; raising one needs an owner-approved [[raise]] with key
# "arch.<key>" (a pin: "arch.module_loc.<path>"). Defaults apply when the table or a key is absent.
RATCHET_DEFAULTS = {"module_loc_ceiling": 1000, "max_import_cycle": 0}


@dataclass(frozen=True)
class Import:
    line: int
    kind: str  # "module" (runs at import), "function" (runs when called) or "type-checking" (never runs)


Graph = dict[str, dict[str, list[Import]]]  # importer -> imported -> the import statements


# ---------------------------------------------------------------- reading code


def tree_files(repo: Path, exclude: list[str]) -> dict[str, str]:
    """path -> text of the working tree's code files (tracked or untracked, not ignored), as the gate sees them."""
    out = {}
    listed = git("-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for path in dict.fromkeys(listed.split("\0")):
        p = repo / path
        if not path or not consolidation.is_code(path, exclude) or p.is_symlink() or not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" not in data[:8000]:
            out[path] = data.decode("utf-8", "replace")
    return out


def rev_files(repo: Path, rev: str, exclude: list[str]) -> dict[str, str]:
    """path -> text of the code files committed at ``rev`` (read from git objects, no checkout)."""
    reader = consolidation.BlobReader(repo)
    try:
        texts = {path: reader.text(sha) for sha, path in consolidation.code_files(repo, rev, exclude)}
    finally:
        reader.close()
    return {p: t for p, t in texts.items() if t is not None}


# ---------------------------------------------------------------- the import graph


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _statements(tree: ast.AST) -> list[tuple[ast.Import | ast.ImportFrom, str]]:
    """Every import statement with its kind, in source order."""
    out: list[tuple[ast.Import | ast.ImportFrom, str]] = []
    work: list[tuple[ast.AST, str]] = [(tree, "module")]
    while work:
        node, kind = work.pop()
        if isinstance(node, ast.Import | ast.ImportFrom):
            out.append((node, kind))
            continue
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            work += [(n, "type-checking") for n in node.body]
            work += [(n, kind) for n in node.orelse]
            continue
        inner = (
            "function"
            if kind == "module" and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
            else kind
        )
        work += [(n, inner) for n in ast.iter_child_nodes(node)]
    return sorted(out, key=lambda s: s[0].lineno)


def _from_base(node: ast.ImportFrom, mod: str, is_pkg: bool) -> str:
    if not node.level:
        return node.module or ""
    pkg = mod.split(".") if is_pkg else mod.split(".")[:-1]
    pkg = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
    return ".".join([*pkg, node.module] if node.module else pkg)


def imports_of(src: str, mod: str, is_pkg: bool, known: set[str]) -> list[tuple[str, Import]]:
    """(imported module, the import) for each module an import statement names; an unknown target is kept as the
    dotted name written (an external module, for forbidden contracts)."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        targets = consolidation.regex_import_targets(src, mod, is_pkg)
        return [(consolidation.resolve(t, known) or t, Import(0, "module")) for t in targets]
    out = []
    for node, kind in _statements(tree):
        imp = Import(node.lineno, kind)
        if isinstance(node, ast.Import):
            out += [(consolidation.resolve(a.name, known) or a.name, imp) for a in node.names]
            continue
        base = _from_base(node, mod, is_pkg)
        for a in node.names:
            sub = f"{base}.{a.name}" if base else a.name
            target = sub if sub in known else consolidation.resolve(base, known) or base
            if target:
                out.append((target, imp))
    return out


def build_graph(files: dict[str, str], type_checking: bool = False) -> tuple[Graph, set[str]]:
    """(graph, the modules defined by ``files``): every .py file is a module named as consolidation names it."""
    mods = {consolidation.module_name(p): p for p in sorted(files) if p.endswith(".py")}
    known = set(mods)
    graph: Graph = {m: {} for m in mods}
    for m, p in mods.items():
        for target, imp in imports_of(files[p], m, p.endswith("__init__.py"), known):
            if target != m and (type_checking or imp.kind != "type-checking"):
                graph[m].setdefault(target, []).append(imp)
    return graph, known


def _adjacency(graph: Graph, keep: set[str]) -> dict[str, set[str]]:
    return {m: {t for t in ts if t in keep} for m, ts in graph.items() if m in keep}


def largest_cycle(graph: Graph, known: set[str]) -> list[str]:
    """The largest strongly connected component of the non-test modules (an import cycle), sorted; [] if none."""
    mods = {m for m in known if not consolidation.is_test(m.replace(".", "/") + ".py")}
    return max(consolidation.tarjan(_adjacency(graph, mods)), key=len, default=[])


# ---------------------------------------------------------------- contracts


def _pattern(p: str) -> re.Pattern[str]:
    """A contract module pattern: the module or any descendant; `*` is one name part."""
    body = r"\.".join(r"[^.]+" if part == "*" else re.escape(part) for part in p.split("."))
    return re.compile(rf"{body}(?:\..+)?")


def _matching(patterns: list[str], names: set[str]) -> set[str]:
    regs = [_pattern(p) for p in patterns]
    return {n for n in names if any(r.fullmatch(n) for r in regs)}


def _weight(imps: list[Import]) -> tuple[int, int]:
    """Cost of cutting an edge: its import statements, those at module level counted again (a lazy import is the
    cheaper cut)."""
    return len(imps), sum(i.kind == "module" for i in imps)


def edges_to_cut(adj: dict[str, set[str]], weight: dict[tuple[str, str], tuple[int, int]]) -> list[tuple[str, str]]:
    """A minimal set of edges whose removal leaves ``adj`` acyclic: drop the lightest edge inside a cycle until none
    is left, then put back every dropped edge that closes no cycle on its own (heaviest first)."""
    adj = {u: set(vs) for u, vs in adj.items()}
    cut: list[tuple[str, str]] = []
    while comps := consolidation.tarjan(adj):
        where = {m: i for i, c in enumerate(comps) for m in c}
        inside = [(u, v) for u, vs in adj.items() for v in vs if u in where and where.get(v) == where[u]]
        u, v = min(inside, key=lambda e: (weight.get(e, (1, 1)), e))
        adj[u].discard(v)
        cut.append((u, v))
    for u, v in sorted(cut, key=lambda e: (weight.get(e, (1, 1)), e), reverse=True):
        adj[u].add(v)
        if consolidation.tarjan(adj):
            adj[u].discard(v)
        else:
            cut.remove((u, v))
    return sorted(cut)


def _sibling(prefix: str, m: str) -> str | None:
    """The direct submodule of the package ``prefix`` (with its trailing dot) that ``m`` is or lies under."""
    return prefix + m[len(prefix) :].split(".")[0] if m.startswith(prefix) else None


def _acyclic(contract: dict, graph: Graph, known: set[str]) -> list[tuple[str, str]]:
    """Edges to cut so that no package at or under an ancestor has an import cycle among its direct submodules (an
    import of a deeper module counts as one of the submodule it lies under), package by package."""
    packages = {".".join(m.split(".")[:k]) for m in known for k in range(1, m.count(".") + 1)}
    cut: list[tuple[str, str]] = []
    for ancestor in sorted(_matching(contract["ancestors"], packages)):
        prefix = ancestor + "."
        adj: dict[str, set[str]] = defaultdict(set)
        weight: dict[tuple[str, str], list[Import]] = defaultdict(list)
        for m, targets in graph.items():
            if (u := _sibling(prefix, m)) is None:
                continue
            adj.setdefault(u, set())
            for t, imps in targets.items():
                if (v := _sibling(prefix, t)) is not None and v != u and t in known:
                    adj[u].add(v)
                    weight[u, v] += imps
        cut += edges_to_cut(dict(adj), {e: _weight(i) for e, i in weight.items()})
    return cut


def _forbidden(contract: dict, graph: Graph, known: set[str]) -> list[tuple[str, str]]:
    """The edges that enter a forbidden module on some import path from a source module."""
    names = known | {t for ts in graph.values() for t in ts}
    banned = _matching(contract["forbidden_modules"], names)
    seen = set(_matching(contract["source_modules"], known)) - banned
    work, entries = sorted(seen), set()
    while work:
        u = work.pop()
        for t in graph.get(u, {}):
            if t in banned:
                entries.add((u, t))
            elif t in known and t not in seen:
                seen.add(t)
                work.append(t)
    return sorted(entries)


def check_contracts(contracts: list[dict], graph: Graph, known: set[str]) -> list[dict]:
    """Each contract with `kept` and the import edges to cut (acyclic) or that reach a forbidden module."""
    out = []
    for c in contracts:
        edges = (_acyclic if c["type"] == "acyclic_siblings" else _forbidden)(c, graph, known)
        lines = {e: [i.line for i in graph[e[0]].get(e[1], [])] for e in edges}
        out.append(
            {
                "name": c["name"],
                "type": c["type"],
                "kept": not edges,
                "edges": [{"from": u, "to": v, "lines": lines[u, v]} for u, v in edges],
            }
        )
    return out


# ---------------------------------------------------------------- hotspots


def churn(repo: Path, rev: str, since: str = "", window: int = WINDOW) -> tuple[Counter[str], int]:
    """(path -> first-parent commits touching it, commits counted): the last ``window`` first-parent commits up to
    ``rev``, stopping at the newest one carrying a `Consolidation-Review:` trailer (or at ``since``), so the commits
    of a review's own refactor do not keep the split file first. A merge counts its diff against its first parent."""
    stop = git("-C", str(repo), "rev-parse", "--verify", f"{since}^{{commit}}") if since else ""
    key = consolidation.REVIEW_TRAILER.rstrip(":")
    fmt = f"--format=%x1e%H %(trailers:key={key},valueonly,separator=%x2C)"
    log = git(
        "-C", str(repo), "log", "--first-parent", "--diff-merges=first-parent", "--name-only", f"-{window}", fmt, rev
    )
    counts: Counter[str] = Counter()
    n = 0
    for record in log.split("\x1e"):
        if not record.strip():
            continue
        head, *paths = record.strip().split("\n")
        sha, _, trailer = head.partition(" ")
        if trailer.strip() or sha == stop:
            break
        n += 1
        counts.update({p for p in paths if p})
    return counts, n


# ---------------------------------------------------------------- ratchets


def ratchet(text: str) -> dict:
    """The [arch] table of a ratchets.toml with defaults filled: module_loc_ceiling, max_import_cycle and the pins
    (module_loc: path -> LOC). ValueError if malformed."""
    return ratchet_table(tomllib.loads(text).get("arch", {}))


def ratchet_table(table: object) -> dict:
    """``ratchet`` of an already parsed [arch] table."""
    if not isinstance(table, dict):
        raise ValueError("[arch]: expected a table")
    if unknown := sorted(set(table) - {*RATCHET_DEFAULTS, "module_loc"}):
        raise ValueError(f"[arch]: unknown key(s) {unknown}; known: {sorted([*RATCHET_DEFAULTS, 'module_loc'])}")
    out = {**RATCHET_DEFAULTS, **{k: v for k, v in table.items() if k != "module_loc"}}
    pins = table.get("module_loc", {})
    values = [*(out[k] for k in RATCHET_DEFAULTS), *(pins.values() if isinstance(pins, dict) else [None])]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
        raise ValueError("[arch]: every value (and each [arch.module_loc] pin) must be a whole number")
    return {**out, "module_loc": dict(pins)}


def ratchet_keys(table: dict) -> dict[str, int]:
    """The [arch] values as [[raise]] keys: "arch.<key>" and "arch.module_loc.<path>"."""
    return {
        **{f"arch.{k}": table[k] for k in RATCHET_DEFAULTS},
        **{f"arch.module_loc.{p}": n for p, n in table["module_loc"].items()},
    }


# ---------------------------------------------------------------- the report


def measure(repo: Path, rev: str | None = None, cfg: config.Config | None = None) -> dict:
    """Everything `dev arch` reports, at ``rev`` (git objects) or, when None, in the working tree."""
    cfg = cfg or config.get()
    t0 = time.perf_counter()
    exclude = cast(list[str], cfg.consolidation["exclude"])
    files = tree_files(repo, exclude) if rev is None else rev_files(repo, rev, exclude)
    graph, known = build_graph(files, bool(cfg.arch["type_checking_imports"]))
    loc = {p: consolidation.count_loc(t, os.path.splitext(p)[1].lower()) for p, t in files.items()}
    code = {p: n for p, n in loc.items() if not consolidation.is_test(p)}
    has_head = rev is not None or bool(git("-C", str(repo), "rev-parse", "--verify", "-q", "HEAD", check=False))
    counts, n = churn(repo, rev or "HEAD", str(cfg.consolidation["since"])) if has_head else (Counter(), 0)
    ranked = sorted(((counts[p] * n_loc, p) for p, n_loc in code.items() if counts[p]), key=lambda s: (-s[0], s[1]))
    try:
        rat = ratchet((repo / cfg.ratchets).read_text()) if rev is None else _rev_ratchet(repo, rev, cfg.ratchets)
    except (OSError, RuntimeError, ValueError, tomllib.TOMLDecodeError):  # none or unreadable: the defaults
        rat = ratchet_table({})
    ceiling = rat["module_loc_ceiling"]
    return {
        "rev": rev or "working tree",
        "modules": len(known),
        "contracts": check_contracts(cast(list[dict], cfg.arch["contracts"]), graph, known),
        "largest_import_cycle": largest_cycle(graph, known),
        "max_import_cycle": rat["max_import_cycle"],
        "churn_commits": n,
        "hotspots": [{"path": p, "commits": counts[p], "loc": code[p], "score": s} for s, p in ranked[:TOP]],
        "module_loc_ceiling": ceiling,
        "over_ceiling": [
            {"path": p, "loc": code[p], "pin": rat["module_loc"].get(p)}
            for p in sorted(code, key=lambda p: (-code[p], p))
            if code[p] > ceiling
        ],
        "pins": rat["module_loc"],
        "loc": code,
        "seconds": round(time.perf_counter() - t0, 2),
    }


def _rev_ratchet(repo: Path, rev: str, rel: str) -> dict:
    return ratchet(git("-C", str(repo), "show", f"{rev}:{rel}"))


def _short(name: str, contract: dict) -> str:
    """A sibling edge end without its ancestor package, as import-linter prints it."""
    if contract["type"] != "acyclic_siblings":
        return name
    return name.rsplit(".", 1)[-1]


def render(r: dict) -> list[str]:
    """The report: at most MAX_LINES lines, each with the number behind it."""
    lines = []
    broken = [c for c in r["contracts"] if not c["kept"]]
    if not r["contracts"]:
        lines.append("contracts: none declared ([[tool.devkit.arch.contracts]] in pyproject.toml)")
    elif not broken:
        lines.append(f"contracts: {len(r['contracts'])} kept")
    for i, c in enumerate(broken):
        if i == SHOWN_CONTRACT_LINES - 1 and len(broken) > SHOWN_CONTRACT_LINES:
            lines.append(f"contracts: {len(broken) - i} more BROKEN (dev arch --json)")
            break
        verb = "cut" if c["type"] == "acyclic_siblings" else "imports"
        shown = [f"{_short(e['from'], c)} -> {_short(e['to'], c)}" for e in c["edges"][:SHOWN_EDGES]]
        more = f" (+{len(c['edges']) - SHOWN_EDGES} more)" if len(c["edges"]) > SHOWN_EDGES else ""
        lines.append(f"contract BROKEN {c['name']!r}: {verb} {', '.join(shown)}{more}")
    cyc = r["largest_import_cycle"]
    names = ", ".join(cyc[:8]) + (", ..." if len(cyc) > 8 else "")
    lines.append(
        f"largest import cycle: {len(cyc)} modules (ratchet {r['max_import_cycle']})" + (f": {names}" if cyc else "")
    )
    if not r["hotspots"]:
        lines.append("hotspots: none (no commits since the last consolidation review)")
    for h in r["hotspots"]:
        lines.append(f"hotspot: {h['path']} {h['commits']} of {r['churn_commits']} recent commits x {h['loc']:,} LOC")
    over = [
        f"{o['path']} {o['loc']:,}" + (f" (pin {o['pin']:,})" if o["pin"] is not None else " (no pin)")
        for o in r["over_ceiling"]
    ]
    lines.append(f"over the {r['module_loc_ceiling']:,}-LOC module ceiling: {', '.join(over) or 'none'}")
    return lines[:MAX_LINES]


# ---------------------------------------------------------------- the gate stage


def check_arch(repo: Path | None = None, cfg: config.Config | None = None) -> Ran:
    """Contracts kept, the largest import cycle within its ratchet, and every module over the LOC ceiling pinned
    and at or below its pin. Raising a ratchet value is checked by checks.check_ratchets ([[raise]])."""
    repo = repo or root()
    cfg = cfg or config.get()
    ran = Ran("arch (contracts kept, import cycle and module LOC ratchets)")
    try:
        ratchet((repo / cfg.ratchets).read_text())
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        ran.problems.append(f"{cfg.ratchets}: {e}")
        return ran
    r = measure(repo, None, cfg)
    ran.checked = len(r["loc"])
    ran.allow_empty = True  # a project with no code yet has nothing to grade
    for c in r["contracts"]:
        if not c["kept"]:
            shown = [f"{e['from']} -> {e['to']} (l.{','.join(map(str, e['lines']))})" for e in c["edges"][:5]]
            what = "cut" if c["type"] == "acyclic_siblings" else "forbidden import"
            ran.problems.append(f"contract BROKEN {c['name']!r}: {what} {'; '.join(shown)}")
    cyc, most = r["largest_import_cycle"], r["max_import_cycle"]
    if len(cyc) > most:
        ran.problems.append(
            f"import cycle of {len(cyc)} modules > [arch] max_import_cycle = {most}: {', '.join(cyc[:10])}. "
            "Break it, or ask the owner for a [[raise]] of arch.max_import_cycle"
        )
    elif len(cyc) < most:
        ran.warnings.append(f"largest import cycle is {len(cyc)} modules: lower [arch] max_import_cycle to {len(cyc)}")
    ceiling = r["module_loc_ceiling"]
    for o in r["over_ceiling"]:
        if o["pin"] is None:
            ran.problems.append(
                f"{o['path']}: {o['loc']} LOC crossed the {ceiling}-LOC module ceiling. Split it, or ask the owner "
                f"for a [[raise]] of arch.module_loc.{o['path']} and pin it in [arch.module_loc]"
            )
        elif o["loc"] > o["pin"]:
            ran.problems.append(
                f"{o['path']}: {o['loc']} LOC > its pin {o['pin']} in [arch.module_loc]. Shrink it, or ask the owner "
                f"for a [[raise]] of arch.module_loc.{o['path']}"
            )
        elif o["loc"] < o["pin"]:
            ran.warnings.append(f"{o['path']}: {o['loc']} LOC: lower its [arch.module_loc] pin from {o['pin']}")
    for path in sorted(set(r["pins"]) - {o["path"] for o in r["over_ceiling"]}):
        ran.warnings.append(f"{path}: at or under the module ceiling (or gone): remove its [arch.module_loc] pin")
    return ran
