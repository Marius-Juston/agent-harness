"""r2 (#111): arithmetic of the repo-L consolidation proposal. Inputs are the measured counts named beside each step."""

TOTAL = 259_163  # repo_metrics.py code LOC at 086fb686 (r1 F1)
steps = [  # (id, kind, LOC removed, source of the number)
    ("S1 legacy v1 tree", "mechanical", 21_359, "r1 F2"),
    ("S2 app/dev H-class -> depend on H", "mechanical", 5_139 + 310, "r2_dev_classes.py"),
    ("S3 attractor scene, disabled by owner, + its test", "mechanical", 691 + 144, "grep -c, scenes/attractor*.py"),
    ("S4 fold pyscn clone groups (app/ only)", "mechanical, upper bound", 4_327, "pyscn 1.32.4 clone_groups"),
    (
        "S5 app/dev M-class merge/gate machinery + tests importing only H/M minus S2 tests",
        "owner: adopt H's PR flow",
        6_523 + 4_235 - 310,
        "r2_dev_classes.py",
    ),
    (
        "S6 dice scenes: keep bauhaus/heartbeat/ember, drop 26 others + their tests",
        "owner: feature cut",
        11_265 - 1_131 - 691 + 2_108 - 144,
        "scene LOC, test_dice_<scene>.py",
    ),
    (
        "S7 one scenario runner for 8 eval/playtest harnesses (30% of 3,535)",
        "design, BELIEVED",
        round(0.30 * 3_535),
        "judgement",
    ),
    (
        "S8 46 orphan commands: drop or wire (direct contract+handler LOC)",
        "owner: feature cut",
        930,
        "r2_orphans.py, lower bound",
    ),
]
cum = 0
for s, kind, n, src in steps:
    cum += n
    print(f"{s:85s} {kind:28s} {n:7,d}  cum {cum:7,d} ({cum / TOTAL:5.1%})  [{src}]")
mech = sum(n for s, k, n, _ in steps if k.startswith("mechanical"))
print(
    f"mechanical only {mech:,} ({mech / TOTAL:.1%}); with owner decisions {cum:,} ({cum / TOTAL:.1%}); remaining {TOTAL - cum:,}"
)
print(f"owner's 1/5 target would leave {TOTAL // 5:,}; gap after all steps {TOTAL - cum - TOTAL // 5:,}")
