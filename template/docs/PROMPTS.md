# Prompts

Repeatable session prompts. The repository carries the context; these lines only pick the routine.

| Situation | Prompt |
|---|---|
| Start or continue work (lead) | `Use the harness-ops:lead skill: integrate what is in review, then dispatch the next wave.` |
| Long unattended run | the `/goal` prompt in [SESSION.md](SESSION.md) |
| Deep research on a topic | `Use the harness-ops:research skill on: <question>. Decision it serves: <decision>.` |
| One issue, by hand | `Work on #N. Start with uv run dev board brief N.` |
| File new work | `File issues for: <idea>. Each with a measurable DONE WHEN, Owns and blockers.` |
| Review before merge | `Spawn a reviewer on PR #P, then integrate it if the verdict is merge.` |
| After a limit or crash | `Resume: uv run dev status, then resume or respawn each worker (lead skill §5).` |
| Improve the agent surface | `Use the harness-ops:harness skill: uv run dev agent-stats, find the costliest waste, propose one change with its measurement.` |

Worker spawn prompts (written by the lead) are one line: `Work on #N. Start with uv run dev board brief N.`
