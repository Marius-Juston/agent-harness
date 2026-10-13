"""Derived numbers for r1-sibling-metrics.md, all from .scratch/all.json (repo_metrics output)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from codenames import APP, entry

D = {d["repo"]: d for d in json.loads(Path(".scratch/all.json").read_text())["repos"]}
for name, d in D.items():
    src = d["code_loc"] - d["test_loc"]
    print(
        f"{name}: code {d['code_loc']} test {d['test_loc']} ({100 * d['test_loc'] / max(d['code_loc'], 1):.0f}%) "
        f"src {src} doc {d['doc_lines']} merges {d['merges']} code/merge {d['code_loc'] / max(d['merges'], 1):.0f}"
    )
dl = entry(D, "repo-L")
legacy = sum(dl["by_dir"].get(k, 0) for k in ["systems", "core", "agents", "models", "utils", "tests"]) + 1985  # bot.py
print(
    "repo-L legacy v1 (bot.py+systems+core+agents+models+utils+tests):",
    legacy,
    f"= {100 * legacy / dl['code_loc']:.1f}% of code",
)
g = {x["fp_index"]: x for x in dl["growth"]}
a, b = g[31], g[622]


def new(x):
    return x["by_dir"].get(APP, 0) + x["by_dir"].get("tests_engine", 0)


print(
    "repo-L app+tests_engine",
    a["date"],
    new(a),
    "->",
    b["date"],
    new(b),
    "over",
    622 - 31,
    "first-parent commits:",
    round((new(b) - new(a)) / (622 - 31)),
    "per fp commit",
)
dev = dl["by_dir2"][f"{APP}/dev"]
print(
    f"{APP}/dev",
    dev,
    f"= {100 * dev / dl['by_dir'][APP]:.1f}% of app;",
    "agent-harness devkit",
    entry(D, "H")["by_dir"]["devkit"],
    "ratio",
    round(dev / entry(D, "H")["by_dir"]["devkit"], 1),
)
i = dl["imports"]
print(
    "repo-L reach: src",
    i["src_loc"],
    "from entry/main",
    i["reach_from_entry_or_main_loc"],
    f"({100 * i['reach_from_entry_or_main_loc'] / i['src_loc']:.1f}%)",
    "unreached",
    i["unreached_loc"],
    "function-level edges",
    i["function_level_internal_edges"],
    "of",
    i["edges"],
    f"({100 * i['function_level_internal_edges'] / i['edges']:.1f}%)",
)
pr = entry(D, "repo-R")
print(
    "repo-R experiments share",
    f"{100 * pr['by_dir']['experiments'] / pr['code_loc']:.1f}%",
    "lazy edges",
    pr["imports"]["function_level_internal_edges"],
    "of",
    pr["imports"]["edges"],
    f"({100 * pr['imports']['function_level_internal_edges'] / pr['imports']['edges']:.1f}%)",
)
ah = entry(D, "H")
gs = {x["fp_index"]: x for x in ah["growth"]}
print(
    "agent-harness",
    gs[3]["code"],
    "->",
    gs[30]["code"],
    f"(+{100 * (gs[30]['code'] / gs[3]['code'] - 1):.0f}%)",
    "tests",
    gs[3]["test_code"],
    "->",
    gs[30]["test_code"],
)
u = ah["usage"]
print("agent-harness commands", len(ah["cli_commands"]), "never run", u["never_run"], "bash calls", u["bash_calls"])
ud = dl["usage"]
print(
    "repo-L commands",
    len(dl["cli_commands"]),
    "never run by agents",
    ud["never_run"],
    "bash calls",
    ud["bash_calls"],
    "files",
    ud["transcript_files"],
)
te = entry(D, "repo-E")
gt = {x["fp_index"]: x for x in te["growth"]}
print("repo-E fp24 -> fp28", gt[24]["code"], "->", gt[28]["code"], gt[24]["by_dir"])
# consolidation bounds (repo-L): legacy + (app/dev - agent-harness devkit) + dev-only tests (6,042, git grep, see note)
dev_only_tests = 6042
cut = legacy + (dev - entry(D, "H")["by_dir"]["devkit"]) + dev_only_tests
print(
    "repo-L measurable cut",
    cut,
    f"= {100 * cut / dl['code_loc']:.1f}%",
    "+ type-2 non-test dup upper bound",
    dl["dup_renamed_nontest"]["duplicated_lines"],
    f"-> <= {100 * (cut + dl['dup_renamed_nontest']['duplicated_lines']) / dl['code_loc']:.1f}%",
)
pcut = pr["by_dir"]["experiments"]
print("repo-R experiments cut", pcut, f"= {100 * pcut / pr['code_loc']:.1f}%")
for n, d in D.items():
    h = d["hotspots"]
    print(
        n,
        "added",
        h["code_lines_added"],
        "deleted",
        h["code_lines_deleted"],
        "ratio",
        h["deleted_per_added"],
        "nonmerge",
        h["non_merge_commits"],
        "top10%share",
        h["churn_share_top10pct_files"],
        "dup",
        d["dup_nontest"]["pct"],
        "dup2",
        d["dup_renamed_nontest"]["pct"],
        "vulture",
        d.get("vulture", {}).get("findings"),
        d.get("vulture", {}).get("high_confidence"),
        "sizes>500",
        d["sizes_nontest"]["over_500"],
        "p90",
        d["sizes_nontest"]["p90"],
        "max",
        d["sizes_nontest"]["max"],
        "cycles",
        d["imports"]["cycles_all"]["count"],
        d["imports"]["cycles_all"]["largest"],
        "modlevel",
        d["imports"]["cycles_module_level"]["count"],
        "unreached",
        d["imports"]["unreached_loc"],
    )
print(
    "PR main never",
    pr["main_modules_run"]["never"],
    pr["main_modules_run"]["never_loc"],
    pr["main_modules_run"]["never_by_dir"],
    "of",
    pr["main_modules_run"]["run"] + pr["main_modules_run"]["never"],
)
