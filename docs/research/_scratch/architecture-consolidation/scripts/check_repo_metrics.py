"""Sanity checks for scripts/repo_metrics.py (run: uv run --python 3.14 python .scratch/test_repo_metrics.py)."""

import importlib.util
import sys

spec = importlib.util.spec_from_file_location("rm", "scripts/repo_metrics.py")
rm = importlib.util.module_from_spec(spec)
sys.modules["rm"] = rm
spec.loader.exec_module(rm)


def check(ok):
    if not ok:
        raise SystemExit("check failed")


print(rm._rename_tokens('result = compute_total(items, "abc", 42)'))
a = """def f(a, b):
    total = a + b
    if total > 10:
        print("big total value here", total)
    values = [x * 2 for x in range(total)]
    return sum(values) + len(values) * 3
"""
b = a.replace("total", "acc").replace("values", "vals").replace("big total", "large acc")
ex = rm.duplication({"a.py": a, "b.py": b}, 4)
rn = rm.duplication({"a.py": a, "b.py": b}, 4, renamed=True)
same = rm.duplication({"a.py": a, "b.py": a}, 4)
print(ex["duplicated_lines"], rn["duplicated_lines"], same["duplicated_lines"], same["normalised_lines"])
check(
    ex["duplicated_lines"] == 0 and rn["duplicated_lines"] > 0 and same["duplicated_lines"] == 10
)  # first window is below the 20-chars-per-line content floor
# loc counting
check(rm.count_loc("#!/usr/bin/env python\n# c\n\nx = 1\n  # d\n", ".py") == 2)
# tarjan
check(rm.tarjan({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}) == [["a", "b", "c"]])
# relative import resolution
info = rm.analyse_py("from . import x\nfrom ..y import z\ndef f():\n    import q.r\n", "pkg.sub.mod", False)
print(info.imports)
check(("pkg.sub.x", True) in info.imports and ("pkg.y.z", True) in info.imports and ("q.r", False) in info.imports)
# rename parsing
check(rm._rename("a/{b => c}/d.py") == ("a/b/d.py", "a/c/d.py"))
check(rm._rename("{old => new}/x.py") == ("old/x.py", "new/x.py"))
check(rm._rename("a/{ => sub}/x.py") == ("a/x.py", "a/sub/x.py"))
print("ok")
