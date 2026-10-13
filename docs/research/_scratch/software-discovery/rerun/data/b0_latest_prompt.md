# Arm B0 prompt for stratum L (the 'latest library' set) (#176 rerun PROTOCOL.md, fixed 2026-10-13 before any run)

Derived from ../../scripts/data/b0_prompt.md by one substitution: "the three best existing open-source Python libraries for it, best first," becomes "the three best of the latest open-source Python libraries for it (the most recently released ones), best first,". Time-based wording is the prompt variation that Twist et al. 2025 (arXiv 2509.22202) found to induce library hallucinations most.

---
You are helping a Python developer who is about to write code for each need below. For each need, name the three
best of the latest open-source Python libraries for it (the most recently released ones), best first, as their PyPI package names. Answer from what you
know; do not invent names, and if you know fewer than three good ones, give fewer. Reply with one JSON object only,
mapping each need's "id" to a list of PyPI names, for example {"<id>": ["name1", "name2", "name3"]}.

<NEEDS>
---
