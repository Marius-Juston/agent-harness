"""r2 (#111): LOC behind the commands repo-L's own surface registry reports as reach "none".

Usage: python3.14 r2_orphans.py <repo-L archive copy>. For each orphan command type: its contract class (a pydantic
model with `type: Literal["<cmd>"]` under app/domain/contracts), the kernel functions decorated @handles(<class>), and
module-level kernel functions that contain the command's type string (private helpers like _wed). LOC = non-blank,
non-comment lines of each node. A rough bound: shared helpers and events/tables they feed are not counted."""

import ast
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
app = root / sys.argv[2] if len(sys.argv) > 2 else None
app = app or next(p for p in root.iterdir() if (p / "kernel").is_dir() and (p / "domain/contracts").is_dir())
surf = json.loads((root / "docs/generated/surfaces.json").read_text())
orph = {s["id"].split(":", 1)[1] for s in surf["surfaces"] if s["kind"] == "command" and s["reach"] == "none"}
cmds = [s for s in surf["surfaces"] if s["kind"] == "command"]


def nloc(src_lines, node):
    return sum(
        1 for ln in src_lines[node.lineno - 1 : node.end_lineno] if ln.strip() and not ln.strip().startswith("#")
    )


cls_of, cls_loc = {}, {}
for f in (app / "domain/contracts").glob("*.py"):
    lines = f.read_text().splitlines()
    tree = ast.parse("\n".join(lines))
    for n in tree.body:
        if isinstance(n, ast.ClassDef):
            for st in n.body:
                if (
                    isinstance(st, ast.AnnAssign)
                    and getattr(st.target, "id", "") == "type"
                    and isinstance(st.value, ast.Constant)
                ):
                    cls_of[st.value.value] = n.name
                    cls_loc[n.name] = nloc(lines, n)
handler_loc, helper_loc, nmulti = {}, {}, 0
for f in (app / "kernel").rglob("*.py"):
    lines = f.read_text().splitlines()
    tree = ast.parse("\n".join(lines))
    for n in tree.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = [
            a.id
            for d in n.decorator_list
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "handles"
            for a in d.args
            if isinstance(a, ast.Name)
        ]
        for c in names:
            handler_loc[c] = handler_loc.get(c, 0) + nloc(lines, n) / len(names)
        consts = {c.value for c in ast.walk(n) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        for cmd in orph & consts:
            if not names:
                helper_loc[cmd] = helper_loc.get(cmd, 0) + nloc(lines, n)
rows = []
for cmd in sorted(orph):
    c = cls_of.get(cmd)
    rows.append((cmd, c, cls_loc.get(c, 0), round(handler_loc.get(c, 0)), helper_loc.get(cmd, 0)))
tot = [sum(r[i] for r in rows) for i in (2, 3, 4)]
for r in rows:
    print(*r)
print(
    f"orphans {len(orph)} of {len(cmds)} commands; contract LOC {tot[0]}, @handles LOC {tot[1]}, helper LOC {tot[2]}, sum {sum(tot)}"
)
all_h = sum(handler_loc.values())
print(
    f"all @handles LOC {round(all_h)} over {len(handler_loc)} contract classes; contract classes with a type: {len(cls_of)}"
)
