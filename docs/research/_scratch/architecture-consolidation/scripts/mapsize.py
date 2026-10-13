"""Size of a push 'interface map' (module: def/class signature + first doc line), as `dev code outline` prints it,
for every non-test .py file of a git repo at HEAD. Usage: mapsize.py <repo> [subdir...]"""

import ast
import subprocess
import sys
from pathlib import Path

repo, subs = sys.argv[1], sys.argv[2:] or ["."]
files = subprocess.run(
    ["git", "-C", repo, "ls-files", "--", *[f"{s}/*.py" if s != "." else "*.py" for s in subs]],
    capture_output=True,
    text=True,
).stdout.split()
files = [
    f
    for f in files
    if "test" not in f.split("/")[0] and not f.split("/")[-1].startswith("test_") and "/tests/" not in f
]


def sig(n):
    a = ast.unparse(n.args)
    r = f" -> {ast.unparse(n.returns)}" if n.returns else ""
    return f"def {n.name}({a}){r}"


for public_only in (False, True):
    lines, defs, src = [], 0, 0
    for f in files:
        try:
            text = Path(f"{repo}/{f}").read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        src += len(text)
        body = []
        for n in tree.body:
            nodes = [(n, "")]
            if isinstance(n, ast.ClassDef):
                nodes += [(m, "  ") for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for m, ind in nodes:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if public_only and m.name.startswith("_") and m.name != "__init__":
                    continue
                d = ast.get_docstring(m)
                d = f"  # {d.strip().splitlines()[0][:100]}" if d else ""
                body.append(ind + (f"class {m.name}" if isinstance(m, ast.ClassDef) else sig(m)) + d)
                defs += 1
        if body:
            lines += [f + ":", *body]
    out = "\n".join(lines)
    print(
        f"{'public' if public_only else 'all   '} files={len(files)} defs={defs} map_chars={len(out)} ~tokens(chars/4)={len(out) // 4} source_chars={src} ratio={len(out) / max(src, 1):.3f}"
    )
