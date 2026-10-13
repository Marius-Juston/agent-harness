"""Re-derive the OpenSSF Scorecard aggregate from per-check scores and risk weights (README: Critical 10, High 7.5, Medium 5, Low 2.5)."""

import json
import sys

risk = {
    "Binary-Artifacts": "High",
    "Branch-Protection": "High",
    "CI-Tests": "Low",
    "CII-Best-Practices": "Low",
    "Code-Review": "High",
    "Contributors": "Low",
    "Dangerous-Workflow": "Critical",
    "Dependency-Update-Tool": "High",
    "Fuzzing": "Medium",
    "License": "Low",
    "Maintained": "High",
    "Packaging": "Medium",
    "Pinned-Dependencies": "Medium",
    "SAST": "Medium",
    "SBOM": "Medium",
    "Security-Policy": "Medium",
    "Signed-Releases": "High",
    "Token-Permissions": "High",
    "Vulnerabilities": "High",
    "Webhooks": "Critical",
}
w = {"Critical": 10, "High": 7.5, "Medium": 5, "Low": 2.5}
d = json.load(open(sys.argv[1]))  # noqa: SIM115 - research script, kept as run
num = den = 0
for c in d["checks"]:
    if c["score"] < 0:
        continue  # -1 = not evaluated, excluded
    k = w[risk[c["name"]]]
    num += k * c["score"]
    den += k
print(f"api={d['score']} recomputed={num / den:.2f}")
