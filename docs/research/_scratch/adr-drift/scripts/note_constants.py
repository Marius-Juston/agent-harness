"""Module-level numeric constants in code that research notes or docs name, with the value each side states.

usage (inside a repo): python3 note_constants.py CODE_GLOB... -- DOC_GLOB...
For every `NAME = <number|tuple of numbers|True/False>` in code, prints each doc line naming NAME and the numbers
on that line, flagged MISMATCH when the code value is not among them. Hand review decides real drift.
"""

import ast
import re
import sys
from pathlib import Path

args = sys.argv[1:]
cut = args.index("--")
code = [p for g in args[:cut] for p in Path(".").glob(g)]
docs = [
    p for g in args[cut + 1 :] for p in Path(".").glob(g) if "/_scratch/" not in str(p) and "generated" not in str(p)
]
consts = {}
for p in code:
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError:
        continue
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name.isupper() and len(name) > 3:
                try:
                    v = ast.literal_eval(n.value)
                except Exception:  # noqa: S112 - research script, kept as run
                    continue
                if isinstance(v, (int, float, bool)) or (
                    isinstance(v, tuple) and all(isinstance(x, (int, float)) for x in v)
                ):
                    consts.setdefault(name, []).append((str(p), v))
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
named = mism = 0
for d in docs:
    for ln, line in enumerate(d.read_text(errors="replace").splitlines(), 1):
        for name, defs in consts.items():
            if re.search(rf"\b{name}\b", line):
                named += 1
                vals = {float(x) for x in NUM.findall(line.split(name, 1)[1][:60])}
                cv = [v for _, v in defs]
                flat = [float(x) for v in cv for x in (v if isinstance(v, tuple) else (v,))]
                ok = any(x in vals for x in flat) or not vals
                mism += not ok
                print(
                    ("MISMATCH " if not ok else "ok       ")
                    + f"{d}:{ln} {name} code={cv} doc-numbers={sorted(vals)[:6]}"
                )
print(f"RESULT: {len(consts)} constants; {named} doc lines name one; {mism} flagged")
