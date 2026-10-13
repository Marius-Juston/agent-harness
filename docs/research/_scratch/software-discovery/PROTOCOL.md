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
