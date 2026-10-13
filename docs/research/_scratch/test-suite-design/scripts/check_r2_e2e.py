"""r2-e2e-by-type arithmetic, from the sources' published numbers only."""

from math import comb

# Ghosh et al. SoCC 2022: testing-type shares among OCE testing suggestions (sec 5.1)
t = {"performance": 12.8, "system/e2e": 23.1, "scenario": 30.8, "validation": 12.8, "integration": 7.7, "unit": 12.8}
print("SoCC22 sum of testing-suggestion shares:", round(sum(t.values()), 1))
print("SoCC22 system+scenario share:", round(t["system/e2e"] + t["scenario"], 1), "unit:", t["unit"])
# Liu et al. HotOS 2019 (via secondary summary): 112 bug incidents ~40% of all
print("HotOS19 implied total incidents:", round(112 / 0.40))
print("HotOS19 data-format incidents ~", round(112 * 0.21), "; fault-handling ~", round(112 * 0.31))
# Liu, Mondal, Chen TOSEM 2024: 423 DB-access bugs, 84.2% SQL/Schema/API
print("DB-access bugs in SQL/Schema/API:", round(423 * 0.842))
# LLM regression evals in a gate: P(all N cases pass) when each passes with prob p per run
for p in (0.99, 0.999):
    for n in (20, 50):
        print(f"evals in gate: p={p} N={n} P(all pass)={p**n:.3f}")
# detecting a case that regressed from p0 to p1 with k trials (any failure = alarm)
for k in (1, 3, 5, 10):
    print(f"k={k}: false alarm at p=0.95 {1 - 0.95**k:.3f}; detect drop to p=0.80 {1 - 0.80**k:.3f}")
# suite-level pass-rate threshold: N cases, alarm if passes <= c


def cdf(c, n, p):
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(c + 1))


for n, c in ((50, 44), (100, 89)):
    print(
        f"N={n} alarm if passes<={c}: false alarm at p=0.95 {cdf(c, n, 0.95):.3f}; detect p=0.85 {cdf(c, n, 0.85):.3f}"
    )
