#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["complexipy==9.0.0"]
# ///
"""Replay candidate architecture metrics over every first-parent commit of this repository (#496).

For each first-parent commit and each module under ``--pkg`` (default ``devkit``) it records, from git objects only:
size (non-blank, non-comment lines), McCabe cyclomatic complexity (own AST count, the radon/mccabe rule set),
cognitive complexity (complexipy 9.0.0), fan-out and fan-in inside the package (Martin's Ce and Ca, module-level and
function-level imports kept apart), and module cohesion (connected components of the top-level definitions'
reference graph). Per commit it also records which files and which top-level definitions the commit changed, and the
issue the commit closes, so `analyse.py` can derive churn, hotspots, change dispersion and the outcomes.

Run from the worktree root: ``uv run docs/research/_scratch/architecture-grading/scripts/history.py [--rev main]``.
Writes ``.scratch/arch_history.json`` (gitignored). Needs ``.scratch/arch_issues.json`` from
``gh issue list --state all --limit 1000 --json number,labels,title,createdAt``.
"""

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

import complexipy


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


class Blobs:
    def __init__(self) -> None:
        self.p = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def text(self, sha: str) -> str:
        self.p.stdin.write(sha.encode() + b"\n")
        self.p.stdin.flush()
        header = self.p.stdout.readline().split()
        size = int(header[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        return data.decode("utf-8", "replace")


def loc(src: str) -> int:
    return sum(1 for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#"))


def mccabe(fn: ast.AST) -> int:
    """1 + decision points, the rule set of the mccabe/radon counters (bool operators count per extra operand)."""
    n = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.Assert)):
            n += 1
        elif isinstance(node, ast.comprehension):
            n += 1 + len(node.ifs)
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.match_case):
            n += 1
    return n


def imports(tree: ast.AST, mod: str, pkg: str) -> tuple[set[str], set[str]]:
    """Package modules imported at module level and inside functions."""
    top, inner = set(), set()
    func_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                func_nodes.add(id(sub))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(mod.split(".")[: -node.level] + ([base] if base else []))
            targets = [base] + [f"{base}.{a.name}" for a in node.names]
        for t in targets:
            if t.startswith(pkg + ".") and t.count(".") == 1:
                (inner if id(node) in func_nodes else top).add(t)
    top.discard(mod)
    inner.discard(mod)
    return top, inner - top


def cohesion(tree: ast.Module, src_lines: list[str]) -> list[int]:
    """Sizes (LOC) of the connected components of the top-level definitions, joined when one references another
    or both reference the same module-level name (a module-level analogue of LCOM4). Sorted, largest first."""
    defs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
    glob = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        glob.add(n.id)
    parent = {d: d for d in defs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    users: dict[str, list[str]] = {}
    for name, node in defs.items():
        refs = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        refs |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} & set(defs)
        for r in refs:
            if r in defs and r != name:
                parent[find(r)] = find(name)
            elif r in glob:
                users.setdefault(r, []).append(name)
    for names in users.values():
        for other in names[1:]:
            parent[find(other)] = find(names[0])
    size: dict[str, int] = {}
    for name, node in defs.items():
        body = src_lines[node.lineno - 1 : node.end_lineno]
        size[find(name)] = size.get(find(name), 0) + loc("\n".join(body))
    return sorted(size.values(), reverse=True)


def measure(src: str, mod: str, pkg: str) -> dict:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"parse_error": True, "loc": loc(src)}
    lines = src.splitlines()
    fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    cc = [mccabe(f) for f in fns] or [0]
    try:
        cog = [f.complexity for f in complexipy.code_complexity(src).functions] or [0]
    except Exception:  # complexipy refuses some files; record it rather than drop the file
        cog = [-1]
    top, inner = imports(tree, mod, pkg)
    defs = [
        [n.name, n.lineno, n.end_lineno]
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return {
        "loc": loc(src),
        "n_func": len(fns),
        "cc_sum": sum(cc),
        "cc_max": max(cc),
        "cc_over10": sum(1 for c in cc if c > 10),
        "cog_sum": sum(cog),
        "cog_max": max(cog),
        "cog_over15": sum(1 for c in cog if c > 15),
        "imports": sorted(top),
        "fn_imports": sorted(inner),
        "components": cohesion(tree, lines),
        "defs": defs,
    }


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def changed_defs(parent: str, commit: str, path: str, defs: list) -> list[str]:
    diff = git("diff", "-U0", "--no-color", "--no-ext-diff", parent, commit, "--", path)
    touched = set()
    for m in HUNK.finditer(diff):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        end = start + max(count, 1) - 1
        for name, a, b in defs:
            if a <= end and start <= b:
                touched.add(name)
    return sorted(touched)


def issue_of(subject: str) -> int | None:
    m = re.match(r"Merge pull request #\d+ from [^/]+/(\d+)-", subject)
    if m:
        return int(m.group(1))
    nums = re.findall(r"\(#(\d+)\)", subject)
    return int(nums[0]) if len(nums) >= 2 else (int(nums[0]) if nums else None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default="main")
    ap.add_argument("--pkg", default="devkit")
    ap.add_argument("--out", default=".scratch/arch_history.json")
    a = ap.parse_args()
    issues = {
        i["number"]: [lb["name"] for lb in i["labels"]]
        for i in json.loads(Path(".scratch/arch_issues.json").read_text())
    }
    log = git("log", "--first-parent", "--reverse", "--format=%H%x09%P%x09%ad%x09%s", "--date=iso-strict", a.rev)
    blobs, cache, rows = Blobs(), {}, []
    for line in log.splitlines():
        sha, parents, date, subject = line.split("\t", 3)
        parent = parents.split()[0] if parents else None
        files = {}
        for ent in git("ls-tree", "-r", sha, "--", a.pkg).splitlines():
            meta, path = ent.split("\t")
            mode, kind, blob = meta.split()
            if kind != "blob" or not path.endswith(".py") or path.count("/") != 1 or mode == "120000":
                continue
            mod = path[:-3].replace("/", ".")
            if blob not in cache:
                cache[blob] = measure(blobs.text(blob), mod, a.pkg)
            files[path] = blob
        if parent:
            changed = git("diff-tree", "-r", "--no-commit-id", "--name-only", parent, sha).split()
        else:
            changed = list(files)
        touched = {}
        for path in changed:
            if path in files and parent and "defs" in cache[files[path]]:
                touched[path] = changed_defs(parent, sha, path, cache[files[path]]["defs"])
        iss = issue_of(subject)
        rows.append(
            {
                "sha": sha[:9],
                "date": date,
                "subject": subject,
                "issue": iss,
                "bug": bool(iss and "type:bug" in issues.get(iss, [])) or subject.lower().startswith("fix"),
                "files": files,
                "changed": [p for p in changed if p.startswith(a.pkg + "/")],
                "touched_defs": touched,
            }
        )
    out = {"rows": rows, "blobs": cache}
    Path(a.out).write_text(json.dumps(out))
    print(f"{len(rows)} commits, {len(cache)} distinct blobs -> {a.out}")


if __name__ == "__main__":
    main()
