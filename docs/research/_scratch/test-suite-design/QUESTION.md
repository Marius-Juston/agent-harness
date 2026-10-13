# Question (#112)
How should an agent-run repository design and maintain its test suite as it grows from small to large: which kinds of
tests, how many, which end-to-end tests are necessary, and how CI/CD should run them?

Scope: test shapes (pyramid, trophy, honeycomb, Google test sizes), empirical data on test counts and test-to-code
ratios, effectiveness measures (coverage vs mutation score), property-based and contract tests, flaky tests, test
selection and impact analysis, CI/CD practice (DORA, stages, merge queues, run time budgets), tests written by LLM
agents (quality, redundancy, gaming), existing tools/plugins/skills to reuse, and data from the sibling repos
the four sibling repositories (repo-L, repo-E, repo-R, H). Extends docs/research/test-floors-and-coverage.md.

Decision it serves: the shape and trigger of an agent-harness test-suite review component (#114) and size-tiered
defaults for the gate and template.

Discovery: `uv run dev lit status` on 2026-10-12: OK, partial (s2 down; openalex, crossref, europepmc, arxiv answered).
