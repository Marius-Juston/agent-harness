# #183 after-run: reuse-before-build trigger rate with #172 and #173 merged

This is the second arm of the pre-registered experiment in #183. The first arm is [trigger-baseline.md](trigger-baseline.md). The scenarios (`evals/scenarios/reuse-*.yaml`) and the grader (`evals/reuse/grade.py`) are unchanged since 3672d85.

| | baseline | after |
|---|---|---|
| commit | 3672d85: main without #172 and #173 | aa6dfe6: main with #173 (77617af) and #172 (aa6dfe6) merged |
| record | `run-1791904391.json` (2026-10-13 10:48) | `run-1791906923.json` (2026-10-13 11:28) |
| agent | `harness-ops:builder` from the commit's own plugin, `model: opus`, Claude Code 2.1.296. The record does not store the resolved model id; BELIEVED to be claude-opus-5-5. | same |
| cost (30 trials) | $8.60 | $8.43 |

Both arms were run by the lead with the owner's approval, because the tester's sandbox refuses `dev eval` commands.

## Result (VERIFIED, `uv run python evals/reuse/grade.py run-1791904391.json --after run-1791906923.json --guarded`)

| scenario | baseline | after (guarded) | after (strict) |
|---|---|---|---|
| reuse-charset | 3/3 | 3/3 | 3/3 |
| reuse-cron | 3/3 | 3/3 | 3/3 |
| reuse-dates | 3/3 | 3/3 | 3/3 |
| reuse-filelock | 3/3 | 3/3 | 3/3 |
| reuse-fuzzy | 3/3 | 3/3 | 3/3 |
| reuse-html2md | 3/3 | 3/3 | 3/3 |
| reuse-ratelimit | 3/3 | 3/3 | 3/3 |
| reuse-retry | 3/3 | 3/3 | 3/3 |
| reuse-schema | 3/3 | 3/3 | 3/3 |
| reuse-yamlrt | 3/3 | 3/3 | 3/3 |
| **all** | 30/30, pass^3 on 10 of 10 | 30/30, pass^3 on 10 of 10 | 30/30 |

- Paired difference (after minus baseline), with a 95% bootstrap interval over scenarios (10,000 resamples, seed 183): **+0.00 [+0.00, +0.00]**.
- The 80% target is met in both arms (1.00).
- "Strict" counts only an explicit `dev reuse` command and ignores the guard. The strict count is 30/30 too, so the `--guarded` rule never changed a result.
- A `uv add` ran in 29 of the 30 after-trials and in 30 of the 30 baseline trials.
- The guard's own output cannot be shown here. The run record keeps Bash commands but not tool results or hook output, so this report cannot tell what the guard printed or whether it refused anything. It does show that every trial went on to finish its run.

## Interpretation

- **Ceiling (the main caveat).** Both arms are at 100%, so this experiment shows non-inferiority only: #172 and #173 did not lower the trigger rate on these scenarios. It cannot show that either change helped.
- The interval is [0, 0] because every per-scenario difference is 0. That is a fact about this ceiling, not a precise estimate of the effect.
- BELIEVED: the trigger was already supplied by builder.md step 3, which tells the builder to name candidates and run `dev reuse check`, together with prompts in which the library need was obvious.
- The question #183 was meant to answer (does the hook raise a rate that the prompt alone leaves low?) moves to **#209**. #209 uses a pre-registered harder set, with the library need buried inside a larger issue, control scenarios where no library fits, an ablation without step 3, and hook output kept in the run record.
