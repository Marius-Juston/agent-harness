"""Flake detection arithmetic, assuming independent runs.
(a) chance k reruns see >=1 failure of a test failing with prob p per run;
(b) clean runs needed to say p < bound with 95% confidence (1-(1-p)^n >= 0.95);
(c) per-test p that keeps spurious suite red <= 5% for N tests."""

import math

ps = [0.001, 0.01, 0.05, 0.2]
print("(a) detect prob", "k:", [5, 10, 20, 50, 200])
for p in ps:
    print(f"  p={p:<6}", [f"{1 - (1 - p) ** k:.0%}" for k in (5, 10, 20, 50, 200)])
print("(b) clean runs for 95% that p < bound")
for p in (0.0175, 0.01, 0.001, 5.1e-4, 5.1e-5):
    print(f"  bound={p}: n={math.ceil(math.log(0.05) / math.log(1 - p))}")
print("(c) per-test p for <=5% spurious red")
for n in (100, 1000, 5000, 10000):
    print(f"  N={n}: p<={1 - 0.95 ** (1 / n):.1e}")
print("(d) spurious-red rate when a budget set for N=100 (p=5.1e-4) is applied higher in the tier")
for n in (927, 999):
    print(f"  N={n}: {1 - (1 - 5.1e-4) ** n:.0%}")
print("(e) the rule of thumb p ~ 0.05/N against the exact bound")
for n in (1000, 5000, 10000):
    print(f"  N={n}: 0.05/N={0.05 / n:.1e} exact={1 - 0.95 ** (1 / n):.1e}")
