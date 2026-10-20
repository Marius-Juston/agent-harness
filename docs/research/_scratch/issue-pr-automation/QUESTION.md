# Question (#346)

Which issue and PR bookkeeping does the lead (and do workers) still do by hand in harness-run repositories,
and which of it can devkit, git or GitHub take over, ranked by measured frequency, cost and saving?

## Scope
- In: board/issue filing, claiming, review relay, integrate loop, scheduling of board sync/sweep, PR bodies,
  HANDOFF, changelog, conflict prediction, duplicate detection.
- Constraints: GitHub Actions billing-blocked until ~Nov 2026; repos on a personal account (no native merge queue,
  no issue fields). Options must run locally or be GitHub-native / hosted apps without Actions minutes.
- Out: anything already filed (#100 #110 #232 #79 #104 #62 #60 #80 #99) or shipped in v0.1.12.

## Decision it serves
Which build issues to file (Follow-ups) and in what order, for devkit and the harness-ops lead skill.

## Known
- docs/research/command-sequences.md (§2 table: review relay n-gram, 134/365 integrate retries, 88 worktree+claim)
- docs/research/harness-efficiency.md (cost model §2)
- docs/research/fix-now-vs-defer.md (follow-up filing volume)
- docs/research/_scratch/command-sequences/sequences.py (detectors, transcript reader)
- Candidates 1-9 in issue #346 Context.
