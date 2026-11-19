"""Mine localization questions from this repository's history (#495).

For each first-parent commit that changed non-test Python code, the question is the title of the issue it closed,
asked of the tree *before* the commit (its first parent), and the answer is the set of functions that existed in that
tree and that the commit modified. Kept: 1-3 such functions (a narrower or broader change is not a localization
question). The issue number comes from `(#N)` in the subject (the first one when there are two: issue, then PR) or
from the branch name of a `Merge pull request #M from owner/N-slug` subject.

usage: uv run python docs/research/_scratch/semantic-code-search/scripts/mine.py ISSUES.json OUT.jsonl
ISSUES.json: `gh issue list --state all --limit 1000 --json number,title,body`.
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

CODE = ("devkit/", "plugins/", "scripts/")
# Registration functions every new command or stage touches (argparse tables, config keys, the stage list, the docs
# generator): they are wiring, not where the behaviour lives, so they are not answers. Fixed before scoring.
PLUMBING = {
    "devkit/cli.py::build",
    "devkit/cli.py::run",
    "devkit/cli.py::main",
    "devkit/config.py::parse",
    "devkit/gate.py::stages",
    "devkit/docs.py::render",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def functions(src: str) -> list[tuple[str, int, int]]:
    """(qualname, first line, last line) of every function and method; classes are containers only. Nested
    functions belong to the outermost function (an agent lands on the outer one)."""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out

    def visit(node: ast.AST, prefix: str) -> None:
        for sub in ast.iter_child_nodes(node):
            if isinstance(sub, ast.ClassDef):
                visit(sub, f"{prefix}{sub.name}.")
            elif isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                start = min([sub.lineno, *[d.lineno for d in sub.decorator_list]])
                out.append((f"{prefix}{sub.name}", start, sub.end_lineno or sub.lineno))

    visit(tree, "")
    return out


def old_ranges(parent: str, commit: str, path: str) -> list[tuple[int, int]]:
    """Old-side line ranges the commit touched in `path` (a pure insertion counts as the line before it)."""
    diff = git("diff", "-U0", "--no-color", parent, commit, "--", path)
    ranges = []
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        ranges.append((start, start + count - 1) if count else (start, start))
    return ranges


def issue_of(subject: str) -> int | None:
    if m := re.match(r"Merge pull request #\d+ from [^/]+/(\d+)-", subject):
        return int(m.group(1))
    nums = re.findall(r"\(#(\d+)\)", subject)
    return int(nums[0]) if nums else None


def main() -> None:
    issues = {i["number"]: i for i in json.loads(Path(sys.argv[1]).read_text())}
    log = git("log", "--first-parent", "--format=%H%x09%P%x09%s", "--", *[f"{c}*.py" for c in CODE])
    questions = []
    for line in log.splitlines():
        sha, parents, subject = line.split("\t", 2)
        parent = parents.split()[0] if parents else None
        n = issue_of(subject)
        if not parent or n not in issues:
            continue
        changed = [
            p
            for p in git("diff", "--name-only", parent, sha).split()
            if p.endswith(".py") and p.startswith(CODE) and "/tests/" not in p
        ]
        gold = set()
        for path in changed:
            try:
                src = git("show", f"{parent}:{path}")
            except subprocess.CalledProcessError:
                continue  # a new file: nothing existed to find
            funcs = functions(src)
            for a, b in old_ranges(parent, sha, path):
                for q, s, e in funcs:
                    if s <= b and a <= e:
                        gold.add(f"{path}::{q}")
        if 1 <= len(gold) <= 3 and (gold := gold - PLUMBING):
            kind = "bug" if subject.startswith("fix") or "bug" in issues[n]["title"].lower() else "feature"
            questions.append(
                {
                    "id": f"h{n}",
                    "kind": kind,
                    "tree": parent,
                    "commit": sha,
                    "issue": n,
                    "query": issues[n]["title"],
                    "gold": sorted(gold),
                }
            )
    # JSON Lines, one question per line (a .json file would have its issue titles read as docs by the gate's
    # `uv run dev` span check); no commit subjects, which name the owner's branches.
    Path(sys.argv[2]).write_text("".join(json.dumps(q) + "\n" for q in questions))
    print(len(questions), "questions")


if __name__ == "__main__":
    main()
