# Arithmetic checks for r1-llm-tests-tools.md (numbers from fetched papers).
swe_abs_before, swe_abs_after = 78.80, 62.20
print(
    "SWE-ABS top-agent drop (pp):",
    round(swe_abs_before - swe_abs_after, 2),
    "relative:",
    round(1 - swe_abs_after / swe_abs_before, 3),
)
# Leith 2608.15188 historical-revert EFFECTIVE rates
for name, eff, scored in [("Extractor(Claude)", 288, 298), ("Django", 342, 350), ("Pandas", 334, 350)]:
    print(name, round(100 * eff / scored, 1), "%")
# ImpossibleBench abort option: GPT-5 54->9, o3 49->12 on Conflicting-SWEbench
for m, a, b in [("GPT-5", 54, 9), ("o3", 49, 12)]:
    print(m, "abort cuts cheating by", a - b, "pp")
