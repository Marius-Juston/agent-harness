# r2a: decision mechanisms compared on this repository's 23 decisions and 5 reversals (#220)

Search coverage: `uv run dev lit status` 2026-10-13: openalex ok (key), crossref ok, europepmc ok, arxiv ok, s2 DOWN (HTTP 429, shared pool), so discovery was "OK, partial". I also fetched the Claude Code docs (memory, sub-agents, hooks, large-codebases), ran 13 live `claude -p` probes (Claude Code 2.1.296), mined 242 local session transcripts, and used `git log`/`gh` on both repositories. The hooks doc was read only to 100k of 269k characters, so I quote the event and decision tables, not the InstructionsLoaded input schema (I logged that schema myself instead).

## Answer
**Yes, something leaner than ADRs fits this repository better, and it is mostly built already.** The minimum set has four parts:
1. Executable tests and hooks for decisions that code can check. They already hold 10 of the 23 decisions.
2. One new gate check over a small decision table. Each row holds a rule, a `why` link and an `enforced-by` path plus literal. It covers the 6 durable rules that live only in prose today. Replayed over history, it fails exactly once, on C1.
3. A `board lint` warning for scope decided in comments (C3, C4).
4. One hook that denies direct calls to the literature-index APIs. That stops the harm behind C1 even if the prompt text drifts again.

**Path-scoped `.claude/rules/` and an ADR directory are not in the minimum.** Neither would have prevented any of the 5 reversals beyond what the marker check does. The one measured rule deletion (C1) happened through a Bash edit that would not have loaded a path-scoped rule (VERIFIED below). Decision-labelled issues with a generated index prevent none of the 5. They would have helped with one project-B re-litigation (a duplicate issue), and only as an advisory search.

## Findings

### M1. Where each of the 23 decisions belongs (coding BELIEVED, one rater; counts and evidence VERIFIED by [r2_mechanisms.py](scripts/r2_mechanisms.py))
| cheapest holder | n | decisions | state today |
|---|---|---|---|
| executable test | 10 | D02 D03 D04 D06 D13 D16 D19 D21 D22 D23 | 9 already have a test; the script checks that each named test literal exists. D02 (lit privacy) is still comment-only |
| hook | 2 | D12 (never curl or WebFetch the index APIs), D20 (architect read-only) | D20 is held by guard_bash and its tests; **D12 is prose only: no test, no hook** |
| rule + reason + marker check | 5 | D01 (SETUP keys section), D05 (one billed security job), D07 (audit cycles), D08 (repo_metrics.py frozen), D11 (search is a recall aid) | prose only; D07 was never written to the research skill (C3) |
| issue body (board) | 6 | D09 D10 D14 D15 D17 D18 | scope, sequence and pending experiments. These are not durable rules, so an ADR is the wrong home for them |

- 10 of 23 decisions are already held by a test or hook (VERIFIED: D03 D04 D06 D13 D16 D19 D20 D21 D22 D23).
- **The marker pattern already exists here as a test.** [test_plugin.py](../../../../tests/test_plugin.py) asserts the literal `prefix every command with cd <abs path> &&` in the lead skill, which holds D16 (VERIFIED).
- **6 durable rules are held only by prose:** D01 D05 D07 D08 D11 D12 (VERIFIED). One of them, "never curl" (D12), is the rule that C1 already deleted once. Nothing stops a second deletion: no test names it (`grep -rn "never curl" tests` returns nothing).
- import-linter would hold 0 of the 23, because none of them is about module dependencies. It remains the right tool for a rule such as "every external API call goes through one module" once such code exists ([r1-tools-against F8](r1-tools-against.md)). Library check (GitHub/PyPI API, 2026-10-13): [import-linter](https://github.com/seddonym/import-linter) 2.15, BSD-2-Clause, 1,217 stars, pushed 2026-10-13, not archived (SOURCED from the APIs).

### M2. How path-scoped rules load: docs (SOURCED) and a live probe (VERIFIED, [r2_rules_probe.sh](scripts/r2_rules_probe.sh), Claude Code 2.1.296)
- **Docs.** A rule with `paths` frontmatter "loads when Claude uses the Read, Write, or Edit tool on a matching file", or on a Bash command that counts as a read, such as `cat` or `head` on one file ([memory docs](https://code.claude.com/docs/en/memory#path-specific-rules)). Three further details from the same page:
  - `paths` is the only field read. A rule whose YAML fails to parse loads "as if it had no `paths`".
  - An invalid glob "matches nothing", silently.
  - After `/compact`, path-scoped rules "load again on demand".
- **Rules are context, not enforcement.** The same page says Claude "treats them as context, not enforced configuration. To block an action regardless of what Claude decides, use a PreToolUse hook" ([memory docs](https://code.claude.com/docs/en/memory)).
- **Subagents.** The sub-agents page lists "project rules" in a subagent's startup context. It says nothing about path-scoped rules ([sub-agents docs](https://code.claude.com/docs/en/sub-agents)). The same page confirms that settings and plugin hooks fire for a subagent's tool calls.
- **Probe results.** An InstructionsLoaded hook logged every load:

  | access | rule loaded? |
  |---|---|
  | Read tool | yes |
  | `cat <file>` | yes |
  | `sed -n 1p <file>` | yes |
  | Write of a new file | yes |
  | Grep tool | **no** |
  | `python3 -c "open(...)"` (the same class as `uv run dev code show`) | **no** |
  | a Read that a PreToolUse hook denies | **no** |
  | a Read inside a general-purpose subagent | **yes**, logged with that subagent's `agent_id` (`load_reason: path_glob_match`) |

  The main session did not see a rule that loaded in its subagent.
- **The rule arrives next to the tool result, and a small model may treat it as injected text (VERIFIED, small n).**
  - Haiku followed a realistic rule ("state the line count of fixture files") in 3 of 7 loads. In the other 4 it said the text "came from the tool result, not from you" and ignored it.
  - Opus followed it in 3 of 3 loads (2 main session, 1 subagent).
  - This is a direction only, not a rate. It matters for a harness whose prompts tell agents to treat tool output as data (BELIEVED).

### M3. Would a path-scoped rule have been in context when the change was made? (VERIFIED, [r2_rule_triggers.py](scripts/r2_rule_triggers.py), 242 local session transcripts of the lead's project folder on this machine, 234 of them subagents)
- **Reads.** 32% of file reads (2,006 of 6,211) use a triggering access. The other 4,205 go through `dev code`, `grep` or `rg` in Bash, which never load a rule.
- **Writes through tools.** 1,301 writes use Edit, Write, MultiEdit or NotebookEdit, and these always load the rule.
- **Writes through Bash.** For 619 Bash writes of a repository file whose target the script can extract, 501 (81%) had already loaded that file through a triggering access in the same session. **118 (19%) were cold.**
- **Precision of the Bash-write heuristic.** I checked a sample of 30 matches by hand, and 16 were real repository-file writes. The 1,206 total is therefore an upper bound, and 587 matches with no extractable target are left out.
- **C1 itself was cold (VERIFIED from the builder's transcript, 2026-10-13):**
  - The builder's only earlier access to `researcher.md` was `grep -n` (06:44:14Z).
  - At 06:50:36Z, a `python3 - <<EOF` replace dropped "a shared, cached, rate-limited client". The same command then ran `uv run dev check | grep -A1 ratchets`.
  - Its first triggering read came at 06:51:38Z (`sed -n 10,60p`), after the deletion.
- **So a rule scoped to `plugins/harness-ops/agents/*.md` would not have loaded before the deletion.** It would not have stopped it even if loaded (BELIEVED). The builder had the exact sentence in its own replace string and removed it to pass the word-budget gate. Text in context loses to a failing gate, and only another gate check changes that outcome.

### M4. The 5 reversals against each mechanism (coding BELIEVED; C1 facts VERIFIED)
| reversal | test / marker check | hook | path-scoped rule | decision issues + index | ADR | board-lint warning |
|---|---|---|---|---|---|---|
| C1: rule text deleted for word budget | **yes**: the gate fails, so a deletion must update the table explicitly | harm only: curl is blocked whatever the prompt says | no: not loaded (M3) | no | only through its `enforced-by` field, which is the marker check | no |
| C2: evidence reversal before merge | n/a (legitimate; recorded in [software-discovery.md](../../software-discovery.md) §0) | n/a | n/a | n/a | n/a | n/a |
| C3: comment default never written to the skill | no: nothing was recorded to check | no | no | no: same recording step | no: "an ADR is required" is the same discipline that failed | **warning**: a lead comment newer than the last body or file edit |
| C4: DONE WHEN narrowed in a comment | no | no | no | no | no | **warning** (r1-adrs §6) |
| C5: stale comment, self-corrected in 6 min | n/a | n/a | n/a | n/a | n/a | n/a |

- **Count of reversals prevented:** marker/test 1, hook 1 (harm only), path-scoped rule 0, decision index 0, ADR 0 beyond its marker field, board-lint warning 2 (VERIFIED arithmetic, [r2_mechanisms.py](scripts/r2_mechanisms.py)). C2 and C5 needed no prevention.
- **Project B's decision-level drift, with ADRs already in place (VERIFIED via `gh`/`git log`):**
  - One project-B issue (private repository, read with `gh`): a research note recommended an experiment arm that an accepted policy ADR forbids. The note added that text at 02:45 on 2026-10-11, 20 minutes after that ADR merged (02:25), so its branch may predate the ADR. It then kept the text through a v1.2 revision at 11:28, when the ADR had been in place for 9 hours. The text was withdrawn at 16:30. Another research round caught it, not the ADR.
  - Another project-B issue: the lead filed an issue at 06:17 for a question that had already been decided in OPEN_QUESTIONS at 02:09 and was closed as a duplicate. A decision index that `board new` searches would have flagged it. This is the one case where an index helps (advisory, keyword match).
  - One ADR was amended in place on the day it was accepted ([r1-adrs F2](r1-adrs.md)).

### M5. Comparison on the owner's criteria
| mechanism | decisions held here | words added to agent context | new drift surface | maintenance | false-positive risk |
|---|---|---|---|---|---|
| executable test / import contract | 10 now, 1 more (D02) | 0 (failure text only) | low: a test edit shows in the diff, and floors stop deletion | 5-20 lines per decision; infrastructure exists | ~0: deterministic, which meets Google's blocking bar ([r1-tools-against F1](r1-tools-against.md)) |
| hook | 2 (D12 to build) | 0 until it fires, then one reason string | the reason text vs the entry point: [#188](https://github.com/Marius-Juston/agent-harness/issues/188) | about 15 lines plus allow-list tests per rule (BELIEVED) | **measured here**: guard false blocks [#7](https://github.com/Marius-Juston/agent-harness/issues/7) (Python globs read as `.env`) and [#45](https://github.com/Marius-Juston/agent-harness/issues/45) (read-only `git config --get` refused) |
| rule + reason + marker check (decision table) | 6 prose rules | the reason only, in the file it lives in. C1's restored reason is 3 words, "(shared rate limits)". The table is in `.devkit/`, never loaded | table vs files, checked mechanically | about 30 lines; [`check_dev_commands`](../../../../devkit/checks.py) is 17 lines and `check_research_notes` 33, by outline line numbers (VERIFIED) | replay over history: **18 markers, 1 failing commit, and it is C1** (VERIFIED, `marker_replay()`). Survivorship bias: 17 literals were picked from today's text. R2b ([r2-replay F7](r2-replay.md)) then found 1 TRUE and 1 FP for short markers (the FP on a marker taken from the rule wording, none on the reason marker), so the check ships as a warning until a held-out replay (amended after the audit of PR #237) |
| path-scoped `.claude/rules/` | 0 that the in-file text does not already hold (D08's FROZEN docstring is read with the file) | 30-60 words per rule, per agent, on each first matching access | glob vs paths (renames match nothing, silently); rule vs CLAUDE.md conflicts | 0 code; budget already counted ([ratchets.toml](../../../../.devkit/ratchets.toml) `context` comment) | low cost if loaded needlessly. The real risk is the miss rate: C1 cold, 19% of Bash writes cold, and Haiku ignored it 4 of 7 times |
| decision issues + generated index | 0 of 23 alone (findability only) | 0 if linked. If imported: 23 × 15 = 345 words, **58% of the 600-word context budget** (VERIFIED arithmetic) | labels forgotten means the decision is invisible | label plus about 40 lines of `dev docs` generator (BELIEVED) | keyword search in `board new`: advisory only |
| ADR directory (baseline) | the same 6 as the table, plus narrative | 0 if linked; project B's bodies are 393 words on average (5 ADRs, 1,966 words) | the largest: prose vs code, notes and supersession links; project B shows an in-place amendment, an orphaned ADR, and a note contradicting a policy ADR | template plus the ~60-line check from r1 plus about 400 words per decision | the reference check warns; in-place-edit check FP unknown |

### M6. Does an ADR add anything that rule + reason + marker does not? (BELIEVED, resolves the r1 contradiction for this repository's data)
- **The 5 comment-only decisions:**
  - D09, D14 and D17 are board scope; D07 belongs in the research skill.
  - D02 is owner privacy policy. A test plus a table row whose `why` links #9 holds it.
  - An ADR's extra content is narrative context and rejected alternatives. Here that already lives in the issue thread or research note that `why` links.
- **The 5 reversals:** an ADR adds nothing beyond its `enforced-by` field (M4).
- **The evidence on agents is weak and points to placement.** Rules naming a command or path keep about half their effect when loaded on demand. Recorded rationale helps on decisions invisible in code ([r1-tools-against F6](r1-tools-against.md), [r1-adrs F4](r1-adrs.md)). Both favour a short "why" next to the rule, not a separate document.
- **Outside this repository, the bottleneck is upkeep.** Conformance checking is "often not automated", partly because of "the high cost involved in setting up and maintaining tool-specific … test specifications" ([A Unified Approach to Architecture Conformance Checking](https://doi.org/10.1109/wicsa.2015.11), Caracciolo et al., WICSA 2015, abstract). A 73-study mapping of erosion finds that non-technical causes need "the same attention as technical reasons" ([Understanding software architecture erosion: A systematic mapping study](https://doi.org/10.1002/smr.2423), Li et al., JSEP 2022, abstract). A table that one check reads keeps upkeep at one row per decision.

## Recommendation: the minimum set (BELIEVED unless marked)
1. **Test or hook first** for any decision that code can check, as today (10 of 23). Add D02's test, and a hook that denies `curl`/`wget`/WebFetch to the index hosts `dev lit` wraps (OpenAlex, Semantic Scholar, Crossref, Europe PMC, the arXiv API), with allow-list tests modelled on `GIT_CONFIG_READS`. Hook false blocks have happened twice here, so the allow cases are not optional.
2. **One decision table plus one check** (a warning until a held-out replay; see r2-replay F7). Put the table in `.devkit/decisions.toml`, or as `[tool.devkit]` data. Each row holds `id`, `rule` (one line), `why` (an issue, comment or note link), `enforced-by = [{path, literal}]`, and `status = active | superseded`, with `by` required when superseded.
   - **What the check tests:** each active literal is present in its path. A removal must flip the row in the same PR, which makes a reversal explicit, the way [[raise]] does for budgets.
   - **Index:** `dev docs` renders `docs/generated/decisions.md`, linked and never imported, so it adds 0 always-loaded words. `board new` may print matching rows as an advisory (the project-B duplicate).
   - **ADRs:** this table replaces them for agent-made decisions. A project that keeps ADRs for owner policy points `why` at the ADR, and the table enforces it.
3. **`board lint` warning:** a lead or owner comment that is newer than the last body edit and says DONE WHEN, scope or decision (C3, C4). It is a warning until R2b measures its precision.
4. **Not now:**
   - Path-scoped rules for decisions (0 of 5; cold on C1). Revisit them for per-area conventions only if a measured miss rate below about 5% holds on this harness's tool mix.
   - A template ADR directory: no reversal prevented beyond the marker, and the largest drift surface.

**What would change this:** a measured re-litigation rate of owner-policy decisions (the duplicate-issue type) above about 1 in 10 decision-touching runs; or owner-policy decisions with rejected alternatives that agents keep re-proposing despite a table row (then add a short ADR body behind `why`); or a Claude Code change that loads path rules on Bash writes and Grep.

## Gaps seen, not closed
- One rater for M1's holder coding and M4's prevention calls; no second coder.
- The probe has small n: Haiku 7 loads, Opus 3. Compliance under the harness's real prompts (which say tool output is data) is untested. Whether a hook-rewritten Read (read_guard's `updatedInput` limit) still triggers was not probed; plain denial does not.
- The Bash-write heuristic's precision is 16/30 on one sample. Cold-write share is computed only on 619 extractable targets.
- The marker replay has survivorship bias: 17 of 18 literals were chosen from today's text. R2b should replay literals picked at decision time.
- The hooks doc was read only to 100k of 269k characters.
- The sub-agents docs do not document path-scoped rules in subagents. The behaviour is VERIFIED on 2.1.296 only, and the docs could change.

## Sources fetched
- Claude Code docs: [memory](https://code.claude.com/docs/en/memory) (rules, path-specific rules, imports, auto memory, compaction), [sub-agents](https://code.claude.com/docs/en/sub-agents) (what loads at startup, hooks in subagents), [hooks](https://code.claude.com/docs/en/hooks) (event and decision tables; first 100k chars), [large-codebases](https://code.claude.com/docs/en/large-codebases) (per-directory CLAUDE.md vs path-scoped rules), fetched 2026-10-13.
- [A Unified Approach to Architecture Conformance Checking](https://doi.org/10.1109/wicsa.2015.11) (Caracciolo, Lungu, Nierstrasz, WICSA 2015; abstract via `dev lit get`).
- [Understanding software architecture erosion: A systematic mapping study](https://doi.org/10.1002/smr.2423) (Li, Liang, Soliman, Avgeriou, JSEP 2022; abstract via `dev lit get`).
- [import-linter](https://github.com/seddonym/import-linter): GitHub API and PyPI JSON, 2026-10-13.
- Repository records: agent-harness and project B issues (two project-B issues, private; [#7](https://github.com/Marius-Juston/agent-harness/issues/7), [#45](https://github.com/Marius-Juston/agent-harness/issues/45), [#188](https://github.com/Marius-Juston/agent-harness/issues/188)) via `gh`, git history, and local session transcripts.
- Round-1 evidence reused, not re-fetched: [r1-adrs](r1-adrs.md), [r1-tools-against](r1-tools-against.md), [r1-taxonomy](r1-taxonomy.md).
