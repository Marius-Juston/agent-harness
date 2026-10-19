# Question (#254)

Which changes to agent-harness (template text, plugin agents/skills/hooks, devkit commands, settings) would
(1) raise the effectiveness of a harness-run repository (merged, correct work per owner-hour, fewer review rounds and
reworks), and (2) cut tokens at equal effectiveness?

Scope: the harness as used in repo-E 2026-10-11..16 (repository C: 16 lead sessions, 644 subagents, 188 merged PRs,
all agents on Opus 5.5), Anthropic's claude.dev blog (all 16 posts) and anthropic.com/engineering posts, Claude Code
docs, and outside measured evidence. Owner emphasis: how Opus 5.5 should be handled (effort, thinking, prompting,
model choice per role).

Decision served: the ranked list of build and experiment issues filed from the note; agent frontmatter (model,
effort), instruction wording, `dev` output formats, and the review loop.

Round 1 (2026-10-19): r1-usage-logs.md (lead, transcripts), r1-blog-*.md (claude.dev, 4 files), r1-engineering
(anthropic.com/engineering + docs), r1-outside (community and papers).
