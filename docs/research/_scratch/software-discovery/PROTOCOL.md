# Evaluation protocol for `dev reuse` ranking (pre-registered by the lead, 2026-10-13, before any tuning)

Owner 2026-10-13: the evaluation must be robust and must not tune the method to its own validation set.

1. **Frozen split before tuning.** Split the benchmark needs once, with a fixed seed, into TUNE (about 40%) and TEST
   (about 60%), stratified by gold source. Commit `data/split.json` and its SHA-256 in this file *before* the first
   tuning run. TEST is not run until step 5. The lead's 16 pilot needs were used to design the method: they may
   only be in TUNE.
2. **Two independent gold sources.** At least one TEST stratum comes from a source not used in TUNE (e.g. TUNE from
   awesome-python categories, TEST also from PyMigBench/LibEval migration targets), so a shift in source is visible.
   Paraphrases for every need are generated once, by a rule written down here, before tuning, and never edited
   after a result is seen.
3. **Small, declared search space.** At most 12 configurations, listed in `data/configs.json` and committed before
   tuning (e.g. PASS-before-HOLD on/off x relevance tier delta in {0.10, 0.20} or none x lambda in {0.15, 0.5, 1}, and
   the shortlist rule). Prefer constants with a cited prior (r1-ranking.md) over fitted ones; anything fitted is
   labelled so in the note.
4. **Metrics fixed in advance.** Primary: hit@3 (a gold library in the top 3, since agents read the top few).
   Secondary: hit@1, hit@10, MRR, and the share of top-3 results that are HOLD or FAIL. Pick the configuration by
   primary metric on TUNE; break ties toward the simpler one.
5. **One shot on TEST.** Run the chosen configuration and the baseline (PR #171 as merged, plus the pilot S6) on
   TEST once. Report paired differences with 95% bootstrap intervals (resampling needs, 10,000 draws, fixed seed)
   and a per-stratum table. If TEST is run again after any change, say so and count it.
6. **Reproducible inputs.** Cache every API answer used in TUNE and TEST in a dated snapshot, so a re-run gives the
   same numbers; a live re-run later is a separate drift check, not a replacement.
7. **Code-snippet cases** (`--from`) form their own TEST-only set: no tuning on them.
8. **Report misses.** List every TEST miss with its cause (not recalled / recalled but ranked low / gated out).

## Amendment 1 (lead, 2026-10-13, before any tuning or TEST run): the real counterfactual
Owner asked whether the tool helps or harms. The baseline that matters is not "no tool" but what an agent does
without it.
9. **Arms on TEST** (all pre-declared): B0 the agent's own pick from memory (the model named, date, a fixed prompt
   with the need only, top 3 names); B1 = B0 filtered by the gates G1-G6; A1 `dev reuse` search alone; A2 search
   pool + B0 names, gated, ordered by the chosen configuration; A3 = A2 shortlist (top 20, with gate results)
   re-ranked by the agent. Primary metric stays hit@3; also report the share of top-3 names that do not exist
   or are FAIL/HOLD (harm).
10. **Decision rule, fixed now.** `dev reuse` search becomes the default in agent prompts only if A2 or A3 beats
   B1 on TEST hit@3 with the 95% bootstrap interval of the paired difference above 0, and its harm share is not
   higher. Otherwise the gates ship as a verifier for named packages and search stays an optional recall aid.

## Amendment 2 (lead, 2026-10-13, before any tuning or TEST run): owner GPU server
Owner has a private server (4x A6000, 256 cores, 10 TiB), so compute no longer limits the index.
11. **Added arms** (declared now; models picked in round 2 from fetched licence and benchmark evidence, free and
   open-weight only, before tuning): A4 an offline index of all PyPI (summary + README from the PyPI JSON API,
   lead-index-cost.md) embedded with a strong open retrieval model, unioned with the A2 pool; A5 = A4 shortlist
   re-ranked by an open cross-encoder. They are compared against B1 and A2/A3 under the same decision rule (10).
12. The index snapshot used for TEST is frozen with the API snapshot (6); model choice and any truncation length are
   fixed on TUNE only.

## Commitment (lead, 2026-10-13 03:40)
- `scripts/data/split.json` SHA-256 `0a3a85c523bc7d0956401cda33981bda50d0311c0defcd99bd5d621e02b7ba04`, committed in the commit that adds this line, before any tuning run.
- TEST run #0 for set C (code cases) happened before this protocol existed: see `scripts/data/TEST_RUNS.log`.

## Amendment 3 (lead, 2026-10-13 03:45, before any tuning or TEST run): is the hard option worth it?
Owner asked whether the index is the best option or something easier is as good.
13. **Simplest sufficient arm wins.** Arms are ordered by cost: B1 (agent pick + gates) < A2 (live search + agent
   names) < A3 (A2 + agent re-rank) < A4/A5 (offline index, GPU-built). An arm is adopted only if it beats the best
   cheaper arm on TEST hit@3 with the 95% paired bootstrap interval above 0 and no higher harm share. The index is
   built and published only if A4 or A5 clears this against A3.
14. Report TEST results separately for needs whose gold is a top-1,000 PyPI package by downloads and for the rest
   (long tail), since an agent's memory is expected to do well on the first group and worse on the second.

## Amendment 4 (lead, 2026-10-13, before any tuning or TEST run): index arms fixed from r2-pipeline.md
15. Index arms, cheapest first (all recall stages; ranking stays `dev reuse` gates + Q + the TUNE-chosen order):
   P1 BM25 (bm25s) + potion-retrieval-32M, RRF k=60, CPU only; P2 (= A4) BM25 + Qwen3-Embedding-0.6B, RRF k=60;
   P3 (= A5) P2 re-ranked by Qwen3-Reranker-4B on the owner's server; P4 P2 over LLM-generated package profiles.
   Each is unioned with the A2 pool. The corpus is open-index/open-pypi caught up from the PEP 691 simple index.
16. **Headroom check on TUNE before building P2-P4** (threshold fixed now): if fewer than 10% of TUNE needs have a
   gold library in P1's top 100 that is absent from the A2 pool, stop at A3 and build no GPU index. Report the
   share either way.
17. Order of the tuning grid (at most 12 configurations in total across amendments 1-4), written to
   `scripts/data/configs.json` and committed before its first run.

## Amendment 5 (lead, 2026-10-13, after TUNE, before any TEST run of A or M): TEST execution details
18. Chosen ranking: R4 (`scripts/data/chosen.json`, committed with this amendment). Lambda 1.0 was fitted on TUNE.
19. TEST arms: B0, B1, A1 (R0 and R4), A2 (R4), A3. Per amendment 4 rule 16 (TUNE headroom 0.047 < 0.10) no GPU
   index is built; P1 runs on TEST as an **exploratory** arm only, reported separately and never used in a decision.
20. B0 on TEST reproduces the TUNE method: one tool-less `claude -p --model claude-opus-5-5` call from an empty
   directory per batch, batches of at most 64 needs (TEST: 2 batches of 56), same prompt file. A3: batches of 16, as
   on TUNE.
21. Harm stays as pre-registered (top-3 names that do not exist or are HOLD/FAIL; copyleft FAIL counts). A secondary
   harm share without licence outcomes is also reported.
22. Validity fix applied to every arm alike: on M needs the migration source package is excluded from the candidate
   list before scoring (it is the thing being replaced, not an answer).

## Amendment 6 (#175 builder, 2026-10-13, before any run on set D): held-out code set D for `--from`
Owner: a new held-out set of at least 20 code cases (not set C), pre-registered, gold not written by us, TEST once.
23. **Cases.** Commits found with GitHub's commit search for messages like "replace custom ... with <library>"
   (queries named retry/tenacity, slugify, natsort, platformdirs, humanize, python-dateutil, filelock, cachetools,
   tabulate, pathspec, rapidfuzz, charset-normalizer, more-itertools, python-dotenv, xmltodict, unidecode, inflect,
   tqdm, packaging, semver, croniter, dateparser, bleach, jsonschema, deepdiff, deepmerge, psutil,
   sortedcontainers, email-validator, python-magic, wcwidth, diskcache, ftfy, pyparsing). A case is a commit whose
   message names the PyPI library that replaced a hand-written Python helper; the helper (the function or class
   the commit deletes or rewrites to call it) is read at the commit's first parent. Gold is that library only:
   the commit's author named it, the case author (the #175 builder) picked the helper. The rule, the exclusions
   (the helper's text names the gold, i.e. leakage; symbol missing; not on PyPI) and the caps (3 per library, 2
   per repository) are in [build_code_cases_175.py](scripts/build_code_cases_175.py), whose docstring held them
   before it first ran; this amendment was written after the build and before any run. 31 picks, 6 dropped for
   leakage, 25 kept.
24. **Arms.** old = `need_from` of main @ f1cfce7; new = #175's `need_from` (committed in dffdbf7 before set D
   was built). Both go through the same current `reuse.search` (R4, n = 10, pypi, model2vec), run live once, in
   one session, interleaved per case ([run_code_175.py](scripts/run_code_175.py)).
25. **Metrics.** Primary hit@3; also hit@1, hit@10, MRR@10. Paired hit@3 difference new - old with a 95%
   percentile bootstrap (10,000 resamples of cases, seed 175). Every miss is listed. Set D is TEST only:
   nothing in `need_from` changes after its run without a new set.
26. **Frozen.** `scripts/data/code_cases_175.json` SHA-256
   `0851961f48070c76e26166fc7a298e5f5c7e4659058e5704aab690f82b428d1a`, committed before the run (item 29).
27. **Scrub fix after the run (disclosed).** That file kept each commit's full message, which carries third-party
   e-mail addresses and links, so `tests/test_scrub.py` failed. The `message` field was removed after the run and
   nothing else changed (load, pop `message`, dump with the same indent); `gold_by` keeps each message's first
   line, and no arm reads `message`. That gave SHA-256
   `e4a562498555c7147c059901722711e0c3c6ad14ab1c069862f39b49f705cee0`; the run's input is the first hash.
28. **No third-party code stored (disclosed, after the run).** The helpers' code has no licence on record here, so
   the lead asked that this repository keep references only. Each case's `text` was replaced by `text_sha256`, the
   SHA-256 of exactly the text the run used; [run_code_175.py](scripts/run_code_175.py) now fetches the file from
   raw.githubusercontent.com at the pinned parent commit, cuts it as the builder does, and exits on a hash
   mismatch (`--verify-only` checks all 25 without searching: 25 ok on 2026-10-13). The run's outputs dropped
   their need strings (docstring and comment words), keeping each need's word count. New SHA-256:
   `code_cases_175.json` `08fa836d2e74199f0ac1fba53372c4e91182038c4706da667a1de8ff67b1475f`,
   `test-code-175-2026-10-13.json` `e26197f708177736b069736434a0bf57290938f3afccd02fc58a8d181807022d` (was
   `bf2a22f4…cd36`), `test-code-175-2026-10-13.md` `0f76521706d7f17095e2230f061a1cde3c86eeb121e127289b4afc92bfdf5d33`
   (was `3ab99b90…c36f`). Scores and ranks are unchanged.
29. **Squashed history (disclosed).** At the lead's request the branch was squashed into one commit, so neither
   the third-party messages nor the code reach `main`. The pre-squash commits are not published, so git cannot
   show the order any more; as recorded before the squash: need_from committed 10:55:06 (dffdbf7), set D with the
   first hash committed 11:15:38 (cb49053), the run logged 11:15:42 in `data/TEST_RUNS.log`, results committed
   11:59:57 (e8e0e6d), all 2026-10-13 -0400.
