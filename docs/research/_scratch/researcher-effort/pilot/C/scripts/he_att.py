"""Check He et al. 2025 Table 2: log-scale ATT -> percent, and whether the 95% CI excludes 0 (estimate, SE)."""

import math

ROWS = {
    "commits": (0.0260, 0.0429),
    "lines_added": (0.2514, 0.1063),
    "warnings": (0.2644, 0.0511),
    "dup_density": (0.0679, 0.0448),
    "complexity": (0.3481, 0.0538),
}
for name, (b, se) in ROWS.items():
    lo, hi = b - 1.96 * se, b + 1.96 * se
    pct = 100 * (math.exp(b) - 1)
    print(f"{name:12s} {pct:+6.2f}%  z={b / se:4.2f}  CI95 [{lo:+.3f},{hi:+.3f}] excludes0={lo > 0}")
