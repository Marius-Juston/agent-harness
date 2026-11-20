"""List candidate 'what calls Y' targets (#495): top-level functions of the modules the consolidation series moved
code into (#442-#450), with the number of non-test functions whose body names Y. Printed for a fixed selection rule:
every function with 2-15 such callers, in module order.

usage: uv run python docs/research/_scratch/semantic-code-search/scripts/pick_callers.py
"""

import ast
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corpus import units_of  # noqa: E402

REPO = HERE.parents[4]
MODULES = ["ci_status", "merging", "fixloop", "prtext", "branches", "quota"]


def main() -> None:
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True).stdout.split()
    units = units_of(REPO, only={f for f in tracked if not f.startswith("docs/")})
    bodies = {}
    for u in units:
        lines = (REPO / u.path).read_text(encoding="utf-8").splitlines()[u.line - 1 : u.end]
        bodies[u.key] = "\n".join(lines)
    for m in MODULES:
        tree = ast.parse((REPO / "devkit" / f"{m}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                y = node.name
                callers = [
                    k
                    for k, b in bodies.items()
                    if k != f"devkit/{m}.py::{y}"
                    and not k.startswith(("tests/", "evals/"))
                    and any(
                        isinstance(n, ast.Name | ast.Attribute)
                        and (getattr(n, "id", None) or getattr(n, "attr", None)) == y
                        for n in ast.walk(ast.parse(_dedent(b)))
                    )
                ]
                if 2 <= len(callers) <= 15:
                    print(f"devkit/{m}.py::{y}", len(callers))


def _dedent(src: str) -> str:
    import textwrap

    s = textwrap.dedent(src)
    try:
        ast.parse(s)
        return s
    except SyntaxError:
        return "pass"


if __name__ == "__main__":
    main()
