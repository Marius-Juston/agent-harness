"""(a) #392: does a stdlib statement-level pass (Python `ast`, shell line continuations) close the gaps the PR #399
author named (an install list split across lines, a command built from string pieces), and at what false-positive
cost on today's tree?

usage (repository root): uv run python docs/research/_scratch/devkit-reuse-survey/scripts/a_installs.py
LINE = devkit.checks' INSTALL regex per line, as install_findings runs it (comments cut, punctuation to spaces).
STMT = the prototype: for .py, every ast.Call whose lines touch an added line is matched as one collapsed source
segment and again as its constant-folded strings (str + str, implicit concatenation, f-strings and Names bound
once to a str/list constant in the file); for .sh, backslash-continued lines are joined first.
Probe strings are assembled at runtime so this file is not itself an install the gate flags.
"""

import ast
import fnmatch
import subprocess
from pathlib import Path

from devkit import checks

PI = "pi" + "p"
UVP = "uv " + PI
PROBES = {  # name: (suffix, source, should_flag)
    "list split over lines": (
        ".py",
        f'import subprocess\nsubprocess.run([\n    "{PI}",\n    "install",\n    "x",\n])\n',
        1,
    ),
    "str + str": (".py", f'import os\nos.system("{PI} " + "install x")\n', 1),
    "implicit concat": (".py", f'import os\nos.system("p" "{PI[1:]} install x")\n', 1),
    "name bound to list": (
        ".py",
        f'import subprocess\nBASE = ["uv", "{PI}"]\nsubprocess.run(BASE + ["install", "x"])\n',
        1,
    ),
    "f-string with name": (
        ".py",
        f'import subprocess\nTOOL = "{PI}"\nsubprocess.run(f"{{TOOL}} install x".split())\n',
        1,
    ),
    "join of list, one line": (".py", f'import os\nos.system(" ".join(["{PI}", "install", "x"]))\n', 1),
    "sh continuation": (".sh", f"#!/bin/sh\n{PI} \\\n  install x\n", 1),
    "sh variable": (".sh", f'#!/bin/sh\nPM={PI}\n"$PM" install x\n', 1),  # needs evaluation, not parsing
    "sh one line": (".sh", f"#!/bin/sh\n{UVP} install x\n", 1),
    "benign: pip list": (".py", f'import subprocess\nsubprocess.run(["{PI}", "list"])\n', 0),
    "benign: comment": (".sh", f"#!/bin/sh\n# run {PI} install yourself\nls\n", 0),
    "benign: hint string": (".py", f'print("to fix it, run: {PI} install devkit")\n', 1),  # LINE flags it; reviewed
}


def norm(s):
    return " ".join(checks.INSTALL_PUNCT.sub(" ", s).split())


def line_hits(text):
    return {i for i, ln in enumerate(text.splitlines(), 1) if checks.INSTALL.search(norm(checks._uncommented(ln)))}


def consts(tree):
    """Names assigned exactly once in the file, to a str or a list/tuple of str constants."""
    seen, out = {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            seen[n.targets[0].id] = seen.get(n.targets[0].id, 0) + 1
            v = n.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out[n.targets[0].id] = v.value
            elif isinstance(v, (ast.List, ast.Tuple)) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in v.elts
            ):
                out[n.targets[0].id] = " ".join(e.value for e in v.elts)
    return {k: v for k, v in out.items() if seen[k] == 1}


def fold(node, names):
    """Every string the expression is built from, folded where the pieces are constants or once-bound names."""
    match node:
        case ast.Constant(value=str() as s):
            return s
        case ast.Name(id=i) if i in names:
            return names[i]
        case ast.BinOp(op=ast.Add(), left=a, right=b):
            return " ".join(x for x in (fold(a, names), fold(b, names)) if x)
        case ast.JoinedStr(values=vs):
            return "".join(fold(v.value if isinstance(v, ast.FormattedValue) else v, names) or "" for v in vs)
        case ast.List(elts=es) | ast.Tuple(elts=es):
            return " ".join(x for e in es if (x := fold(e, names)))
        case ast.Call(func=f, args=args):
            inner = " ".join(x for a in args if (x := fold(a, names)))
            base = fold(f.value, names) if isinstance(f, ast.Attribute) else ""
            return " ".join(x for x in (base, inner) if x)
    return ""


def stmt_hits(text, suffix):
    if suffix == ".sh":
        joined, cur = [], None
        for i, ln in enumerate(text.splitlines(), 1):
            body = checks._uncommented(ln)
            cur = (cur[0], cur[1] + " " + body) if cur else (i, body)
            if not body.rstrip().endswith("\\"):
                joined.append(cur)
                cur = None
        return {i for i, s in joined if checks.INSTALL.search(norm(s.replace("\\", " ")))}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return line_hits(text)
    names, hits = consts(tree), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            seg = ast.get_source_segment(text, n) or ""
            if checks.INSTALL.search(norm(seg)) or checks.INSTALL.search(norm(fold(n, names))):
                hits.add(n.lineno)
    return hits | line_hits(text)


def main():
    print("probe | expected | LINE | STMT")
    score = {"LINE": 0, "STMT": 0}
    for name, (suffix, src, want) in PROBES.items():
        a, b = bool(line_hits(src)), bool(stmt_hits(src, suffix))
        score["LINE"] += a == bool(want)
        score["STMT"] += b == bool(want)
        print(f"{name} | {bool(want)} | {a} | {b}")
    print(f"probes {len(PROBES)} correct: {score}")

    allowed = checks.install_allowlist(Path(".devkit/install-allow.txt").read_text())
    files = subprocess.run(["git", "ls-files", "*.py", "*.sh"], capture_output=True, text=True, check=True).stdout
    la = sb = 0
    extra = []
    for f in files.split():
        if any(fnmatch.fnmatch(f, g) for g in allowed) or not Path(f).is_file():
            continue
        text = Path(f).read_text(errors="replace")
        a, b = line_hits(text), stmt_hits(text, Path(f).suffix)
        la += len(a)
        sb += len(b)
        extra += [f"{f}:{n}" for n in sorted(b - a)]
    print(f"today's tree as if every line were added (allowlist applied): LINE {la} hits, STMT {sb}")
    for e in extra[:20]:
        print("   STMT only:", e)


main()
