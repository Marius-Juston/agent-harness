# r2b: the proposed mechanical drift checks, replayed over both repositories' history (#220)

Search coverage: lit status as given by the lead, 2026-10-13: discovery OK, partial (s2 down). No literature search was needed for this sub-question. The evidence is the two repositories' own history, read-only: `git log/show/archive` and `gh api` (issues, PRs, GraphQL body-edit history, one Actions log). Two external sources were also read: a Wayback Machine CDX listing of captures and the live code.claude.com pages.

Method. Every check runs at every first-parent commit on `main`: agent-harness 69 commits through `635a833`, project B 56 through its tip. Each commit is checked against its own state. The CLI check uses that commit's own parser. Project-B commits after the migration use agent-harness at the tag pinned in their `pyproject.toml`. Issue checks use each body as it was at the time, rebuilt from GitHub's `userContentEdits` (agent-harness 87 edits, project B 183). I classified every flag by hand (one rater). A flag is a TRUE catch when the drift was real, meaning that someone later had to change text, issues or code because of it. Otherwise it is a false positive (FP). The bar is the one from [r1-tools-against](r1-tools-against.md): a gate needs zero FP on history. Trees and test runs live in `.scratch/r2/` (gitignored). The one throwaway worktree per repository was removed afterwards.

## Findings

| # | Check (r1 proposal) | Flags on history | TRUE | FP | Precision | Recommend |
|---|---|---|---|---|---|---|
| 1a | `uv run dev` code spans in md/sh/yml/json/jinja: names, subcommands, flags, leftover positionals | 1 event | 1 | 0 | 1/1 | **gate** (amended after the PR #237 audit: warning until a held-out replay, since the syntax rules were fitted here) |
| 1b | the same in `.py` strings and comments | 2 events | 1 (same event as 1a) | 1 | 1/2 | warning |
| 1c | bare `` `dev X` `` spans (no `uv run`) | 7 events | 0 | 7 | 0/7 | none, or a warning that exempts lines with an issue link |
| 1d | the same check on issue bodies | 25 issues at filing, 10 at close or now | 0 | 35 | 0/35 | **drop** |
| 2 | open issue's Owns path existed and is gone | 11 issues, 1 root event | 11 | 0 | 11/11 | **gate at integrate**, plus a `board lint` error |
| 3 | DONE WHEN met but unticked | no signal: 0 ticked boxes in 451 body versions | — | — | — | generate, don't check |
| 4 | integrate writes the printed `min_tests` raise | 9 simulated raises, 0 false failures | 0 losses to catch | 0 | — | **adopt (generated write)** |
| 5 | orient lists memory newer than the last HANDOFF | 4 entry-sessions listed | 4 | 0 | 4/4, recall 4/16 | sweep: list **every** memory entry |
| 6 | HANDOFF names closed work as open | at write: 10 (5 after a "said done" rule); later: 19 | 2 at write; 19 later | 8 (3 after the rule) | 20 % (40 %) at write; 19/19 at read | generated live state at orient; no gate |
| 7 | `enforced-by` literal marker present | short markers: 2 events; sentence markers: 5 | 1 | 1 short, 4-5 sentence | 1/2 short | warning by the strict bar; see F7 |
| 8 | periodic URL sweep of cited docs | lychee: 539 error URLs; anchors: 0 of 28 pages | 0 verified dead | ≥113 verified alive | ≤ 4.8 % | sweep, fixed config; quote check for code-cited docs |

**F1. CLI check: the positional parse is what catches real drift. Flags alone caught nothing, and only `uv run dev` code spans are clean enough to gate (VERIFIED, [r2_cli_replay.py](scripts/r2_cli_replay.py) with [r2_cli_worker.py](scripts/r2_cli_worker.py); prose variant [r2_cli_prose.py](scripts/r2_cli_prose.py)).**
- The one TRUE event was `` `uv run dev board block <n> "<why>"` ``, in `plugins/harness-ops/agents/builder.md` and in `devkit/weaken.py`'s hint. It entered with PR #69 for #55 (merge `94214398f`, index 19) and was fixed by [PR #151](https://github.com/Marius-Juston/agent-harness/pull/151) at index 39. It was present in 20 consecutive merges over 12 h 48 min (VERIFIED arithmetic). argparse leaves `"<why>"` over because `block` takes `--reason`. This is r1's finding 11.
- The flag-only check that r1 proposed (K3) found 0 TRUE in the whole history. Its only flag is project B's bare `dev eval --model`, which proposes a future command. The other two r1 CLI findings are out of reach of any prose check. [#179](https://github.com/Marius-Juston/agent-harness/issues/179) was `--help` text that did not show a working mode. [#188](https://github.com/Marius-Juston/agent-harness/issues/188) was a message string against an environment variable. So the check covers 1 of the 3 historical CLI findings.
- **Noise before tuning.** The naive parser gave 174 unique flags across both histories. 127 of them were argparse errors, mostly a missing required argument (prose names a command without its arguments) or a placeholder `N` failing an int conversion. Five syntax rules brought this to 11:
  1. parse with required options and positionals loosened;
  2. accept `a|b` alternatives and `settings.json` globs (`board list*`);
  3. treat `...` as elision;
  4. cut the span at shell operators (`&&`, `<<`, `>`, `, `, a sentence-final `.`);
  5. treat `N` and `<x>` as placeholders.
- These rules were fitted on this same history, so the 0-FP result in 1a is an in-sample number (n = 1 TRUE event). The checks the gate already runs (command and subcommand names for `uv run dev`) raised 0 flags over the whole history.
- **Bare `dev X` spans are where authors write proposals.**
  - 5 of the 7 FP propose a future command and cite its issue: `dev mutants` #134, `dev ci`, `dev reuse` #29, `dev setup-repo` #62, `dev eval --model` #82.
  - 1 is a docstring that demonstrates an argparse bug (`devkit/cli.py`: `` `dev reuse check --json a b` ``).
  - 1 is a comment in `guard_bash.py` (`dev board|code`).
- **Issue bodies:** 0 TRUE out of 35 flagged issues. Every flag at filing names the command or flag that the issue itself proposes. At close, the flags come from quoting inside prose (`'lit search'`, `lit (shared`). The positional parse on prose that is not in backticks catches [#143](https://github.com/Marius-Juston/agent-harness/issues/143)'s DONE WHEN (`uv run dev board block <n> "<why>"`), but at 1 TRUE in 30 flags. Most of the noise is "`uv run dev check passes`", where `passes` is read as an argument.

**F2. Owns paths deleted under open issues: 0 FP. Every flag was an issue that needed action, all from one event (VERIFIED, [r2_owns_replay.py](scripts/r2_owns_replay.py)).**
- agent-harness: 73 issues with Owns, 213 paths, 3,812 (path, open-commit) checks, 0 flags.
- Project B: 101 issues, 200 paths, 2,910 checks. Every flag comes from its migration merge (2026-10-11 07:54 UTC). That merge deleted `devkit/`, `.claude/agents/` and `.claude/skills/`, which were now served by agent-harness.
  - Within 3 minutes the lead closed 6 issues by hand as "Moved to the single source of truth".
  - The check would also have listed 5 more:
    - 2 were done but left open, and were closed 30 h later.
    - 3 still own deleted paths, 58 h on (VERIFIED arithmetic).
- Coded after the PR #237 audit as `RESULT-AT-MERGE` in [r2_owns_replay.py](scripts/r2_owns_replay.py): 11 flagged issues at 1 merge (6 closed within 15 min, 2 later, 3 open), with the PR's own issue taken from its closing keywords or its `<issue>-<slug>` branch.
- The check has to skip the merging PR's own issue. Without that rule (`--no-window`) it also flags the migration issue itself, whose PR deleted those paths, and one issue that was closed 12 minutes before the merge. Run at integrate time, against the paths the PR deletes, the check fires at the moment the fix is cheap.
- Owns sections are read as they were at each commit, from the edit history. Nine issues changed their Owns across edits, and none of those were flagged. The precision rests on n = 1 root event.

**F3. DONE WHEN ticks carry no information here, so a tick-based check cannot work (VERIFIED, [r2_donewhen_replay.py](scripts/r2_donewhen_replay.py), [r2_issue_edits.py](scripts/r2_issue_edits.py)).**
- No version of any issue body ever had a ticked DONE WHEN box: 0 of 198 versions in agent-harness and 0 of 253 in project B. Issues close through `Closes #N` at integrate, so "all ticked but open" fired 0 times in 6,238 (open issue, commit) checks.
- Partial PRs (`Refs #N`, 31 in all) report what they met in at least 4 free-text formats: `- [x]`, `**met**`, `MET`, and "met:". The parser found a met item in 11 of 31. Of the 23 met lines, it matched 15 to the right box by word overlap (Jaccard ≥ 0.25; BELIEVED correct by reading) and left 8 unmatched, one of them a real item: #151's "1. … MET".
- Seven of those 11 partial PRs said every box was met (AH #57, #55, #53, #45, #35, #23 and one project-B PR). All seven are from before decision D04 (complete hand-backs write `Closes`), and each issue was closed by hand within 2 minutes.
- What actually drifts is an open issue whose met items were never written back. Today that is [AH#174](https://github.com/Marius-Juston/agent-harness/issues/174) with 2 of 3 met, [AH#143](https://github.com/Marius-Juston/agent-harness/issues/143) with item 1 met, and #196, whose scope was changed in a comment.
- Recommendation (BELIEVED): `dev pr --partial` copies the issue's DONE WHEN checklist into the PR body. The worker ticks it, and integrate writes the ticks back by exact text match, so the mapping is exact by construction. A partial PR that ticks every box is refused as a `Closes`.

**F4. Writing the floor raise at integrate: 9 raises and 0 false failures in 69 merges (VERIFIED, [r2_floor_replay.py](scripts/r2_floor_replay.py): the gate's own test selection, `-m "not slow and not live"`, run in a throwaway worktree at each commit and counted from JUnit).**
- In agent-harness the passed count never dropped from one merge to the next: 505 at merge index 7, 1,518 at the tip, with 0 failures at any commit on this machine.
- The gate's raise notice fired at 37 of the 41 merges that had a floor. The hand raises came twice (759 → 884 → 1,021). Today the floor is 1,021 against 1,518 passed, so losing up to 497 tests (32.7 %) would pass the gate.
- With the policy in `gate.py` (raise to 0.95 × passed once the floor is below 0.90 × passed), the floor would have risen 9 times, to 1,442. That catches any loss above 76 tests (5.0 %), with 0 false failures.
- There were 0 real test losses to catch, so the check's precision is undefined, not 100 %.
- Project B: `min_tests = 1` and 1 test passed at every commit since the migration, so no notice fired.
- The counterfactual matters. Project B's migration moved 363 of 364 tests to agent-harness, which is a legitimate drop. Any floor fails that, and the fix is a `[[raise]]` that the owner approves. That is the intended friction.
- Caveat: this machine passes more tests than the gate recorded (852 at #58 here against 799 in its note), because skips depend on the tools installed. So integrate must write the count from the gate run it just did, never from a local re-run (VERIFIED difference; cause BELIEVED).

**F5. "Memory newer than HANDOFF" is precise but misses most unpromoted entries. List all of them (VERIFIED, [r2_memory_replay.py](scripts/r2_memory_replay.py), machine-local data).**
- I replayed the rule at the 7 project-B lead-session starts on this machine and at the next one.
- It listed an entry 4 times, all correct, because none of the 5 entries has been promoted (r1-taxonomy F6). It stayed silent 12 times while an unpromoted entry existed. One entry was written at 12:09, and the 16:27 HANDOFF commit then hid it from every later session start.
- Recall is 4 of 16 (25 %). An unpromoted entry is not "new since HANDOFF" but "still in memory". So orient should list every memory entry, and promotion should mean moving the rule into the repository and deleting the entry. That listing is precise by construction.

**F6. HANDOFF claims go stale within about an hour, and checking the prose at write time is too noisy to gate (VERIFIED, [r2_handoff_replay.py](scripts/r2_handoff_replay.py)).**
- 9 HANDOFF versions carry 156 `#n` references in the claims-open sections (In flight, Next, Waiting, Paused).
- At write time, 10 of them were already closed:
  - 2 TRUE: a Next item and an In-flight line that both waited on a PR, committed after that PR had merged.
  - 5 FP, where the text itself says "merged" or "done".
  - 3 FP, where closed issues are the subject of a re-sweep.
  - That is 20 % precision, or 40 % with a "said done" rule.
- Before the next version replaced them, 19 references went stale. All 19 were real (BELIEVED by reading). The median time to stale was 1.1 h; the maximum was 14.6 h.
- So the gain is in generating the live state when orient reads it (open PRs, claims, pin, and the live status of every Next item), not in checking the prose.
- The harness-version claim matched the pin at write in all 9 versions. It then went stale after the next harness update (r1-taxonomy F6).

**F7. `enforced-by` markers: a short marker on the *reason* catches C1. A marker on the rule does not, and it raises the one FP (VERIFIED, [r2_marker_replay.py](scripts/r2_marker_replay.py)).**
- Set A: the 18 recorded decisions with r1's short markers had 0 disappearances. These markers were picked at HEAD, so the set can only show delete-then-restore events.
- Set C, the C1 reversal:
  - The reason marker ("shared, cached, rate-limited client") disappears at #171 (`e96188345`). That is the TRUE catch: it would have blocked the budget trim.
  - The rule marker ("never call those APIs through WebFetch") survived #171, so it would not have caught C1. It vanished at #190, where the restore reworded the rule to "never curl or WebFetch them (shared rate limits)". That is the FP.
- Sentence-length markers (an author writes the whole rule sentence as the marker) give more FP:
  - agent-harness: 1 FP in 18 markers, a Status line rewritten.
  - Project B: 4 events in 18 ADR-linked sentences.
    - Three come from a CLAUDE.md budget trim in one PR. Two keep the rule in other words, so they are FP. One drops a clause from a policy ADR's one-line summary, which is ambiguous.
    - One is a HANDOFF rewrite (FP).
- Every flag lands on the PR that edits the marked text, so an FP costs one edit to the marker in the same diff. By the strict zero-FP bar this is a warning (1 TRUE, 1 FP for short markers). Gating it with markers of 5 words or fewer, chosen on the reason, is a judgement call for the lead (BELIEVED).

**F8. The weekly dead-link sweep is about 0 % precise as configured, and no URL sweep would have caught `IMPORT_HOPS` (VERIFIED, [r2_url_sweep.py](scripts/r2_url_sweep.py), [r2_doc_anchors.py](scripts/r2_doc_anchors.py)).**
- Project B's only `maintenance.yml` run (2026-10-12) reported 1,245 errors among 6,387 links, covering 539 unique URLs. I re-checked them today:
  - 461 doi.org links were 403. All 60 in a random sample are registered DOIs (publisher bot blocks).
  - 36 of 36 GitHub 503s exist (`gh api` contents).
  - Every 404, 405, 504, timeout and connection error that could be reached is alive.
  - 26 could not be judged: 403 bot walls, one government site (which answers 404 even for its own home page), and TLS failures from this machine.
- So 0 dead links were verified, and at most 26 of 539 (4.8 %) could be dead. The run predates the `_scratch` exclusion, but the 379 non-scratch errors have the same mix (345 are doi.org 403).
- agent-harness has no maintenance workflow, so its own links are never swept.
- **`IMPORT_HOPS = 5` was stale when it was written, not stale through drift.**
  - The archived Claude Code memory docs said "max-depth of 5 hops" in May 2025 ([Wayback 20250512](https://web.archive.org/web/20250512021958/https://docs.anthropic.com/en/docs/claude-code/memory)).
  - By July 2026 they said "maximum depth of four hops" ([Wayback 20260704](https://web.archive.org/web/20260704180931/https://code.claude.com/docs/en/memory)), and they still do (fetched 2026-10-13).
  - The constant first appeared in project B on 2026-10-11, so it was written after the change, most likely from memory of the 2025 docs (BELIEVED).
- So a "page changed since cited" sweep would not fire. Most Wayback captures of these pages are JavaScript shells whose digest differs on every capture, which makes raw change alerts useless anyway.
- A page-and-anchor sweep of the 28 code.claude.com pages that either repository cites (10 anchors) found 0 problems today. It too misses `IMPORT_HOPS`.
- The comment cites `code.claude.com/docs/en/memory` without `https://`. Only a quote-presence sweep would catch this class: the comment cites a literal sentence, and the sweep checks that the sentence is still on the page. None of the 5 doc-citing comment lines in `devkit/checks.py` carries a quote, so this is not measurable on history (BELIEVED).

## Recommendation for the build issues (BELIEVED, from the table)
- **Gate:**
  - 1a: extend `check_dev_commands` with flags and leftover positionals, using the five syntax rules, in the file types it reads today.
  - 2: integrate refuses a merge that deletes paths owned by open issues other than its own, and `board lint` reports them as errors.
- **Generated:**
  - 4: integrate writes the floor raise from the gate's own count.
  - 3: `dev pr --partial` echoes the DONE WHEN checklist, and integrate writes the ticks back.
  - 6: orient prints live state for each HANDOFF reference and for the pin.
- **Warning:**
  - 1b (`.py`).
  - 7 (short reason markers; the lead may promote it to a gate).
- **Sweep:**
  - 5: orient lists all memory entries.
  - 8: lychee with `403` accepted for doi.org (cite-check already resolves DOIs), the job token for github.com, the same workflow in agent-harness, and a quote-presence check for doc URLs cited in code.
- **Drop:** 1c as a gate, and 1d entirely.

## Gaps seen, not closed
- Each check's precision rests on very few TRUE events: 1 for the CLI check, 1 root event for Owns, 1 for markers, and 0 test losses. The CLI syntax rules were tuned on the same history. A held-out period (the next 2 weeks of merges) would give out-of-sample numbers.
- One rater classified every flag. The judgements for #3 (15 box matches) and #6 (19 went-stale references) were made by reading, not by a second coder.
- The memory replay uses file birth times on one machine, and other machines' memory is invisible. Project B's HANDOFF stands in for agent-harness, which has no HANDOFF of its own.
- The URL re-check could not judge 26 URLs (bot walls, TLS from this machine). The government site's 404-for-everything was seen once and not explained.
- I did not measure the quote-presence sweep (F8), because there are no quoted doc citations to replay.
- The floor counts come from this machine's environment. CI passes fewer tests, so the simulated raises are upper bounds on what integrate would write.

## Sources fetched
- Both repositories' history and records, read-only: `git log/show/archive`, `gh api` issues, PRs and GraphQL `userContentEdits` (2026-10-13), and `gh run view --log` of project B's maintenance run.
- Claude Code memory docs: [live page](https://code.claude.com/docs/en/memory) (2026-10-13), [Wayback 2026-07-04](https://web.archive.org/web/20260704180931/https://code.claude.com/docs/en/memory), [Wayback 2025-05-12 (docs.anthropic.com)](https://web.archive.org/web/20250512021958/https://docs.anthropic.com/en/docs/claude-code/memory), and the Wayback CDX API listing of captures.
- The 28 code.claude.com/docs pages cited in either repository (fetched by [r2_doc_anchors.py](scripts/r2_doc_anchors.py)), the doi.org handle API for 60 sampled DOIs, and `gh api` contents for 36 GitHub blob URLs.
- The zero-FP gate bar and the sweep-vs-gate criteria: [r1-tools-against.md](r1-tools-against.md) F1 and F4.
