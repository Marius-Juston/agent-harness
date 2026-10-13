# Question (#169)

How should agents in a harness-run repository find the best existing free software (GitHub repos, PyPI and other
registry packages) before building, including from a section of code, with semantic rather than keyword-only search?
And which agent surface (CLI command, skill, MCP server, subagent, plugin) should carry library discovery, the
literature search (`dev lit`) and the research loop?

Scope: free and keyless (or free-key) sources; Python first, other ecosystems where cheap. Out of scope: paid
services, private registries.

Decision it serves: the defaults of `dev reuse` (#29: sources, query expansion, re-rank, maintenance score,
hallucination guard, `--from` code mode) and whether to add or change skills, agents or MCP servers.

Pilot: [scripts/pilot_recall_bench.py](scripts/pilot_recall_bench.py) (lead, 2026-10-13; results in issue #169).
