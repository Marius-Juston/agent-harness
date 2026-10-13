# r1: measured deferral (#149, round 1)

Search coverage: no literature search in this round (local data only). `uv run dev lit status` on 2026-10-13:
openalex, crossref, europepmc and arxiv up; s2 down (rate limited).

Data: [scripts/measure_deferral.py](scripts/measure_deferral.py) (stdlib, read-only `gh`). It reads this machine's Claude
Code transcripts (327 main and 659 subagent files), the GitHub issues and PRs of H, repo-E and repo-R, and repo-L's
file board with its git history. An item counts as filed by an agent when a `board new`, `gh issue create` or
`gh api .../issues` POST call in a transcript created it. The item is matched by the URL or ticket id in the output,
or for `gh api` calls by title and time. The origin is the issue the filer was working on, taken from the worktree path,
branch, subagent description or prompt, or a "From #N" in the context. Sizes are added plus deleted lines of the
closing PR or ticket merge. "Net" leaves out generated docs, lock files, handoffs, logs, the file board and research
scratch. Outputs stay in the gitignored `.scratch/fix-now-vs-defer/` (items.csv, quotes.txt, small.txt, summary.txt).
Window: transcripts hold 2026-10-03 to 2026-10-13 only. The earliest filing found is 2026-10-03T03:16Z.

## Findings

**F1. Volume. VERIFIED (script).** 859 filing calls created 848 items with known outcomes. 640 of them were filed
during other work (origin known): repo-L 493, repo-E 75, H 46, repo-R 26. Subagents filed 600 of the 640: builders and
testers 399, researchers 172, the architect 8, other subagents 21. The lead filed 40 that name an origin. Filing
comes in bursts. In session `7058214b`, on 2026-10-13 on H, 25 items were filed during other work (31 in all):
researchers 10, builders 8, lead 7. That confirms the lead's "about 25" in [QUESTION.md](QUESTION.md). 21 of the 25
are still open.

**F2. Follow-ups close in the same session or not at all. VERIFIED.** 481 of 640 (75%) are still open. For those that
closed, the time from filing to the merged closing change was a median of 3.1 h (IQR 1.2 to 10.8 h, max 32 h). None
closed later than 1.4 days after filing. repo-L is the only repository whose follow-ups are at least 7 days old. There,
366 of 493 (74%) are still open after 8 to 10 days. Its sessions stopped on 2026-10-05, so "open" means work stopped,
not that the items failed review. H, repo-E and repo-R items are under 3.5 days old (right-censored).

**F3. Closed follow-ups are ordinary-sized changes, not small fixes. VERIFIED.** The closing diffs of the 143 that
closed with a merged change had a median of 1,072 net lines (IQR 551 to 1,665). Only 4 of 143 (3%) were 30 net lines
or fewer, and 10 of 143 (7%) were 100 or fewer. Per repository, against the baseline of every merged change in it:

| repo | closed follow-ups (net lines, median) | of those <= 30 | all merged changes (median) | of those <= 30 |
|---|---|---|---|---|
| repo-L | 1,145 (n=124) | 0% | 1,099 (n=159) | 6% |
| repo-R | 431 (n=7) | 0% | 156 (n=64) | 16% |
| H | 173 (n=6) | 1 of 6 | 267 (n=38) | 13% |
| repo-E | 68 (n=6) | 3 of 6 | 78 (n=53) | 40% |

67 closing changes closed several items at once (repo-L "Merge T-a, T-b, T-c"), so per-item sizes there are upper
bounds.

**F4. The four small closed ones. VERIFIED (PR files).** All four were blocked by a role or an owner rule:
- H [#136](https://github.com/Marius-Juston/agent-harness/issues/136): xfail_strict on and min_tests raised, 25 net
  lines in PR 150, closed 55 min after filing. The filer was a researcher on #112 (issue mode, Owns = docs only).
- repo-E #73: one settings.json allow line plus 4 ADR lines (5 lines). The builder's own wording: "did not edit
  permission settings (harness file, owner decision)".
- repo-E #182: a 2-line wording fix in a research note, found by a researcher's audit ("One-line wording fix").
- repo-E #175: 20 lines, filed by the lead from a research round.

**F5. Open items: about 12% look small, about 3% were also free of any rule blocker. BELIEVED (manual reading of 147
items; size was not measured).** The script's proxies over the 481 open items: wording that hints at small size 7%,
exactly one DONE WHEN 13%. In H, 9 of 40 had small-size wording and 21 of 40 had one DONE WHEN. My reading of the H,
repo-E and repo-R items:
- H, 10 of 46 small (30 lines or fewer): #47, #88, #90, #93, #136, #139, #140, #143, #145, #146. Only 3 or 4 had no
  rule against fixing in place.
  - #47 (`IMPORT_HOPS = 5` should be 4) sits in `devkit/checks.py`, which origin PR 49 touched. It was filed 2 min
    before that PR merged.
  - #90 (a CI step for a script #81 added) was left out because "workflows were out of its scope".
  - #145 (git -c on the read-only allowlist) was found by the lead reviewing PR #142, in a file that PR changed.
  - #88 (fold the plugin CI job) also needs a change to the required checks.
  - The rest were blocked by a rule. Parallel ownership: #93 ("Deferred because PR #73 owned workflow.py") and #146
    ("#114 avoided editing those lines while #142 is open"). Role: #136 and #143 (researchers). Design: #139 and #140,
    because the architect "proposes, never deletes".
- repo-E, 5 of 75 small: #53, #73, #123, #173, #182. Each had a blocker. Harness file or owner rule: #73 and #123
  ("Harness file: use the harness skill"). Research-only role: #173 and #182. Outside the builder's Owns: #53 (a
  one-line shadowing bug in board.py found by builder-36). 10 more repo-E items were throwaway `[test]` issues made to
  check board automation, not follow-ups.
- repo-R, 2 of 26 small: #163 and #80. #163 is the clearest fix-now case in the data: #106's new guard makes `simulate`
  crash on an empty plan, and the fix was filed instead of made. The other repo-R items are investigations.
- repo-L: its builders file feature-sized "gap tickets" by protocol. 59 repo-L items were filed right after narration
  such as "Now file the gap tickets".

Estimate: about 17 of 147 (12%) of the follow-ups in H, repo-E and repo-R were 30 lines or fewer. About 4 or 5 of 147
(3%) were also free of any parallel-ownership, role, harness-file or owner-decision rule. In H alone that is 3 or 4 of
46 (7 to 9%), the same order as the lead's example.

**F6. Where the follow-up lands. VERIFIED.** Most are filed before the origin change merges: only 86 of 618 (14%) were
filed after it. So the fix could still have gone into the open PR. A closing change touched the origin change's code or
config files in 92 of 142 cases (65%), and the origin issue's Owns in 75 of 142 (53%). Docs and `*.md` are left out of
that overlap. In H the figures are 1 of 5 and 0 of 5. repo-L's lanes are broad, so its 69% overstates "same place".

**F7. How agents justify deferring. VERIFIED counts (crude regex); quotes as written.** 395 of 640 (62%) give no
reason at all in the filing text or the two assistant messages before it. Where a reason shows, the categories are
multi-label: research gap or protocol follow-up 19%, owner decision 9%, size or "later" 7%, parallel work, open PR or
hotspot 7%, found incidentally 3%, scope, Owns or lane 2%. Verbatim:
- Parallel ownership: "Deferred because PR #73 owned workflow.py" (H #93); "#114 avoided editing those lines while
  #142 is open. Once it merges: ..." (H #146); "Deferred so as not to collide with T-0740 and T-0782, which rewrite
  those functions" (repo-L).
- Scope: "workflows were out of its scope, so CI does not run it yet" (H #90); "Left out of #114" (H #147).
- Owner or harness rule: "did not edit permission settings (harness file, owner decision)" (repo-E #73); "Harness
  file: use the harness skill" (repo-E #123).
- Incidental find, no reason given: "Found while working #44." (H #47); "Found reviewing PR #142" (H #145); "Found in
  #59 (VERIFIED ...)" (repo-R, 4 items).
- Protocol narration: "Now file the gap tickets"; "Let me file the gap tickets first so I can cite them in the note";
  hand-back "the rest is T-0803" (repo-L).

**F8. Instructions that push filing. SOURCED (local files read 2026-10-13).**
- [builder.md](../../../../plugins/harness-ops/agents/builder.md):
  - "stay inside your issue's Owns" (line 9).
  - An edit outside Owns is made "only when the issue needs it end to end" (line 22).
  - The PR body lists "follow-ups filed as issues" (line 27).
  - The hand-back lists "follow-up issue numbers" (line 30).
  - Nothing tells a builder to fix a small defect it finds.
- [tester.md](../../../../plugins/harness-ops/agents/tester.md) line 15 is the only fix-now rule: "A small defect
  inside the issue's Owns: fix it. Otherwise ... file the defect".
- Researchers: "File each important gap you did not close as an issue"
  ([researcher.md](../../../../plugins/harness-ops/agents/researcher.md) line 32), and the
  [research skill](../../../../plugins/harness-ops/skills/research/SKILL.md) line 46 files follow-ups for gaps.
- The architect files "one issue per step" ([architect.md](../../../../plugins/harness-ops/agents/architect.md)
  line 12).
- [multi-agent.md](../../../../docs/specs/multi-agent.md) line 79 allows "A small edit outside Owns that the issue
  needs". "Needs" excludes incidental finds.
- repo-L's agent files have the same shape. Its ticket skill says "File gaps you find with ... board new". Its
  researcher adds "a backlog ticket for every important gap". Only its test agent has the "fix it if the fix is small
  and inside the ticket's files" rule.

## Gaps seen, not closed
- Right-censoring: re-run the script in 2 to 4 weeks for closing diffs of the H, repo-E and repo-R items that are now
  open. Run it before treating 3% or 12% as more than an order of magnitude.
- The cost of deferring beyond diff size is not measured: the tokens and wall time of a fresh worker's orientation and
  gate per small follow-up (for example PR 150 for H #136) against the marginal cost in place. Subagent transcripts
  hold the usage.
- Transcripts keep about 10 days, and repo-R has only 4 main-session files, so older filings and other machines are
  missing. 23 repo-R and 86 repo-L bodies carry follow-up wording, a superset by text markers.
- Lead filings with no "From #N" (planning, and some hand-backs) are not counted as during-other-work.
- The size of open items is my reading (BELIEVED). A second rater, or estimating each item's lines from its DONE WHEN
  and Owns, would firm it up.

## Sources fetched
- GitHub issues and PRs (`gh issue list`/`gh pr list --state all`, read-only, 2026-10-12/13) of H, repo-E and repo-R.
  repo-L's board files and `git log` (first-parent merges, board moves).
- Claude Code transcripts under `~/.claude/projects/*/` (main and `subagents/`, with `.meta.json`), 2026-10-03 to
  2026-10-13.
- H instruction files linked in F8. repo-L's agent and skill files (code name only; paths not given).
