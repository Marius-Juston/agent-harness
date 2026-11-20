"""Arithmetic behind the #497 note: shares and token equivalents of the coded failure clusters.

Inputs are copied from coded.py and turn_cost.py output (2026-11-19 snapshot); run: python3 synth_arith.py
"""

corpora = {
    # name: (all calls, doc-preventable failing calls, their recovery calls, upper-bound other path misses,
    #        median context tokens per builder turn)
    "H": (4029, 19, 22, 23, 64_827),
    "repo-S": (42400, 170, 199, 308, 170_036),
    "repo-V": (15996, 32, 35, 76, 133_533),
}
for name, (calls, fail, rec, other, ctx) in corpora.items():
    print(
        f"{name}: doc-preventable failing {100 * fail / calls:.2f}% of calls; recovery {100 * rec / calls:.2f}%; "
        f"upper bound with every other path miss {100 * (fail + other) / calls:.2f}%; "
        f"recovery ~{rec * ctx / 1e6:.1f}M context tokens re-read"
    )

# Largest single cluster: repo-S's project class constructed with missing fields (coded.py)
runs_s = 58
print(f"repo-S data-class cluster: 67 failing calls in {runs_s} runs; {runs_s} of 484 runs = {100 * runs_s / 484:.1f}%")
print(f"  token cost of its 73 recovery calls ~{73 * 170_036 / 1e6:.1f}M context tokens")

# Always-loaded cost of a generated module map for H, if imported into CLAUDE.md: one line per devkit module
# 33 devkit modules; their docstrings' first lines total 514 words (measured on 2026-11-19 with ast, mean 15.6)
words = 514
print(
    f"module map imported: ~{words} words = {100 * words / 600:.0f}% of the 600-word context budget, "
    f"~{words * 1.33:.0f} tokens per session start (1.33 tokens/word ASSUMED)"
)

# Always-loaded text is re-read on every turn of every run that loads it (CLAUDE.md loads into subagents too).
turns = {"H": 3508, "repo-S": 38343, "repo-V": 13422}  # turn_cost.py, all runs
for name, n in turns.items():
    print(f"{name}: one always-loaded word costs ~{n * 1.33:,.0f} tokens over the window")
mm = 514 * 1.33 * turns["H"]
saved = 4 * 64_827  # the 4 module-path recovery calls in H, if a map prevented every one
print(f"H module map imported: ~{mm / 1e6:.2f}M tokens spent vs at most ~{saved / 1e6:.2f}M saved ({mm / saved:.0f}x)")
