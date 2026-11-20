"""Docstring coverage of a Python tree, by kind, with the stdlib only (#497); counts what interrogate or ruff's D1
rules would count, without installing either.

Usage: python3 doc_coverage.py <dir> [...]
Public = name not starting with "_" (dunder methods other than __init__ skipped, as interrogate does by default).
"""

import ast
import pathlib
import sys


def walk(tree):
    """(kind, documented) for the module and every public class, function and method."""
    yield "module", ast.get_docstring(tree) is not None

    def visit(node, in_class):
        for n in ast.iter_child_nodes(node):
            if isinstance(n, ast.ClassDef):
                if not n.name.startswith("_"):
                    yield "class", ast.get_docstring(n) is not None
                    yield from visit(n, True)
            elif isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
                if n.name.startswith("_"):
                    continue
                yield ("method" if in_class else "function"), ast.get_docstring(n) is not None

    yield from visit(tree, False)


def main(dirs):
    for d in dirs:
        p = pathlib.Path(d).expanduser()
        tot = {}
        files = [f for f in p.rglob("*.py") if ".venv" not in f.parts and "node_modules" not in f.parts]
        for f in files:
            try:
                tree = ast.parse(f.read_text(errors="replace"))
            except SyntaxError:
                continue
            for kind, ok in walk(tree):
                a, b = tot.get(kind, (0, 0))
                tot[kind] = (a + ok, b + 1)
        all_ok = sum(a for a, _ in tot.values())
        all_n = sum(b for _, b in tot.values())
        parts = ", ".join(f"{k} {a}/{b} ({100 * a / b:.0f}%)" for k, (a, b) in sorted(tot.items()))
        print(f"{p.name}: files={len(files)} all {all_ok}/{all_n} ({100 * all_ok / max(1, all_n):.0f}%); {parts}")


if __name__ == "__main__":
    main(sys.argv[1:])
