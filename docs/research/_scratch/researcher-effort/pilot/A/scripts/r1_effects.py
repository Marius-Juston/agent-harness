"""Recompute the percentage effects quoted in r1-answer.md from the reported log-scale coefficients.

Run: uv run python docs/research/_scratch/eval-q/scripts/r1_effects.py
"""

import math


def pct(beta: float) -> float:
    """Percent change implied by a coefficient on a log outcome."""
    return (math.exp(beta) - 1) * 100


# He et al. (Cursor DiD), Table 2 coefficients as reported in arXiv 2511.04427v3.
cursor = {"lines added": 0.2514, "static warnings": 0.2644, "complexity": 0.3481, "duplicate density": 0.0679}
# Agarwal et al. (agent adoption DiD), Table 2, agent-first (AF) and IDE-first (IF).
agents = {
    "AF warnings": 0.163,
    "AF complexity": 0.299,
    "AF duplication": 0.076,
    "IF complexity": 0.357,
    "IF duplication": -0.009,
}
# He et al. GMM: lagged effect on lines added (Table 3).
gmm = {"complexity -> L(t+1)": -0.718, "warnings -> L(t+1)": -0.588, "Cursor -> complexity (size-controlled)": 0.086}

for name, table in (("cursor", cursor), ("agents", agents), ("gmm", gmm)):
    for k, b in table.items():
        print(f"{name:7s} {k:42s} beta={b:+.4f} -> {pct(b):+6.1f}%")

# Larsen & Moghaddam: counts +1.1 %, LOC +12.8 % -> implied density change if multiplicative.
implied = (1.011 / 1.128 - 1) * 100
print(f"larsen  implied smell density from counts/LOC          -> {implied:+6.1f}% (paper's own ASD estimate: -6.7%)")

# GitClear 2026 page: moved share 21 % (2022) -> 3.8 % (2026 YTD); copy/paste 9.4 % -> 15.7 %.
print(f"gitclear moved-code share ratio 2022/2026          -> {21 / 3.8:.1f}x lower")
print(f"gitclear copy/paste share change 2022->H1 2026     -> {(15.7 / 9.4 - 1) * 100:+.0f}%")
print(f"gitclear block duplication 40.3 -> 73.0            -> {(73.0 / 40.3 - 1) * 100:+.0f}%")
