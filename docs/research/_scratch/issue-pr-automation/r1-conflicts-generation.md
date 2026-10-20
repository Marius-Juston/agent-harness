# r1: conflict prediction among parallel agents, a local merge queue, and what bookkeeping text can be generated

Round 1 sub-question (d) of #346, candidates 1, 5, 6, 7 and 9. Search coverage: `discovery 2026-10-20: OK (openalex,
s2 answered)` on every `dev lit search` (one query also fell back to crossref for 3 results). Date of all checks:
2026-10-20. Data: every PR and issue of Marius-Juston/agent-harness (107 merged PRs) and repo-E (190
merged into main), read with `gh api graphql`, and the PR heads fetched into scratch bare clones outside the
repository. repo-E is private: only counts and top-level directories appear here.

Scripts (rerun to refresh every VERIFIED number):
[conflicts_fetch.py](conflicts_fetch.py) (read the history), [conflicts_replay.py](conflicts_replay.py) (the
`git merge-tree` replay and the predictors; its docstring holds the exact definitions),
[conflicts_summary.py](conflicts_summary.py) (both repositories combined), [conflicts_generation.py](conflicts_generation.py)
(PR bodies and HANDOFF versions). Outputs: [conflicts-agent-harness.json](conflicts-agent-harness.json),
[conflicts-repo-E.json](conflicts-repo-E.json), [conflicts-summary.json](conflicts-summary.json),
[conflicts-generation.json](conflicts-generation.json).

## Findings

### F1. Conflicts between parallel PRs are rare, and every one was already in the heads when the later PR was handed back.
- Method (VERIFIED, conflicts_replay.py). An at-risk pair (A, B) is one where A landed on main while B was being
  built or reviewed, and B's hand-back head did not contain A. The hand-back head is the last commit at or before
  B's `dev pr`. B is first brought up to main as it was just before A landed, then merged with A's merge commit, with
  main's `.gitattributes` (`--attr-source`) and integrate's generated-file driver.
- Results (VERIFIED, conflicts-summary.json):
  - 581 at-risk pairs were judged, 382 of them with both PRs open together. 19 pairs conflict: **3.3 % of pairs**.
  - 19 of 297 merged PRs (**6.4 %**) had a hand-back head that conflicted with main at merge time: 6 of 107 in
    agent-harness, 13 of 190 in repo-E.
  - Integrate sent back 10 PRs ("conflicts with main" comments). All 10 are among those 19, and each has an
    identified conflicting pair.
  - A further 90 pairs were set aside because B already conflicted with an earlier PR.
- The generated-file driver already removes about a third of the conflicts (VERIFIED, `--no-driver` rerun): without
  it, 28 pairs conflict instead of 19. `docs/research/INDEX.md` alone accounts for 9 of repo-E's 20. This confirms
  that `git merge-tree` honours a custom merge driver and `--attr-source` (git 2.43.0).
- Prior art (SOURCED). Owhadi-Kareshk et al. find 8.12 % of 267,657 merge scenarios conflicting, and the number of
  files both branches change is "the most important feature by far"
  ([Predicting Merge Conflicts in Collaborative Software Development](https://arxiv.org/abs/1907.06274),
  Owhadi-Kareshk, Nadi, Rubin 2019, ESEM, §V-B and §VI). Brun et al. report that conflicts in nine open-source
  systems "persist, on average, 10 days"
  ([Proactive detection of collaboration conflicts](https://homes.cs.washington.edu/~mernst/pubs/vc-conflicts-fse2011.pdf),
  Brun, Holmes, Ernst, Notkin 2011, ESEC/FSE, §1).
- Here the time from A's merge to B's merge for a conflicting pair has a **median of 41 min** (range 2-477;
  VERIFIED). That is a different regime from Brun et al.'s 10 days.

### F2. Issue Owns overlap predicts conflicts poorly; changed-file overlap catches all of them but with many false alarms; `git merge-tree` at `dev pr` time is exact.
Pair level, both repositories (VERIFIED, conflicts-summary.json `predictors`):

| predictor (when it is known) | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| Owns overlap (`board.overlap`, at claim / `board wave`) | 7 | 17 | 12 | 0.29 | 0.37 |
| the same, pairs where both issues declare Owns | 7 | 17 | 7 | 0.29 | 0.50 |
| changed files intersect, union and generated paths left out (at `dev pr`) | 19 | 50 | 0 | 0.28 | 1.00 |
| `merge-tree` pre-check at the later of the two `dev pr` calls | 19 | 3 | 0 | 0.86 | 1.00 |

- PR level, where a PR counts as flagged if any of its pairs is (VERIFIED, `pr_level`):
  - Owns: precision 0.32, recall 0.37.
  - Files: precision 0.33, recall 1.00.
  - `merge-tree`: precision 1.00, recall 1.00.
  - The 3 pair-level false positives of `merge-tree` are PRs that conflicted with main because of a different PR
    landed earlier. The flag was right; only the pair attribution was wrong.
- Caveat (VERIFIED by construction). The `merge-tree` predictor uses the same merge machinery as the ground truth, on
  the heads as they stood at `dev pr` time. Its 1.00 therefore says that no conflict was introduced after hand-back.
  It is not an independent model of conflicts. It is blind to semantic conflicts, which the merged-tree gate in
  integrate catches.
- Why Owns misses (VERIFIED, conflicted paths):
  - In repo-E, 5 of the 13 conflicting pairs conflict only in `pyproject.toml` and/or `uv.lock`, both from dependency
    additions. No issue owns those files. A `uv.lock`-only regeneration rule would remove just 1 of the 5.
  - The other conflicts sit in shared hub files: `tests/test_plugin.py` (2), `CHANGELOG.md`, `_typos.toml`, and app
    and library modules.
  - In 5 of the 12 misses, one issue declares no Owns. `board wave` treats a missing Owns as overlapping nothing
    (VERIFIED, `wave` docstring in `devkit/board.py`).
- So the `board wave` guarantee ("Owns overlap neither each other nor work in progress") prevented few conflicts.
  Where Owns did overlap, 17 of 24 pairs did not conflict. This matches the workspace-awareness literature: tools act
  on concrete changed files, not declared intent. Palantír informs developers "of potential conflicts arising because
  of concurrent changes to the same file" and led to "earlier detection and earlier resolution of a larger number of
  conflicts" in two laboratory experiments
  ([Palantir: Early Detection of Development Conflicts Arising from Parallel Code Changes](https://doi.org/10.1109/tse.2011.64),
  Sarma, Redmiles, van der Hoek 2011, IEEE TSE, abstract). Cassandra schedules tasks under file-level conflict
  constraints and "would have successfully avoided a majority of conflicts" in four open-source projects
  ([Cassandra: Proactive conflict minimization through optimized task scheduling](https://doi.org/10.1109/icse.2013.6606619),
  Kasi, Sarma 2013, ICSE, abstract). It needs a file-level forecast of each task, which Owns is not here (BELIEVED).

### F3. How early: a pre-check can fire no earlier than the `dev pr` call, and only a third of conflicts can be fixed before hand-back.
Data from VERIFIED, conflicts-summary.json.
- **7 of 19 conflicting pairs had A already on main at B's `dev pr`.** 4 of those 7 were later sent back. A
  `merge-tree` of the branch against `origin/main` inside `dev pr`, refusing with the conflicted paths, would have
  had the live worker merge main and re-gate before handing back. That removes up to 4 send-backs. Each avoided
  resume saves at most about $1.27 per builder (fix resume cost, SOURCED from
  [harness-efficiency.md](../../harness-efficiency.md) §4), so **about $5 for both repositories over 10 days**
  (VERIFIED arithmetic). The merge itself is moved, not removed.
- **12 pairs were both open.** Here the conflict appears only when A lands, a median of about 12 min after B's
  `dev pr` (1 to 227 min).
  - Run over every commit of both branches, a continuous check flags the conflict a median of about 2 min before
    B's `dev pr` (−28 to +51 min). That is the hand-back commit itself, so a check at claim or `board wave` time
    would have no code to see.
  - Detecting it earlier does not avoid the fix: whichever PR lands second must merge. Of these 12, 6 were sent back.
- **A post-merge sweep moves the send-back, not the work.** After each merge, integrate could run `merge-tree` of
  the new main against every open PR head and post the send-back at once.
  - Today the send-back follows A's merge by 0-10 min in agent-harness and by 23-454 min in repo-E (median of all
    10: 30.5 min; VERIFIED).
  - Cost: at most 9 PRs were open at once in either repository. One `merge-tree` takes 3.7 ms on repo-E's 7 MB pack
    (30 head pairs, VERIFIED). One fetch of `refs/pull/*/head` and fewer than 50 merges is well under a second plus
    the fetch (BELIEVED for the fetch time).
- Verdict on candidate 5 (BELIEVED, from the numbers above):
  - Build the `dev pr` check against main: cheap, exact, removes a resume.
  - Make the post-merge sweep a few lines inside integrate.
  - Do not build a pairwise pre-check in `board wave`. At claim time the only signal is Owns (recall 0.37), and at
    `dev pr` time pairwise flags change no decision except "expect a send-back".
  - This sharpens r1-against's threshold of "5 per window that Owns overlap did not predict": 12 conflicting pairs
    and 7 of the 10 send-backs were not predicted by Owns, but most of them cannot be avoided by any pre-check.

### F4. `dev integrate --ready`: a spec from the existing code (candidate 1)
What integrate's outcomes depend on (VERIFIED, reading `_integrate`, `unfinished`, `refusal`, `merge_main`,
`_merge_if_unchanged` in `devkit/workflow.py`):

| outcome | it is a function of | exact cache key |
|---|---|---|
| `refused`, unfinished feedback | head SHA and `unfinished(info, feedback)`: latest checks, standing CHANGES_REQUESTED, unresolved threads | (head, sha256 of the sorted `unfinished` list) |
| `refused`, session link in a public repo | PR title and body | (head, sha256 of title+body) |
| `refused`, deletes Owns | main and the merged tree, and open issues' Owns | (head, main, hash of open Owns); or not cached |
| `conflict` | head and main (`merge_main` is deterministic given main's attributes) | (head, main) |
| `gate-failed` | head and main (plus flaky stages) | (head, main), with `--retry-failed` |
| `harness-review` | head (the lead's read is the missing input) | (head) until the lead passes `--harness-ok` for that PR |
| `waiting` | the head integrate pushed | already resumed by `_waiting_file`; not cached |
| `stopped`, `not-open` | transient (head or main moved, GitHub lag, crash) | never cached |

- Store: `state_dir("integrate")/queue.json`, machine-local like the existing `_waiting_file`. Re-checking it costs
  the same 2 reads (`gh pr view` plus the feedback GraphQL) that integrate makes first anyway (VERIFIED).
- Order:
  - Candidates are open, non-draft PRs into main from this repository (`refusal`) whose head and feedback key
    differs from the cached refusal.
  - Sort by the linked issue's effective priority (`board.ranks`, as `board next`), then by PR number (FIFO, as
    hosted queues).
  - BELIEVED: with one local gate at a time, the speculative parallel builds of Uber's SubmitQueue do not apply.
    Its later version prioritises "smaller changes blocked by conflicting larger time-consuming changes"
    ([CI at Scale: Lean, Green, and Fast](https://arxiv.org/abs/2501.03440), Juloori et al. 2025, ICSE-SEIP,
    abstract), which is an optional tie-break: smaller diff first among PRs that conflict pairwise.
- Stop conditions:
  - `--max N` merges.
  - The quota reading at or above the cap (`quota_reading`).
  - Two consecutive `gate-failed` results on different PRs: main itself is probably red (BELIEVED).
  - A `stopped` result whose reason is not "head moved" or "main moved"; those two retry once at the end.
  - Nothing left whose key changed.
- Exit: one `RESULT-JSON` line per PR, then one `QUEUE-JSON` summary (merged, skipped-unchanged, needs-lead with
  reason).
- Never passes `--override` or `--harness-ok` (the lead skill reserves both).
- Two machines are already safe: `_merge_if_unchanged` pins the head with `--match-head-commit` and re-reads main
  just before merging (VERIFIED).
- The judgement objection (r1-against: a merging loop skips the lead's read) is met if the queue drains only PRs the
  lead has marked after reading. Two ways: a label set by `dev integrate --enqueue <pr>`, or an explicit list
  `dev integrate --queue 12 15 17`. The cache alone (skip unchanged) is the read-only half and is useful without the
  loop (BELIEVED).
- Not measured here: how many of the 134 integrate retries (command-sequences.md §2) were on an unchanged key. Only
  transcripts can tell, which is sub-question (a)'s data.

### F5. HANDOFF: about a fifth of its lines are data devkit already has, and the hand-written count was off by a third (candidate 7)
- Where HANDOFF exists:
  - agent-harness has no `docs/HANDOFF.md` on main, only `template/docs/HANDOFF.md` (VERIFIED).
  - repo-E has 23 committed versions, from 2026-10-11 to 2026-10-16; none since, although harness PRs merged up to
    2026-10-20 (VERIFIED, `git log -- docs/HANDOFF.md`).
- Composition: lines by section kind over all 23 versions (VERIFIED, conflicts-generation.json):

  | section kind | share of lines | generated from |
  |---|---|---|
  | state (harness version, binding ADRs, quota) | 32 % | mostly copies of facts living elsewhere (`.copier-answers`, ADRs, `dev status`) |
  | next | 18 % | half data: `board next` ranks and `board wave` filters, but the lead reorders |
  | preamble (who, when, why stopped) | 14 % | judgement |
  | owner decisions and waiting | 15 % | judgement, pointing to OPEN_QUESTIONS |
  | in flight | 14 % | data |
  | merged | 8 % | data |

  Only in flight and merged, about **21 %**, is pure data.
- What devkit already produces live (VERIFIED, `orient` in `devkit/workflow.py`):
  - "in flight": board rows in progress, review or blocked, each with `@agent/host` (`board._line`), plus this
    machine's worktrees (`status`).
  - "next": the 5 startable items.
  - It does not produce "just merged". That list is `git log --first-parent` on main since the last HANDOFF commit,
    whose subjects carry `(#issue) (#pr)`, plus `board.merged_pr`'s closes.
- Error a generator removes:
  - The latest repo-E HANDOFF says "Merged 2026-10-15/16 (about 70 PRs)". **104 PRs** merged between 2026-10-15
    00:00 local time and that HANDOFF commit (VERIFIED, conflicts-fetch data; 103 first-parent commits between the
    two HANDOFF commits).
  - It is also 44 lines against the lead skill's "under 40 lines" (VERIFIED).
  - An older version lists "#210 merged" under "In flight" (VERIFIED, `git show 4d4ddf4:docs/HANDOFF.md`).
- Recommendation (BELIEVED), agreeing with r1-against's "an orient section, not a file":
  - Add a "Merged since the last HANDOFF commit" block to `orient`: a count plus PR numbers grouped by area label.
  - Have the lead's HANDOFF keep only judgement: why stopped, next with reasons, decisions pending.
  - This shortens the file by the ~21 % that is data and removes the miscount class. Coordinate with #80, which
    gives HANDOFF per-host sections.

### F6. PR body: the deterministic parts are a seventh of the text, and most already exist (candidate 6)
- Merged PR bodies, line classes by regular expression (VERIFIED, conflicts-generation.json; a heuristic):
  - Median body length is 2,420 characters (agent-harness) and 2,520 (repo-E).
  - Worker prose is **81 % / 86 % of characters**.
  - Gate and test-count lines are 14 % / 11 %, present in 97 of 107 and 128 of 191 bodies.
  - Checkbox (DONE WHEN) lines are 3.6 % / 1.1 %.
  - "Filed #N" lines are 0.8 % / 1.3 % (12 and 46 bodies).
  - File lists are 0.0 %: workers do not paste diffstats.
- Generated from data devkit has (VERIFIED, reading `pr`, `_summary_body`, `pr_areas`, `reuse_needs`):

  | part | status |
  |---|---|
  | `Closes`/`Refs` line, title, area labels | exist |
  | the `Reuse:` requirement | checked |
  | DONE WHEN checklist | #232 |
  | conventional title | #100 |
  | "follow-ups filed" | derivable: open issues whose Refs name #n, created after the claim |
  | gate summary | needs data the gate does not keep |

  The gate record (`state_dir("gate")/<tree key>.json`) holds only `when`, `seconds` and `root` (VERIFIED, a record
  from 2026-10-20). A gate line would need the gate to store its stage results first.
- `dev pr` already refuses an ungated tip (`_gate_passed`), so "the gate passed" is true of every PR by construction.
  A generated line could replace the 11-14 % of hand-typed gate text with one exact line. The lead must still not
  read it as evidence, since integrate re-gates. r1-against's automation-bias objection applies (BELIEVED).
- The diffstat would add text nobody writes today. The lead's step 1 already reads `gh pr diff --name-only`.

### F7. `board claim --worktree` is fully deterministic (candidate 9)
- Every input exists (VERIFIED):
  - `board brief` already prints the branch `<n>-<slug>`, the same slug rule a `--worktree` flag would use.
  - The research skill names the path `../<repo>-worktrees/<n>-<slug>`.
  - `claim` already resolves claim races (post, wait 2 s, the earliest live claim wins).
- Correct order:
  1. `git fetch origin main`.
  2. Claim. A lost race leaves no worktree behind.
  3. `git worktree add <path> -b <branch> origin/main`.
  4. If that fails (the path or branch exists), release the claim with the error as the reason.
  5. Print the path.
- Saving: about 10 lead turns, $0.85 per window (command-sequences.md §3.4). Its value is the error path, not
  tokens.
- One mismatch to settle in the spec: brief's slug is the title truncated to 40 characters (this issue's is
  `346-research-issue-and-pr-bookkeeping-the-le`), while the lead chose `346-issue-pr-automation`. `--branch` must stay
  overridable.

## Gaps not closed
- The 134 integrate retries are not split by unchanged versus changed (head, feedback) key. That needs transcripts:
  sub-question (a).
- Semantic conflicts (merged tree fails the gate without a textual conflict) are not replayed. Integrate's
  `gate-failed` outcomes (none in the 365 calls of command-sequences.md §2 named as such) would be the data.
- Owns are read from issue bodies as they are now. Edits after the PR merged could change the Owns predictor
  slightly.
- Commit dates stand in for push times, and a force-pushed commit is invisible (no force pushes are expected: auto
  mode refuses them).
- Brun et al.'s and Owhadi-Kareshk et al.'s rates come from human open-source projects. They give the order of
  magnitude only.

## Sources fetched
- [Predicting Merge Conflicts in Collaborative Software Development](https://arxiv.org/abs/1907.06274) (Owhadi-Kareshk,
  Nadi, Rubin 2019, ESEM): abstract via `dev lit get`, §V-B and §VI via the arxiv MCP.
- [Proactive detection of collaboration conflicts](https://homes.cs.washington.edu/~mernst/pubs/vc-conflicts-fse2011.pdf)
  (Brun, Holmes, Ernst, Notkin 2011, ESEC/FSE): page 1 (abstract, contributions).
- [Palantir: Early Detection of Development Conflicts Arising from Parallel Code Changes](https://doi.org/10.1109/tse.2011.64)
  (Sarma, Redmiles, van der Hoek 2011, IEEE TSE): abstract.
- [Cassandra: Proactive conflict minimization through optimized task scheduling](https://doi.org/10.1109/icse.2013.6606619)
  (Kasi, Sarma 2013, ICSE): abstract.
- [CI at Scale: Lean, Green, and Fast](https://arxiv.org/abs/2501.03440) (Juloori et al. 2025, ICSE-SEIP): abstract.
- [Keeping Master Green at Scale](https://doi.org/10.1145/3302424.3303970) (Ananthanarayanan et al. 2019, EuroSys):
  abstract only; it frames the always-green requirement that integrate's re-gate serves.
- [Improving early detection of software merge conflicts](https://doi.org/10.1109/ICSE.2012.6227180) (Guimarães, Silva
  2012, ICSE): abstract. It continuously merges committed and uncommitted changes in the background. That is the
  "earliest" predictor of F3, which here gains a median of only 2 min.
- [Understanding predictive factors for merge conflicts](https://doi.org/10.1016/j.infsof.2020.106256) (Dias, Borba,
  Barreto 2020, IST): abstract, 73,504 merge scenarios. Bigger contributions and longer development time go with more
  conflicts. This is consistent with the conflicting PRs here taking longer from open to merge: median 1.43 h against
  0.25 h in repo-E and 0.40 h against 0.10 h in agent-harness (VERIFIED, `b_open_to_merged_hours_median`;
  correlational).
- Code: `devkit/workflow.py` (`_integrate`, `unfinished`, `refusal`, `merge_main`, `send_back`, `retarget_stacked`,
  `_merge_if_unchanged`, `pr`, `orient`, `status`), `devkit/board.py` (`wave`, `overlap`, `owns_of`, `claim`,
  `in_flight_files`, `brief`, `_line`), `devkit/paths.py`, `.gitattributes` of both repositories.
