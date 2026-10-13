"""Fail-before/pass-after replay of the tests a merged PR added or changed.

For each first-parent merge M of a clone: tests added or modified in tests/ by M^1..M are run on the tip (all code
of M) and on the base (code of M^1 with the tests/ folder of M). A test that passes on the base did not need the
PR's code change. Usage: uv run python replay_new_tests.py <clone> <n_merges> <head>
"""

import ast
import json
import subprocess
import sys
import time
from pathlib import Path


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def test_funcs(src: str) -> dict[str, str]:
    """name (Class::func or func) -> dumped AST of each test function in a module."""
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out[node.name] = ast.dump(node)
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out[f"{node.name}::{sub.name}"] = ast.dump(sub)
    return out


def show(repo, rev, path):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=repo, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def new_or_changed(repo, base, tip):
    files = [f for f in git(repo, "diff", "--name-only", base, tip, "--", "tests/").split() if f.endswith(".py")]
    ids = []
    for f in files:
        if not Path(f).name.startswith("test_"):
            continue
        old, new = test_funcs(show(repo, base, f)), test_funcs(show(repo, tip, f))
        ids += [(f, name, name not in old) for name, body in new.items() if old.get(name) != body]
    return ids


def outcomes(repo, node_ids):
    """Per node id (parametrized ids collapsed to their function): 'passed' only if every case passed."""
    if not node_ids:
        return {}, 0.0
    report = Path(repo) / ".replay.json"
    t = time.time()
    subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "pytest-json-report",
            "python",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "--json-report",
            f"--json-report-file={report}",
            "-m",
            "not live",
            *node_ids,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    wall = time.time() - t
    res = {nid: "uncollected" for nid in node_ids}  # not collected (e.g. import error) counts as error
    if report.exists():
        data = json.loads(report.read_text())
        for c in data.get("collectors", []):
            if c.get("outcome") == "failed":
                for nid in node_ids:
                    if nid.startswith(c["nodeid"] + "::") or nid == c["nodeid"]:
                        res[nid] = "collect-error"
        seen: dict[str, list[str]] = {}
        for tcase in data.get("tests", []):
            key = tcase["nodeid"].split("[")[0]
            outcome = tcase["outcome"]
            if outcome in ("failed", "error"):
                stage = tcase.get("call") if outcome == "failed" else tcase.get("setup")
                msg = ((stage or {}).get("crash") or {}).get("message", "")
                exc = msg.split(":")[0].split(".")[-1].strip() or "?"
                is_assert = msg.startswith("assert") or exc in ("AssertionError", "Failed")
                outcome = "assert" if is_assert else f"exc-{exc}"
            seen.setdefault(key, []).append(outcome)
        for nid, outs in seen.items():
            if nid in res:
                bad = [o for o in outs if o not in ("passed", "skipped")]
                res[nid] = (
                    "passed"
                    if all(o == "passed" for o in outs)
                    else ("skipped" if all(o == "skipped" for o in outs) else ("assert" if "assert" in bad else bad[0]))
                )
        report.unlink()
    return res, wall


def main():
    repo, n, head = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    git(repo, "checkout", "-q", "--force", head)
    merges = git(repo, "log", "--merges", "--first-parent", f"-{n}", "--format=%h %s", head).splitlines()
    total = {"new": 0, "changed": 0, "pass_before": 0, "pass_before_new": 0, "fail_after": 0}
    for line in merges:
        m, subject = line.split(" ", 1)
        base = git(repo, "rev-parse", f"{m}^1").strip()
        found = new_or_changed(repo, base, m)
        ids = [f"{f}::{name}" for f, name, _ in found]
        git(repo, "checkout", "-q", "--force", m)
        after, wall_after = outcomes(repo, ids)
        git(repo, "checkout", "-q", "--force", base)
        git(repo, "checkout", "-q", m, "--", "tests/")
        before, wall_before = outcomes(repo, ids)
        git(repo, "checkout", "-q", "--force", base)
        pb = [(f, name, isnew) for f, name, isnew in found if before[f"{f}::{name}"] == "passed"]
        fa = [i for i in ids if after[i] != "passed"]
        n_new = sum(1 for *_, isnew in found if isnew)
        total["new"] += n_new
        total["changed"] += len(found) - n_new
        total["pass_before"] += len(pb)
        total["pass_before_new"] += sum(1 for *_, isnew in pb if isnew)
        total["fail_after"] += len(fa)
        for i in ids:
            total["base_" + before[i]] = total.get("base_" + before[i], 0) + 1
        print(f"{m} {subject[:60]}")
        print(
            f"   tests new {n_new}, changed {len(found) - n_new}; pass on base {len(pb)}; "
            f"not passing on tip {len(fa)}; wall base {wall_before:.1f}s tip {wall_after:.1f}s"
        )
        for f, name, isnew in pb:
            print(f"     passes before: {f}::{name} ({'new' if isnew else 'changed'})")
        for i in fa:
            print(f"     not passing after: {i} ({after[i]})")
    git(repo, "checkout", "-q", "--force", head)
    print("TOTAL", json.dumps(total))


if __name__ == "__main__":
    main()
