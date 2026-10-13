# Arm B0W prompt: B0 plus the agent's own WebSearch and WebFetch (#176 rerun PROTOCOL.md, fixed 2026-10-13 before any run)

Derived from ../../scripts/data/b0_prompt.md (#169, unchanged) by one substitution: "Answer from what you know;" becomes "You may use web search and web fetch to find or check libraries;". Run by run_agent.py --web (tools WebSearch and WebFetch only), one need per call.

---
You are helping a Python developer who is about to write code for each need below. For each need, name the three
best existing open-source Python libraries for it, best first, as their PyPI package names. You may use web search and
web fetch to find or check libraries; do not invent names, and if you know fewer than three good ones, give fewer. Reply with one JSON object only,
mapping each need's "id" to a list of PyPI names, for example {"<id>": ["name1", "name2", "name3"]}.

<NEEDS>
---
