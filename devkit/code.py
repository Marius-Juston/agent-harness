"""Navigate by symbol, not by dumping files: capped answers an agent can act on.

What an agent reads stays in its context for every later call, so a whole-file read early in a run is the most
expensive thing it does (one measured repository: whole-file `cat` was 58 % of all `cat` output). These commands answer the
four questions agents actually ask, each with a hard cap:

    outline <path>        signatures + first doc line (.py), headings (.md), size + head (other)
    show <Symbol> [path]  the body of a function/class/method (Class.method), with line numbers
    refs <Symbol>         who mentions it, grouped by file, tests last
    find <regex> [--notes] lines matching, capped per file (--notes: docs/ only)

Search uses `git grep`, so it sees exactly the tracked and untracked-but-not-ignored files of this worktree.
"""

import ast
import re
import subprocess
from pathlib import Path

from devkit.paths import root

SHOW_CAP = 150
REFS_CAP = 60
PER_FILE = 6


def _grep(pattern: str, *paths: str, word: bool = False, ignore_case: bool = False) -> list[tuple[str, int, str]]:
    args = ["git", "grep", "-n", "-I", "--untracked", "-E"]
    if word:
        args.append("-w")
    if ignore_case:
        args.append("-i")
    args += ["-e", pattern, "--", *(paths or ["."])]
    out = subprocess.run(args, cwd=root(), capture_output=True, text=True, check=False)
    hits = []
    for line in out.stdout.splitlines():
        path, _, rest = line.partition(":")
        num, _, text = rest.partition(":")
        if num.isdigit():
            hits.append((path, int(num), text))
    return hits


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}"


def _doc1(node: ast.AST) -> str:
    doc = (
        ast.get_docstring(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module)
        else None
    )
    return f"  # {doc.strip().splitlines()[0][:100]}" if doc else ""


def outline_python(text: str) -> list[str]:
    tree = ast.parse(text)
    lines = []
    if doc := _doc1(tree):
        lines.append(f"module{doc}")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            lines.append(f"{node.lineno:>5}  {_signature(node)}{_doc1(node)}")
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            lines.append(f"{node.lineno:>5}  class {node.name}({bases}){_doc1(node)}")
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                    lines.append(f"{sub.lineno:>5}      {_signature(sub)}{_doc1(sub)}")
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [ast.unparse(t) for t in targets]
            if any(n.isupper() for n in names):
                lines.append(f"{node.lineno:>5}  {', '.join(names)} = ...")
    return lines


def outline_markdown(text: str) -> list[str]:
    lines, fenced = [], False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,4} ", line):
            lines.append(f"{i:>5}  {line}")
    return lines


def outline(path: str) -> str:
    p = (root() / path) if not Path(path).is_absolute() else Path(path)
    if not p.is_file():
        return f"no such file: {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    n = text.count("\n") + 1
    head = f"{path}: {n} lines"
    try:
        if p.suffix == ".py":
            body = outline_python(text)
        elif p.suffix in {".md", ".markdown"}:
            body = outline_markdown(text)
        else:
            body = [f"{i:>5}  {line}" for i, line in enumerate(text.splitlines()[:40], 1)]
    except SyntaxError as exc:
        body = [f"(cannot parse: {exc})"]
    return "\n".join([head, *body]) + "\n(next: `uv run dev code show <Symbol>`, or a `sed -n a,bp` window)"


Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _find_defs(name: str, path: str | None) -> list[tuple[str, Def]]:
    parts = name.split(".")
    leaf = parts[-1]
    files = (
        [path] if path else sorted({h[0] for h in _grep(rf"^\s*(async\s+)?(def|class)\s+{re.escape(leaf)}\b", "*.py")})
    )
    found: list[tuple[str, Def]] = []
    for f in files:
        try:
            tree = ast.parse((root() / f).read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and len(parts) == 2 and node.name == parts[0]:
                found += [(f, sub) for sub in node.body if isinstance(sub, Def) and sub.name == leaf]
            elif (
                len(parts) == 1
                and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and node.name == leaf
            ):
                found.append((f, node))
    return found


def show(name: str, path: str | None = None, full: bool = False) -> str:
    defs = _find_defs(name, path)
    if not defs:
        return f"no definition of {name} (try `uv run dev code find '{name.split('.')[-1]}'`)"
    out = []
    for f, node in defs[:3]:
        lines = (root() / f).read_text(encoding="utf-8").splitlines()
        start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
        end = node.end_lineno or node.lineno
        cap = end if full else min(end, start + SHOW_CAP - 1)
        out.append(f"== {f}:{start}-{end}")
        out += [f"{i:>5}  {lines[i - 1]}" for i in range(start, cap + 1)]
        if cap < end:
            out.append(f"... {end - cap} more lines (--full)")
    if len(defs) > 3:
        out.append(f"... and {len(defs) - 3} more definitions: pass a path")
    return "\n".join(out)


def _group(hits: list[tuple[str, int, str]], cap: int) -> str:
    by_file: dict[str, list[tuple[int, str]]] = {}
    for f, n, t in hits:
        by_file.setdefault(f, []).append((n, t))
    order = sorted(by_file, key=lambda f: (f.startswith("tests/"), f))
    out, shown = [], 0
    for f in order:
        rows = by_file[f]
        out.append(f"== {f} ({len(rows)})")
        for n, t in rows[:PER_FILE]:
            out.append(f"{n:>5}  {t.strip()[:160]}")
            shown += 1
        if len(rows) > PER_FILE:
            out.append(f"      ... {len(rows) - PER_FILE} more in this file")
        if shown >= cap:
            rest = len(order) - order.index(f) - 1
            if rest:
                out.append(f"... capped: {rest} more files; narrow the pattern or pass a path")
            break
    return "\n".join(out) if out else "no matches"


def refs(name: str) -> str:
    return _group(_grep(re.escape(name.split(".")[-1]), word=True), REFS_CAP)


def find(pattern: str, notes: bool = False, ignore_case: bool = False) -> str:
    paths = ("docs",) if notes else ()
    return _group(_grep(pattern, *paths, ignore_case=ignore_case), REFS_CAP)


def source(path: str, name: str | None = None) -> str:
    """The text of `path`, or of definition `name` (Class.method too) in it, for tools that read code (dev reuse
    --from). Raises LookupError when the file or the definition is missing."""
    p = Path(path) if Path(path).is_absolute() else root() / path
    if not p.is_file():
        raise LookupError(f"no such file: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if not name:
        return text
    defs = _find_defs(name, str(p.relative_to(root())) if p.is_relative_to(root()) else str(p))
    if not defs:
        raise LookupError(f"no definition of {name} in {path}")
    node = defs[0][1]
    start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
    return "\n".join(text.splitlines()[start - 1 : node.end_lineno or node.lineno])
