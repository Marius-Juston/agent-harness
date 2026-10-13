# Arm A3 prompt (PROTOCOL.md amendment 1, fixed 2026-10-13 in round 3 before any A3 run)

Run exactly as B0 (`b0_prompt.md`): a fresh `claude -p --tools '' --strict-mcp-config --setting-sources ''` call from an
empty directory, no repository, notes, gold lists or web. `run_agent.py` sends the text below verbatim, with `<NEEDS>`
replaced by a JSON array of at most 16 needs (id, need, also_described_as, shortlist) and nothing else. The
shortlist is the top 20 of arm A2 under the configuration chosen on TUNE, one line per candidate in the fixed format
of `r3_score.py` `shortlist_line`: rank. name | STATUS (non-passing gates) | description | stars | downloads/month |
dependents | last release | licence.

---
You are helping a Python developer who is about to write code for each need below. For each need, a search tool
has already found a shortlist of existing open-source candidates and checked each one. PASS means the name exists on
its registry or GitHub, no malware is known, it is not new, archived or dormant, and its licence is permissive. HOLD
means one of those checks needs a human look; the reason follows it. For each need, choose from its shortlist only
and list up to ten of its names, best first, that you would recommend the developer use for that need. Write each
name exactly as it appears in the shortlist. Reply with one JSON object only, mapping each need's "id" to a list of
names, for example {"<id>": ["name1", "name2"]}.

<NEEDS>
---
