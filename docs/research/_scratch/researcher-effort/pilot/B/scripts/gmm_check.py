"""Check He et al. 2026 GMM: log-log coefficients vs stated '100% increase -> X% decrease'."""

import math

for name, b, stated in [("complexity", -0.718, 64.5), ("warnings", -0.588, 50.3)]:
    loglog = 1 - 2**b  # elasticity reading: doubling X multiplies Y by 2^b
    expb = 1 - math.exp(b)  # one log-unit increase multiplies Y by e^b
    print(f"{name}: b={b} 1-2^b={loglog:.3f} 1-e^b={expb:.3f} stated={stated / 100:.3f}")
