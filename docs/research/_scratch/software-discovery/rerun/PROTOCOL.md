# Rerun protocol for the #169 discovery benchmark (#176), pre-registered 2026-10-13 before any rerun arm ran

#169's [PROTOCOL](../PROTOCOL.md) binds this rerun: a frozen split before any run, gold that the researcher did not
write, at most 12 configurations declared in advance, hit@3 as the primary metric, harm reported with it, paired
95% bootstrap intervals, one shot on TEST, and the simplest arm that no costlier arm beats wins. Below are only what
is new or changed. Scripts are in [scripts/](scripts/) and frozen data in [data/](data/).

Scope of this pass (lead, 2026-10-13): commit this protocol, the split and the configurations, then run TUNE only.
The lead triggers TEST after the `dev reuse` fixes #179, #182 and #175 merge (rule R6). #176's first DONE WHEN item
(B0 and A3 from a different model, or a model at least 6 months newer, on #169's frozen TEST snapshot) is out of
scope: the lead set the model under test to the current one (R4).

## Needs and gold (R1-R3)
- **R1 Strata. None of the needs is in #169's split** ([build_needs.py](scripts/build_needs.py) docstring).
  - **A2: fresh awesome-python, 38 needs.** These are the eligible subcategories of awesome-python@ba49ec7 that #169
    did not draw, under #169's parse rule. The section "Miscellaneous" is dropped, as #169 drops subcategories with
    that name. Gold is every GitHub repository the list files under the subcategory.
  - **M2: fresh PyMigBench, 57 needs.** These are the eligible migration sources that #169 did not sample. Gold is
    the libraries that real projects migrated to. The source package is removed from every arm's list before the cut,
    as in #169 rule 22.
  - **L: latest, 105 needs.** Each need's gold is a library first released on PyPI after the model's training
    cutoff ([build_latest.py](scripts/build_latest.py), rules L1-L6 in its docstring).
    - L1: the repository is public, not a fork, not archived, Python, created 2026-07-01..2026-10-12, with at least
      100 stars at fetch time ([fetch_new_repos.py](scripts/fetch_new_repos.py), 2,308 repositories, file SHA-256
      `0efb8366…6d64a`, kept outside git).
    - L2: identity runs from repository to package. The PyPI project must list the repository.
    - L3: every package of the repository was first uploaded on or after 2026-07-01.
    - L4: the need text is the package's own PyPI summary with its names masked. The masking removes the package
      name, the repository name and the owner.
    - L5: gold is the repository plus every package name.
    - L6: at least 2 releases. This drops 16 placeholders.
    - Selection: 2,183 of the 2,308 repositories have no linked PyPI package. 3 have a pre-cutoff release, 1 need was
      too short after masking, and 16 have only one release. First uploads run from 2026-07-01 to 2026-10-09, and the
      median repository has 259 stars.
- **R2 Need text comes from outside sources only.** Nothing is paraphrased, by hand or by a model.
  - A2: the label (#169's q0) and the list's own section description.
  - M2: the source package's PyPI summary and keywords (as #169).
  - L: the masked summary and masked keywords.
  - So the researcher's influence on inputs is smaller than in #169, which used hand-written paraphrases. As a
    result, A2 queries are not comparable to #169's A queries.
  - Agents see opaque ids for M2 (`M2-xx`) and L (`L-xxx`). A2 ids are the labels, as in #169.
- **R3 Split.** Within each stratum, the ids are sorted and then shuffled with `random.Random("176-split")`. The first
  round(0.4 n) go to TUNE and the rest to TEST.
  - TUNE has 80 needs (A2 15, M2 23, L 42). TEST has 120 needs (A2 23, M2 34, L 63).
  - `overlaps_tune` holds 4 TEST needs whose gold also appears in TUNE gold.
  - `overlaps_169` holds 25 needs whose gold also appears in #169 gold: M2 20 and A2 5. Many migrations share
    targets such as click.

## Model, arms and prompts (R4-R5)
- **R4 Model under test.** claude-opus-5-5. Each call's own `modelUsage` report is recorded in the answer files. The
  stated knowledge cutoff is June 2026, so "after the training cutoff" means on or after 2026-07-01. This rests on
  the model's system prompt and is ASSUMED to bound its training data. Runs: TUNE on 2026-10-13; TEST on the date
  the lead triggers it, which is recorded. B0W's WebSearch tool also calls claude-haiku-5-5 internally, and the
  report records that too.
- **R5 Arms** ([configs.json](data/configs.json); scored by [score.py](scripts/score.py)).
  - **B0** is the agent's first 3 names. Each call is a tool-less, fresh `claude -p` from an empty directory
    ([run_agent.py](scripts/run_agent.py)). The prompt is #169's [b0_prompt.md](../scripts/data/b0_prompt.md),
    unchanged, for A2 and M2, and [b0_latest_prompt.md](data/b0_latest_prompt.md) for L. Batches hold at most 64
    needs.
  - **B1** is B0 checked by `dev reuse check`: FAIL, UNVERIFIED and NOT FOUND names are dropped and HOLD names are
    kept, as in #169.
  - **B0W** is the same agent with only WebSearch and WebFetch (`--tools WebSearch,WebFetch --allowedTools
    WebSearch,WebFetch`). It is called once per need, the realistic builder case, with 4 calls in parallel. Its
    prompts are [b0w_prompt.md](data/b0w_prompt.md) and [b0w_latest_prompt.md](data/b0w_latest_prompt.md). **B1W**
    is B0W gated the same way as B1.
  - **A1** is shipped `dev reuse` search alone, top 10.
  - **A2** is A1's judged shortlist before the cut to n, plus the B0 names. The names go through `dev reuse check`,
    with R and S = R + λ·Q computed as search computes them. Arms keep PASS, flag and HOLD names. The list uses the
    shipped order (PASS before HOLD, then S) and is cut to 10. This is #169's "A2 R4". R4 is now the shipped code,
    so no replay of the old configurations is needed ([snapshot.py](scripts/snapshot.py)).
  - **A3** is the agent's pick from A2's top 20, shown in #169's shortlist-line format. The prompt is #169's
    [a3_prompt.md](../scripts/data/a3_prompt.md) for A2 and M2, and [a3_latest_prompt.md](data/a3_latest_prompt.md)
    for L. Batches hold 16 needs.
  - The prompts for L and B0W are derived from #169's by the substitutions stated in their headers. L's time-based
    wording is the variation that [Twist et al. 2025](https://arxiv.org/abs/2509.22202) found induced library
    hallucinations in up to 85% of tasks (abstract, SOURCED).

## Execution (R6-R8)
- **R6 `dev reuse` version.** TUNE uses `devkit/reuse.py` at main `28faf8b7` (SHA-256 `7f1cd2ef…b701d`; the #180 G5
  fix is in).
  - TEST uses the first `main` commit that contains the merges of #179 (help text), #182 (G5 on undeclared
    mixed-licence lists) and #175 (`--from` need text). That commit and the reuse.py SHA-256 are recorded in every
    TEST snapshot (`devkit`).
  - Before TEST, TUNE is snapshotted and scored again on that commit. This rerun tunes nothing, so the second TUNE
    run only checks that nothing broke, and both TUNE results are reported.
  - #175 and #179 do not touch text search. #182 can change G5 outcomes.
  - Snapshots, the API cache and the L universe go to `<git-common-dir>/devkit/bench/` with their SHA-256, as in
    #169.
- **R7 Metrics.** As in #169: hit@3 is primary, and hit@1, hit@10 and MRR@10 are secondary.
  - Harm is the share of the top 3 that is HOLD, FAIL, UNVERIFIED or NOT FOUND. It is also reported without G5
    (licence).
  - **On L, the decision uses harm without G3.** G3 holds any package first released less than 90 days ago, and a
    "latest" request asks exactly for those. About 94 of 105 L golds were under 90 days old on 2026-10-13.
  - The 95% intervals come from `recall_bench.boot`: 10,000 resamples over needs, seed 169, paired for differences.
- **R8 Configurations.** [configs.json](data/configs.json) declares 8 configurations: B0, B1, B0W, B1W, A1, A2, A3
  and P1. None is tuned. TUNE is a dry run that checks the pipeline, measures cost and decides R10.
  - TUNE may expose an execution bug, such as a parse failure, a crash or a missing answer. The fix is written here
    as an amendment before TEST and applied to every arm alike.
  - Nothing that changes how an arm chooses is changed after TUNE.

## Decision rules (R9-R12)
- **R9 Simplest unbeaten gated arm.** The order of cost is B1 < B1W < A2 < A3 (< P1, only if R10 admits it). Start
  with W = B1.
  - For each costlier arm X in that order, X replaces W if two conditions hold. First, the 95% interval of the
    paired hit@3 difference X − W lies above 0. Second, the paired harm difference is at most 0 (point estimate, as
    #169 read "no higher harm").
  - The rule is applied separately to **main** (A2 + M2) and to **L**.
  - #169 rule 10 (search becomes the default only if A2 or A3 beats B1) holds on a stratum only if R9 ends at A2 or
    A3. B1W ending the chain means the agent's own web search, verified by `dev reuse check`, is the recommended
    default.
  - The ungated B0 and B0W are reported but never chosen.
- **R10 Index headroom (#169 rule 16, threshold unchanged).** On TUNE, count the share of needs with a gold library
  in P1's top 100 that is absent from the A2 pool (everything search recalled plus the B0 names)
  ([p1_query.py](scripts/p1_query.py)). P1 is #169's CPU index over the PyPI corpus caught up to 2026-10-13, so L
  gold is in it.
  - At 0.10 or more on main or on L, P1 becomes a TEST arm on that stratum. It is A2's list plus P1's top 10 names
    (tag `p1`, checked), in shipped order, and it is placed last in R9.
  - P2 (GPU) is built only if P1 wins R9 on TEST, as #169 section 7 says.
  - Caution, BELIEVED: L's need text is the gold's own summary, and the index stores that summary. So L headroom and
    any lookup arm's L score are upper bounds for a need phrased in the user's own words.
- **R11 One shot on TEST.** Only the lead triggers it. Every TEST scoring is logged in `data/TEST_RUNS.log`. TEST
  runs every agent arm fresh, and no TUNE answer is reused.
- **R12 Reporting.** Each stratum gets a table, plus main split by head and tail (top-1,000 PyPI gold, #169 rule 14).
  - Sensitivity analyses: main without `overlaps_169`, TEST main without `overlaps_tune`, and **L without "name
    words"**. That last subset drops the L needs whose text still contains a 4+ letter part of a gold name, such as
    "kifly". The rule is `score.py name_word`; such words are about a quarter of L and mostly descriptive.
  - Misses for B1, B1W and A2.
  - Cost: seconds per need for search, and web searches and turns per B0W call.
  - B0's nonexistent-name rate (NOT FOUND share of all names) on L against main, compared with Twist et al.

## Expectations written before any run (BELIEVED; not decision inputs)
- On main, B1 stays at or near A3, as in #169.
- On L, memory arms score near 0 by construction, B1W leads, and A2 trails because PASS-before-HOLD and G3 sink new
  packages.
- On L, B0's NOT FOUND share rises well above #169's 1.2%.

## Commitment
Every file below is committed before any rerun arm runs. The SHA-256s follow.

```
c03a29b8de08affea66da44d3af811b09f973df09418d27fc6ced337730fd898  data/needs.json
bfb801ef5a3f4dee32395fa5dca9e185f033ed0ac93a2df8296b32d3d32e57ee  data/split.json
5d36a2d2e1942fa9a166b2c78037205d31d212fb90f33cbed8773c96be94d404  data/configs.json
2fc616e3559b318e0b4bdb595bc5828405f215e00890a7bc4bb4bcb83a3c6506  data/latest_needs.json
88caaf57a5745578aeb469cf81f6734612093da82beb20a25a4740cfd90816f8  data/inputs/test_base.json
1cbe0572be35f5790cf42daf9247d804f366389565de9f39a6cb3d95c5764d4b  data/inputs/test_latest.json
20a1125bef74d64e27b9e87b66a9d55d6edaf7e8143637aa457ba9414bead2e1  data/inputs/tune_base.json
c525fdfe971a2ce59db67bdb13950673a9e6b70d6a625cf5eb274b2d22f374c9  data/inputs/tune_latest.json
f6b45d303d78f5fc7df786bde986302177138b66b59d85e2f2661f1cdd7606ef  data/a3_latest_prompt.md
f5d1030f9fc8f41115080f85d2714aef9ac0bd1bfd4b1bb90fe4a4a3427f9b4c  data/b0_latest_prompt.md
e06aac3ac39fb4a4a59344a9b3c358b8cd884cad796eed0c16d5f321f78ab034  data/b0w_latest_prompt.md
4185b1c15ab0a6750f4c9c5b9fbe618a262ea5be543667e5e997110f1248b07b  data/b0w_prompt.md
c55cb9894386da24e3729aeb9f4159d0d981328d4887ba59d914ceae9dc63f2c  scripts/build_latest.py
fdb0cd7534d0eb91e9882f7d2cab1e706be91c1cb9b5ead9c6f0f03c71a00143  scripts/build_needs.py
b02cf7af8f3f7a17b305f8db45e18aff5a4b3190f1026302272ac1cde86fcde2  scripts/fetch_new_repos.py
ce1026bdea97113006e0716289c9e5dbee21e5aa8cb29c433c02450ba0dc3c26  scripts/p1_query.py
03619adb4ea2cfa19fc6647967c45223a0bba5c132ca4695f1450ed6231fc786  scripts/run_agent.py
d7b0944ea59a2cff275816c1195f3872d4e43fd413559e5e2e29d56e0aa2418d  scripts/score.py
ac416bc1001c096f4132f320022cdd951881994e8a634ed14eb26d7b3a218f0d  scripts/snapshot.py
895543744ca52c3e145e8c37203978181014d9bb43a87177001a39481f66de62  ../scripts/data/b0_prompt.md
128c9d4d019f50431f2f8a46f82a041bdb8ce610a24aa79a4983560f55b9e026  ../scripts/data/a3_prompt.md
```

## Amendment 1 (researcher, 2026-10-13, after TUNE, before any TEST run)
TUNE ran once on 2026-10-13. All 80 needs were scored, with claude-opus-5-5 (B0W also used claude-haiku-5-5 inside
WebSearch) and devkit at `28faf8b7`. The report is [tune-report-2026-10-13.md](data/tune-report-2026-10-13.md), and
the answers and A3 inputs are in [data/tune/](data/tune/). The snapshot and P1 top 100 are in
`rerun176-tune-2026-10-13.tar.gz` (SHA-256 `2a40ad9b…d88a5`). The recall_bench cache entries the run added are in
`rerun176-tune-2026-10-13-rbcache-delta.tar.gz` (`7b5ea80f…68363`). Both are in `<git-common-dir>/devkit/bench/`.

1. **Execution fixes to `score.py`, made before the full TUNE score.** A dry run on the first 22 snapshotted needs
   printed numbers, and these fixes followed it. They apply to every arm alike.
   - Gold matching now canonicalises only repositories whose name part equals a gold repository's, as #169's
     `gold_in` does. Before, it canonicalised every repository, about 100 GitHub calls per need, which was too slow.
   - The B0W pair filter crashed (`DECIDE.index("B0W")`).
   - R10 now treats an L need's repository and package names as one gold library. Before, a package-name hit could
     count as headroom even when the pool held the same library through its repository.
2. **R10 fires on both strata.** Headroom is main 4/38 = 0.105 (seleniumbase, argparse, faststream, zvec) and L 7/42
   = 0.167, against the 0.10 threshold. So **P1 is a TEST arm on main and on L**, as pre-registered.
   - For the snapshot, its names are the top 10 `name` fields of `p1_query.py`'s output, passed as
     `--names p1=<{id: [names]}>`.
   - BELIEVED: on L, P1 puts the gold at rank 0 for 6 of the 7 headroom needs, because the need text is the gold's
     own summary. That is the self-retrieval R10 warned of.
3. **R9 has a weakness on L, and this amendment proposes a fix.** It is not applied unless the lead accepts it
   before TEST.
   - R9 compares harm as a share of the top 3, so an arm that names nothing scores 0 harm. B0 and B1 named no
     package for 28 of 42 L needs.
   - So B1W (hit@3 +0.79 [+0.67, +0.90] over B1) failed the harm condition: no-G3 harm was +0.11 [+0.03, +0.19].
     The chain went on to A2 (+0.31 over B1 with lower harm).
   - Proposed reading: on each stratum, the harm condition uses only the needs where both arms named at least one
     package, and n is reported.
   - The outcome motivates this proposal, so TEST reports R9 under both the registered reading (primary unless the
     lead decides otherwise) and the proposed one.
4. **Recommended for the TEST commit: #215.** G5 matches SPDX ids case-sensitively, so `mit` and `apache-2.0` hold.
   That accounts for 24 of 116 "not rated" HOLDs in the TUNE snapshot, and it raises every arm's harm, including
   B1W's harm on L.
5. **Disclosure about B0W versus B0.** On A2 and M2, B0W ran 1 web search in 38 calls. So on main, B0W − B0 measures
   one need per call against a batch of 38, plus sampling, and not the web. On L it ran 57 searches in 42 calls.
6. Nothing else changes. Every arm, prompt, constant and the split stay as committed in `0c92c91`.

## Amendment 2 (lead decision, 2026-10-13, before any TEST run)
1. **R9's harm condition, primary reading:** harm is compared only on the needs where both arms named at least one
   package. `score.py` reports n for each comparison.
   - If no need was named by both arms, the condition is treated as met.
   - The registered all-needs reading is reported as secondary, with its own R9 outcome.
   - Each arm's table now has an `empty` column: the share of needs where it named nothing.
   - Hit@3 stays the primary metric, and on L the harm is still the version without G3 (R7).
2. **P1 enters TEST on main and on L**, as R10 required (amendment 1, item 2).
3. **TEST waits for one commit** that contains #175 and the #215 fix (case-insensitive SPDX ids), plus #179 and #182
   (R6). The lead sends its id, and TEST does not run before then.
4. **TUNE re-scored under this amendment** (offline, same snapshot: [tune-report-2026-10-13-amendment2.md](data/tune-report-2026-10-13-amendment2.md)).
   No pick changes.
   - Main: B1 under both readings.
   - L: A2 under both readings.
   - On L, B1W − B1 harm on the 14 needs where both arms named something is +0.036 [−0.107, +0.167]. Its point
     estimate is above 0, so B1W still fails the harm condition even though its hit@3 gain is +0.79 [+0.67, +0.90].

## TEST commit (lead, 2026-10-13, before any TEST run)
- **TEST runs on agent-harness main `2e559f1`.** It contains #179 and #182 (`0cd1952`), and #175 and #215
  (`47c99ec`).
  - `devkit/reuse.py` at `2e559f1` has SHA-256 `23f4aab3471964e4b3be937fddcc58a91ec33ba4149e2f2cc33d0f877d911919`.
  - The rerun branch merged `origin/main` at `2e559f1`, so its `devkit/reuse.py` is byte-identical. Each TEST
    snapshot records the branch HEAD and that SHA-256.
- **Order of runs.**
  1. The TUNE re-check (R6): TUNE is re-snapshotted on this commit with the TUNE answers already recorded, then
     re-scored. No agent is called again; A3 names that left the shortlist count as dropped.
  2. TEST: fresh B0 and B0W, P1's top 100 (its top 10 names become tag `p1`), the snapshot, A3, and one logged
     `score.py --split test --confirm-test-run`.

## TEST run (2026-10-13, once)
- **Order.** The TUNE re-check on `2e559f1` ran first ([report](data/tune-recheck-2e559f1-2026-10-13.md)). It changed
  no pick: main B1 and L A2, under both readings. Then TEST ran: fresh B0, B0W and A3 answers, which are in
  [data/test/](data/test/), and one scoring run, logged in [TEST_RUNS.log](data/TEST_RUNS.log).
- **Report:** [test-report-2026-10-13.md](data/test-report-2026-10-13.md).
- **Decisions.** R9 picks B1 on main and B1W on L, under both harm readings. #169 rule 10 is not met on either
  stratum. P1 loses on both, so no P2 is built.
- **Deviations: none.** Three cosmetic points:
  - In the sensitivity table "L, no name word", the paired harm is the full harm, not the version without G3. No
    decision uses that table.
  - The local scratch paths in `TEST_RUNS.log` were replaced by `<scratchpad>`.
  - For this public repository's scrub gate, one word of a third-party description in `data/test/a3_in_latest.json`
    became `[redacted]`. The agent saw the original, and scoring does not read descriptions.
- **Archive and replay.** The TEST scores JSON (`test_scores.json`, SHA-256 `626dd12f…579d5`) is now inside
  `rerun176-test-2026-10-13.tar.gz`, which was re-packed with it (SHA-256 `8a434da4…eafa`; the first packing was
  `c5864494…a5768`).
  - `score.py score --split test --replay` re-prints the TEST report from the snapshot and does not write to
    `TEST_RUNS.log`. The replay gives rows identical to the logged run, and it adds the NOT FOUND counts and the
    pool rate for L only ([replay](data/test-report-2026-10-13-replay.md)).
  - `recheck.py` re-computes hit@3 only.
- **History.**
  - **Squash.** The PR branch was squashed into one commit on main to remove a scrub-flagged word, so 0c92c91 (the
    pre-registration, 15:11Z, pushed) is reachable by SHA only. 800b66a (the first TEST commit) still holds that
    word unredacted; it is harmless third-party text, and the commit is no longer on the branch.
  - **Order.** Amendments 1 and 2 were committed at 0150bbb and 34887b7. The TEST commit was recorded at 8ec6b1f
    (16:13Z). TEST was scored at about 17:36Z.
  - **Hashes.** The SHA-256 of `score.py` in the commitment list no longer matches, as expected: amendments 1 and 2
    changed it before TEST.
