# Question (#111)
When and how should an agent-run repository step back and re-architect itself so it keeps every feature and its
robustness at a fraction of the code (owner's example: 100k LOC that could be 20k), without deleting progress?

Scope: triggers (size, churn, duplication, time, merged-PR count), techniques (expert guidance and when each applies),
safe-refactor protocols (feature inventory, characterisation tests, strangler fig), evidence on AI-generated code
growth, existing tools/plugins/skills/repos to reuse, and data from the sibling repos
the four sibling repositories (repo-L, repo-E, repo-R, H) (owner calls them good/bad examples).
Out of scope: language-specific style rules.

Decision it serves: the shape and trigger of an agent-harness component (#113): agent, skill, `uv run dev` command,
plugin, or a mix; Python-first but generic.

Discovery: `uv run dev lit status` on 2026-10-12: OK, partial (s2 down; openalex, crossref, europepmc, arxiv answered).
