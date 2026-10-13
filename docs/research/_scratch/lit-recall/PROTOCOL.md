# Evaluation protocol for literature-search recall (pre-registered 2026-10-13, before any query or arm run)

Issue #192, modelled on [the #169 protocol](../software-discovery/PROTOCOL.md): frozen split, declared arms, metrics
fixed in advance, TEST run once, paired bootstrap intervals, a per-domain table, every miss explained.

1. **Units and gold.** A unit is one merged research note with a question written before its research. Units: this
   repository's notes A1-A4 (`scripts/common.py`) and nine notes of one private consuming project, code-named
   P01-P09 (the mapping, their question text, queries, gold titles and raw results stay in the gitignored private
   directory named in `scripts/common.py`, since this repository is public and `tests/test_scrub.py` forbids that
   project's words). Excluded: notes with no pre-research question text, and notes citing fewer than 3 papers.
   Gold = every work the merged note (the synthesis note only, not its scratch files) links or names by DOI, arXiv
   id, PMID or PMCID (`scripts/build_inputs.py`), resolved to one record per work through OpenAlex (via the shared
   `devkit.lit` client) or, for arXiv ids OpenAlex lacks, the arXiv API; records of type dataset, software,
   erratum or paratext, and ids that resolve to nothing, are dropped and counted. Papers a note cites only by a
   publisher or PDF link with no such id are not gold (a known bias toward indexed works; reported).
2. **Question text.** The unit's `QUESTION.md`, verbatim; for P02 the round-2 question its v2 was built from; for P07,
   which has none, its issue's title and Context section (written before the research).
3. **Frozen split.** `scripts/make_split.py`: stratified by domain (EDU vs the rest), 40% TUNE, 60% TEST, seed
   "192-split". TUNE A3 P03 P04 P05 P07; TEST A1 A2 A4 P01 P02 P06 P08 P09. Domain is fixed per unit before any
   result: CS (software engineering, agents, ML) A1-A4 P02 P05 P07; EDU (education, psychology, health) P01 P03 P04
   P06 P08; SEC (security, privacy, law) P09.
4. **Queries.** 8 per unit, written once by `scripts/run_agent.py` with `scripts/data/query_prompt.md` (a fresh,
   tool-less `claude -p --model claude-opus-5-5` call per unit from an empty directory; it sees the question only).
   Never edited after a result is seen. Every arm runs the same 8 strings unchanged (a real agent would adapt its
   syntax per tool: a stated limit).
5. **Arms.**
   - **L0** `dev lit search -n 20` as on main (commit f1cfce7): `--source auto`, no years, through `devkit.lit`'s
     shared client and cache. PR #190 is open and its diff changes only `get`/`lookup` and record fields, not the
     search URL, merge or order, so a "with #190" arm returns the same list by construction and is not run; if it
     merges before TEST, the search functions are diffed again and the result stated.
   - **L1-L4** (TUNE only, the whole tuning space): `--source` = L1 `all`, L2 `openalex,crossref`, L3
     `openalex,arxiv`, L4 `openalex,europepmc`, each -n 20 (interleaved by `Client.search`). **L\*** = the arm
     among L0-L4 with the highest TUNE macro recall@20; ties go to L0.
   - **W** Claude Code's WebSearch tool, `mode: standard`, each query verbatim, run by `claude -p --model haiku`
     allowed only that tool, from an empty directory, one call per unit; results are the tool's own result blocks
     (title, URL) read from the call's stream-json output, in their order. It returns about 10 links per query, so
     W is compared at k = 10; its @20 equals its @all.
   - **X** the arxiv MCP server's `search_papers` (`uvx arxiv-mcp-server`, version recorded), each query verbatim,
     `max_results` 20, `sort_by` relevance, `abstract_mode` none, no categories or dates, called over stdio by an MCP
     client.
   - Exploratory, never used in a decision: **U** = L\* + X and **V** = L\* + W (per-query union of both lists).
6. **Matching** (`scripts/score.py`, applied identically to every arm). A result hits gold g when: its DOI, arXiv
   id, PMID, PMCID or OpenAlex id (from its ids or its URL) equals g's; or its normalised title (lowercase
   alphanumerics) equals g's, or one contains the other with the shorter at least 30 characters (a trailing ellipsis
   is stripped first); or, as a last rule for results without ids, g's first-author family name and year both appear
   as words in the result's title and URL and no other gold work of the same unit matches that way. TUNE may refine
   these rules only after a hand audit of TUNE matches and misses (amendments dated, before TEST).
7. **Metrics, fixed now.** Per unit u, arm a, depth k: recall R = share of u's gold works hit by a result at rank
   <= k of at least one of u's 8 queries. **Primary: macro recall@20** (mean of R over units). Secondary: macro
   recall@10, micro recall@10 and @20 (pooled over gold works), recall by the gold work's OpenAlex domain and by
   whether it has an arXiv id, the share of gold found by exactly one arm, and results returned per query.
8. **One shot on TEST.** L0, L\*, W and X (and U, V) run on TEST once, with `dev lit status` recorded before and
   after (Semantic Scholar was DOWN at protocol time: `dev lit status`, 2026-10-13, "rate limited ... daily budget
   spent"). Paired differences get 95% percentile intervals from 10,000 bootstrap draws with seed "192-boot":
   primary over units (cluster bootstrap, 8 TEST units, so wide), secondary over gold works. A rerun of TEST after any
   change is stated and counted in `scripts/data/TEST_RUNS.log`.
9. **Reproducible inputs.** Every raw answer of every arm is saved (public units under `scripts/data/runs/`, private
   ones in the private directory) and scoring reads only those files; a live rerun is a drift check.
10. **Decision rules, fixed now.** (a) If W or X beats L\* on TEST macro recall@10 (X also @20) with the paired 95%
   unit-level interval above 0, the researcher instructions name that tool next to `dev lit search` for discovery.
   (b) If L\* is not L0 and beats L0 on TEST macro recall@20 with the interval above 0, `dev lit`'s default sources
   change to L\*'s; otherwise they stay. (c) Otherwise no tool change; U and V are reported as what a combined pass
   adds.
11. **Report misses.** For TEST, every gold work no arm found is counted by cause: not in any arm's results / in a
   result list below k / matched only by a rule another arm lacks; a sample of 20 misses is checked by hand.

## Commitment
- `scripts/data/split.json` SHA-256 `f8d851aa690908f25e1b83e09703051a5ae72ee71c17981495f14ebee43a6871`, committed
  with this file, before any query is written or any arm is run.

## Amendment 1 (2026-10-13, after queries and gold were built, before any arm run)
12. arXiv ids that OpenAlex lacks are resolved from DataCite's record of their arXiv DOI (10.48550/arXiv.<id>),
   not the arXiv API: the arXiv API throttled this machine's IP throughout the resolution run (`dev lit status`:
   "arxiv DOWN rate limited"). Title, first author and year are the same fields.
13. Frozen inputs (SHA-256): `queries_A.json` f0c073d6234f59fe2949598feefad7d45f4509be551c7fbcce94e78e834886c6,
   `gold_A.json` 5a5436513d189e99cd2b401347f05d898fc9b24fe7e29b6175942f0ac41bb8b3; private `questions_P.json`
   1604c8b6c9ca9be198e5e26d961b65dfca5dbc8cf0dc67de2ef2d4f25aec1180, `queries_P.json`
   16659faca8afefb9dc46a67b5daa918ed9b72dcc155387c6735d53e2c49763bd, `gold_P.json`
   a2082ed9579271109631d3f9e620ab4049fbca1d234b0d4eae7e345c3685915c. Gold: 885 works over 13 units
   (`gold_counts.json`).

## Amendment 2 (lead request, 2026-10-13, after L0 ran on TUNE, before any other arm run and before TEST)
14. Two arms added from issue #192's comment (PR #202, `dev lit search --also`, issue #194). L0 had already run on
   TUNE when they were added, so both are **exploratory**: reported, never used in a decision rule.
   - **L0-1**: each unit's first query alone, L0's settings (`-n 20`, `--source auto`). It is L0's saved list for
     that query (L0 runs each query alone with these settings), so it needs no separate run.
   - **LA**: the first query with the other 7 as `--also`, `-n 20`: one merged list per unit, from
     `devkit.lit.Client.search(q0, ..., also=tuple(qs[1:]))` on PR #202's branch while it is unmerged (the commit is
     recorded), with the same S2 and cooldown handling as the other L arms. Run on TUNE and TEST.
   - Reported: paired LA - L0-1 at recall@10 and @20 (unit and gold-work bootstrap, rule 8), and L0 (8 lists of 20)
     as the ceiling LA compresses into 20.

## Amendment 3 (2026-10-13, after the TUNE hand audit of rule 6, before any TEST run)
15. TUNE audit (`score.py tune --audit`, private file): all 13 distinct non-identifier matches were correct by hand;
   in a seeded sample of 40 unmatched web results one was a gold work the rules missed, because OpenAlex stores
   that work's title without its subtitle ("Learning Styles" for "Learning Styles: Concepts and Evidence"). Fix:
   the title rule also compares each gold work's **link texts in its note** (`scripts/alt_titles.py`: markdown
   links whose URL names one of the work's ids, at least 2 words). The author-year rule never fired on TUNE; it is
   kept unchanged. Many web results are repository or PDF pages whose title is a host name or a file id; those
   cannot be matched by any rule, so W's recall is a lower bound (stated in the note).

## Amendment 4 (2026-10-13, TUNE choice, before any TEST arm was scored)
16. The arXiv API throttled this machine's IP for the whole session (`dev lit status`: "arxiv DOWN rate limited",
   shared cooldown streak 9, about 512 s per hold; four other arxiv MCP server processes were running). L1 and L3
   (which include arXiv) could not finish one TUNE query in 25 minutes and are **not measured**; their partial files
   were deleted. L\* is chosen among L0, L2 and L4: TUNE macro recall@20 L0 0.141, L2 0.139, L4 0.138, so
   **L\* = L0** (also the tie rule). U and V are therefore L0 + X and L0 + W.
17. X runs on TEST only (it has no TUNE choice to make), retrying each rate-limited answer after its
   retry_after_seconds up to 60 times. If arXiv stays throttled past this session, X is reported as not measured
   and a follow-up issue reruns it from the saved queries; no other arm waits for it.
18. W ran on TEST before this amendment (`TEST_RUNS.log`) and was not scored before L\* was fixed.

## Amendment 5 (2026-10-13, after the TEST run; nothing here changes a TEST number)
19. PRs #190 (11:01 EDT) and #202 (11:20 EDT) merged before L0 ran on TEST (11:41 EDT) from this branch's older
   `devkit/lit.py` (f1cfce7). `scripts/check_main_l0.py` re-ran all 64 TEST queries with the merged `lit.py` from
   the same cache: 64 of 64 lists are identical, so L0 is also "dev lit search as on main after #190 and #202". The
   merged search path equals PR #202's 53a78f9 function for function, so LA is the merged `--also`.
20. Raw answers of every unit (public ones too) moved to the private directory: third-party result titles hold
   words `tests/test_scrub.py` forbids. `scripts/manifest.py` commits their SHA-256 in `data/runs_manifest.json`.
21. The TEST audit found one wrong author-year match (W, unit A1: a blog host name containing the first author's
   surname, with the year in its URL). Rules are not changed after TEST; the note reports W with and without it.

## Amendment 6 (2026-10-13, post-audit record; changes no number)
22. The branch was squashed to one commit so no public commit holds a third-party title the scrub forbids. The
   pre-squash commits that time-stamp this protocol (e766bc2 11:00 protocol and split; fe29295 11:04 queries and
   gold; 88397c9 11:18 amendment 2; e606f0c 11:26 amendment 3; 7c72628 11:41:18 amendment 4, after W began on TEST
   at 11:38 unscored, before scoring at 11:43; 7eed68b 11:57 TEST complete) are listed in
   `scripts/data/TEST_RUNS.log` and kept as a git bundle in the private backup (never pushed).
