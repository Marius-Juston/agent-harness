# #183 baseline: reuse-before-build trigger rate of builders

The pre-registration is #183's Context. The scenarios are `evals/scenarios/reuse-*.yaml`, with fixtures in `evals/reuse/`, and the grader is `evals/reuse/grade.py`. All three were committed before this run, and neither the scenarios nor the grader may change after it.

| | |
|---|---|
| commit | 3672d85, which is main 28faf8b plus the scenarios. It does not include #172 (the uv add guard) or #173 (the Reuse: line). |
| run | `uv run dev eval reuse-<10 names> --trials 3`, record `run-1791904391.json` (when: 2026-10-13 10:48) |
| agent | `harness-ops:builder` from that commit's plugin. Its frontmatter says `model: opus`, with Claude Code 2.1.296. The record does not store the resolved model id; it is BELIEVED to be claude-opus-5-5, the opus model on 2026-10-13. |
| who ran it | the lead, from its own session with the owner's approval. The tester's worktree sandbox refused any command containing the word `eval` (a false positive for shell `eval`). The command was the tester's, unchanged. |
| cost | $0.71 to $1.30 per scenario (3 trials) |

## Result (VERIFIED, `uv run python evals/reuse/grade.py run-1791904391.json`)

| scenario | passes | rate | pass^3 |
|---|---|---|---|
| reuse-charset | 3/3 | 1.00 | yes |
| reuse-cron | 3/3 | 1.00 | yes |
| reuse-dates | 3/3 | 1.00 | yes |
| reuse-filelock | 3/3 | 1.00 | yes |
| reuse-fuzzy | 3/3 | 1.00 | yes |
| reuse-html2md | 3/3 | 1.00 | yes |
| reuse-ratelimit | 3/3 | 1.00 | yes |
| reuse-retry | 3/3 | 1.00 | yes |
| reuse-schema | 3/3 | 1.00 | yes |
| reuse-yamlrt | 3/3 | 1.00 | yes |
| **all** | 30/30 | 1.00 | 10 of 10 |

The baseline already meets the 80% target. In every trial the builder ran `dev reuse check` (or `dev reuse "<need>"`), usually on two or three candidates it named itself, and then added one of them with `uv add`.

## Ceiling (read this before the after-run)

The baseline is at 100%, so this scenario set has no room to show an improvement from #172 or #173. The after-run can show non-inferiority only, meaning the rate does not drop. The paired difference can only be zero or negative.

A likely reason (BELIEVED): at this commit, builder.md step 3 already says to name candidates and run `uv run dev reuse check`. Also, each prompt here is a small, isolated task inside a sandbox, where the need for a library is more visible than in a real issue. The 2-of-6 rate in #169 was measured on real builder transcripts (docs/research/_scratch/software-discovery/scripts/builder_reuse.py), not on scenarios like these.

Do not change the scenarios or the grader for the after-run. A harder set, with the library need buried inside a larger issue, would be a new pre-registration under a new issue.
