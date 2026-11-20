"""Navigate by symbol, not by dumping files: capped answers an agent can act on.

What an agent reads stays in its context for every later call, so a whole-file read early in a run is the most
expensive thing it does (one measured repository: whole-file `cat` was 58 % of all `cat` output). These commands answer the
four questions agents actually ask, each with a hard cap:

    outline <path>        signatures + first doc line (.py), headings (.md), size + head (other)
    show <Symbol> [path]  the body of a function/class/method (Class.method), with line numbers
    refs <Symbol>         who mentions it, grouped by file, tests last
    find <regex> [--notes] lines matching, capped per file (--notes: docs/ only)
    search "<words>" [-n N] [--tests]  the N functions a task description most likely means, one line each

refs and find use `git grep`, so they see exactly the tracked and untracked-but-not-ignored files of this worktree;
search ranks the functions and methods of the same .py files outside docs/ (and outside tests unless --tests) by
BM25 over path words, qualname, signature, first docstring paragraph and the body's identifiers, attribute names and
string literals: on 47 questions replayed from history it put the answer in its 5 cards for 0.53 of them, against
0.17 for one `find` call, at 1/26 of the tokens per answer (docs/research/semantic-code-search.md §3.1).
"""

import ast
import contextlib
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from devkit.paths import root, state_dir

SHOW_CAP = 150
REFS_CAP = 60
PER_FILE = 6
SEARCH_N = 5
SEARCH_LITERAL_MAX = 300  # longer string literals are prose or templates, not names a task would use
SEARCH_CACHE_FILES = 5000  # parsed files kept in the shared cache before unused entries are dropped
SEARCH_FORMAT = b"code-search 1\0"  # part of every cache key: change it when _parse_units changes


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
    --from, which sends its words to search APIs). Raises LookupError when the file or the definition is missing, or
    the file is outside the repository."""
    p = (Path(path) if Path(path).is_absolute() else root() / path).resolve()
    if not p.is_relative_to(root().resolve()):
        raise LookupError(f"outside the repository: {path}")
    if not p.is_file():
        raise LookupError(f"no such file: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if not name:
        return text
    defs = _find_defs(name, str(p.relative_to(root().resolve())))
    if not defs:
        raise LookupError(f"no definition of {name} in {path}")
    node = defs[0][1]
    start = min([node.lineno, *[d.lineno for d in node.decorator_list]])
    return "\n".join(text.splitlines()[start - 1 : node.end_lineno or node.lineno])


def is_test(path: str) -> bool:
    """Test and eval-fixture code, which `search` leaves out unless asked: test functions named after a feature
    crowd its top 5 (with them, the replay's top 5 fell from 0.53 to 0.28)."""
    p = PurePosixPath(path)
    return (
        p.parts[0] in {"tests", "evals"}
        or "tests" in p.parts[:-1]
        or p.name.startswith("test_")
        or p.name == "conftest.py"
    )


def _words(text: str) -> list[str]:
    from devkit.board import STOP_WORDS
    from devkit.reuse import words

    return [w for w in words(text) if len(w) > 1 and w not in STOP_WORDS]


@dataclass(frozen=True)
class Unit:
    """A function or method of one file, as `search` ranks and prints it."""

    path: str
    line: int
    qualname: str  # Class.method for a method
    signature: str  # (args) -> returns
    doc: str  # the docstring's first line
    words: tuple[str, ...]  # what BM25 indexes: path words, then the unit's own

    def card(self) -> str:
        return f"{self.path}:{self.line}  {self.qualname}{self.signature}" + (f"  # {self.doc}" if self.doc else "")


def _body_words(node: ast.AST) -> list[str]:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.append(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) < SEARCH_LITERAL_MAX:
            out.append(sub.value)
    return out


def _parse_units(text: str) -> list[list]:
    """[qualname, line, signature, doc, words] of every function and method in `text`, without the path (so it caches
    by content). A function nested in a function belongs to the outer one."""
    out: list[list] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for sub in ast.iter_child_nodes(node):
            if isinstance(sub, ast.ClassDef):
                visit(sub, f"{prefix}{sub.name}.")
            elif isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname, sig = f"{prefix}{sub.name}", _signature(sub)
                doc = ast.get_docstring(sub) or ""
                para = doc.split("\n\n")[0].replace("\n", " ")
                first = doc.strip().splitlines()[0][:100] if doc.strip() else ""
                text = " ".join([qualname, sig, para, *_body_words(sub)])
                out.append([qualname, sub.lineno, sig[sig.index("(") :], first, " ".join(_words(text))])

    visit(ast.parse(text), "")
    return out


def _search_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "-c", "-o", "--exclude-standard", "--", "*.py"],
        cwd=root(),
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return sorted({f for f in out.split("\0") if f and not f.startswith("docs/")})


def units() -> list[Unit]:
    """Every function and method of this worktree's .py files outside docs/. Parsing is most of a cold run, so parsed
    files are cached by content hash in <git-common-dir>/devkit/code-search/, shared by every worktree and tree."""
    cache_file = state_dir("code-search") / "units.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    used, added, found = set(), False, []
    top = root()
    for rel in _search_files():
        try:
            data = (top / rel).read_bytes()
        except OSError:
            continue  # deleted in the worktree, still in the index
        key = hashlib.sha256(SEARCH_FORMAT + data).hexdigest()[:32]
        used.add(key)
        if key not in cache:
            try:
                cache[key] = _parse_units(data.decode("utf-8", errors="replace"))
            except (SyntaxError, ValueError):
                cache[key] = []
            added = True
        path_words = tuple(_words(rel))
        found += [Unit(rel, line, q, sig, doc, path_words + tuple(w.split())) for q, line, sig, doc, w in cache[key]]
    if added:
        if len(cache) > SEARCH_CACHE_FILES:
            cache = {k: v for k, v in cache.items() if k in used}
        tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
        with contextlib.suppress(OSError):
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, cache_file)  # atomic: a concurrent reader sees the old file or the new one, never half
    return found


def query_words(query: str) -> list[str]:
    from devkit.board import terms

    return _words(" ".join(terms(query)))


def rank(pool: list[Unit], query: str) -> list[Unit]:
    """`pool` by BM25 score for `query`, best first (ties in pool order); a unit sharing no word with it is left out."""
    from devkit.board import bm25

    scores = bm25([list(u.words) for u in pool], query_words(query))
    return [pool[i] for i in sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])]


def search(query: str, n: int = SEARCH_N, tests: bool = False) -> str:
    hits = rank([u for u in units() if tests or not is_test(u.path)], query)[: max(n, 1)]
    if not hits:
        more = "" if tests else ", or --tests"
        return f"no function matches {query!r} (try other words, `uv run dev code find <regex>`{more})"
    return "\n".join(u.card() for u in hits)
