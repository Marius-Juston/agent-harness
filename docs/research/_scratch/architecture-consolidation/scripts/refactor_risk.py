"""Check numbers used in r1-against-and-safety.md."""

# 1. Differential fuzzing (arXiv 2602.15761): 19-35% non-equivalent, ~21% of those missed by tests.
for ne in (0.19, 0.35):
    print(f"silent breakage per refactoring: {ne * 0.21:.4f}")
# 2. Liu et al. (2411.04444): unsafe solutions
print(f"ChatGPT unsafe {13 / 176:.4f}, Gemini unsafe {9 / 137:.4f}")
# 3. Compounding over N independent steps with per-step silent-breakage p
for p in (0.01, 0.04, 0.074):
    print(p, {n: round(1 - (1 - p) ** n, 3) for n in (1, 5, 10, 20, 50, 100)})
# 4. GPT-5.1 iterative readability: <1% drop per iteration, 5 iterations -> upper bound
print(f"5 iterations at 1%: {1 - 0.99**5:.4f}")
# 5. RefactorBench human/agent ratio
print(f"human/agent solve ratio: {0.87 / 0.22:.2f}")
