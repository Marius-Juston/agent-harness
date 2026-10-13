# Arm B0 prompt (PROTOCOL.md amendment 1, fixed 2026-10-13 before any B0 run)

Run by a fresh agent with no access to this repository's research notes, the gold lists or the web (no tools). Record
the model id and date in the output file. One call per batch of needs is fine; the text below is sent verbatim, with
`<NEEDS>` replaced by the JSON array from `b0_tune_input.json` (id, need, also_described_as) and nothing else.

---
You are helping a Python developer who is about to write code for each need below. For each need, name the three
best existing open-source Python libraries for it, best first, as their PyPI package names. Answer from what you
know; do not invent names, and if you know fewer than three good ones, give fewer. Reply with one JSON object only,
mapping each need's "id" to a list of PyPI names, for example {"<id>": ["name1", "name2", "name3"]}.

<NEEDS>
---

Output: `b0_tune_answers.json` next to this file, plus `{"_model": "...", "_date": "YYYY-MM-DD"}` keys. Score with
`replay.py <snapshot> <cache> --b0 data/b0_tune_answers.json`.
