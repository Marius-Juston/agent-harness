# r1 (a): the issue and PR bookkeeping still done by hand, measured in the harness-run transcripts

Round mode for #346, sub-question (a). Written 2026-10-20.

Search coverage: no `dev lit search` was run. This round measures local Claude Code transcripts and the GitHub API only.

Labels: **VERIFIED** = measured here, with the script named; **BELIEVED** = judgment; **ASSUMED** = unchecked premise.
The transcripts belong to the owner, so the numbers reproduce only on the owner's machine.

**Data and method.**
- Transcripts: repo-E and H, the same two harness-run projects as [command-sequences.md](../../command-sequences.md), 2026-10-11..20.
  That is 735 transcripts, 41,050 turns and $2,291 of modelled spend; the lead's share is 20 sessions, 4,043 turns and $445.
  This #346 session is excluded (`--exclude`) (VERIFIED, [bookkeeping.py](bookkeeping.py) `overview`).
- Reuse: [bookkeeping.py](bookkeeping.py) imports [sequences.py](../command-sequences/sequences.py).
  It reuses its transcript reader (`extract_file`), step labels (`annotate`) and cost model (`turn_cost`, `carry_cost`).
  It adds four fields that reader drops: full timestamps, SendMessage recipients and lengths, Agent subagent types, and Edit/Write paths.
  It also adds the subagent hand-backs that reach the lead as messages.
- GitHub data: `bookkeeping.py fetch` ran read-only GraphQL on 2026-10-20 17:49 UTC.
  It fetched all 306 PRs (commits, comments, reviews, force pushes) and all 791 issues (label, comment and close timelines) of both repositories.
- Output and arithmetic: the committed output is [report-bookkeeping.json](report-bookkeeping.json), which holds counts only, no command text and no repo-E paths.
  The savings come from [rank.py](rank.py).
- Cost: as in [command-sequences.md](../../command-sequences.md) §1.
  A turn costs its context re-read plus the command and 233 hidden output tokens. Output is carried until the next compaction.
- Categories: each call is put in the bookkeeping categories it touches (`categories()` in bookkeeping.py).
  A category's dollars count each turn once, plus every call's carry. One call can be in two categories (a `worktree add && board claim && board new` chain is in 4 and 9), so the category rows overlap.

## Findings

**F0. Bookkeeping is about a third of the lead's turns.** It is about a quarter of the lead's spend and about 9 % of all spend.
- Lead calls in categories 1-9 (no reviewer spawns): 1,288 of 4,043 lead turns (31.9 %) and $118 of $445 (26.5 %).
- All roles: 2,947 turns and $196 of $2,291 (8.5 %).
- VERIFIED, report-bookkeeping.json `categories.*."ANY of 1-9 (no reviewer spawn, no x-rows)"`.
- A turn spent on bookkeeping is not a turn a tool can remove. The removable part is the "upper" column of F10 below: about $95 over the window, overlaps included. That is 4.1 % of spend. Each upper bound assumes the tool removes every such turn (BELIEVED).

**F1. Candidate 1, the integrate loop. Retries are cheap; the cost is one turn per PR.**
- The lead ran 351 `dev integrate` calls with a PR number, on 236 PRs: 214 merged, 47 with unknown output, 42 refused, 20 harness-review, 12 stopped, 8 conflict.
- There were 115 retries: a try on a PR already tried in any lead session. I compared GitHub between consecutive tries.
  - 37 (32 %) had a new head commit.
  - 16 had only a new comment or review.
  - 26 had the same head, with the lead adding `--harness-ok` (20) or `--override` (6). This is the prescribed second step after a `harness-review` outcome.
  - 36 (31 %) had nothing changed. The try before them had:
    - a refusal for a pending check (15), which v0.1.11's wait (#322) now absorbs;
    - an output the lead never saw (13): backgrounded, blocked or permission-denied;
    - a diagnostic re-run after `gate-failed` (3);
    - another refusal (3), a merge (1) or a stop (1).
- The median gap between tries is 1.7 minutes.
- VERIFIED, report-bookkeeping.json `integrate`.
- Limits:
  - Only 2 retries had both head SHAs printed (RESULT-JSON `head`). The others are classed from commit `committedDate`, so a commit made before try a but pushed after it reads as "nothing changed": "new head" is a lower bound.
  - [command-sequences.md](../../command-sequences.md) §2 counted 134 retries within a session. That count included calls with no parsable PR number. This count keys by repository and PR, across sessions.
- **The 134 retries, counted the command-sequences way** (VERIFIED, [retries134.py](retries134.py)):
  - Method: a retry is a call whose PR key was already tried in the same lead session, as in detector G.
  - On today's cache that gives 141 retries out of 379 calls. That is 7 more than 134, because the cache now includes the lead sessions of 2026-10-20.
  - What changed before each retry:
    - 34 had the same head, the same feedback and the same flags.
    - 27 had the same head and the same feedback, but the lead added a flag (`--harness-ok` or `--override`).
    - 37 had a new head commit.
    - 16 had the same head but a new comment or review.
    - 26 had no PR number (a loop variable or `--help`), so they were not classed.
    - 1 was on a PR not found on GitHub.
  - **Same head and same feedback: 61 of 141 (43 %).** That is 61 of the 115 that have a PR number (53 %).
  - Only the 34 with the same flags too would have been answered by a cache keyed on head SHA and feedback state. Fifteen of those waited on a pending check, which #322's wait now covers (F1). So a SHA-keyed refusal cache would answer about 19-34 retries. The 27 flag retries carry a new lead decision, so they are not cache hits.
  - The head comparison rests on commit dates, so "same head" is an upper bound.
- Cost:
  - The loop is 441 lead turns, $40.2: 378 integrate-call turns ($35.4) and 63 turns that only read or wait on checks ($4.8). That is 2.07 turns per merged PR.
  - The turns fall into 59 bursts (a gap over 20 minutes starts a new one), with a median of 5 turns each.
  - VERIFIED, `integrate_loop_turn_split` and `integrate_bursts`.
- Saving, VERIFIED arithmetic in [rank.py](rank.py):
  - A refusal cache keyed on head SHA and feedback state saves at most the 21 nothing-changed retries not explained by pending checks: $2.0.
  - A queue (`integrate --ready`) running one call per burst saves at most 319 turns, $29.9. Most of those turns carry a lead judgment, though (`--harness-ok` on 302 of 351 calls, `--override` on 96), so the queue needs those decisions as input (BELIEVED).
- The diagnostic re-runs are a safety case, not a cost case. One `gate-failed` PR was re-run with `--override "x"` and merged, and the lead had to post a correction on the PR (VERIFIED, lead `gh pr comment` bodies, read in the cache).
  - A `dev integrate <pr> --why` that prints the last refusal or gate failure for the current head, without re-running, would remove that path (BELIEVED).

**F2. Candidate 2, the review relay. It is the largest by turns and by lead-written text.**
- 164 PRs went through 331 review passes (reviewer or auditor spawns plus re-review requests). 78 PRs had 1 pass, 43 had 2, and 30 had 4 or more.
- The lead spent 396 relay turns on them, $43.7. That is 2.41 turns per PR and 1.20 per pass. The relay turns are:
  - 155 PR comments posting a reviewer's verdict (137 k characters copied);
  - 193 findings messages to the author that name a PR (of 250 in all, mean 1,052 characters);
  - re-review requests.
- The lead also posted 59 review verdicts as issue comments, which are not keyed to a PR.
- VERIFIED, `review_relay_per_pr`, `lead_sendmessage_kinds`, `comment_posts`, `categories.lead."2 review relay"` (409 turns, $44.5).
- Reviewers post their own review only rarely: 21 `gh pr comment` calls (10 of them a review) and 0 `gh pr review` calls in 235 reviewer transcripts (VERIFIED, `comment_posts`).
- Reviewer hand-backs to the lead: 186, mean 4,958 characters, carried for $8.8 (VERIFIED, `handbacks_to_lead`).
- Saving, VERIFIED arithmetic, [rank.py](rank.py): $3.1 to $23.9. The spec behind it:
  - The reviewer posts one GitHub review itself (`dev review post`) and hands back a 300-character verdict.
  - The author reads the review with `board feedback` or `board brief`.
  - The lead stops posting, and its findings message becomes a 200-character pointer.
  - The lower bound counts only the output tokens of the copied text; the upper bound adds the posting turns and the hand-back carry.

**F3. Candidate 3, the scheduler. The dead Actions cron cost little, because local devkit does the board moves.**
- repo-E's scheduled `board` workflow:
  - succeeded 16 times, last on 2026-10-15 17:19 UTC, and has failed 23 times since (VERIFIED, `gh run list --workflow board.yml` on repo-E, 2026-10-20);
  - H has no board workflow (VERIFIED, `gh run list` answers HTTP 404 for `board.yml`).
- In the whole history of both repositories, `github-actions` made 10 of 2,354 status or stale label changes. Only one `stale` label was ever applied, and not by the cron. VERIFIED, `staleness.*.status_or_stale_label_changes_by_actor`.
- integrate re-syncs blockers after every merge (`board.merged_pr` calls `sync()`), so blocked-to-ready propagation does not depend on the cron (VERIFIED, devkit/board.py `merged_pr`).
- Board drift while the cron was dead:
  - In repo-E, 9 claimed in-progress items went more than 6 h without a comment, after 72 claims.
  - Seven of those were closed 6.1 h after their last comment, in one batch.
  - One sat idle 11.4 h before a comment.
  - One claim sat idle 91.8 h, and no stale label flagged it.
  - H had 1 episode, of 1.8 h over the limit.
  - A read-only `sync` dry run at 2026-10-20 found 1 blocked item in repo-E and 2 in H whose blockers had all closed. Some may be manual blocks.
  - VERIFIED, `staleness`, and a read-only call to devkit `board.fetch`/`resolved` in each checkout.
- By hand, the lead ran board upkeep 70 times (sync, list, next, lint, wave, `usage-poll --once`, `quota-cap`): $5.9 (VERIFIED, `categories.lead."3 board upkeep runs"`).
- A local timer would save at most that $5.9, and would have flagged the one 92-hour claim (VERIFIED arithmetic, rank.py).

**F4. Candidate 4, duplicate checks. Follow-up filing is the largest worker bookkeeping cost; duplicates are a small part of it.**
- Filing and issue upkeep cost $57.8 and 900 turns across roles, the largest category by dollars. Researchers spent $19.1 on it, builders $10.8 and the lead $26.8 (VERIFIED, `categories.*."4 filing/upkeep of issues"`).
- 630 filings carry an issue URL in their output. On 2026-10-20:
  - 398 (63 %) were still open, 194 closed completed and 11 closed not planned;
  - 27 (4.3 %) had a last comment matching duplicate, superseded, covered by or tracked upstream.
  - VERIFIED, `board_new.fate`.
- That regex counts more than the hand read in [r1-dedup.md](r1-dedup.md) F1 (13 of 791 issues, 1.6 %), because "tracked upstream" includes moves to H. Use r1-dedup's count for precision; mine is an upper bound.
- 65 filings (10 %) followed an issue search within 3 calls (VERIFIED, `board_new_preceded_by_issue_search`). The lead closed 15 issues as duplicate, superseded or covered (VERIFIED, `lead_manual_closes`).
- Saving: $1.7 to $8.4 (VERIFIED arithmetic, rank.py).
  - The upper bound counts every duplicate filing turn and its lead close turn, plus the manual searches.
  - It ignores the cost of a 63 % open backlog, which is the fix-now question (BELIEVED).

**F5. Candidate 5, conflicts. They are rare and a third are mechanical.**
- 8 of 351 integrates ended in `conflict` (2.3 %). 34 calls showed a merge conflict in their output: builder 19, reviewer 7, researcher 5, lead 3. Builders' conflict calls fail 31.6 % of the time.
- The lead sent 26 conflict or base-branch relays, $2.7 (VERIFIED, `conflicts`, `categories.*."5 *"`).
- Of 64 file conflicts, 22 (34 %) are lock files (7), docs/generated (7), research INDEX.md (4) or pyproject.toml (4). Regeneration or a merge driver could settle these without a model (BELIEVED for the remedy, VERIFIED for the counts).
- Saving: $0.8 to $4.9 (VERIFIED arithmetic).

**F6. Candidate 6, PR bodies. The model's text is mostly findings, not boilerplate.**
- Builders ran `dev pr` 318 times. 92 carried a heredoc summary, median 2,761 characters, $2.19 of output in total.
- The error rate is 36.8 %, mostly isolation refusals before v0.1.10 (see [command-sequences.md](../../command-sequences.md) §2, detector D).
- Checkbox lines are 5 % and gate or test result lines 7 % of the summary text, the part a generator would write. VERIFIED, `pr_summaries`, `pr_summary_composition_chars`.
- Saving: $0.3 to $2.8 (VERIFIED arithmetic).

**F7. Candidate 7, HANDOFF.** The lead wrote HANDOFF.md or SESSION.md 19 times, in 16 turns over 6 days. That was 34.6 k characters, $1.9 to $2.3, with 3 errors (15.8 %). It read HANDOFF.md 4 times (VERIFIED, `handoff`, `categories.lead."7 HANDOFF/SESSION write"`). This is a small target. A skeleton saves at most $2.3.

**F8. Candidate 8, the changelog.**
- 19 writes to CHANGELOG.md (Edit, Write or a Bash write), 15 of them by builders on release PRs: 24.5 k characters, $0.66 (VERIFIED, `changelog`).
- No conflict involved CHANGELOG.md (VERIFIED, `conflicts.files_top`).
- It is the smallest target.

**F9. Candidate 9, `board claim --worktree`.**
- The lead ran `git worktree add` 116 times. 82 (71 %) were already in the same call as `board claim`; 3 of 173 claims failed (VERIFIED, `worktree_claim`).
- The flag saves at most the 34 unchained adds, $3.1 (VERIFIED arithmetic). It is mostly command text and an error path.

**F10. Rank by upper-bound saving over the 10 days** (VERIFIED, [rank.py](rank.py) on [report-bookkeeping.json](report-bookkeeping.json); spend $2,291):

| rank | candidate | lower $ | upper $ | upper % of spend | frequency, error rate |
|---|---|---|---|---|---|
| 1 | 1 integrate queue + refusal cache | 2.0 | 29.9 | 1.30 | 351 calls, 115 retries, 13 % unknown outcome |
| 2 | 2 reviewer posts its own review | 3.1 | 23.9 | 1.04 | 164 PRs, 331 passes, 396 relay turns, 0 % errors |
| 3 | 4 duplicate check in board new | 1.7 | 8.4 | 0.37 | 630 filings, 27 later closed as duplicate (upper) |
| 4 | N1 owner-decision record | 0 | 6.3 | 0.28 | 115 AskUserQuestion, 67 hand-written records |
| 5 | 3 local scheduler | 0 | 5.9 | 0.26 | 70 manual upkeep runs; 1 claim idle 92 h |
| 6 | 5 conflict pre-check | 0.8 | 4.9 | 0.21 | 8 integrate conflicts, 34 conflict calls |
| 7 | N2 close issues done by a merged PR | 0 | 3.5 | 0.15 | 38 hand closes |
| 8 | 9 board claim --worktree | 0 | 3.1 | 0.14 | 116 adds, 71 % already chained |
| 9 | N3 reviewer checkout worktrees | 0 | 2.9 | 0.13 | 78 adds, 70 removes |
| 10 | 6 generated PR body | 0.3 | 2.8 | 0.12 | 318 builder `dev pr`, 12 % generable text |
| 11 | 7 HANDOFF skeleton | 0 | 2.3 | 0.10 | 19 writes in 6 days, 15.8 % errors |
| 12 | 8 git-cliff changelog | 0 | 0.7 | 0.03 | 19 writes |

The dollars are small for the same reason [command-sequences.md](../../command-sequences.md) §4 gives: a turn costs about $0.09 and a cache read is cheap. On the owner's goal (less pressure on the LLM), candidates 1 and 2 also take away the most lead turns (378 and 396) and the most lead-written text (F2: 137 k characters copied into PR comments and 263 k in findings messages) (BELIEVED that turns and text are the better measure of pressure).

## Other recurring bookkeeping not in the list

- **N1, recording owner decisions.**
  - The lead asked the owner 115 questions (AskUserQuestion). 44 were followed within 7 calls by an "Owner decision ..." comment on an issue or PR, or by a relay to a worker.
  - That makes 42 records and 25 relays, 75 k characters, $6.3 (VERIFIED, `owner_decisions`, `categories.lead."x owner-decision *"`).
  - A `dev board decide <n>` that asks and records the answer in one step is a new candidate (BELIEVED).
- **N2, closing issues by hand after a merge.**
  - 38 of the lead's 58 `gh issue close` calls close an item "done in PR #N" or "released", on 7 days from 2026-10-11 to 2026-10-20. 15 close duplicate, superseded or covered items (VERIFIED, `lead_manual_closes`, `lead_close_days`).
  - integrate's `merged_pr` closes only issues the PR body names with `Closes #N`. These are most likely PRs that said `Refs` (partial) or items spread over several PRs (ASSUMED, not checked per PR).
- **N3, reviewer checkouts.** Reviewers ran `git worktree add` 78 times and `remove` 70 times, in 56 transcripts, $2.9 (VERIFIED, `reviewer_worktrees`).
- **Not bookkeeping, but nearby.**
  - The lead's 742 spawn prompts average 681 characters, $4.5 of output (VERIFIED, `dispatch_prompts`).
  - Agents ran `gh issue view` 723 times, although `board brief` exists (VERIFIED, `issue_view_reads`).

## Gaps not closed
- Head SHAs are known for only 2 of 115 integrate retries, so "new head" versus "nothing changed" rests on commit dates (F1). Fetching push events per PR (REST `/events`, or the PR timeline's `HeadRefForcePushedEvent` plus `PullRequestCommit`) would close this.
- Owner-decision chains and issue-close kinds are classed by regex on the first 300-400 characters of command text, and I hand-checked only small samples. Duplicate counts are an upper bound against [r1-dedup.md](r1-dedup.md).
- I did not count the carry of check-wait output or of `board brief`, and I did not measure wall-clock time. Both matter for candidate 1.
- usage-poll: the transcripts show 6 manual `usage-poll --once` runs. Whether the detached poller ran is not in them.

## Sources fetched
- Transcripts: the owner's Claude Code project folders for repo-E and H (local, not committed), read by [bookkeeping.py](bookkeeping.py) `extract`.
- GitHub GraphQL API (PRs, issues, timelines), read-only, by `bookkeeping.py fetch`, for repo-E (private) and H, the public [Marius-Juston/agent-harness](https://github.com/Marius-Juston/agent-harness) repository. Workflow runs came from `gh run list --workflow board.yml` on both repositories.
- devkit source in this worktree: `devkit/board.py` (`sync`, `sweep`, `merged_pr`, `event`) and `devkit/workflow.py` (`integrate` outcomes, `pr`).
