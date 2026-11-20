"""Wilson 95 % intervals for the #502 baseline proportions. Usage: python -I wilson.py k/n [k/n ...]"""

import math
import sys

for arg in sys.argv[1:]:
    k, n = map(int, arg.split("/"))
    p, z = k / n, 1.959964
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    print(f"{arg}: {p:.1%} (95% CI {centre - half:.1%}-{centre + half:.1%})")
