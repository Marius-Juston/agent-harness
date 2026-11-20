"""The retrieval units of a tree: every function and method of every tracked Python file outside docs/ (#495).

A unit is `path::Qualname` (nested functions belong to the outermost function, as in mine.py). Each unit has a
`head` text (path words, qualname, signature, first docstring paragraph) and a `full` text (head plus the body's
identifiers, attribute names and string literals), and a one-line `card` an agent would be shown:
`path:line  def name(args)  # first doc line`. Tokens are estimated as characters / 4 (ASSUMED proxy; the arms are
compared by ratio).
"""

import ast
import re
import subprocess
import tarfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

SKIP = ("docs/",)


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


@dataclass
class Unit:
    key: str  # path::Qualname
    path: str
    line: int
    end: int
    card: str
    head: str
    full: str


def extract(sha: str, dest: Path) -> Path:
    """The whole tree at `sha` under dest/sha (cached)."""
    out = dest / sha
    if not out.exists():
        data = subprocess.run(["git", "archive", "--format=tar", sha], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=BytesIO(data)) as tar:
            tar.extractall(out, filter="data")
    return out


def split_ident(s: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", s) if w]


def _sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"def {node.name}({ast.unparse(node.args)}){ret}"


def _body_words(node: ast.AST) -> list[str]:
    words = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            words.append(sub.id)
        elif isinstance(sub, ast.Attribute):
            words.append(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) < 300:
            words.append(sub.value)
    return words


def units_of(root: Path, only: set[str] | None = None) -> list[Unit]:
    """Units of every .py under root outside docs/, or of the relative paths in `only`."""
    out = []
    files = sorted(root / f for f in only) if only is not None else sorted(root.rglob("*.py"))
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel.startswith(SKIP):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        def visit(node: ast.AST, prefix: str, rel: str = rel) -> None:
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, ast.ClassDef):
                    visit(sub, f"{prefix}{sub.name}.")
                elif isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                    q = f"{prefix}{sub.name}"
                    doc = ast.get_docstring(sub) or ""
                    para = doc.split("\n\n")[0].replace("\n", " ")
                    sig = _sig(sub)
                    first = doc.strip().splitlines()[0][:100] if doc.strip() else ""
                    card = f"{rel}:{sub.lineno}  {prefix}{sig[4:]}" + (f"  # {first}" if first else "")
                    head = " ".join([*split_ident(rel), *split_ident(q), sig, para])
                    full = head + " " + " ".join(_body_words(sub))
                    out.append(Unit(f"{rel}::{q}", rel, sub.lineno, sub.end_lineno or sub.lineno, card, head, full))

        visit(tree, "")
    return out
