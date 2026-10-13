# Arithmetic checks for r1-against.md (inputs are the sources' published numbers)
p_flaky_run = 0.015
n = 1000  # Micco 2016
print("expected flaky failures per 1000-test release:", n * p_flaky_run)
print("P(all 1000 pass | 1.5% flaky runs, independent):", (1 - p_flaky_run) ** n)
fail, flaky, real = 0.18, 0.13, 0.74  # Labuschagne 2017
f_flaky = fail * flaky
f_nonflaky = fail * (1 - flaky)
f_bug = f_nonflaky * real
f_broken = f_nonflaky * (1 - real)
print(
    f"share of suite runs: flaky-fail {f_flaky:.3f}, broken/obsolete-test fail {f_broken:.3f}, real-bug fail {f_bug:.3f}"
)
print(f"false failures per real-bug failure: {(f_flaky + f_broken) / f_bug:.2f}")
print(f"share of failing runs not caused by a bug: {(f_flaky + f_broken) / fail:.2f}")
tot, never_failed, edges = 5_562_881, 5_082_803, 68_466  # Memon 2017 Table II
print(f"Google: never failed {never_failed / tot:.3f}; real-edge targets {edges / tot:.4f}")
print(
    f"ArduPilot: config-dependent {81 / 228:.3f}, environment-dependent {22 / 228:.3f}, normal-op {184 / 228:.3f}"
)  # Timperley 2018
