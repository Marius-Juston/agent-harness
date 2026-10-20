# r1-against: the case against more issue and PR automation (#346, round 1, sub-question e)

Search coverage: worst `discovery` line printed was `discovery 2026-10-20: OK, partial (s2 down; openalex answered)`
(the tool-selection query); every other `uv run dev lit search` printed `OK (openalex, s2 answered)`. Abstracts were
fetched with `uv run dev lit get`; vendor and platform docs with WebFetch (which summarises with a small model, so
quotes from it are SOURCED but were not read line by line).

Scripts (run from the repository root; they read git and do arithmetic, nothing else):
[surface_growth.py](scripts/surface_growth.py) (devkit size, commands, fix share per module, per tag) and
[against_thresholds.py](scripts/against_thresholds.py) (break-even, saving ceilings, base rates).

Labels: VERIFIED = measured here by the scripts above or a `gh` query named inline; SOURCED = cited and fetched;
BELIEVED = judgement; ASSUMED = unchecked premise.

## 0. Verdicts in one table

The break-even used below (§1.3): a candidate costs about **$19** to build including its expected bug fixes, so it
pays back within 30 days of harness use only if it saves **at least $6.4 per 10-day window** (0.29 % of the
window's spend), or removes a measured recurring error (refusal, wrong action, missed item) at least 5 times per
window. Stopgaps that Actions will replace (~Nov 2026) must pay back in 2 windows: **$9.6 per window**.

| # | candidate | work it removes | measured ceiling per 10-day window | verdict from the against seat |
|---|---|---|---|---|
| 1 | `dev integrate --ready` local queue | bookkeeping (which PR changed since its refusal) **and** judgement (read the PR, check numbers, `--harness-ok`) | ≤ $10.3 (all 134 retries removed; an upper bound) | Build only the read-only half: a per-head-SHA refusal cache shown by `board feedback`/orient. **Reject** a loop that merges, it skips step 7's read. |
| 2 | `dev review post` inline threads | bookkeeping (relay text) | ≤ $2.3 (review-relay n-gram, n = 10) | **Below threshold.** Inline threads also widen integrate's blocking surface and anyone on the owner's account can resolve them. Fold into #83 / #110. |
| 3 | local scheduler for `board event` / `usage-poll` | bookkeeping | not measured; multi-agent.md says the board needs no schedule | **Reject** as a stopgap: WSL idles out, per-machine timers duplicate sweeps, Actions return within weeks. |
| 4 | duplicate check in `board new` | judgement (is this the same work?) | 2 duplicates in 235 issues (0.85 %) | **Advisory only, never refuses.** At this base rate a 90 %-recall detector with 2 % false alarms is right 28 % of the time. Overlaps #238. |
| 5 | `git merge-tree` pre-check across open PR heads | bookkeeping | ≤ $10.2 (8 conflict outcomes in 365 integrates, each fix resume avoided) | **Below threshold in practice**: `board wave` already blocks Owns overlap, and a pre-check moves detection earlier without removing the fix. |
| 6 | generated PR body | bookkeeping (layout) plus a judgement hazard (a generated "gate passed" read as evidence) | ≤ $6.0 (all body-edit loops, all roles) | **Below threshold**; overlaps #232 and #100. Generate only the checklist, never a verdict. |
| 7 | generated HANDOFF skeleton | bookkeeping (snapshot) | 0 HANDOFF commits in this repository; not measured in repo-E | **Reject the snapshot**: it copies facts orient already shows live, against HARNESS.md's "link to a fact where it lives". Coordinate with #80, #233. |
| 8 | local changelog with git-cliff | bookkeeping, but the curated text is judgement | 14 CHANGELOG commits over 13 tags | **Reject as a stopgap** before #100; #60 (release-please) replaces it once Actions return. |
| 9 | `board claim --worktree` | bookkeeping | $0.85 (command-sequences.md §3.4) | **Reject**: already rejected on the same numbers; 88 % of dispatches are one Bash call. |

## 1. Cross-cutting evidence against adding surface

### 1.1 The harness's own code is growing fast, and bookkeeping code is a quarter of it
- **Growth.** From v0.1.0 (2026-10-11) to v0.1.12 (2026-10-20), devkit grew from 3,722 to 11,064 lines (x2.97), its
  tests from 2,807 to 13,705 (x4.88), and all tracked lines (excluding round scratch files and `uv.lock`) from 9,171 to
  34,854 (VERIFIED, [surface_growth.py](scripts/surface_growth.py)). The brief's "17k → 43k LOC" figure is not in
  [architecture-consolidation.md](../../architecture-consolidation.md) as I read it; the numbers here are the measured
  ones for agent-harness.
- **Bookkeeping share.** `workflow.py` (pr, integrate) plus `board.py` are 2,786 of 11,064 devkit lines (25 %), the two
  largest modules, and 7 of the 9 candidates land in them (VERIFIED, same script; landing sites BELIEVED from reading
  the candidates).
- **Command surface.** 17 top-level `dev` commands at v0.1.0, 22 at v0.1.12; `dev board` alone has 16 actions now
  (VERIFIED, same script and `uv run dev board --help`).
- **Test burden per line grows.** Tests per devkit line went from 0.75 to 1.24 (VERIFIED). Each new command arrives
  with tests the gate must run on every check.
- **Fix churn is highest in the bookkeeping modules.** Commits whose subject says fix/bug/regress/wrong: `board.py`
  31 % (4 of 13), `workflow.py` 22 % (6 of 27), against `lit.py` 16 % and `gate.py` 10 % (VERIFIED, subject-keyword
  heuristic, so a rough proxy). Issues with "integrate" in the title: 22, of which 4 are `type:bug` (#334, #251, #305,
  #340) and several more add guards after a failure (VERIFIED, `gh issue list --search "integrate in:title"`).
  Architecture-consolidation names the mechanism: the measured smells in agent-built repositories are dormant
  subtrees and large churny files ([architecture-consolidation.md](../../architecture-consolidation.md) §4, §6), and
  every new bookkeeping path makes `workflow.py` larger and churnier.

### 1.2 Savings from bundling commands are already small here
- [command-sequences.md](../../command-sequences.md) §0 and §4: agents already chain mechanical steps in one Bash call
  (84-88 % of the lead's mechanical sequences), so a new subcommand removes command text and error paths, not turns;
  the three changes worth filing together save about $9 per 10-day window, 0.4 % of $2,252 (SOURCED, internal note).
- [harness-efficiency.md](../../harness-efficiency.md) §2: the lead is 16 % of spend and its cost is context size
  (mean 372k per call), not command count; polling all agents cost $18 in a week (SOURCED, internal note). So the
  lead's bookkeeping turns are a small slice of a slice.
- The counter-weight to that: meta-tools cut LLM calls up to 11.9 % on benchmarks where each tool call is a separate
  model step (SOURCED, Abuzakuk et al. 2026, [Optimizing Agentic Workflows using Meta-tools](https://arxiv.org/abs/2601.22037), arXiv, abstract,
  as cited in command-sequences.md §4). That setting is not ours (BELIEVED).

### 1.3 The break-even arithmetic (VERIFIED, [against_thresholds.py](scripts/against_thresholds.py))
- A merged PR costs about $15 all-in (the $14.7-15.6 per merged PR of [harness-efficiency.md](../../harness-efficiency.md)
  §2, a window average across roles, ASSUMED to apply to a small devkit feature).
- `workflow.py` had 0.29 fix commits per non-fix commit, so a feature there costs about $15 x 1.29 = **$19.3** with
  its expected fixes. That leaves out review rounds beyond the average, the per-check test time, and help lookups
  (each `board new --help` costs about $0.034, 154 in a window, command-sequences.md §3.2).
- Payback within 1 / 2 / 3 / 6 windows needs $19.3 / $9.6 / $6.4 / $3.2 per window (0.86 / 0.43 / 0.29 / 0.14 % of
  spend). I use 3 windows (30 days) as the default horizon because the harness changes fast (13 tags in 10 days,
  code x3), so a feature that pays back slower is likely to be rewritten first (BELIEVED), and 2 windows for
  stopgaps that Actions replace (~Nov 2026, per the brief; ASSUMED date).
- Money is not the owner's only goal ("less pressure on the LLM"). The non-money threshold, in the same spirit as
  #322 (filed for 15 refusals and a prescribed manual step, not for its $3, command-sequences.md §3.1): a candidate that removes **at least 5 recurring errors per
  window** (refusals, wrong merges, missed items) is worth building even under $6.4 (BELIEVED).

### 1.4 Tool surface and selection accuracy: real, but the evidence is about schemas, not CLI verbs
- With many tools in the prompt, tool selection degrades: retrieving a subset raised tool-selection accuracy from
  13.62 % to 43.13 % and cut prompt tokens by over 50 % in an MCP stress test (SOURCED, Gan & Sun 2025,
  [RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation](https://arxiv.org/abs/2505.03275), arXiv, abstract);
  reducing the tools offered improved function-calling success and cut execution time up to 70 % (SOURCED,
  Paramanayakam et al. 2024, [Less is More: Optimizing Function Calling for LLM Execution on Edge Devices](https://arxiv.org/abs/2411.15399), arXiv,
  abstract; small edge models). Both are older or smaller models than Opus 5.5 and measure tool schemas in the
  prompt.
- `dev` subcommands are not schemas: they cost nothing until an agent runs `--help`, so the direct analogue is the
  help-lookup count (256 help-then-use pairs, command-sequences.md §2), not selection accuracy (BELIEVED). The real
  surface cost here is the lead skill and HARNESS.md lines that tell the lead when to use each flag, and those are
  where conflicting rules hurt ([harness-efficiency.md](../../harness-efficiency.md) §6) (BELIEVED).

### 1.5 Bot noise and false alarms
- In interviews with 21 practitioners, **noise** was "a recurrent and central problem" of bots on pull requests,
  overwhelming and distracting developers (SOURCED, Wessel et al. 2021,
  [Don't Disturb Me: Challenges of Interacting with Software Bots on Open Source Software Projects](https://doi.org/10.1145/3476042), PACM HCI (CSCW), abstract).
  Their follow-up with 32 practitioners asked for a separate place for bot output and a mediator that summarises it
  (SOURCED, Wessel et al. 2022, [Bots for pull requests](https://doi.org/10.1145/3510003.3512765), ICSE, abstract).
- Contrary evidence: adopting a code-review bot **increased** monthly merged PRs and decreased non-merged ones over
  1,194 projects (regression discontinuity), with less developer communication (SOURCED, Wessel et al. 2022,
  [Quality gatekeepers: investigating the effects of code review bots on pull request activities](https://doi.org/10.1007/s10664-022-10130-9), EMSE, abstract).
  So bots on PRs are not net-negative; noise is the cost to manage.
- Automated PRs that notify too much get ignored: only a third of dependency-update PRs were merged over 7,470
  projects (SOURCED, Mirhosseini & Parnin 2017,
  [Can automated pull requests encourage software developers to upgrade out-of-date dependencies?](https://doi.org/10.1109/ase.2017.8115621), ASE, abstract);
  Dependabot users configure it to reduce notifications and 11.3 % of projects dropped it (SOURCED, He et al. 2023,
  [Automating Dependency Updates in Practice: An Exploratory Study on GitHub Dependabot](https://arxiv.org/abs/2206.07230), TSE, abstract).
- The mechanism from human-factors research: "Disuse … is commonly caused by alarms that activate falsely", because
  "the base rate of the condition to be detected is not considered" (SOURCED, Parasuraman & Riley 1997,
  [Humans and Automation: Use, Misuse, Disuse, Abuse](https://doi.org/10.1518/001872097778543886), Human Factors, abstract). And the opposite failure:
  automation bias yields omission and commission errors when aids are imperfect, in experts too, and "cannot be
  prevented by training or instructions" (SOURCED, Parasuraman & Manzey 2010,
  [Complacency and Bias in Human Use of Automation: An Attentional Integration](https://doi.org/10.1177/0018720810376055), Human Factors, abstract). These are
  human studies; that an LLM lead shows the same two failures is BELIEVED, not measured.

## 2. Per candidate

### (1) `dev integrate --ready`: local merge queue, refusals cached per head SHA
- **Surface and maintenance.** Lands in `workflow.py` (1,363 lines, 22 % fix commits, 22 integrate issues). New
  failure modes: a queue that runs the merged-tree gate per PR holds the machine gate lock for N gates in a row
  (BELIEVED); a refusal cache keyed on head SHA goes stale when feedback changes without a push (a new review, a
  resolved thread, a check re-run), so the key must include the feedback state, which is the hard half (BELIEVED);
  two machines each running a queue race on the same PR (BELIEVED; #79 covers claim leases, not merges). Merge-queue
  vendors list the same classes for hosted queues: flaky tests, CI infrastructure and rate limits, misconfigured
  required checks, and conflicts from a PR merged first, and a failed PR restarts the PRs behind it (SOURCED, Trunk,
  [Handle failed pull requests](https://docs.trunk.io/merge-queue/using-the-queue/handle-failed-pull-requests), vendor docs, WebFetch).
- **Judgement or bookkeeping.** Both. "Has anything changed since the last refusal" is bookkeeping. "Read the PR and
  check its numbers against the data" ([multi-agent.md](../../../specs/multi-agent.md) §2 step 7; the lead skill: "A
  worker's report is not evidence") and `--harness-ok` "only after reading the harness diff" are judgement.
- **Rule conflicts.** A `--ready` loop that merges every green PR skips step 7's read and would have to either pass
  `--harness-ok` blindly (a weakened check) or stop at every harness PR. It must never pass `--override`, which the
  lead skill reserves for "the owner's word". "Only `dev integrate` merges" holds if the loop calls integrate.
- **Threshold.** The ceiling is 134 retries x $0.077 = $10.3 per window if every retry were waste (VERIFIED
  arithmetic); some retries follow a real fix and are necessary, so the true saving is lower and unmeasured. Build
  the cache only if transcripts show **≥ 84 same-head, same-feedback retries per window** ($6.4 / $0.077), or ≥ 5 wrong or
  wasted gate runs. Never build the merging loop.

### (2) `dev review post`: findings as one GitHub review with inline threads
- **Surface.** A new command plus GraphQL thread handling in `board feedback`/`brief`. Inline comments anchor to lines
  that a fix push moves; GitHub marks such conversations outdated (SOURCED, GitHub Docs,
  [Commenting on a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/commenting-on-a-pull-request), WebFetch).
  The lead skill already says outdated threads block integrate, so every inline finding becomes a thread someone must
  resolve.
- **Judgement or bookkeeping.** The relay text is bookkeeping; the disposition of each finding (fix, decline,
  follow-up) is judgement and is already filed as #83.
- **Rule conflicts.** Integrate refuses unresolved threads ([multi-agent.md](../../../specs/multi-agent.md) §2 step 7).
  Threads can be resolved by "the person who opened the pull request" or anyone "with write access" (SOURCED, same
  GitHub page). Every agent acts as the owner's account, so a worker can resolve its reviewer's thread: the check
  then certifies itself, a weakened check (BELIEVED). Posting Nits as threads also turns non-blocking findings into
  merge blockers, against the lead skill's "Only Blocking or untagged findings return the PR". Noise: one thread per
  finding is the bot pattern practitioners call noise (§1.5).
- **Threshold.** The relay n-gram is n = 10 in the lead (command-sequences.md §2), ≤ $2.3 per window. Build only if
  relays reach **≥ 28 per window** ($6.4), and then only Blocking findings as threads, resolved by the reviewer or
  lead, never by the worker.

### (3) Local scheduler (systemd timer or cron) for `board event` and `usage-poll`
- **Surface.** Installer and doctor checks per OS (systemd user units, cron, macOS launchd), log rotation, an orient
  warning when the timer is dead: a new subsystem with no test that runs on a real clock (BELIEVED).
- **New failure modes.** On WSL 2 the VM shuts down after `vmIdleTimeout` (default 60,000 ms) and a distro after
  `instanceIdleTimeout` (default 15,000 ms) of idleness (SOURCED, Microsoft Learn,
  [Advanced settings configuration in WSL](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)), so a timer runs only while the owner has a session open, when
  orient already syncs. On this machine `systemctl is-system-running` reports `degraded` (VERIFIED, 2026-10-20), so a
  failing unit would be one more silent failure. Each machine with a timer sweeps every claim; a sweep on an idle
  machine labels another machine's live claim STALE between heartbeats (BELIEVED). An unattended process needs the
  owner's `gh` token with write scope outside any session, which ADR-level security review should see first
  (BELIEVED).
- **Judgement or bookkeeping.** Bookkeeping, but already done: "The board needs no scheduled job either: `dev orient`
  and `dev integrate` run `board sync`, so the template's `board.yml` is optional" ([multi-agent.md](../../../specs/multi-agent.md) §2 step 8).
  `usage-poll` already runs as a detached loop by design (`uv run dev --help`).
- **Threshold.** Build only if orient shows **≥ 5 stale-board events per window** that `board sync` at orient or
  integrate did not catch in time (count them first), and only if Actions are still blocked when it would ship. A
  stopgap with a 2-window horizon needs $9.6 per window, and no saving is measured.

### (4) Related or duplicate issue check in `board new`
- **Surface.** A TF-IDF index over open and recently closed issues: one more network read per `board new`, a cache,
  a similarity cut-off to tune and re-tune as the board grows (BELIEVED).
- **Judgement or bookkeeping.** Judgement: "is this the same work" needs the DONE WHEN, not word overlap. The two
  closed duplicates here, #10 (of #104) and #37 (of #240), were caught by the lead reading the board (VERIFIED, closing
  comments).
- **Base rate.** 2 of 235 issues closed as "Duplicate of" (0.85 %; VERIFIED, `gh issue list --state all` and the
  closing comments). With recall 90 % and a 2 % false-alarm rate per filing, 28 % of alarms are right; at 80 % and 5 %,
  12 % (VERIFIED, [against_thresholds.py](scripts/against_thresholds.py)). That is the false-alarm regime where
  operators stop heeding the alarm (§1.5, Parasuraman & Riley). Duplicate-detection research reports ranking metrics
  (recall@k, P@1) on curated duplicate sets, not alarm precision at a real base rate (SOURCED, Wang et al. 2019,
  [Duplicate Pull Request Detection](https://doi.org/10.1145/3361242.3361254), Internetware, abstract: +14.36 % and
  +11.93 % recall@k over Ren et al.; Liao et al. 2024, [DupLLM:Duplicate Pull Requests Detection Based on Large Language Model](https://doi.org/10.1109/apsec65559.2024.00023),
  APSEC, abstract: P@1 0.929). An industrial case study notes that such techniques "have not seen widespread
  industrial adoption" and studies human-in-the-loop use (SOURCED, Götharsson et al. 2024,
  [Exploring the Role of Automation in Duplicate Bug Report Detection: An Industrial Case Study](https://doi.org/10.1145/3644032.3644450), AST, abstract).
- **Rule conflicts.** None if advisory. A refusing check would block filing on a similarity score, a gate with a 72-88 %
  false-alarm share. #238 already adds an advisory keyword search in `board new` (against decisions.toml); one
  advisory block, not two.
- **Threshold.** Print related issues only, never refuse, and only if the dedup round shows **precision ≥ 50 % at the
  shown top-k on a held-out split** with the real base rate; otherwise, at under 3 duplicates per 235 issues, the lead's
  read of `board list` is enough.

### (5) `git merge-tree` conflict pre-check across open PR heads
- **Surface.** Pairwise merges grow with the square of open PRs; the output is a new warning in `board wave` and
  `dev pr` that must say what to do (BELIEVED).
- **Judgement or bookkeeping.** Bookkeeping, and cheap with `git merge-tree` (no worktree). But the decision that
  avoids conflicts already exists: `board wave` returns items "whose Owns overlap neither each other nor work in
  progress", and the lead skill dispatches parallel builders "only when `board brief` shows no Owns overlap".
- **Base rate.** 8 conflict outcomes in 365 integrate calls (2.2 %; command-sequences.md §2). Even avoiding every
  conflict's fix resume ($1.27 each, harness-efficiency.md §4) is ≤ $10.2 per window, and a pre-check does not avoid
  the fix, it only moves it earlier (VERIFIED arithmetic; the "earlier" claim BELIEVED). ML conflict predictors reach
  F1 0.95-0.97 for safe merges but only 0.57-0.68 for conflicting ones over 267,657 merge scenarios (SOURCED,
  Owhadi-Kareshk et al. 2019, [Predicting Merge Conflicts in Collaborative Software Development](https://arxiv.org/abs/1907.06274), ESEM, abstract);
  `merge-tree` is exact for textual conflicts but blind to semantic ones, which the merged-tree gate already catches.
- **Threshold.** Build only if conflict outcomes exceed **5 per window that Owns overlap did not predict** (count
  the 8 against their Owns first).

### (6) Generated PR body (DONE WHEN, diffstat, gate summary)
- **Surface.** `dev pr` already builds the body: it prefixes `Closes`/`Refs`, refuses `--partial` with `Closes`, and
  checks the `Reuse:` line (VERIFIED, `pr` and `_summary_body` in `devkit/workflow.py`). #232 adds the DONE WHEN
  checklist and #100 the title, both filed.
- **Judgement or bookkeeping.** Layout is bookkeeping; the summary of what changed and what is unverified is the
  worker's judgement. A generated "gate passed" line is a hazard: the lead must not read it as evidence (the lead
  skill: "A worker's report is not evidence"), and automation bias predicts it will be (§1.5; BELIEVED for an LLM).
  The diffstat is one `gh pr diff --name-only` away and is already step 1 of the lead's loop.
- **Threshold.** All body-edit loops cost about $6.0 per window (command-sequences.md §3.4), under $6.4, and #232
  removes most of the checklist edits. Build nothing beyond #232 and #100 unless edit loops pass **150 per window**
  (command-sequences.md §5's own re-measure trigger).

### (7) Generated HANDOFF skeleton
- **Surface.** A writer for a file that `dev orient` already summarises live (handoff, board, worktrees, gate), plus
  merge handling across machines, which #80 owns (per-host sections, union merge).
- **Judgement or bookkeeping.** The parts worth writing down are judgement: "next (ranked startable items, why)" and
  "owner decisions pending" (lead skill §6). The parts a script can produce (in flight, just merged) are a copy of
  board state.
- **Rule conflicts.** HARNESS.md: "Link to a fact where it lives; never copy counts, field lists or commands into
  prose." A generated snapshot of board and worktree state is that copy, and goes stale between runs; #233 already
  moves orient toward the live state of HANDOFF references.
- **Threshold.** No HANDOFF commits in this repository (VERIFIED, `git log -- docs/HANDOFF.md`), so no frequency to
  save here; measure lead HANDOFF edit turns in repo-E first. Build a generated "in flight / just merged" section only
  if those turns exceed $6.4 per window, and then as an orient section, not a file.

### (8) Local changelog with git-cliff (after #100)
- **Tool.** git-cliff is maintained and passes the reuse gates (Apache-2.0, 12,309 stars, pushed 2026-09-18; VERIFIED,
  `uv run dev reuse check git-cliff` and `gh api repos/orhun/git-cliff`).
- **Judgement or bookkeeping.** Which changes a consumer must act on at `copier update` is judgement. Keep a Changelog:
  "Using commit log diffs as changelogs is a bad idea: they're full of noise", and an entry should "document the
  noteworthy difference, often across multiple commits" (SOURCED, [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), WebFetch).
- **Rule conflicts and overlap.** #60 already plans release-please, which reads the same Conventional Commit titles
  (#100) but runs on Actions. A local git-cliff now plus release-please later is two generators for one file, a
  migration and a config to retire (BELIEVED).
- **Threshold.** 14 CHANGELOG commits over 13 tags (VERIFIED), about one per release. Build only if writing the entry
  costs **≥ $9.6 per window** (2-window stopgap horizon) measured in release sessions, and only if #60 is still
  blocked then.

### (9) `board claim --worktree`
- Already rejected with numbers: 77 of 88 worktree-plus-claim sequences are one Bash call; the flag would save about
  10 turns, $0.85 per window (SOURCED, internal, [command-sequences.md](../../command-sequences.md) §3.4). Same-repo
  workers get their worktree from `isolation: worktree`; for cross-repo dispatch the lead skill keeps an "empty
  isolation worktree" too, so a second worktree path from `claim` adds one more thing to clean up (BELIEVED).
- **Threshold.** Reconsider only if split dispatches (worktree and claim in separate calls) exceed **75 per window**
  ($6.4 at the lead's turn cost) or their failure count reaches 5.

## 3. What the against seat would build anyway
The cases where the counter-argument is weakest (BELIEVED):
- The read-only half of (1): a refusal cache per (head SHA, feedback state) shown in `board feedback` and orient, so
  the lead stops re-running integrate on unchanged PRs. It removes bookkeeping and leaves the read and the merge to
  the lead.
- #232 and #100 as filed, instead of (6) and (8).
- The duplicate check of (4) only as an advisory line next to #238's, if the dedup round's held-out precision clears
  50 %.

## 4. Gaps not closed
- How many of the 134 integrate retries were on an unchanged head and unchanged feedback (the transcripts round owns
  this; it decides (1)).
- Whether the 8 conflict outcomes had overlapping Owns (decides (5)).
- Lead HANDOFF and CHANGELOG edit cost in repo-E transcripts (decides (7) and (8)).
- Automation bias in LLM leads (as opposed to humans) is not measured anywhere I found; it is BELIEVED by analogy.
- No source found on merge-bot failures in self-hosted queues beyond vendor docs; the bors-ng and Kubernetes queues
  were only mentioned in search snippets and not fetched.

## Sources fetched this round
Internal: [harness-efficiency.md](../../harness-efficiency.md) (§2, §4, §6, §8),
[r2-against](../harness-efficiency/r2-against.md) (§0), [command-sequences.md](../../command-sequences.md),
[architecture-consolidation.md](../../architecture-consolidation.md) (§0, §4, §6),
[multi-agent.md](../../../specs/multi-agent.md) §2; code read: `devkit/workflow.py` (`pr`, `_summary_body`),
`devkit/board.py`, `plugins/harness-ops/skills/lead/SKILL.md`.

Papers (abstracts via `uv run dev lit get`, 2026-10-20):
- Wessel, Wiese, Steinmacher & Gerosa, [Don't Disturb Me: Challenges of Interacting with Software Bots on Open Source Software Projects](https://doi.org/10.1145/3476042), PACM HCI 2021.
- Wessel, Abdellatif, Wiese, Conte, Shihab, Gerosa & Steinmacher, [Bots for pull requests](https://doi.org/10.1145/3510003.3512765), ICSE 2022.
- Wessel, Serebrenik, Wiese, Steinmacher & Gerosa, [Quality gatekeepers: investigating the effects of code review bots on pull request activities](https://doi.org/10.1007/s10664-022-10130-9), EMSE 2022.
- Mirhosseini & Parnin, [Can automated pull requests encourage software developers to upgrade out-of-date dependencies?](https://doi.org/10.1109/ase.2017.8115621), ASE 2017.
- He, He, Zhang & Zhou, [Automating Dependency Updates in Practice: An Exploratory Study on GitHub Dependabot](https://arxiv.org/abs/2206.07230), TSE 2023.
- Parasuraman & Riley, [Humans and Automation: Use, Misuse, Disuse, Abuse](https://doi.org/10.1518/001872097778543886), Human Factors 1997.
- Parasuraman & Manzey, [Complacency and Bias in Human Use of Automation: An Attentional Integration](https://doi.org/10.1177/0018720810376055), Human Factors 2010.
- Gan & Sun, [RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation](https://arxiv.org/abs/2505.03275), arXiv 2025.
- Paramanayakam, Karatzas, Anagnostopoulos & Stamoulis, [Less is More: Optimizing Function Calling for LLM Execution on Edge Devices](https://arxiv.org/abs/2411.15399), arXiv 2024.
- Abuzakuk, Kermarrec, Sharma, Veski & de Vos, [Optimizing Agentic Workflows using Meta-tools](https://arxiv.org/abs/2601.22037), arXiv 2026 (via command-sequences.md).
- Owhadi-Kareshk, Nadi & Rubin, [Predicting Merge Conflicts in Collaborative Software Development](https://arxiv.org/abs/1907.06274), ESEM 2019.
- Wang, Xu, Xia, Wang & Li, [Duplicate Pull Request Detection](https://doi.org/10.1145/3361242.3361254), Internetware 2019.
- Liao, Liu, Lan & Sun, [DupLLM:Duplicate Pull Requests Detection Based on Large Language Model](https://doi.org/10.1109/apsec65559.2024.00023), APSEC 2024.
- Götharsson, Stahre, Gay & de Oliveira Neto, [Exploring the Role of Automation in Duplicate Bug Report Detection: An Industrial Case Study](https://doi.org/10.1145/3644032.3644450), AST 2024.

Docs (WebFetch, 2026-10-20): Trunk, [Handle failed pull requests](https://docs.trunk.io/merge-queue/using-the-queue/handle-failed-pull-requests);
GitHub Docs, [Commenting on a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/commenting-on-a-pull-request);
Microsoft Learn, [Advanced settings configuration in WSL](https://learn.microsoft.com/en-us/windows/wsl/wsl-config);
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
