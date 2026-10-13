"""Re-derive ratios quoted in r1-empirical.md from the raw counts in the sources."""

import math

# Juergens et al. ICSE 2009, Table 2 sums
C, IC, UIC, F = 1427, 724, 203, 107
print("IC/C pooled", round(IC / C, 3))
print("UIC/IC pooled", round(UIC / IC, 3))
print("F/IC pooled", round(F / IC, 3))
print("F/UIC pooled", round(F / UIC, 3))
# Ma et al. 2026 RepoReuse ablation (OpenCode + Qwen3.7-plus): "Lines" = distinct source lines viewed, not written
lines_no, lines_if = 878, 576
print("distinct source lines viewed, change with interface memory", round(lines_if / lines_no - 1, 3))
print("redundancy pp change", 46.7 - 65.1)
# Liu et al. 2026 survival table
print("survival", round(105364 / 464900, 4))
# Huang et al. 2026 AMR ratio
print("AMR ratio", round(0.2867 / 0.1532, 2))
# He et al. 2026 GMM: break-even multipliers given 1.84x baseline lines-added and per-doubling effects
for name, per_doubling in [("complexity", 0.645), ("warnings", 0.503)]:
    # lines multiplier from a k-fold increase if each doubling cuts by per_doubling: (1-per_doubling)**log2(k)
    k = 2 ** (math.log(1 / 1.84) / math.log(1 - per_doubling))
    print(f"break-even {name} fold-increase", round(k, 2))
# GitClear: copy/paste share 8.3% -> 12.3%
print("copy/paste relative rise", round(12.3 / 8.3, 2))
