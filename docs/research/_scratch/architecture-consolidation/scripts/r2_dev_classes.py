"""r2 (#111): classify repo-L's <app>/dev modules and count LOC per class, plus tests that import only a class.

Usage: python3 r2_dev_classes.py <repo-L archive copy> [app package, default app]. Classification is judgement (BELIEVED) from each
module's docstring; the counts are mechanical. LOC = non-blank lines not starting with '#'."""

import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
APP = sys.argv[2] if len(sys.argv) > 2 else "app"
CLASSES = {
    "H harness, agent-harness devkit has an equivalent": "board board_edit workflow codegraph ratchets weakening ran __init__",
    "M one-machine merge/gate throughput, no devkit equivalent": "queue greentip rehearse gitmerge merge_driver prompt_versions batching dispatch headroom deadline durations longest_first failfast failfast_plugin flakes flake_plugin snapshot testselect testmon_plugin timing",
    "X repo checks and registries (generic mechanism, product content)": "checks registry",
    "Q generic quality tools not in devkit": "mutants swallows",
    "P product verification harnesses": "playtest playtest_stub fight_stub marathon sentinels coverage simulation noninterference plan_fuzz interface_bench agent_eval model_eval preflight_session surfaces census scale corpus query_budget",
    "O ops and runtime commands": "up live observe projections sources dice_samples conversation_smoke generation_smoke inference_smoke discord_smoke prompt_budget",
}


def loc(p):
    return sum(1 for ln in p.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#"))


mods = {p.stem: p for p in (root / APP / "dev").glob("*.py")}
cls_of = {m: c for c, ms in CLASSES.items() for m in ms.split()}
missing = set(mods) - set(cls_of)
extra = set(cls_of) - set(mods)
if missing or extra:
    raise SystemExit(f"unclassified modules {missing}, unknown names {extra}")
tot = {c: sum(loc(mods[m]) for m in ms.split()) for c, ms in CLASSES.items()}
# tests: which dev classes does each test file import (<app>.dev.X or from <app>.dev import X)?
pat = re.compile(rf"{APP}\.dev\.(\w+)|from {APP}\.dev import ([\w, ()]+)")
tests = {}
for t in (root / "tests_engine").rglob("*.py"):
    src = t.read_text()
    hits = set()
    for a, b in pat.findall(src):
        for name in [a] if a else re.findall(r"\w+", b):
            if name in cls_of:
                hits.add(cls_of[name])
    other = re.search(rf"^\s*(from|import) {APP}\.(?!dev\b)", src, re.M)
    if hits:
        tests[t] = (hits, bool(other), loc(t))
print(f"{APP}/dev total {sum(tot.values())}")
for c in CLASSES:
    only = sum(n for h, o, n in tests.values() if h == {c} and not o)
    print(f"{tot[c]:6d}  {c}  | tests importing only this class and no other app pkg: {only}")
hm = {k for k in CLASSES if k[0] in "HM"}
print(
    "H+M modules",
    tot[next(k for k in CLASSES if k[0] == "H")] + tot[next(k for k in CLASSES if k[0] == "M")],
    "| tests importing only H/M dev modules, no other app pkg:",
    sum(n for h, o, n in tests.values() if h <= hm and not o),
)
