"""Map the added/changed lines of `git diff -U0 <base>` under devkit/ to mutmut 3 mutant-name globs.

mutmut 3 names mutants per function: `pkg.mod.x_func__mutmut_N`, methods `pkg.mod.xǁClassǁmeth__mutmut_N`.
Usage (run in a clone at the PR tip): python changed_functions.py <base>   -> one glob per line on stdout,
plus a summary on stderr.
"""

import ast
import re
import subprocess
import sys

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed(base: str) -> dict[str, set[int]]:
    out = subprocess.run(
        ["git", "diff", "-U0", base, "--", "devkit/*.py"], capture_output=True, text=True, check=True
    ).stdout
    res: dict[str, set[int]] = {}
    path = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        m = HUNK.match(line)
        if m and path:
            start, n = int(m.group(1)), int(m.group(2) or 1)
            res.setdefault(path, set()).update(range(start, start + n))
    return res


def functions(src: str):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield f"x_{node.name}", node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"xǁ{node.name}ǁ{sub.name}", sub


def main():
    base = sys.argv[1]
    globs, outside, total = [], 0, 0
    for path, lines in sorted(changed(base).items()):
        mod = path[:-3].replace("/", ".")
        with open(path) as fh:
            src = fh.read()
        inside = set()
        for name, node in functions(src):
            span = set(range(node.lineno, node.end_lineno + 1))
            if span & lines:
                globs.append(f"{mod}.{name}__mutmut_*")
                inside |= span & lines
        total += len(lines)
        outside += len(lines - inside)
    print("\n".join(globs))
    print(f"{len(globs)} functions; {total} changed lines, {outside} outside any function", file=sys.stderr)


if __name__ == "__main__":
    main()
