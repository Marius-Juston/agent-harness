r2: trigger calibration by replaying history (agent-harness#111, round 2, gap 3)

Search coverage: no literature was searched; this round replays local git history only. `uv run dev lit status` on
2026-10-12: OK, partial (s2 down; openalex, crossref, europepmc, arxiv up). pyscn metadata came from the GitHub and
PyPI APIs on 2026-10-12.

Repos use the code names from round 1's scrub: repo-L (game server, app package `app/`), repo-R (robot), repo-E (the
other research repo), H (this repository). Measured at the same tips as r1 (L `086fb686`, R `7e5c305`, E `3748c23`,
H `bd9abb9`); every read used `git log`/`ls-tree`/`cat-file` or a `git archive` copy, and `git status` of each
checkout stayed clean.

Reproduce (L takes about 55 s without tools and 2 min with them; the others take under 30 s):

```sh
uv run scripts/repo_metrics.py --replay 1 --replay-tools 10 <repo> --json .scratch/replay_<name>.json
python3 scripts/r2_simulate.py                        # single signals: fires, first crossing, share of history
python3 scripts/r2_composite.py                       # composite variants A-E
uv run scripts/repo_metrics.py --trigger [--since REV] <repo>   # the fast trigger for `dev orient`
uv run --python 3.14 python scripts/r2_agree.py <repo>...       # fast trigger vs AST replay
```

What changed in [scripts/repo_metrics.py](../../../../scripts/repo_metrics.py): the default run is unchanged. The new
flags are `--replay STRIDE`, `--replay-tools N`, `--trigger` and `--since`, and the new functions are `replay`,
`replay_tools`, `trigger_signals` and `fast_trigger`. The lead's scrub requests are also in: the AST loop variable
was renamed to `sub`, and the hard-coded repo names in `transcript_usage` were replaced by the CLI repo names. `tests/test_scrub.py`
passes (16 passed). r1's `scripts/check_repo_metrics.py` still passes.

Method. Each signal was computed at every first-parent commit: L 623 commits, R 326, H 31 and E 53. A counter
trigger (growth, commits, merges, days) resets when it fires. That models a review which removed nothing, so it is
the pessimistic case. A state trigger (a smell) fires when it first becomes true, or when it becomes true again
after a review at which it was false. "Ground truth" is r1's two accretions in L:
- the v1 tree, dead from the day `app/` started (first-parent index 32, 2026-09-14), at about 19-22k LOC;
- `app/dev`, the in-product harness. It reached 5k LOC at index 332 (2026-10-03), 10k at 516 (2026-10-04) and 23.5k
  at the tip.

E's PR #127 is the one real consolidation in these histories.

## Findings

### F1. Days and merged PRs are poor cadence units at agent pace; first-parent commits and LOC growth work (VERIFIED, r2_simulate.py)

| trigger (reset on fire) | L fires (first) | R | H | E |
|---|---|---|---|---|
| days >= 7 | 4 (05-03, during the 4-month lull) | 0 | 0 | 0 |
| days >= 14 | 3 | 0 | 0 | 0 |
| merged PRs >= 40 | 5 (first at index 303, 2026-10-03, 135k LOC) | 2 | 0 | 1 |
| first-parent commits >= 100 | 6 (first at 100, 2026-09-25, 50k) | 3 | 0 | 0 |
| code x2 and +5k LOC | 4 (24k, 49k, 100k, 201k) | 3 | 1 (bootstrap from an empty root) | 0 |
| code +50% and +2k LOC | 7 | 4 | 2 | 2 |

- **Days.** L added 229k LOC in 22 active days, and R added 35k in 4 days. A 7-day clock therefore never fires in R,
  H or E. In L its fires fall in the dormant months (2026-05-03, 2026-09-13), when nothing changed.
- **Merged PRs.** L committed straight to master until index 63 (2026-09-25). A PR count therefore misses the first
  half of the rewrite: its first fire came at 108-135k LOC, when the dead v1 tree was already 12 days old.
  First-parent commits count PRs in a PR workflow and commits otherwise, so they work in both. In H and E the two
  counts are nearly the same (30 of 31 and 52 of 53 first-parent commits are merges).
- **Doubling.** "Code doubled and grew by at least 5k LOC" fires about log2(growth) times: 4 times over L's 21x
  growth, 3 times in R and 0 in E. In L that is at 24k, 49k, 100k and 201k LOC.

### F2. Orphaned dormancy caught the v1 tree on the day it died, with no false alarm elsewhere (VERIFIED)

- **Definition.** A unit is a two-level directory, or a file at the root. It is *dormant* when none of the last K
  first-parent commits touched it. It is *orphaned* when, in addition, no module of an active unit imports it. A test
  unit is orphaned only if it also imports nothing active. The signal is the orphaned share of code LOC.
- **K=30, share at least 5% (repo-L).** First true at index 59 (2026-09-14): 56% of 38k LOC, made up of `systems`,
  `tests`, `core`, `agents` and `bot.py`. That is 27 first-parent commits after `app/` appeared. It stayed true at
  86% of later commits and fell to 9.8% at the tip only because the code around it grew.
- **K=30 in the other repos.** It was never true in R, H or E.
- **K=50.** It fires later in L, at index 112 (2026-09-25).
- **Plain dormancy is noisier.** Without the orphan filter (dormancy alone, K=50), R's `sim/levels` (2.4k LOC, 10%)
  counts as dormant for about 60 commits and is then edited again, so it is a false alarm. The import filter
  removes it because live `sim` modules import it.
- **Tests.** Before the rule for test units was added, R's root `tests` unit (527 LOC) came out as orphaned at index
  63, because tests are never imported. The rule fixed this.
- **A known false negative (VERIFIED, replay rows 59-100).** One "updated documents" commit that touched the v1 tree
  (index 62, 2026-09-25) reset its dormancy for 38 commits. Counting only touches that change code LOC would close
  this gap (BELIEVED).

### F3. Lazy imports, vulture and pyscn separate the repos but do not time accretion (VERIFIED)

| signal | L | R | H | E |
|---|---|---|---|---|
| function-level share of internal import edges (AST) | 43% at the first commit, 12-17% after | 10-54% | 3-6% | 0-6.5% |
| indented share of import statements (regex) | 46% at the first commit, 16-18% after | 11-42% | 2.1-2.6% | 0.9-2.0% |
| largest import cycle through lazy imports >= 10 modules | from index 132 (59k) | never (max 3) | never (5) | never |
| vulture findings per kLOC (60% confidence) | 18.4 at the first commit, 4.1 at the tip | 23.9 to 4.8 | 0.1-0.2 | small |
| pyscn health (archive copy) | 80-85 (B) | 85-88 (B) | 87-88 (B) | 80-85 or none |
| pyscn duplication % | 13.2 falling to 5.7 | 1.2-3.2 | 2.6-3.8 | 2.8-11.4 |

- **Lazy imports.** The share is 10% or more for 100% of L's and R's history and 0% of H's and E's. It is a standing
  style trait present from the first commit, so as a "new smell" it never fires. It belongs in the review's list of
  standing smells. The regex proxy ranks the repos the same way as the AST measure.
- **vulture.** Its count tracks size: Pearson r against LOC is 0.974 in L, 0.945 in R and 0.841 in H over 5-10
  samples. Per kLOC it separates H (0.2) from L and R (4-5), but L was already at 18/kLOC on its first commit. It
  does not flag the v1 tree, which still uses its own code. It takes seconds, so it does not fit `orient`.
- **pyscn.** The health score stays at grade B in every repo throughout the history, and L's duplication % falls
  while it accretes. Neither measure can time accretion. (pyscn 1.32.4 on `git archive` copies.)
- **pyscn depends on what is on disk.** r1-tools ran pyscn on R's working tree and got 70 C and 10.7% duplication.
  On the tracked-only archive at the same tip it gives 88 B and 2.8%. Any pyscn trigger must fix its input.
- **One-off scripts.** The share of LOC in `__main__` scripts that nothing imports is noisy. H's plugin hooks keep it
  at 16.6-18.4%, E sits at 3-22%, R at 24-29% and L at 0-2%. It does not separate lean repos from bad ones without
  run evidence from transcripts, which costs too much for a trigger.

### F4. Large churny files appear early in L and R and never in H or E (VERIFIED)

- **Definition.** Non-test files over 1,000 LOC with at least 30 non-merge commits, renames followed.
- **L.** First at index 114 (2026-09-25, 53k LOC): `app/domain/contracts.py`, 1,007 LOC and 40 commits. The count
  reached 4 at the tip.
- **R.** First at index 204 (2026-10-09): `sim/route.py`.
- **H and E.** Never.
- **The `app/dev` link.** A file joined this list at index 524 (2026-10-04): `app/dev/workflow.py`, 1,197 LOC and
  30 commits. It was the first file-level signal pointing into `app/dev`.

### F5. No generic signal identified `app/dev` as duplicate tooling; cadence reviews would have seen it while small (VERIFIED, BELIEVED where marked)

- **Size while it grew.** `app/dev` stayed at 3.4-5.1% of code until index 524. It then grew from 11k to 23.5k LOC
  over the last roughly 100 commits (2026-10-04 to 10-05).
- **What a review would have seen.** At the composite's fires (F6) it was the 2nd to 7th fastest-growing two-level
  directory, at 1.6k, 3.5k, 5.0k, 6.0k, 7.5k, 8.9k and 11.0k LOC.
- **Why no signal could name it.** H, the shared harness it duplicates, did not exist until 2026-10-11 (its first
  commit). No signal could have said "copy of a dependency" in time.
- **What the review should do** (BELIEVED):
  - List the fastest-growing directories since the last review.
  - Compare the repo's CLI commands with the installed harness's commands. r1 found 14 shared names.

### F6. Composite variants (VERIFIED, r2_composite.py)

| variant | L | R | H | E |
|---|---|---|---|---|
| A: doubling only | 4 | 3 | 1 | 0 |
| B: A, or 100 first-parent commits | 7 | 3 | 1 | 0 |
| **C: B, or a new smell (orphaned dormancy, large churny file or one more of them, lazy-import cycle of 10 or more, indented-import share of 10% or more)** | **11** | **4** | **1** | **0** |
| D: C with 40 merged PRs instead of 100 commits | 12 | 5 | 1 | 1 |
| E: C with only the dormancy and churny smells | 10 | 4 | 1 | 0 |

- **C in repo-L** fired 11 times, about once per 54 first-parent commits or 22k LOC added, at these indexes:

  | index | date | LOC | cause |
  |---|---|---|---|
  | 35 | 09-14 | 24k | growth |
  | 59 | 09-14 | 38k | **dead v1 tree** |
  | 114 | 09-25 | 53k | churny |
  | 121 | 09-25 | 54k | churny+ |
  | 132 | 09-25 | 59k | 66-module lazy cycle forming |
  | 232 | 09-26 | 99k | commits |
  | 332 | 10-03 | 142k | commits |
  | 432 | 10-03 | 178k | commits |
  | 478 | 10-03 | 193k | churny+ |
  | 511 | 10-04 | 205k | churny+ |
  | 524 | 10-04 | 214k | churny+ (`app/dev/workflow.py`) |

  Every fire had the v1 tree pending, so none was empty.
- **C in repo-R** fired 4 times: growth at 9k, commits at 14k, churny at 21k and commits at 36k. `experiments/` was
  13%, 17%, 30% and 29% of code at those points, so every fire had something actionable in it (BELIEVED, from r1 F4).
- **H and E: no fires** apart from H's bootstrap commit, which grew from an empty tree. A review marker set at
  adoption removes that fire.
- **E's PR #127 had no trigger.** E grew from 2.4k to 7.2k LOC before #127 (+4.8k, just under 5k), so neither
  doubling nor a smell would have prompted the extraction. It was planned by the owner.

### F7. The fast trigger runs in 0.4 s on the 259k-LOC repo and matches the AST replay (VERIFIED)

- **What it does.** `fast_trigger` reads two trees (HEAD and the last review) through one `git cat-file --batch`.
  It takes imports from a regex rather than `ast`, gets dormancy from `git log --first-parent -30 --name-only`, and
  gets churn from one `git log --no-merges --name-only`.
- **Speed.** Wall time with `uv run` start-up: L 0.41-0.46 s, R 0.18 s, H and E 0.08 s. The largest single
  `trigger_signals` call over 114 sampled commits took 0.38 s.
- **Agreement with the AST replay.** Over 114 commits sampled across all four repos, the two never disagreed on
  whether a smell was present. The orphaned share differed by at most 0.0005, and the largest cycle and the churny
  count matched exactly (`scripts/r2_agree.py`).
- **The last review.** It comes from `--since`, else from the newest commit with a `Consolidation-Review:` trailer
  (tested on a throwaway clone of H), else from the root commit. With no review yet, every smell present counts as
  new, so the first review sees them all. At L's tip with the base at index 524, the trigger is correctly not due:
  98 commits since, and no new smell.

## Recommendation for #113 (BELIEVED, calibrated on the data above)

**`review_due` is true when any of the following holds:**

- since the last review, code LOC at least doubled **and** grew by at least 5,000 LOC;
- **or** at least 100 first-parent commits landed since the last review;
- **or** a smell is present now that was absent at the last review;
- **or** there is one more large churny file than at the last review.

The smells:

| smell | condition |
|---|---|
| orphaned dormancy | at least 5% of code LOC is in units untouched for 30 first-parent commits and not imported by active units |
| large churny file | a non-test file over 1,000 LOC with at least 30 non-merge commits |
| lazy-import cycle | an import cycle of at least 10 modules through function-level imports |
| indented imports | at least 10% of import statements are indented, counted only when there are at least 50 imports |

Notes on the rule:
- **Time is not a trigger.** `orient` shows "days since review" for information only.
- **Merged PRs are not the cadence unit.** First-parent commits replace them.
- **Calendar unit.** In L, 100 first-parent commits came to about 1-4 days.
- **Expected rate.** About one review per 50-80 first-parent commits in an accreting repo, and zero in the lean ones.

Further recommendations:
- **Compute it with** `fast_trigger`/`trigger_signals`, which fit in `orient`'s budget at 0.4 s on 259k LOC. Port them
  into devkit as is.
- **Keep the thresholds in one place.** They are the `TRIGGER` dict in the script; in devkit they belong in
  `[tool.devkit]`.
- **Record the review.** Put a `Consolidation-Review: <verdict>` trailer on the review PR. A sha in a file would also
  work, but the trailer needs no new file.
- **Keep slow tools out of the trigger.** pyscn, vulture, type-2 duplication and transcript usage take seconds to
  tens of seconds. They belong in the review report.
- **What the review report should open with:**
  - the standing smells;
  - the orphaned units;
  - the fastest-growing directories since the last review;
  - CLI or command names that overlap with installed shared tooling.

## Gaps seen, not closed

- **Small sample.** The calibration rests on 4 repos, and 2 of them are 1-2 days old. Neither K=30 dormancy nor
  100-commit cadence can fire in H or E yet. Recalibrate once H has a few hundred first-parent commits.
- **Thin ground truth.** It is two accretions in L plus R's `experiments/`. "Noise" was judged by whether an
  actionable item was present at each fire (BELIEVED). No review was actually run at those points.
- **Duplicate tooling has no cheap detector.** The CLI-name overlap check against installed tooling is proposed but
  was not built or measured.
- **Dormancy resets on trivial edits.** A doc-only or trivial edit resets it (F2). The fix (ignore touches that
  change fewer than N code lines) is untested.
- **One-off scripts.** They need run evidence from transcripts, not structure, and no cheap trigger was found.
- **Regex limits.** Imports inside strings or docstrings and multi-line parenthesised imports are only partly seen.
  Agreement with the AST replay was exact on these repos, but other code styles could differ.

## Sources and tools fetched

- [ludo-technologies/pyscn](https://github.com/ludo-technologies/pyscn): MIT, 1,091 stars, pushed 2026-10-10;
  [PyPI](https://pypi.org/project/pyscn/) 1.32.4, run as `uvx pyscn@1.32.4 analyze --json --no-open` on archive
  copies.
- [jendrikseipp/vulture](https://github.com/jendrikseipp/vulture): metadata as in r1; run as `uvx vulture==2.16
  --min-confidence 60`.
- Local git history of the four repos at the tips above (read-only).
