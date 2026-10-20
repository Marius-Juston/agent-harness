# Research: recurring command sequences a dev subcommand or flag should replace

Status: v1 (2026-10-20). Issue: #321. Extends [harness-efficiency.md](harness-efficiency.md) §5 (#254 counted single commands; this note mines multi-step sequences). Serves #322, #323, #324 and #325.
Labels: SOURCED = cited and fetched; VERIFIED = measured here (script named; the transcripts are the owner's, so the numbers reproduce only on the owner's machine); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit search` on 2026-10-20 printed `discovery 2026-10-20: OK, partial (s2 down; openalex, crossref answered)` for the first query and `OK (openalex, s2 answered)` for the next two. The evidence is mostly measured on transcripts; outside work is used only as prior art.
Companions: [harness-efficiency.md](harness-efficiency.md) (cost model, §5 tool output, §8 what not to do), [r2-against](_scratch/harness-efficiency/r2-against.md) §3 (output cuts can raise cost). Scripts and output: [_scratch/command-sequences/](_scratch/command-sequences/sequences.py).

## 0. Findings in one paragraph
Across 1,129 transcripts of three harness-run repositories (2026-09-25..10-20: 80,743 turns, 65,731 Bash calls, about $6,072 modelled), **mechanical multi-step sequences are rare and cheap; the recurring cost is exploration.** The most frequent sequence in every role is `grep -n` then a `sed -n` window of the same file (4,234 pairs, a turn cost of $155 or 2.6 % of spend), but each window is a decision on what grep printed, and printing context around every hit instead costs more than the turn it saves in 67 % of the pairs whose window holds a hit. The mechanical sequences the brief names are real (the 2026-10-19 lead session: 18 integrates with a hand-written checks wait, 13 worktree-add-plus-claim commands), but agents already chain them into one Bash call (84 % and 88 %), so a dev subcommand saves command text and error paths, not turns: about 40 lead turns ($4) for integrate, $1 for dispatch prep. The premise of "python3 -c json filters over gh output" does not hold: 85 % of `gh --json` calls already use `--jq`, and 957 of the 1,146 JSON filters read `curl` output, mostly researchers calling the GitHub, literature and registry APIs directly. Three cheap changes are worth filing because each removes a prescribed manual step or a recurring failure, not for tokens: **integrate waits for pending checks** (#322: 43 % of integrate calls carry a manual wait, 15 refusals), **`dev board brief` prints the `board new` form** (#324: `board new --help` in 151 transcripts), and **researchers check GitHub repositories with authenticated `gh api --jq`** (#323: 360 curl-into-python calls, 8.3 % failing). Everything else is rejected with a reason in §3.4. Together the three save about $10, under 0.2 % of spend, against up to 11.9 % fewer LLM calls reported for meta-tools on benchmarks where each tool call is a separate model step.

## 1. Question, data and method
The question (#321): which recurring multi-step command sequences, run by the lead and by workers, should a dev subcommand, a flag or a script replace, ranked by frequency, tokens and error rate, with a spec and an estimated saving for each, and without proposing what `uv run dev --help` or the v0.1.10 [CHANGELOG](../../CHANGELOG.md) already provide?

**Data (VERIFIED, [sequences.py](_scratch/command-sequences/sequences.py), output [report.json](_scratch/command-sequences/report.json), summary [savings.py](_scratch/command-sequences/savings.py)).** Claude Code transcripts on the owner's machine for repo-E (721 transcripts), L1 (406) and H, this repository (2 main sessions; most H work runs from repo-E sessions and is counted there). Main sessions are the lead; subagents take their role from `.meta.json` (builder, researcher, reviewer = reviewer + security-reviewer + research-auditor, tester, other). 94,161 tool calls. Modelled spend: repo-E $2,245, L1 $3,819, H $7.5. repo-E's figure is of the same order as the $2,356 modelled for its first week in [harness-efficiency.md](harness-efficiency.md) §2 (this model leaves out visible output), a sanity check of the cost model, not a replication.

**Method.**
- Each Bash command is split into steps at unquoted `&&`, `||`, `;`, `|` and newlines (heredoc bodies dropped), and each step is normalised to a label: command plus subcommand words, no arguments (`dev board claim`, `gh pr checks`, `git worktree add`, `python -c json<stdin`). Only public command names reach the report; anything else becomes `other` or `script`.
- Sequences are counted three ways: pipes inside one call (the step that consumes an output), n-grams (2-4) of consecutive tool calls with at least two Bash calls, and detectors for the sequences the brief names (sections A-K of `detectors`).
- Cost per occurrence: each turn re-reads its context at the cache-read price and writes the command plus 233 hidden output tokens; each output is written to the cache once and re-read on every later turn until the next compaction. Opus 5.5 list prices, 4 characters per token of tool output and 0.443 tokens per character of model text, all as in [harness-efficiency.md](harness-efficiency.md) §2. The mean modelled cost of the turn that issues a Bash call: lead $0.096, builder $0.046, researcher $0.038, reviewer $0.019 (VERIFIED).
- Rank score: modelled $ x (1 + error rate). A saving counts the turns a replacement removes at that role's turn cost, plus intermediate output no longer carried; it ignores wall-clock time.

**Limits.** Every model is priced as Opus 5.5 (ASSUMED for L1's earlier sessions). Pipes mask exit codes, so error rates of piped commands are lower bounds. Outputs are kept as their first and last 400 characters. L1's lead sessions include the owner's interactive work, and harness versions changed during the window; v0.1.10 shipped on 2026-10-19, so one lead session (2026-10-19..20) runs on it.

## 2. What recurs, per role
- **Exploration dominates every role (VERIFIED, report.json `ngrams_top_by_score_per_role`).** The top-scored n-gram is `grep > sed -n` for the lead (100), builder (1,141), researcher (310) and reviewer (215), followed by its mirror `sed -n > grep` and longer alternations. Pipes say the same: `grep | head` is the most common pipe in every role (builder 11,197). The lead's next sequences are Python heredoc file edits from L1's interactive sessions, not harness steps.
- **The mechanical sequences, counted exactly** (VERIFIED, report.json `detectors` and `lead_sessions`; "in one call" means the agent already chained the steps in one Bash command):

| sequence (detector) | main role | n | in one call | errors | note |
|---|---|---|---|---|---|
| checks wait + `dev integrate` (G) | lead | 158 of 365 integrates | 133 (84 %) | 15 refusals for a pending check | 2026-10-19 session: 18 of 29 |
| `git worktree add` + `dev board claim` (H) | lead | 88 | 77 (88 %) | 1.3 % one call, 9 % split | 2026-10-19 session: 13 |
| `dev integrate` piped into `tail` or `grep` (D) | lead | 364 of 365 | all | 1.9 % (masked) | the lead reads only the RESULT lines |
| integrate outcomes (G) | lead | 365 | n/a | n/a | merged 227, unknown 53 (background, interrupted), refused 39, harness-review 17, stopped 12, conflict 8; 134 calls retried a PR already tried in the session |
| `curl` into a `python -c` JSON filter (A) | researcher (842) | 957 | all | 5-8 % | GitHub API 360, literature APIs 258, registries 154, Hugging Face 79 |
| `gh … --json` (A) | all | 1,511 | n/a | 4.6 % | 1,284 (85 %) already use `--jq` or `-q`; gh output into Python: 17 |
| `dev code outline X`, then a window of X (B) | builder, researcher | 469 outlines | n/a | 2 % | `sed -n` 295 (63 %), `dev code show` 20, Read 8 |
| `grep -n`, then `sed -n` on the same file (C) | builder (2,349) | 4,234 | n/a | 2.1 % | the window holds a grep hit in 1,708 |
| `dev <cmd> --help`, then that command (K) | builder, researcher | 256 | n/a | 6 % | `board new --help` 154 times in 151 transcripts |
| PR or issue body read, edit, write (J) | builder, researcher | 107 | 2.69 calls each | 7.5 % | mostly fixing DONE WHEN text or session links |
| persisted output read back (E) | researcher, builder | 520 of 1,001 | n/a | 0-3 % | by Read 417 |
| `sleep` outside a loop (F) | all | 587 | n/a | 27.6 % | Claude Code refuses long foreground sleeps |
| `dev pr` piped into `tail` (D) | builder | 249 | n/a | 44 % | isolation refusals, before v0.1.10 |

- **Pytest outside `dev test`.** `pytest` ran directly 4,870 times (L1 3,338, repo-E 1,524) against 239 `dev test` calls, and builders piped it into `tail` or `grep` 3,832 times (VERIFIED). That is a single command, outside this question; filed as #325.

## 3. Candidates: spec, saving, and what exists
### 3.1 `dev integrate` waits for checks still running on the PR head (#322, P2)
- **Exists?** integrate already waits for every check on a head it pushed (`await_checks`, bounded by `checks_wait_s`, 900 s by default), but on the PR's own head it counts a pending check as unfinished feedback and refuses (VERIFIED by reading `_integrate` and `unfinished` in `devkit/workflow.py`). Step 3 of the lead skill tells the lead to "wait for green checks" first.
- **Spec.** Input: the PR number, as today. When the only unfinished items are pending checks, wait for them with `await_checks`, read feedback again, then continue. A failing check, requested change or unresolved thread still refuses at once; a timeout ends in the existing `waiting` result, which a rerun resumes. Output unchanged: the lead branches on the last `RESULT-JSON` line.
- **Saving (VERIFIED, savings.py).** 25 lead turns spent reading checks in a separate call and 15 reruns after a pending-check refusal: 40 lead turns, $3.86 over the window (0.06 % of spend), 15 of the 39 refusals, and the hand-written `gh pr checks <n> --watch --interval 20 >/dev/null 2>&1;` in 133 commands. Worth building because it reuses code that exists and removes a prescribed manual step (BELIEVED), not for tokens.
- **Not proposed:** a quieter integrate. The lead pipes 364 of 365 integrate calls, but what it keeps is about 400 characters, r2-against warns that cutting output can raise cost, and the `RESULT-JSON` contract already serves the next step.

### 3.2 `dev board brief` prints the `board new` form (#324, P3)
- **Exists?** The brief's Commands section lists claim, navigate, test, check, pr and beat, but not `board new` (VERIFIED, `devkit/board.py`), which workers need for out-of-scope findings.
- **Spec.** One line: `uv run dev board new --type <type> --title "..." --done "..." --owns <path> --refs <n>`. No new command.
- **Saving (VERIFIED).** 154 `board new --help` calls in 151 transcripts, $5.24 of turns; 256 help-then-use pairs across all dev commands. Small in money; it removes a lookup that most follow-up-filing runs make (BELIEVED).

### 3.3 Researchers check GitHub repositories with authenticated `gh api --jq` (#323, P3)
- **Exists?** `dev reuse check` verifies registry packages (PyPI, npm, cargo) only (VERIFIED, `check` in `devkit/reuse.py`). researcher.md tells researchers to check every other tool "through the API (`https://api.github.com/repos/<o>/<r>`…)", which they do with unauthenticated `curl` piped into a hand-written Python filter.
- **Spec.** A text change: `gh api repos/<o>/<r> --jq '{stars: .stargazers_count, pushed_at, archived, license: .license.spdx_id}'`. `gh` is authenticated, and guard_bash allows read-only `gh api` for every role (VERIFIED, `plugins/harness-ops/scripts/guard_bash.py`).
- **Saving (VERIFIED).** 360 calls (about 107 per 100 researcher runs), 30 failed (8.3 %), $1.14 of failed turns; 15 of 268 GitHub API curls printed a rate-limit message or a 403. The unauthenticated limit is 60 requests per hour against 5,000 for an authenticated user (SOURCED, [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)). A `dev reuse check owner/repo` mode would also apply gates G1-G6, but the one-line text change is the simplest arm and goes first (BELIEVED).

### 3.4 Not worth building, with the reason
- **`grep -n` then `sed -n` windows** (the largest by volume). One call would have to print context around every hit. In 1,143 of the 1,708 pairs whose window holds a hit (67 %), that extra output costs more than the turn it saves, and the 565 pairs where it wins sum to about $26 over the window (VERIFIED, `C one-call grep -C instead`; the window per hit is ASSUMED to equal the window the agent chose). The window is a decision on grep's output, so a fixed command cannot make it. `dev code find` and `dev code show` exist and are 3-6 % of navigation ([harness-efficiency.md](harness-efficiency.md) §5).
- **`dev code outline` then `sed -n`.** No turn to save: the outline already ends with the `dev code show` or `sed -n` hint, and choosing the window needs the outline (VERIFIED, 295 of 469 outlines).
- **Dispatch prep (`git fetch` + `git worktree add` + `dev board claim`).** 77 of 88 are already one compound call; a `claim --worktree` flag would save 11 turns ($1.06 at the lead's turn cost) and some command text (VERIFIED).
- **JSON filters over `gh` output.** 85 % of `gh --json` calls already use `--jq`; 17 gh outputs went into Python (VERIFIED). The curl filters on the literature APIs (258) are now denied by guard_bash (#227, v0.1.10), and `dev lit get` takes several ids at once.
- **PR and issue body edit loops.** 107 runs of 2.69 calls with 7.5 % errors, about $8 of turns (VERIFIED); a `--replace OLD NEW` body edit would save little. Re-measure after v0.1.10, whose session-link refusal in `dev pr` and `integrate` may make these loops more frequent.
- **Persisted outputs read back.** 417 of the 520 read-backs use Read, which the v0.1.10 read guard now windows (#265); the Bash read-backs are about 100 (VERIFIED).
- **Polling and `sleep`.** 587 bare sleeps fail 27.6 % of the time, but all polling costs $18 for all agents in repo-E's first week ([harness-efficiency.md](harness-efficiency.md) §2), and the fix is Claude Code's background notification, not a dev command.
- **Trimming `dev check` or `cite-check` output that agents pipe into `tail`.** Rejected in [r2-against](_scratch/harness-efficiency/r2-against.md) §3: their output is already a summary. The refusals of `dev pr` behind a pipe were fixed in v0.1.10 (#261).

## 4. Why the savings are this small
- **Agents already chain deterministic steps.** In Claude Code a Bash call takes a whole shell program, so the agent chains mechanical steps itself (84-88 % of the lead's mechanical sequences, and every JSON filter, run in one call; VERIFIED §2). A dev subcommand then removes command text and error paths, not turns. Meta-tools save most where each tool call is a separate model step: Agent Workflow Optimization turns recurring tool-call sequences into "deterministic, composite tools" and "reduces the number of LLM calls up to 11.9% while also increasing the task success rate by up to 4.2 percent points" on two benchmarks (SOURCED, Abuzakuk et al. 2026 ([arXiv 2601.22037](https://arxiv.org/abs/2601.22037)), abstract). Workflows induced from past runs and given to the agent raised relative success 24.6 % and 51.1 % on Mind2Web and WebArena, with fewer steps on WebArena (SOURCED, Wang et al., ICML 2024 ([arXiv 2409.07429](https://arxiv.org/abs/2409.07429)), abstract). Neither measures shell-based coding agents, and here most of the bundling has already happened (BELIEVED).
- **The harness already replaced the costly ones.** `dev pr`, `dev integrate` (re-gate, merge, close issues, retarget), `dev board brief` (issue, feedback, commands) and `dev board claim` each stand for several steps; what remains is the glue around them.
- **The remaining cost is reading.** The grep/sed loop is how agents read code ([harness-efficiency.md](harness-efficiency.md) §5: 28,911 Bash reads against 1,301 Read); changing it is a navigation question, not a command-sequence one.

## 5. Recommendation for this project
- Build #322 (P2), then #324 and #323 (P3). Together they remove about 40 lead turns, 154 help lookups and 30 failed API calls per four weeks of these three repositories: about $10, under 0.2 % of the $6,072 modelled spend (VERIFIED arithmetic, savings.py). Judge each on its DONE WHEN (manual waits under 10 % of integrate calls, `board new --help` in under 3 % of transcripts, GitHub curls under 10 per 100 researcher runs), not on spend, which these changes cannot move measurably.
- Build no command for the grep/sed exploration loop, dispatch prep, gh JSON filters or body edits (§3.4). Re-run `sequences.py` after two weeks on v0.1.10; if body-edit loops pass 200 per four weeks, or a new sequence above $20 per four weeks appears whose steps do not depend on the previous output, file it then.
- Reuse this script for later harness versions rather than writing a new one: `python3 sequences.py extract --cache <a path outside the repo> repo-E=<dir> L1=<dir> H=<dir>`, then `python3 sequences.py report --cache <same path> > report.json`, then `python3 savings.py report.json`.

## Sources
Measured (VERIFIED): [sequences.py](_scratch/command-sequences/sequences.py) (extract and report), [report.json](_scratch/command-sequences/report.json) (its output: labels and counts only), [savings.py](_scratch/command-sequences/savings.py) (the shares and savings quoted above). Inputs: the owner's Claude Code transcripts for repo-E, L1 and H, 2026-09-25..10-20, read 2026-10-20. Code read: `devkit/workflow.py` (`_integrate`, `unfinished`, `await_checks`), `devkit/board.py` (brief), `devkit/reuse.py` (`check`), `plugins/harness-ops/scripts/guard_bash.py`, `plugins/harness-ops/agents/researcher.md`, `plugins/harness-ops/skills/lead/SKILL.md`, and [CHANGELOG.md](../../CHANGELOG.md) v0.1.10.

Documentation: GitHub, [Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) (fetched 2026-10-20).

Papers (abstracts fetched with `uv run dev lit get` on 2026-10-20):
- Abuzakuk, Kermarrec, Sharma, Veski & de Vos, [Optimizing Agentic Workflows using Meta-tools](https://arxiv.org/abs/2601.22037), arXiv 2026.
- Wang, Mao, Fried & Neubig, [Agent Workflow Memory](https://arxiv.org/abs/2409.07429), ICML 2024.

## Follow-ups
- [ ] #322 integrate waits for pending checks. DONE WHEN the wait ships with tests and, over ≥3 lead sessions, manual checks waits fall under 10 % of integrate calls and pending-check refusals to 0.
- [ ] #324 brief prints the `board new` form. DONE WHEN the line ships with a test and transcripts with `board new --help` fall from 13 % to under 3 %.
- [ ] #323 researcher.md uses `gh api --jq` for GitHub repositories. DONE WHEN the text ships and GitHub API curls fall from about 107 to under 10 per 100 researcher runs.
- [ ] #325 direct `pytest` against `dev test`. DONE WHEN a committed script reports both per repository and version, with error, rerun and output size, and ends with steer, leave or build.
- [ ] Re-run `sequences.py` on two weeks of v0.1.10 transcripts. DONE WHEN §2's table is refreshed and any sequence above $20 per four weeks whose steps do not depend on the previous output is filed.
