# Gaps after round 1 (lead, 2026-10-19)

Read from the hand-backs of r1-usage-logs, r1-blog-* (4), r1-engineering-and-docs and r1-outside-evidence.

## Findings that change the recommendation
- Cost is context x turns: cache reads 48 %, cache writes 29 %, output 20 % of spend (r1-usage-logs). Researchers are
  48 % of spend, so the research loop is the first place to look, not CLAUDE.md length.
- Token cuts can raise cost: a 38 % tool-output cut raised billed cost 6.8 % (r1-outside, arXiv 2607.12161), and one
  brevity line cost 3 % on Anthropic's evals and was reverted (r1-engineering). Every cut needs cost per merged issue,
  cache writes priced apart, and a pre-registered eval. `devkit/stats.py` counts cache writes as fresh (r1-blog D).
- Review convergence: Anthropic went from 2.08 to 1.32 mean review rounds with re-review-the-new-diff, no repeated
  findings, whole bug class at once, round cap 3 (r1-blog A). repo-E rough count is 2.43 rounds, 20 % one-round passes.
- Model and effort: all 668 spawns and all 7 plugin agents are Opus 5.5; only researcher and research-auditor set
  effort. Sonnet 5.5 costs half and is aimed at investigation and review; Opus 5.5 at high matches Fable at max
  on half the tokens; self-preference bias argues for a different reviewer model (r1-blog B, r1-outside).
- Instruction text: Anthropic cut 80 % of the Claude Code system prompt for Claude 5 with no measured loss, but
  ablates line by line (r1-blog C, r1-engineering).
- Workflow mechanics: 51 % of Bash calls are `cd`-prefixed into another worktree, causing 685 isolation refusals,
  read-guard bypass and allowlist misses; 164 Edit-before-Read failures (r1-usage-logs).
- Agent-shaped tool output: summary lines, details in a log file, ERROR lines greppable, concise vs detailed modes
  (r1-engineering, C-compiler and tools posts).

## Contradictions
- Cache TTL: r1-usage-logs fits a cache-write price of about 2x input (1-hour writes) across sessions, while
  r1-engineering says only builder, researcher and tester set `cacheTtl: 1h` and others default to 5 minutes.
  Sent to r2-against.
- Word budgets: the harness ratchets words down; Anthropic warns that brevity lines can cost quality and that
  "cruft != length". Sent to r2-surface-audit and r2-against.

## Weak or single-source claims
- 2.43 rounds / 20 % one-round passes in repo-E: approximate, from PR comments. Sent to r2-review-convergence.
- Several 2026 arXiv figures in r1-outside are abstract-level or SECONDARY. Sent to r2-against.
- Before/after builder tokens (27.6 M pre-harness vs 4.5 M) compare different projects and tasks: not causal.

## Unanswered, assigned to round 2
- Opus 5.5 handling per role (model, effort, thinking, prompt style): r2-opus55-handling.
- Why research and the lead cost so much, by phase: r2-research-and-lead-cost.
- Which harness text to cut, rewrite or move to tooling: r2-surface-audit.
- Risks of each leading change: r2-against.

Stop reason: not yet; round 2 runs on these gaps.
