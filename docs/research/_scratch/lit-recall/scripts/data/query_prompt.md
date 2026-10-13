# Query-writing prompt (PROTOCOL.md rule 4, fixed 2026-10-13 before any query was written)

Sent verbatim by `run_agent.py` to a fresh, tool-less `claude -p --model claude-opus-5-5` call from an empty
directory, one question per call (`--batch 1`), with `<NEEDS>` replaced by a JSON array holding one {id, question}
object and nothing else. The model sees the question only: no note, no source list, no web.

---
You are a researcher about to search the scholarly literature for the research question below. You will run each of
your queries, unchanged, in three tools: a scholarly index (OpenAlex), the arXiv search API and a general web search.
Write the 8 search queries you would run first, covering the question's different parts. Each query is plain
keywords, 3 to 10 words: no quotes, no boolean operators, no field prefixes, no years, no author names. Use only the
question below. Reply with one JSON object only, mapping the question's "id" to a list of 8 query strings, for
example {"<id>": ["query one", "query two"]}.

<NEEDS>
---

Output: `queries_A.json` (here) and `queries_P.json` (private), each with `_model`, `_date` and `_how` keys.
