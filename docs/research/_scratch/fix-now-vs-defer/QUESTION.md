# Question (#149)
Why do agents in a harness-run repository file a follow-up issue for work they could finish in the same change,
and what rule makes them fix it now without breaking scope discipline?

Scope: how often it happens and what the deferred work cost when it was finally done (agent-filed issues later
closed by a small diff), which harness instructions push towards filing (Owns, one issue per PR, round mode,
hand-back limits, "file the rest", the lead never builds), literature and prior art (self-admitted technical
debt and TODO survival, boy-scout rule, agent scope behaviour and over-caution), and a decision rule.
Data: this machine's Claude Code transcripts and the four sibling repositories (code names repo-L, repo-R,
repo-E, H; mapping in the gitignored .scratch/repos.md). Public repo: code names only, tests/test_scrub.py must pass.

Example (BELIEVED, lead): in one 2026-10-12/13 session on H, workers filed about 25 follow-ups; some were a few
lines inside or next to the issue's Owns (an xfail_strict flag and floor raise, a guard rule, an agent listing).

Decision it serves: the wording of builder, tester, researcher and lead instructions and any gate or hand-back check.
