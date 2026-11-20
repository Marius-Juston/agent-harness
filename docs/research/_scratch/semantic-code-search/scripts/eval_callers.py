"""'What calls Y' at HEAD (#495): Y = every top-level function of the modules the consolidation series (#442-#450)
moved code into that has 3-15 non-test functions naming it (pick_callers.py prints the pool; the rule is fixed).

Arms (each gives a set of caller functions, `path::Qualname`, or `path::<module>` for module-level code, and the
text an agent would read):
  refs      `uv run dev code refs Y` itself (git grep -w, grouped, capped), lines mapped to enclosing functions
  astgrep   ast-grep 0.51 with the call patterns `Y($$$)` and `$O.Y($$$)` (what an agent writes for callers)
  ty        ty's language server, textDocument/references at Y's definition (includeDeclaration false)
  resolver  a stdlib call graph: names and attributes equal to Y in modules that define Y or import it from Y's
            module (`from devkit.M import Y`, `from devkit import M` / `import devkit.M` then M.Y)

Gold: functions both ty and the resolver report, plus every disagreement adjudicated by reading the line (the
ADJUDICATED table below, written after looking at each disagreement, never at an arm's score). Import statements are
not callers.

usage: uv run python eval_callers.py OUT.json
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(HERE))
from corpus import Unit, tokens, units_of  # noqa: E402

TARGETS = [
    "devkit/ci_status.py::check_key",
    "devkit/ci_status.py::latest_checks",
    "devkit/ci_status.py::unfinished",
    "devkit/ci_status.py::running",
    "devkit/ci_status.py::_named",
    "devkit/ci_status.py::actions_enabled",
    "devkit/ci_status.py::standing",
    "devkit/merging.py::_remote_shas",
    "devkit/merging.py::git_version",
    "devkit/merging.py::_merge_rules",
    "devkit/merging.py::merge_conflicts",
    "devkit/merging.py::deleted_owns",
    "devkit/merging.py::regenerate",
    "devkit/fixloop.py::fix_loop",
    "devkit/fixloop.py::send_back",
    "devkit/fixloop.py::_sent_back_file",
    "devkit/prtext.py::title_problem",
    "devkit/prtext.py::session_leaks",
    "devkit/branches.py::worktrees",
    "devkit/branches.py::drop_branch",
    "devkit/quota.py::quota_cap",
    "devkit/quota.py::quota",
]


def tracked_units() -> list[Unit]:
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True).stdout.split()
    keep = {f for f in files if not f.startswith("docs/")}
    return [u for u in units_of(REPO, only=keep)]


def enclosing(units_by_path: dict[str, list[Unit]], path: str, line: int) -> str:
    best = None
    for u in units_by_path.get(path, []):
        if u.line <= line <= u.end and (best is None or u.line >= best.line):
            best = u
    return best.key if best else f"{path}::<module>"


_IMPORTS: dict[str, set[int]] = {}


def is_import(path: str, line: int) -> bool:
    """The line belongs to an import statement (multi-line ones too)."""
    if path not in _IMPORTS:
        tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
        _IMPORTS[path] = {
            n
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for n in range(node.lineno, (node.end_lineno or node.lineno) + 1)
        }
    return line in _IMPORTS[path]


# ---------- arms ----------
def arm_refs(name: str, by_path) -> tuple[set[str], str, float]:
    t = time.perf_counter()
    out = subprocess.run(["uv", "run", "dev", "code", "refs", name], cwd=REPO, capture_output=True, text=True).stdout
    ms = (time.perf_counter() - t) * 1000
    found, cur, kept = set(), None, []
    for line in out.splitlines():
        if line.startswith("== "):
            cur = line[3:].rsplit(" (", 1)[0]
        if cur and cur.startswith(OWN):  # this benchmark's own scripts name every target: not the repository's code
            continue
        kept.append(line)
        if cur and line[:5].strip().isdigit() and cur.endswith(".py"):
            n = int(line[:5])
            if not is_import(cur, n):
                found.add(enclosing(by_path, cur, n))
    return found, "\n".join(kept), ms


OWN = "docs/research/_scratch/semantic-code-search/"


def arm_astgrep(name: str, by_path) -> tuple[set[str], str, float]:
    t = time.perf_counter()
    outs, found = [], set()
    for pat in (f"{name}($$$)", f"$O.{name}($$$)"):
        r = subprocess.run(
            [
                "uvx",
                "--from",
                "ast-grep-cli==0.51.3",
                "ast-grep",
                "run",
                "-l",
                "python",
                "-p",
                pat,
                "--json=compact",
                "devkit",
                "tests",
                "plugins",
                "scripts",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        for m in json.loads(r.stdout or "[]"):
            found.add(enclosing(by_path, m["file"], m["range"]["start"]["line"] + 1))
            outs.append(f"{m['file']}:{m['range']['start']['line'] + 1}: {m['lines'].strip()[:160]}")
    return found, "\n".join(outs), (time.perf_counter() - t) * 1000


class LSP:
    def __init__(self) -> None:
        self.p = subprocess.Popen(
            ["uvx", "ty@0.0.107", "server"],
            cwd=REPO,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.n = 0
        self.req(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": REPO.as_uri(),
                "capabilities": {},
                "workspaceFolders": [{"uri": REPO.as_uri(), "name": "repo"}],
            },
        )
        self.notify("initialized", {})

    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode()
        self.p.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.p.stdin.flush()

    def _read(self) -> dict:
        n = 0
        while line := self.p.stdout.readline().strip():
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":")[1])
        return json.loads(self.p.stdout.read(n))

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def req(self, method: str, params: dict):
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            m = self._read()
            if m.get("id") == self.n and "method" not in m:
                return m.get("result")
            if "method" in m and "id" in m:  # a server request (e.g. workspace/configuration): answer empty
                self._send(
                    {"jsonrpc": "2.0", "id": m["id"], "result": [None] * len(m.get("params", {}).get("items", [1]))}
                )

    def close(self) -> None:
        self.p.kill()


def arm_ty(lsp: LSP, target: Unit, by_path) -> tuple[set[str], str, float]:
    path = REPO / target.path
    text = path.read_text(encoding="utf-8")
    line = text.splitlines()[target.line - 1]
    col = line.index(target.key.split("::")[1].split(".")[-1])
    lsp.notify(
        "textDocument/didOpen",
        {"textDocument": {"uri": path.as_uri(), "languageId": "python", "version": 1, "text": text}},
    )
    t = time.perf_counter()
    res = (
        lsp.req(
            "textDocument/references",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": {"line": target.line - 1, "character": col},
                "context": {"includeDeclaration": False},
            },
        )
        or []
    )
    ms = (time.perf_counter() - t) * 1000
    found, outs = set(), []
    for loc in res:
        rel = Path(loc["uri"].removeprefix("file://")).relative_to(REPO).as_posix()
        n = loc["range"]["start"]["line"] + 1
        src = (REPO / rel).read_text(encoding="utf-8").splitlines()[n - 1].strip()
        outs.append(f"{rel}:{n}: {src[:160]}")
        if not is_import(rel, n):
            found.add(enclosing(by_path, rel, n))
    return found, "\n".join(outs), ms


def arm_resolver(target: Unit, units: list[Unit], by_path) -> tuple[set[str], str, float]:
    t = time.perf_counter()
    mod_path, name = target.path, target.key.split("::")[1]
    mod = mod_path.removesuffix(".py").replace("/", ".")
    pkg, _, leaf = mod.rpartition(".")
    found, outs = set(), []
    for path in sorted({u.path for u in units}):
        tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
        bare = path == mod_path  # Y by its own name
        aliases = set()  # names bound to Y's module
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == mod:
                bare |= any(a.name == name and (a.asname or a.name) == name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module == pkg:
                aliases |= {a.asname or a.name for a in node.names if a.name == leaf}
            elif isinstance(node, ast.Import):
                aliases |= {a.asname for a in node.names if a.name == mod and a.asname}
        for node in ast.walk(tree):
            hit = (bare and isinstance(node, ast.Name) and node.id == name) or (
                isinstance(node, ast.Attribute)
                and node.attr == name
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            )
            if hit:
                found.add(enclosing(by_path, path, node.lineno))
                outs.append(f"{path}:{node.lineno}")
    found.discard(target.key)
    return found, "\n".join(outs), (time.perf_counter() - t) * 1000


# Disagreements between ty and the resolver, judged by reading each line after the first run. All 22 are ty-only
# and true: train.py and two test files call Y through `workflow.Y`, a name workflow.py re-exports from Y's module
# after the consolidation moves (#442-#450); the resolver follows direct imports only.
ADJUDICATED: dict[str, dict[str, bool]] = {
    "devkit/merging.py::_remote_shas": {
        "devkit/train.py::_close_merged": True,
    },
    "devkit/merging.py::_merge_rules": {
        "devkit/train.py::_build": True,
    },
    "devkit/merging.py::merge_conflicts": {
        "devkit/train.py::_send_back_conflict": True,
        "tests/test_workflow.py::test_dev_pr_fails_closed_when_it_cannot_check_against_main": True,
        "tests/test_workflow.py::test_merge_conflicts_matches_what_merge_main_finds": True,
    },
    "devkit/merging.py::deleted_owns": {
        "devkit/train.py::_build": True,
        "devkit/train.py::_regenerated_steps": True,
        "tests/test_train.py::test_the_regenerated_tree_is_checked_for_deleted_owns_too": True,
    },
    "devkit/merging.py::regenerate": {
        "devkit/train.py::_build": True,
        "devkit/train.py::_regenerated_steps": True,
        "tests/test_merge_hygiene.py::test_a_failing_regenerate_command_stops_before_the_gate": True,
        "tests/test_train.py::test_a_regenerate_failing_on_an_intermediate_step_stops_with_its_own_reason": True,
    },
    "devkit/fixloop.py::fix_loop": {
        "devkit/train.py::_admit": True,
        "devkit/train.py::_bisect": True,
        "devkit/train.py::_recheck": True,
        "devkit/train.py::_send_back_conflict": True,
        "devkit/train.py::_send_back_owns": True,
    },
    "devkit/fixloop.py::send_back": {
        "devkit/train.py::_send_back_conflict": True,
    },
    "devkit/prtext.py::title_problem": {
        "devkit/train.py::_admit": True,
        "devkit/train.py::_recheck": True,
    },
    "devkit/prtext.py::session_leaks": {
        "devkit/train.py::_admit": True,
        "devkit/train.py::_recheck": True,
    },
}


def main() -> None:
    units = tracked_units()
    by_path: dict[str, list[Unit]] = {}
    for u in units:
        by_path.setdefault(u.path, []).append(u)
    by_key = {u.key: u for u in units}
    t = time.perf_counter()
    lsp = LSP()
    ty_start = (time.perf_counter() - t) * 1000
    rows = []
    for key in TARGETS:
        target = by_key[key]
        name = key.split("::")[1]
        res = {}
        for arm, fn in (
            ("refs", lambda name=name: arm_refs(name, by_path)),
            ("astgrep", lambda name=name: arm_astgrep(name, by_path)),
            ("ty", lambda target=target: arm_ty(lsp, target, by_path)),
            ("resolver", lambda target=target: arm_resolver(target, units, by_path)),
        ):
            found, out, ms = fn()
            found.discard(key)
            res[arm] = {"found": sorted(found), "tokens": tokens(out), "ms": ms}
        both = set(res["ty"]["found"]) & set(res["resolver"]["found"])
        disputed = set(res["ty"]["found"]) ^ set(res["resolver"]["found"])
        judged = ADJUDICATED.get(key, {})
        gold = both | {k for k in disputed if judged.get(k)}
        pending = sorted(k for k in disputed if k not in judged)
        rows.append({"target": key, "gold": sorted(gold), "pending": pending, "results": res})
        print(key, "gold", len(gold), "pending", pending)
    lsp.close()
    Path(sys.argv[1]).write_text(json.dumps({"ty_start_ms": ty_start, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
