# Research: library discovery for reuse-before-build, and the agent surface for discovery tools

Status: v1.1 (2026-10-13), set D added by #175, §11 added by #196 (fit check, research half). Issue: #169. Served #29 (`dev reuse`, PR #171, merged as 22d7ce9 with this note's R4 constants and `dev reuse check`) and the surface choice for `dev reuse`, `dev lit` and the research loop.
Labels: SOURCED = cited and fetched; VERIFIED = measured here (script named; the API snapshots live outside git, in `<git-common-dir>/devkit/bench/`, with their SHA-256 in the round files); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit status` on 2026-10-13 (every round and this synthesis) showed openalex, crossref, europepmc and arxiv up and s2 DOWN (HTTP 429, keyless pool), so Semantic Scholar-only papers and citation counts are missing. Also used: the arXiv MCP (abstracts and single sections), WebSearch and WebFetch for vendor docs, the GitHub REST and GraphQL APIs, PyPI JSON and the PEP 691 simple index, packages.ecosyste.ms, api.deps.dev, api.osv.dev, api.scorecard.dev, the Hugging Face Hub API, ClickHouse's public `pypi` tables, and this machine's Claude Code transcripts. context7 ran out of its keyless monthly quota (200 requests) during round 1. Three rounds plus a pre-registered one-shot TEST; the stop reason is that the decision rules fired (§4).
Companions: [agent-harness.md](agent-harness.md) §2 (a rule broken in a quarter of runs becomes a hook) and §9 (dependency confusion); [free-security-scanning.md](free-security-scanning.md) (OSV and dependency scanning in CI). Round files: [_scratch/software-discovery/](_scratch/software-discovery/QUESTION.md): [PROTOCOL](_scratch/software-discovery/PROTOCOL.md) with amendments 1-6, [gaps-r1](_scratch/software-discovery/gaps-r1.md), [r1-surface](_scratch/software-discovery/r1-surface.md), [r1-ranking](_scratch/software-discovery/r1-ranking.md), [r1-benchmark](_scratch/software-discovery/r1-benchmark.md), [r2-pipeline](_scratch/software-discovery/r2-pipeline.md), [r3-tuning](_scratch/software-discovery/r3-tuning.md) (its last section is the TEST), and four lead measurements ([coverage](_scratch/software-discovery/lead-coverage.md), [data coverage](_scratch/software-discovery/lead-data-coverage.md), [index cost](_scratch/software-discovery/lead-index-cost.md), [prior art](_scratch/software-discovery/lead-prior-art-index.md)).

## 0. Findings in one paragraph
The best way for an agent to find an existing library is to **name candidates from memory and verify them with the gates** (arm B1), not to search first. This was settled by a protocol pre-registered before any tuning, with a frozen TUNE/TEST split and one shot on TEST: 112 needs whose gold comes from awesome-python subcategories (72) and real PyPI migrations in PyMigBench (40), with claude-opus-5-5 on 2026-10-13. B1 scored hit@3 0.83 [0.76, 0.90]. `dev reuse` search plus the agent's names, gated and ordered by the tuned ranking (A2), scored 0.71, paired −0.12 [−0.19, −0.05]. The same shortlist re-ranked by the agent (A3) tied B1 exactly: +0.00 [−0.05, +0.05], 4 wins and 4 losses. Search alone, as PR #171 was first built (A1 R0, commit 4d61af0), scored 0.33. So neither search arm cleared the pre-registered bar, and search stays an optional recall aid (VERIFIED, [r3_score.py](_scratch/software-discovery/scripts/r3_score.py)). The agent's memory is not the risk; policy is: of 328 TEST names, 4 did not exist, 25 failed a gate (licence or archived; at least 4 of the licence FAILs are a bug, #180) and 61 were HOLD (dormant or unclear licence). A3 cuts the HOLD/FAIL share of the top 3 from 0.19 to 0.11 at equal hit@3. That trade-off is outside the rule and is left to the owner (#177). An offline index of all PyPI adds nothing once the agent's names are in the pool: TUNE headroom 0.047, under the pre-registered 0.10, and the exploratory CPU index P1 tied A2 on TEST. So no GPU index is built and nothing is published to Hugging Face. The tuned ordering R4 (PASS before HOLD, union shortlist, λ = 1.0, fitted) beats PR #171's first-build order by +0.09 [+0.02, +0.16] on the same recall; the merged `dev reuse` ships it, and `uv run dev reuse check <name>...` runs G1-G6 on named packages. **Surface:** keep `dev reuse` and `dev lit` as CLIs; no MCP server. Enforce reuse with a `uv add` guard hook running gates G1, G2 and G5 (#172) and a `Reuse:` line in the PR summary that the completion gate checks (#173), because builders looked for a library before a new module in only 2 of 6 runs and model-invoked skills trigger worst. `dev lit` loses traffic to missing fields, not to its surface (#174). Bounds: every hit rate is a lower bound (incomplete gold), awesome-python is very likely in the model's training data, and the result holds for one model on one date (#176). **Fit check (#196, §11):** after the gates, asking the agent explicitly whether the package does the need, from memory (F0), caught 20 of 20 wrong-capability picks on well-known packages with no false alarm. No costlier arm (PyPI text, a sandboxed run, a run plus one repair) beat it under the pre-registered rule, on that set or on 24 cases from unfamiliar post-cutoff packages, where F0 scored 0.79 [0.62, 0.96]. So the method is F0: a `fit:` line in the `Reuse:` summary. Exploratory only, not adopted: a docs-plus-sandbox check with one repair (F4r) scored 0.92 [0.79, 1.00] on the unfamiliar set, and running it only when F0 hedges scored 22/24. That route stays provisional until #218 re-tests it. context7 could not be tested: its keyless quota was spent (#217).

## 1. Question and scope
[QUESTION.md](_scratch/software-discovery/QUESTION.md) asks how agents should find the best free software (GitHub repositories, PyPI packages) before building, including from a section of code, with semantic rather than keyword search. It also asks which surface should carry library discovery, `dev lit` and the research loop: a CLI, skill, MCP server, subagent or plugin. Scope: free, keyless or free-key sources; Python first. Out of scope: paid services and private registries. The owner added three requirements on 2026-10-13. The evaluation must not tune on its own validation set. The real counterfactual is the agent without a tool. And a GPU-built index is worth building only if it beats something simpler ([PROTOCOL](_scratch/software-discovery/PROTOCOL.md) amendments 1-3).

## 2. Method: a pre-registered benchmark (VERIFIED)
- **Gold not written by the lead.** Stratum A is one need per awesome-python subcategory, 120 drawn with seed 169 from 159 at commit `ba49ec7`. Each need's gold is every GitHub repository the list files there. Stratum M is 40 PyMigBench migration sources (`d8ffb98`, MIT); their gold is the libraries real projects migrated to, and the only query is the source package's PyPI summary. Stratum P is the lead's 16 pilot needs, TUNE only. Set C is 10 hand-written helpers at pinned commits, for `--from`, TEST only. Six C answers were named by an outside source (ruff S314, pip's NEWS, awesome-python) and 4 by the round-1 researcher, so those 4 are BELIEVED mappings ([r1-benchmark](_scratch/software-discovery/r1-benchmark.md) F1, [recall_bench.py](_scratch/software-discovery/scripts/recall_bench.py), [build_gold.py](_scratch/software-discovery/scripts/build_gold.py)).
- **Frozen split and grid.** [make_split.py](_scratch/software-discovery/scripts/make_split.py) gave TUNE 64 needs (48 A, 16 P) and TEST 112 (72 A, 40 M) plus set C. `split.json` (SHA-256 `0a3a85c5…7ba04`) and the 12-configuration grid `configs.json` (`f6b2376a…ba69`) were committed before the first tuning run. Paraphrases (2 per A need) were written once from the label alone and never edited. Deviation: PROTOCOL step 2 asked for the paraphrase rule to be written in PROTOCOL.md; it is stated only in r1-benchmark.md F1.
- **Arms** (amendments 1, 4, 5). B0 is the agent's own top 3 from memory: a tool-less `claude -p --model claude-opus-5-5` call from an empty directory, with the fixed prompt [b0_prompt.md](_scratch/software-discovery/scripts/data/b0_prompt.md). B1 is B0 with FAIL, unverified and nonexistent names dropped. A1 is `dev reuse` search alone, ordered by R0 (PR #171 as built) or by the tuned order. A2 is the A1 pool plus the B0 names, all gated and always shortlisted, ordered by the chosen configuration. A3 is A2's shortlist (at most 20 lines with gate results) re-ranked by a fresh agent with [a3_prompt.md](_scratch/software-discovery/scripts/data/a3_prompt.md). P1 to P4 are index arms (§7).
- **Metric and decision rules, fixed in advance.** The primary metric is hit@3 (a gold library in the top 3). Harm is the share of top-3 names that do not exist or are FAIL or HOLD. Rule 10: search becomes the default only if A2 or A3 beats B1 on TEST hit@3 with the 95% paired bootstrap interval above 0 and no higher harm. Rule 13: the simplest sufficient arm wins, with costs ordered B1 < A2 < A3 < index. Rule 16: build GPU index arms only if at least 10% of TUNE needs have a gold library in P1's top 100 that is absent from the A2 pool. Intervals use 10,000 resamples over needs, seed 169. TEST results are also split into top-1,000 PyPI gold (64 needs) and long tail (48) ([head_flags.py](_scratch/software-discovery/scripts/head_flags.py)).
- **Reproducible inputs.** Every API answer is cached in dated snapshots outside git: the round-3 TUNE snapshot is 176 MB (`bbfed5dd…9625`) and the TEST snapshot 71 MB (`492206a0…7eb`). Every number below replays offline from them.
- **Disclosures.**
  - Set C was run once, as hit@10 of arms C0-C4, before PROTOCOL.md existed, and the round-1 researcher saw it. It is logged as TEST run #0 for C; no C arm or constant changed after it.
  - A pre-protocol run of A and M was stopped and its output deleted unread.
  - The first TEST scoring attempt for A and M crashed before printing any metric. Once the M source package was excluded (amendment 5, rule 22), an A1 shortlist reached an unenriched candidate. The A1 TEST snapshot was re-enriched under the exclusion, [check_shortlists.py](_scratch/software-discovery/scripts/check_shortlists.py) confirmed every R0 and R4 shortlist, and the next run scored. The pair counts as TEST run #1 ([TEST_RUNS.log](_scratch/software-discovery/scripts/data/TEST_RUNS.log)).
  - Deviations, all fixed before any TEST score:
    - The pilot's S6 was dropped from the TEST arms by amendment 5.
    - B0 ran in 2 batches of 56 (amendment 5, rule 20). The `test_arms` text in `chosen.json` still says batches of 16; amendment 5 supersedes it. B0's batching on TUNE was not recorded.
    - P1 ran on TEST as an exploratory arm only.
    - The P1 corpus catch-up used ClickHouse instead of the plan in amendment 4 (the PEP 691 simple index plus PyPI JSON). This affects only the headroom figure.
    - TEST execution code changed after `chosen.json` was written and before any TEST score, as amendment 5 allows: commits c84e764 and 121271c changed `r3_score.py` (the M source exclusion; B arms take the first 3 names that are not excluded), `add_named.py` (`--exclude`) and `run_agent.py` (`--model`). The hashes in `chosen.json` match commit 8f97ea3. The scored versions at 9f52bc8 have SHA-256 `959168440e11…` (`r3_score.py`), `2b2ef046f948…` (`add_named.py`) and `6932700d097d…` (`run_agent.py`).
  - Edits after the runs, for this public repository's scrub and gate only: local paths in `TEST_RUNS.log` and the TUNE report became `<scratchpad>` or repository-relative; two words in third-party package descriptions in `a3_tune_input.json` became `[redacted]`; the sponsor banner's relative link in the awesome-python copy now points to GitHub; and the round scripts were formatted with ruff. From 9f52bc8 to this note's commit, `r3_score.py`, `add_named.py` and `run_agent.py` are AST-equal except one corrected docstring sentence in `r3_score.py` (it said "TUNE only"; the script scores TEST with `--split test --confirm-test-run`). Scoring reads the answers and the gold, not the descriptions, so no number changes.

## 3. TEST results (VERIFIED, one shot)
Scored by [r3_score.py](_scratch/software-discovery/scripts/r3_score.py) `score --split test`. Full report: [test-r3-2026-10-13.md](_scratch/software-discovery/scripts/data/test-r3-2026-10-13.md). The means and the three main paired intervals were re-computed with separate bootstrap code by [recheck_test.py](_scratch/software-discovery/scripts/recheck_test.py), and the intervals agree to within 0.01. Agent arms: claude-opus-5-5, 2026-10-13.

| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR | harm (top 3) | harm without licence | A (72) | M (40) | top-1,000 (64) | long tail (48) |
|---|---|---|---|---|---|---|---|---|---|---|
| A1 R0 (PR #171 first build, 4d61af0) | 0.33 [0.25, 0.42] | 0.22 | 0.47 | 0.29 | 0.45 | 0.35 | 0.36 | 0.28 | 0.38 | 0.27 |
| A1 R4 | 0.42 [0.33, 0.51] | 0.30 | 0.50 | 0.37 | 0.06 | 0.04 | 0.47 | 0.33 | 0.47 | 0.35 |
| A2 R4 | 0.71 [0.63, 0.79] | 0.49 | 0.85 | 0.62 | 0.04 | 0.02 | 0.79 | 0.57 | 0.84 | 0.54 |
| B0 (memory) | 0.84 [0.77, 0.90] | 0.71 | 0.84 | 0.77 | 0.27 | 0.20 | 0.90 | 0.72 | 0.92 | 0.73 |
| **B1 (memory + gates)** | **0.83 [0.76, 0.90]** | 0.71 | 0.83 | 0.77 | 0.19 | 0.16 | 0.90 | 0.70 | 0.91 | 0.73 |
| A3 (agent re-rank of A2) | 0.83 [0.76, 0.89] | 0.67 | 0.86 | 0.75 | 0.11 | 0.10 | 0.90 | 0.70 | 0.92 | 0.71 |
| P1 R4 (exploratory) | 0.71 [0.63, 0.79] | 0.45 | 0.84 | 0.58 | 0.02 | 0.01 | 0.79 | 0.57 | 0.84 | 0.54 |

Paired hit@3 differences [95% CI]:

| comparison | all (112) | A (72) | M (40) | top-1,000 (64) | long tail (48) |
|---|---|---|---|---|---|
| A2 R4 − B1 | −0.12 [−0.19, −0.05] | −0.11 [−0.19, −0.04] | −0.12 [−0.28, +0.03] | −0.06 [−0.14, +0.02] | −0.19 [−0.31, −0.08] |
| A3 − B1 | +0.00 [−0.05, +0.05] | +0.00 [−0.04, +0.04] | +0.00 [−0.12, +0.12] | +0.02 [−0.05, +0.08] | −0.02 [−0.08, +0.04] |
| A3 − A2 R4 | +0.12 [+0.04, +0.19] | +0.11 [+0.03, +0.19] | +0.12 [−0.03, +0.28] | +0.08 [−0.02, +0.17] | +0.17 [+0.04, +0.29] |
| A1 R4 − A1 R0 | +0.09 [+0.02, +0.16] | +0.11 [+0.03, +0.21] | +0.05 [−0.07, +0.17] | +0.09 [+0.00, +0.20] | +0.08 [+0.00, +0.19] |

- **Harm, paired.** A2 − B1 is −0.15 [−0.20, −0.10] and A3 − B1 is −0.07 [−0.11, −0.04]. Without licence outcomes the gaps are −0.14 and −0.07 [−0.10, −0.03], both intervals below 0.
- **Without the 7 TEST needs whose gold also appears in TUNE** (n = 105), the result is unchanged: A2 − B1 −0.12, A3 − B1 +0.00.
- **Recall is not the bottleneck for search.** Gold is somewhere in the A1 pool for 0.64 of TEST needs and in the A2 pool for 0.91, yet A2's hit@3 is 0.71.
- **What the agent named.** B0 named 328 packages on TEST. 4 do not exist (renpy, python-scrapinghub, leon-ai, pyobfuscate), so 1.2%. 1 is unverified (erpnext), 25 are FAIL and 61 are HOLD. On TUNE, all 175 names existed; 16 were FAIL (13 strong copyleft, 3 archived) and 22 HOLD ([r3-tuning](_scratch/software-discovery/r3-tuning.md) F1). Spracklen et al. measured at least 5.2% hallucinated packages for commercial models over 576,000 code samples ([Spracklen 2024](https://arxiv.org/abs/2406.10279), SOURCED). A replication of that method on five frontier models released October 2025 to March 2026, including Claude Sonnet 4.6, measured 4.62% to 6.10%, and found 127 invented names shared by all five models ([Churilov 2026](https://arxiv.org/abs/2605.17062), SOURCED). The task differs (packages imported in generated code, not names asked for directly), but the order of magnitude matches this TEST's 1.2%, and shared invented names are what squatters register. Both say G1 is needed. Prompt wording moves the rate a lot: asking for the latest libraries induced library hallucinations in up to 85% of tasks across seven LLMs ([Twist 2025](https://arxiv.org/abs/2509.22202), SOURCED). B1's prompt asked for well-known libraries, so its 1.2% is a bound for that kind of request only, not for "newest library for X".
- **Set C, `dev reuse --from` alone (n = 10).** R0 scored hit@3 0.20 [0.00, 0.50] and R4 0.40 [0.10, 0.70], a difference of +0.20 [+0.00, +0.50]. R4 hits pip retry → tenacity, the frontmatter split → python-frontmatter, CPython's levenshtein → rapidfuzz and Django's slugify → python-slugify. 5 of 10 were not recalled at all: devkit's backoff, token bucket, `.env` parser and safe XML parse, and tqdm's `format_sizeof`. 1 was recalled but ranked low: Home Assistant's `parse_datetime` → dateparser. The need text `--from` builds is noisy: module names, every stdlib import and comment words ([test-r3-code-2026-10-13.md](_scratch/software-discovery/scripts/data/test-r3-code-2026-10-13.md)). This is the TEST run #1 for C; run #0 is disclosed in §2. Follow-up: #175.
- **Set D, `--from` need text old vs new (n = 25, #175).** #175 builds the need from the symbol's name, its docstring's first sentence and its third-party imports only (stdlib, relative and the project's own modules dropped). Set D is 25 hand-written helpers whose replacement library a migration commit's message names ("replace custom ... with <library>"), read at the commit's parent; 6 more picks were dropped because the helper names the gold. Cases and gold were frozen (SHA-256 `0851961f…8d1a`, PROTOCOL amendment 6) before one live run, both arms through the same R4 search with model2vec. The repository stores references and hashes only, not the third-party code; post-run changes are disclosed in PROTOCOL items 27-29. Old (main @ f1cfce7) scored hit@3 0.32 [0.16, 0.52], new 0.28 [0.12, 0.48]; paired new − old −0.04 [−0.24, +0.16]. Hit@1 0.16 → 0.20, hit@10 0.40 → 0.36, MRR@10 0.25 both. So the cleaner need text is shorter and printed, but it is not better: no difference was detected, and the interval allows a loss of 0.24. New won 2 cases (opensteuerauszug → platformdirs, verifiers' `with_retry` → tenacity) and lost 3 (bpod-core's `LRUCache` and powercord's cache → cachetools, causaganha's `TokenBucket` → aiolimiter); qteasy's `progress_bar` fell from 1 to 4. Visible causes (BELIEVED, set D is spent, so not tested): the file's third-party imports crowd short needs (`progress bar numpy pandas numba`), a class's own docstring is thinner than its methods' docstrings, and own-module detection misses packages rooted below the repository root. 15 of 25 were missed by both ([test-code-175-2026-10-13.md](_scratch/software-discovery/scripts/data/test-code-175-2026-10-13.md), [run_code_175.py](_scratch/software-discovery/scripts/run_code_175.py), VERIFIED). Follow-up: #211.

## 4. What the pre-registered rules decide
- **Rule 10 (search as the default): not met.** A2 is worse than B1, and A3's interval does not clear 0. So the gates ship as a verifier for named packages, and search stays an optional recall aid (VERIFIED).
- **Rule 13 (simplest sufficient arm): B1.** Neither costlier arm beats it (VERIFIED).
- **Rule 5 (chosen order vs PR #171's first build): R4.** On the same recall, R4 beats R0 by +0.09 [+0.02, +0.16], and the HOLD/FAIL share of the top 3 falls from 0.45 to 0.06 (VERIFIED).
- **Rule 14 (head vs tail).** Memory leads in both groups: B1 scores 0.91 on top-1,000 gold and 0.73 on the long tail. The tail is where search was expected to help, yet A2 trails B1 most there: −0.19 [−0.31, −0.08] (VERIFIED).
- **Rule 16 (index headroom): stop.** See §7.
- **The A3 trade-off (owner decision, #177).** A3 matches B1's hit@3 with 0.11 harm against 0.19, which is 42% less (arithmetic VERIFIED). The rule is decided on hit@3, so it picks the cheaper B1. Three cautions:
  - "Harm" here is the default gate policy, not demonstrated damage. HOLD means a last upload over 730 days ago (an ASSUMED threshold), a first release under 90 days ago, or an unknown licence. FAIL means strong copyleft, archived, malicious or nonexistent.
  - A3 costs a full `dev reuse` search per need plus a re-rank call. The TEST snapshot took about 60-70 s per need to build (r1-benchmark gaps). B1 costs gate lookups on about 3 names.
  - BELIEVED: if the `uv add` guard prints HOLD reasons at the moment of adding (#172), B1 recovers much of A3's harm reduction for little cost. This is unmeasured. Default until the owner decides: B1.

## 5. Misses (PROTOCOL 8; VERIFIED, full lists in the TEST report)
- **B1, 19 misses.** 18 are "not named": the agent's names are not in the gold. Several look like sensible alternatives the gold omits (BELIEVED): requests → httpx and urllib3, pyyaml → ruamel.yaml, click → typer, sphinx-rtd-theme → furo. The remaining miss was gated out: chardet → cchardet FAILed G5. That FAIL is a bug: cchardet is dual-licensed (MPL-1.1 OR GPL OR LGPL), and `devkit/reuse.py` joins licences with AND, so G5 reads a conjunction. pyside6, dramatiq and pysimpleguiweb (LGPL or dual) fail the same way ([#180](https://github.com/Marius-Juston/agent-harness/issues/180)). Fixed, it would flip one B1 miss and one A2 miss (cchardet in both), the same for every arm, so no decision changes (VERIFIED from the miss lists). #172's `uv add` guard waits for #180, or it would refuse PySide6.
- **A2 R4, 32 misses.** 10 were not recalled, 21 were recalled but ranked low, and 1 was gated out. By the status of the first gold candidate shown, the 21 ranked-low cases are 11 PASS gold below other packages (often a smaller package below a more adopted one), 7 HOLD gold that PASS-before-HOLD sinks (pyserial, biopython, ldap3, gpiozero, pcapy, ipaddress, mssql-python), and 3 gold never enriched (hermes-agent, odoo, qtpy) (VERIFIED, tally of the TEST report).
- **A3, 19 misses.** 15 were not on the shortlist, so they were limited upstream by recall or order. 4 were on the shortlist but not picked: pyarmor, quart and bottle for flask, celery for huey, and pcapy.
- **Gold is incomplete for every arm alike.** renpy is not on PyPI, odoo is a repository only, and the Qt gold is outside GitHub. All hit rates are lower bounds (BELIEVED).

## 6. Why search loses here, and what the pilot got wrong
- **The pilot was optimistic.** The lead's pilot strategy S6 scored hit@3 0.88 on its own 16 needs but 0.33 [0.21, 0.48] on the 48 TUNE needs from awesome-python (VERIFIED, [r1-benchmark](_scratch/software-discovery/r1-benchmark.md) F3). The method was designed on those 16, which is why PROTOCOL step 1 confined them to TUNE. The pilot's "14/16 in the top 10" does not generalise.
- **Ranking, not recall, is what limits search.** On TUNE, gold is in the pool of PR #171's first build for 0.83 of needs, but its hit@3 is 0.45 (VERIFIED). The live failure in [gaps-r1](_scratch/software-discovery/gaps-r1.md) shows three causes. HOLD candidates ranked among passes. Relevance spread among near-duplicates outweighed adoption at λ = 0.15. And the shortlist was cut by relevance before enrichment, so tenacity was never scored for "retry with exponential backoff".
- **Wider pools hurt the top of the list without a better order.** Adding the top-30k PyPI index to S6 raised TUNE hit@10 by +0.05 [−0.05, +0.14] and lowered hit@3 by −0.03 [−0.11, +0.05] (VERIFIED, r1-benchmark F3).
- **The agent's names are the largest single gain to search.** A2 − A1 (both R4) on TUNE is +0.27 [+0.16, +0.38], with 17 wins and 0 losses. Gold enters the pool for 0.98 of TUNE needs (VERIFIED, r3-tuning F1, F3).
- **Memory is strong on these strata.** B0 scored 0.97 on TUNE and 0.84 on TEST. M is a different gold source (real migrations), and memory leads there too, 0.70 against A2's 0.57, but the paired interval crosses 0: −0.12 [−0.28, +0.03] (VERIFIED). M is also not a clean out-of-training test: PyMigBench is public data from before the model's training cutoff, and while M ids were opaque to the agent, the A-stratum B0 ids were the literal awesome-python headings (`b0_test_input.json`). Older evidence: in LibRec's ablation, retrieval alone raised migration-target Precision@1 from 0.461 (without retrieval) to 0.505 (full framework), averaged over 10 LLMs from 2024-2025 ([Han 2025](https://arxiv.org/abs/2508.09791), Table III, SOURCED). That is a different model generation, and here the 2026 model's memory already beats the retrieval arm.

## 7. Sources, the index, and what would reopen it
- **Coverage.** Neither GitHub nor PyPI contains the other. 30% of 150 random PyPI projects have no repository URL. 97% of the top 150 by downloads are on GitHub. Among active GitHub Python repositories, 22-56% (by star band) are on PyPI. PyPI has no search API (VERIFIED, [pypi_github_coverage.py](_scratch/software-discovery/scripts/pypi_github_coverage.py), [lead-coverage](_scratch/software-discovery/lead-coverage.md)).
- **Free sources measured** (VERIFIED, r1-benchmark F4):
  - GitHub repository search: 30 requests a minute, 1,000 results.
  - ecosyste.ms keyword pages raised pool hit from 0.59 to 0.91 (mixed with query relaxation); 5,000 requests an hour anonymous.
  - ecosyste.ms `related_packages` is unsafe. For `requests`, all 20 entries were typosquats claiming `github.com/psf/requests`. A repository URL is a claim, so identity must be checked from repository to canonical package.
  - context7's keyless search allows 200 requests a month and added nothing: −0.04 [−0.11, +0.00] on 27 needs.
  - libraries.io needs a free key: 60 requests a minute ([libraries.io API](https://libraries.io/api), SOURCED).
  - deps.dev, OSV and api.scorecard.dev are keyless enrichment sources, not discovery sources.
  - Sourcegraph's anonymous global search returned nothing.
- **P1 as built.** BM25 ([bm25s](https://github.com/xhluca/bm25s), MIT) and potion-retrieval-32M (MIT), fused with RRF k = 60, over 777,561 packages. The corpus is open-index/open-pypi (ODC-By, 712,894 unique packages, a frozen 2026-04-11 snapshot) caught up from ClickHouse. The build took 98 s on 32 CPU cores at 14 GB peak RAM (VERIFIED, [p_corpus.py](_scratch/software-discovery/scripts/p_corpus.py), [p_index.py](_scratch/software-discovery/scripts/p_index.py)).
- **Headroom (rule 16): 3 of 64 = 0.047, under 0.10.** Three TUNE needs have a gold library in P1's top 100 that is absent from the A2 pool: google-cloud-python, ansible (FAIL anyway) and parsy. The index is the only source of gold for 0 needs. Against the A1 pool alone, headroom is 0.28, so the agent's names close almost all of the gap an index would close (VERIFIED, r3-tuning F3).
- **On TEST, P1 tied A2:** +0.00 [−0.05, +0.05] (VERIFIED, exploratory). So P2 (Qwen3-Embedding-0.6B), P3 (plus Qwen3-Reranker-4B) and P4 (LLM document expansion) were not built, and the owner's GPU server was not used. Nothing is published to Hugging Face.
- **What would reopen the index** (BELIEVED, stated before any such data):
  1. A re-run (#176) on a new model, or on a stratum of needs whose gold postdates the model's training cutoff, shows TUNE headroom of P1 over the A2 pool of at least 0.10.
  2. Then P1 beats B1 (and A3, if adopted) on that TEST under rule 13.
  3. Only then would P2 be built and compared against P1.
- **If it reopens, the design is ready** ([r2-pipeline](_scratch/software-discovery/r2-pipeline.md), SOURCED and VERIFIED there):
  - Change detection: the PEP 691 JSON simple root, one 45 MB GET with every project's `_last-serial`. XML-RPC is "planned for deprecation" ([PyPI API docs](https://docs.pypi.org/api/), SOURCED).
  - Fetching: a serial PyPI JSON client at 1-2 requests a second, per PyPI's guidance.
  - Representation: one vector per package, from name, summary, keywords and the README head at 512 tokens.
  - Retrieval: BM25 plus Qwen3-Embedding-0.6B (Apache-2.0; MTEB(Code) 75.41, reproduced from the public results), fused with RRF.
  - Search: exact, 54 ms over 817k vectors.
  - Rerankers: only the 4B reranker helps code retrieval (75.41 → 81.20); the 0.6B one hurts (73.42) ([Qwen3-Reranker-0.6B card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B), SOURCED).
  - Evidence against more complexity: ColBERTv2 scored below BM25 on tool retrieval ([ToolRet](https://arxiv.org/abs/2503.01763), SOURCED). LLM query expansion "generally harms stronger models" ([Weller 2023](https://arxiv.org/abs/2309.08541), SOURCED), and the calling agent already writes paraphrases.
- **Publishing conditions, if ever.**
  - ODC-By covers the database, not its contents, so a derived set must carry attribution ([ODC-By 1.0](https://opendatacommons.org/licenses/by/1-0/), SOURCED).
  - Leave out README text, author names and emails: the [PyPI Terms of Service](https://policies.python.org/pypi.org/Terms-of-Service/) forbid harvesting users' personal information (SOURCED).
  - Treat vectors as carrying their text: vec2text recovers 92% of 32-token inputs exactly ([Morris 2023](https://arxiv.org/abs/2310.06816), SOURCED).
  - Keep CC BY-SA ecosyste.ms dumps out ([open data](https://packages.ecosyste.ms/open-data), SOURCED).
  - Hugging Face storage beyond a few GB is "best-effort" ([storage limits](https://huggingface.co/docs/hub/storage-limits), SOURCED).

## 8. Ranking, maintenance score and the hallucination guard
The full evidence is in [r1-ranking](_scratch/software-discovery/r1-ranking.md); R4 is chosen in r3-tuning F2.

- **Order (R4).** Gates first. Then every PASS before every HOLD. Then S = R + λ·Q, with R the semantic relevance in [0, 1] and λ = 1.0. λ = 1.0 is **fitted** on TUNE: R4 − R3 (λ 0.5) is +0.05 [−0.02, +0.11], within noise, and the cited prior was 0.15. The shortlist is a union: the n best by R plus the n best by adoption (dependents, then downloads) among candidates with R ≥ best R − 0.20. The 0.20 is a fixed constant, not tuned. PASS-before-HOLD is the large effect on harm: on the A1 pool, R1 − R0 is +0.09 [+0.02, +0.17] hit@3 and the HOLD share falls from 0.41 to 0.02. On hit@3 over the A2 pool, λ matters more: R2 → R4 (λ 0.15 → 1.0) is +0.18 (0.70 → 0.88), against R1 − R0 +0.11 (0.58 → 0.69) (VERIFIED, `tune-r3-2026-10-13.md`). Tier rules (R5-R7) did worse (VERIFIED, r3-tuning F2).
- **Quality Q** = Σ w_k·f_k, with each signal normalised as log(1+S)/log(1+max(S,T)), the [OpenSSF criticality_score](https://github.com/ossf/criticality_score) form (SOURCED). The weights are the share of 115 surveyed developers who rated each measurable factor "high influence", normalised: maturity .242, documentation .199, maintenance .172, security .152, popularity .117, community .117. The raw shares are 62, 51, 44, 39, 30 and 30% ([Larios Vargas 2020](https://arxiv.org/abs/2005.12574), Table 2, SOURCED; arithmetic VERIFIED, [quality_score.py](_scratch/software-discovery/scripts/r1/quality_score.py)). Per factor:
  - **Maturity.** Months since first release and number of releases. libraries.io SourceRank also rewards age and a 1.0 release ([source_rank.rb](https://github.com/librariesio/libraries.io/blob/main/app/models/concerns/source_rank.rb), SOURCED).
  - **Documentation.** A docs URL, else README length. README size predicts selection in npm ([Mujahid 2022](https://arxiv.org/abs/2204.04562), SOURCED).
  - **Maintenance.** The larger of Scorecard Maintained/10 and release recency. Maintained counts only the last 90 days ([Scorecard checks](https://github.com/ossf/scorecard/blob/main/docs/checks.md), SOURCED), so httpx scores 0 on it (VERIFIED).
  - **Security.** The Scorecard aggregate. Its outcome evidence is mixed. It explained only 9-12% of the variance in vulnerability counts ([Zahan 2023](https://arxiv.org/abs/2210.14884), SOURCED), yet adoption went with 5.2 fewer vulnerabilities across 145K npm packages ([Zahan 2025](https://arxiv.org/abs/2504.14026), SOURCED). Hence it is a weight, not a gate.
  - **Popularity.** Downloads and stars, capped. Stars can be faked: about six million suspected fake stars ([He 2026](https://arxiv.org/abs/2412.13459), SOURCED).
  - **Community.** Contributors. The CHAOSS [Contributor Absence Factor](https://chaoss.community/kb/metric-bus-factor/) is the better signal (SOURCED).
  - **Validity.** Q is not validated against any outcome, and its thresholds T are ASSUMED. No free composite scorer is validated either (r1-ranking F5).
- **Gates and the hallucination guard.**

| gate | rule | evidence |
|---|---|---|
| G1 exists | PyPI JSON returns 200, else FAIL; stdlib names are not packages | ≥5.2% of packages hallucinated by commercial models and 21.7% by open ones; 43% of hallucinations repeat in all 10 re-prompts, so they can be squatted ([Spracklen 2024](https://arxiv.org/abs/2406.10279), SOURCED). Only 13.4% are within edit distance 1-2 of a real name, so existence beats typo distance (same source). Earlier methods overstate Python rates by up to 9.4 points by counting stdlib modules, and RAG cut the rate in 18 of 32 model-language settings ([Djiré 2026](https://arxiv.org/abs/2608.22652), SOURCED) |
| G2 not malicious | no OSV `MAL-` advisory, else FAIL | [ossf/malicious-packages](https://github.com/ossf/malicious-packages) feeds OSV. `0requests` returns MAL-2026-15859 (VERIFIED). A squatted name exists, so G1 alone is not enough |
| G3 not new | first release at least 90 days ago, else HOLD | one working definition counts post-cutoff packages as hallucinations ([Krishna 2025](https://arxiv.org/abs/2501.19012), SOURCED) |
| G4 alive | not archived, deprecated or all-yanked (FAIL for archived); over 730 days since the last upload = HOLD | 16% of 1,932 popular projects were abandoned ([Avelino 2019](https://arxiv.org/abs/1906.08058), SOURCED). The 730 days is ASSUMED |
| G5 licence | Blue Oak permissive tiers pass; weak copyleft passes with a flag; strong or network copyleft FAIL unless the owner overrides; unknown = HOLD | 14.2% of ChatGPT-recommended libraries had copyleft licences it did not mention ([Latendresse 2024](https://arxiv.org/abs/2408.05128), SOURCED). 7.27% of PyPI releases have a licence incompatibility, 61.3% of them transitive ([Xu 2023](https://arxiv.org/abs/2308.05942), SOURCED). Tiers from the [Blue Oak list](https://blueoakcouncil.org/list) |
| G6 advisories | open OSV or GHSA advisories: flag | 36.7-55.7% of tasks with a model-pinned version contain a known CVE ([Wang 2026](https://arxiv.org/abs/2605.06279), SOURCED), so uv resolves versions, never the model |

- **Search inside the repository first** (`uv run dev code find`) and check the stdlib: coding agents leave duplicated logic in 50.8% of task chains by turn 5 ([Ma 2026](https://arxiv.org/abs/2609.35357), SOURCED). A trivial helper is better built than imported (BELIEVED; see the [Never Work in Theory summary](https://neverworkintheory.org/2021/09/17/why-do-developers-use-trivial-packages.html) of Abdalkareem et al., SOURCED, secondary).

## 9. Surface: CLI, skill, MCP, subagent, plugin, hook
The evidence is in [r1-surface](_scratch/software-discovery/r1-surface.md). Transcript counts come from [surface_usage.py](_scratch/software-discovery/scripts/surface_usage.py) and [builder_reuse.py](_scratch/software-discovery/scripts/builder_reuse.py), over this machine's runs from 2026-10-11 to 2026-10-13.

- **A CLI named in the agent's own prompt is followed** (VERIFIED). harness-ops researchers ran `dev lit status` in 60 of 62 runs and `dev lit search/get` in 54. WebFetch breaches of the literature APIs fell from 16 of 38 legacy runs to 1 of 62.
- **The CLI leaks through missing fields, not through its surface** (VERIFIED). 13 of 62 researcher runs (27 calls) and 7 of 11 research-auditor runs (15 calls) curl OpenAlex or Crossref directly, bypassing the shared rate limiter. They fetch authors, open-access PDF links and full abstracts. The auditor's prompt never names `dev lit`. Fix: #30 and #174.
- **Builders rarely search before building** (VERIFIED, small N). 2 of 6 builder runs that wrote a new non-test module looked for a library first, and 8 of 39 builder runs did any lookup. Two of the four misses followed notes that had already weighed tools, so the true gap is smaller (BELIEVED). The harness's own rule is that a rule broken in a quarter of runs becomes a hook ([agent-harness.md](agent-harness.md) §2).
- **Model-invoked skills trigger worst:**
  - In Vercel's evals a skill was never invoked in 56% of cases, while an always-on docs index scored 100% ([Vercel 2026](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals), SOURCED). This is a vendor benchmark of docs knowledge, not of a tool procedure.
  - When a skill does run it helps: a mean Skill Lift of 0.213 [0.197, 0.230] over 947 paired cases ([ACES 2026](https://arxiv.org/abs/2608.20614), SOURCED).
  - Claude Code's own docs say the most common first finding of a plugin eval is a delta near zero with the skill-trigger grader failing ([plugin evals](https://code.claude.com/docs/en/plugin-evals), SOURCED).
- **MCP's token cost is mostly gone in Claude Code, but it buys nothing here** (the reading is BELIEVED):
  - With tool search, only tool names and server instructions load at start ([MCP docs](https://code.claude.com/docs/en/mcp)), and the features overview rates MCP "Low until a tool is used" ([features overview](https://code.claude.com/docs/en/features-overview), SOURCED).
  - Vendor CLI-vs-MCP benchmarks disagree, and all ran on GitHub tasks with small n:
    - CLI ahead: 25/25 against 18/25 success, all 7 MCP failures remote timeouts ([Scalekit](https://www.scalekit.com/blog/mcp-vs-cli-use), SOURCED).
    - Flat: 0.845 to 0.826 correctness across bash, MCP and skills ([Arize](https://arize.com/blog/mcp-vs-cli-skills-for-agents-what-our-eval-found-and-which-you-should-use/), SOURCED).
    - MCP ahead for many unfamiliar endpoints (Smithery, second-hand via [Nordic APIs](https://nordicapis.com/mcp-vs-cli-which-is-better-for-agentic-ai/), SOURCED; the original post returns 404).
  - `dev reuse` and `dev lit` are a few subcommands whose output agents pipe and filter. That favours the CLI.
  - MCP adds a second process lifecycle and is invisible to `uv run dev check`. And 97.1% of 856 MCP tools had at least one description smell ([arXiv 2602.14878](https://arxiv.org/abs/2602.14878), SOURCED).
  - A warm server would save under 1 s per call: model load is 0.56-0.65 s against network calls of seconds (VERIFIED, [coldstart.sh](_scratch/software-discovery/scripts/coldstart.sh)).
  - No existing free package-search MCP server is fit to adopt (r1-surface F8).
- **Decisions.**
  - `dev reuse`: a CLI. The reuse rule is enforced by a PreToolUse `uv add` guard that runs G1, G2 and G5 on each named package (#172), and by a `Reuse:` line in the PR summary that the completion gate checks (#173). It is not carried by a model-invoked skill.
  - `dev lit`: stays a CLI. Close the bypass with fields and the auditor prompt (#30, #174).
  - The research loop: stays a user- or lead-invoked skill that fans out researcher subagents, as now. A subagent keeps `dev lit` output (median 5.1k, p90 13.5k characters per call) out of the lead's context (VERIFIED, r1-surface F6). A builder's few reuse checks do not justify a spawn (BELIEVED).
  - The plugin is the carrier for the hook. The harness-ops plugin already ships `guard_bash.py` and `completion_gate.py`.
- **What would change it to MCP** (BELIEVED):
  - the harness must serve clients without a shell (claude.ai, Desktop);
  - or a stateful index takes over about 5 s per CLI call to load (Qwen3-0.6B on CPU: 2.8-2.9 s, r2-pipeline F8);
  - or a `claude plugin eval` shows higher use as a typed MCP tool than as a prompted CLI, with the hook held constant.

## 10. Recommendation for this project
1. **Default method, B1.** An agent that needs a library names up to 3 candidates from memory and verifies them with `uv run dev reuse check <name>...` (G1-G6; NOT FOUND only on a registry 404, UNVERIFIED when the registry gives no answer) before `uv add`. FAIL is dropped; HOLD is shown with its reason. Expected on this benchmark: hit@3 0.83 [0.76, 0.90] and harm 0.19 (VERIFIED, one model). Merged PR #171 (22d7ce9) already says this in the builder, researcher, research skill and template text: name, verify with `check`, search only for more.
2. **`dev reuse` search is a recall aid.** Use it when the agent can name no candidate that passes the gates, or for a research note that must list alternatives. Merged PR #171 ships R4 (`PASS_BEFORE_HOLD`, `R_FLOOR` 0.20 union shortlist, `LAMBDA` 1.0, `ENRICH_CAP` 3n): PASS before HOLD; union shortlist with n by R plus n by adoption among R ≥ best R − 0.20; S = R + 1.0·Q, with λ fitted. It replaced R0, which put 0.45 of its top 3 in HOLD or FAIL (VERIFIED). When search runs, pass the agent's own names in with it (A2 − A1 +0.27 on TUNE) and let the agent re-rank the shortlist (A3 − A2 +0.12 [+0.04, +0.19] hit@3 on TEST). The re-rank costs harm: A3 − A2 is +0.076 [+0.033, +0.121] (VERIFIED); the likely cause is that the agent lifts well-known HOLD names back into the top 3 (BELIEVED).
3. **Gates G1-G6 as in §8.** The guard must check repository → canonical package, not trust a package's repository link (VERIFIED, §7), and must whitelist `sys.stdlib_module_names`. The licence policy (strong copyleft FAIL) is the default; the owner can override it per project (BELIEVED policy).
4. **Enforcement:** the `uv add` guard hook (#172) and the `Reuse:` line (#173). No MCP server and no model-invoked reuse skill (BELIEVED, from the §9 evidence).
5. **`--from`:** keep it, but treat its output as weak: hit@3 0.28 on 25 held-out cases with #175's need text, no better than before (§3, set D). It prints the need it built, so an agent can rewrite that need as a quoted query instead (#211).
6. **No PyPI index, no GPU build, no Hugging Face publication now** (VERIFIED by rule 16 and the P1 tie). Reopen only on the evidence in §7: headroom ≥ 0.10 on a re-run, then a TEST win over B1.
7. **`dev lit`:** a CLI, plus the fields and the auditor prompt (#30, #174).
8. **Owner decision:** whether new runtime dependencies of a product use A3, at a harm share of 0.11 instead of 0.19 (#177). Default: B1.
9. **Fit check (#196, §11).** After `dev reuse check`, the agent states in the `Reuse:` line which public API of the package does the need: "fit: <package> does <need> via <API>", answered from memory, or "fit: unsure" when it cannot vouch for it. This is the pre-registered F0, and it is all the build half implements: 20/20 catches and 0/23 false alarms on known packages, at no tool cost (VERIFIED). A sandboxed check (`dev reuse try`: `uv sync --no-build` into a throwaway environment, bubblewrap with no network and a read-only file system except /tmp, 60 s, FIT only on exit 0 with a last line `FIT_OK`, written with the PyPI page in hand, one repair) is BELIEVED and provisional. Its only evidence is exploratory (F4r 0.92 [0.79, 1.00], cascade 22/24), and it waits for #218. A sandbox check written without the docs is no better than memory and raises false alarms (F3: 8/10 on N), so it is not used. context7 is not the default: it needs a key, and its keyless quota is spent (#217).

**Bounds.**
- Hit rates are lower bounds: gold lists are incomplete, and only canonical GitHub `owner/repo` names match.
- awesome-python is very likely in the model's training data, which favours B0 on strata A and P, and A-stratum ids named the heading. M (opaque ids) agrees in direction, but its A2 − B1 interval crosses 0, and PyMigBench is also pre-cutoff public data.
- A missing arm: B0 with web search (the agent's names plus a WebSearch step) was not run. It would bound how much a live lookup adds to memory without `dev reuse`; it is filed on #176.
- One model (claude-opus-5-5), one date (2026-10-13), one call per batch: sampling variance across agent calls was not measured.
- Python and PyPI only.
- Hit@3 measures naming the gold library, not whether the code worked.
- The paraphrases have one author.
- Set C is 10 cases and now spent.
- The Q weights come from one perception survey.

## 11. Fit check: does the chosen library do the need (#196)
The gates (§8) say a package exists, is alive and is licensed; they do not say it does the job. [#196](https://github.com/Marius-Juston/agent-harness/issues/196) asks for the cheapest reliable free check of that. Everything below is under [fit/PROTOCOL.md](_scratch/software-discovery/fit/PROTOCOL.md) (pre-registered before any arm ran, with amendments 1-4), claude-opus-5-5, 2026-10-13, one fresh tool-less `claude -p` call per case.

- **Arms, in cost order.**
  - F0 asks the fit question from memory.
  - F1 adds the package's PyPI summary and the first 12,000 characters of its README.
  - F2 is a context7 docs lookup. It was declared but **could not run**: the keyless quota was spent until 2026-11-01, and both the REST API and the MCP tool refused (VERIFIED, 2026-10-13). The documented free plan with a key is 1,000 calls a month ([r1-benchmark](_scratch/software-discovery/r1-benchmark.md) F4, SOURCED there). Filed as #217.
  - F3: the agent writes a check of at most 40 lines, and the harness runs it in a throwaway sandboxed environment.
  - F3r: F3 plus one repair after a failure.
  - F4 and F4r (exploratory, amendment 4): F3 and F3r with the F1 text in the prompt.
- **Cases and gold.** Two strata, scored separately. Gold is VERIFIED per case by a gold script, run in the same sandbox ([gold/](_scratch/software-discovery/fit/gold/), [gold_n/](_scratch/software-discovery/fit/gold_n/), [gold_run.py](_scratch/software-discovery/fit/scripts/gold_run.py)).
  - TEST: 43 (package, need) pairs on well-known packages, 23 FIT and 20 NOT_FIT. The NOT_FIT pairs are plausible wrong picks: tomli for writing TOML, xlrd 2 for .xlsx, orjson for JSON with comments, schedule for cron expressions, aioredis on 3.13, nltk or tiktoken offline, and so on.
  - N: 24 cases on 12 packages sampled by rule from PyPI projects first uploaded on or after 2026-07-15 ([n_selection.md](_scratch/software-discovery/fit/data/n_selection.md)). Each package has a claim from its README's first example and an adjacent need it does not claim. There are 10 FIT and 14 NOT_FIT. Two README claims do not hold: ezgb shells out to an absent `git bug` CLI, and satusehat-integration's wheel imports a `src` package it does not ship.
  - Deviation: by PyPI's own upload dates, 4 of the 12 N packages predate 2026-07-15. The mirror used to sample them is incomplete. The post-cutoff subset is reported separately below.

| arm | TEST accuracy [95% CI] | TEST catch / false alarm | N accuracy [95% CI] | N catch / false alarm | calls, s per case (N) |
|---|---|---|---|---|---|
| F0 memory | 1.00 [1.00, 1.00] | 20/20, 0/23 | 0.79 [0.62, 0.96] | 11/14, 2/10 | 1.00, 5.8 |
| F1 PyPI text | 1.00 [1.00, 1.00] | 20/20, 0/23 | 0.83 [0.67, 0.96] | 12/14, 1/10 | 1.00, 6.2 |
| F3 sandbox | 0.93 [0.84, 1.00] | 20/20, 3/23 | 0.62 [0.42, 0.83] | 13/14, 8/10 | 1.00, 233 |
| F3r sandbox + repair | 1.00 [1.00, 1.00] | 20/20, 0/23 | 0.71 [0.50, 0.88] | 13/14, 6/10 | 1.88, 421 |
| F4 PyPI text + sandbox (exploratory) | not run | | 0.83 [0.67, 0.96] | 14/14, 4/10 | 1.00, 10.9 |
| F4r PyPI text + sandbox + repair (exploratory) | not run | | 0.92 [0.79, 1.00] | 14/14, 2/10 | 1.75, 15.7 |

"Catch" counts NOT_FIT gold judged NOT_FIT; "false alarm" counts FIT gold judged NOT_FIT. F1's two UNPARSED replies on N count as wrong. Reports: [TEST](_scratch/software-discovery/fit/data/test-fit-2026-10-13.md) and [N](_scratch/software-discovery/fit/data/n-fit-2026-10-13.md), from [score.py](_scratch/software-discovery/fit/scripts/score.py) (10,000 paired bootstrap draws over cases, seed 196). On TEST, F0 cost 2,928 input and 127 output tokens and 4.4 s per case; most of the input is `claude -p`'s own overhead.

**Post-cutoff subset of N** (the 8 packages first uploaded on or after 2026-07-15 by PyPI's dates, 16 cases: 7 FIT, 9 NOT_FIT; [post_cutoff.py](_scratch/software-discovery/fit/scripts/post_cutoff.py), [report](_scratch/software-discovery/fit/data/post-cutoff-2026-10-13.md), VERIFIED):

| arm | F0 | F1 | F3 | F3r | F4 (expl.) | F4r (expl.) |
|---|---|---|---|---|---|---|
| right of 16 | 12 | 12 | 9 | 11 | 14 | 15 |

- **Rule 6 picks F0 on both strata** (VERIFIED). No costlier arm beats F0 with an interval above 0.
  - On TEST, F1 and F3r tie F0 exactly.
  - On N: F1 − F0 +0.04 [−0.12, +0.21]. F3 is not better, with 8/10 false alarms: F3 − F0 −0.17 [−0.42, +0.08]. F3r − F0 −0.08 [−0.33, +0.17]. Exploratory F4r − F0 +0.12 [−0.04, +0.29].
  - Asked explicitly, the model caught every wrong-capability pick on well-known packages (20 of 20). That meets the #196 DONE WHEN condition of catching at least one.
- **Exploratory: hedged F0 answers and a retrieve-when-unsure cascade** (BELIEVED as a method; counts VERIFIED by [hedge.py](_scratch/software-discovery/fit/scripts/hedge.py), [output](_scratch/software-discovery/fit/data/hedge-2026-10-13.txt)).
  - Definition, fixed in PROTOCOL rule 21 before counting: a reason hedges if it contains "can't confirm", "not verified", "no reliable knowledge", "limited recall", "uncertain" or "I don't have", case-insensitive. The flag is computed on the full reason, before the committed copy is cut to 300 characters.
  - On TEST, 0 of 43 F0 reasons hedged. On N, 15 of 24 hedged: F0 was right in 10 of those 15 and in all 9 that did not hedge.
  - The cascade "F0, then F4r only when F0 hedges" scores 22/24 on N and 43/43 on TEST, where it equals F0.
  - The idea is Mallen et al.'s: retrieve only when the model is likely not to know. They found retrieval helps most on less popular entities, where unassisted models struggle, and that retrieving only when necessary improved accuracy while cutting inference cost ([Mallen 2022](https://arxiv.org/abs/2212.10511), SOURCED). CloudAPIBench found the same for code: documentation retrieval helped low-frequency APIs and hurt high-frequency ones unless triggered selectively. GPT-4o's valid low-frequency invocations rose from 38.58% to 47.94%, and its high-frequency ones fell by up to 39.02 points with weak retrievers ([Jain 2025](https://doi.org/10.1109/icse-seip66354.2025.00027), SOURCED; older model).
  - Bound: verbalized confidence is a weak trigger. Xiong et al. found LLMs "tend to be overconfident" when they state confidence, and that black-box confidence predicts failure only modestly (AUROC 0.522 against 0.605 for white-box methods in their example) ([Xiong 2023](https://arxiv.org/abs/2306.13063), SOURCED). The perfect split here is 24 cases from one author, and #218 re-tests it.
- **Why the sandbox alone fails on unfamiliar packages** (VERIFIED from the run outputs).
  - Without the docs, the agent guesses the API, so 8 of 10 true claims failed.
  - Three F3 calls on N ran 850-900 s, two of them emitting about 129,000 output tokens.
  - Given the PyPI text (F4r), the same check had 2 false alarms in 10, both failing on the agent's own assertions (n01 demanded a password-verification method the need does not ask for), and took 15.7 s per case.
- **Audit (rule 7), per case** (VERIFIED from the raw outputs).
  - Sandbox FIT on NOT_FIT gold: only n04 (F3 and F3r carry-over). The check called the first public reminis name matching "lora" or "train" and accepted any non-None result, so it is vacuous ([n04 script](_scratch/software-discovery/fit/data/n_f3_scripts/n04.txt)). No check printed FIT_OK unconditionally, and none wrote the capability by hand.
  - Sandbox NOT_FIT on FIT gold, TEST: F3 t04, t30 and t32 were over-strict assertions, and one repair fixed all three.
  - Sandbox NOT_FIT on FIT gold, N, F3 (8 cases):
    - wrong API guessed: n01 (TypeError on an invented keyword), n05 (ImportError), n23 (TypeError on the result object);
    - search for the API by keyword missed the real name: n03, n13, n17;
    - over-strict assertion: n15 (asserted a table name);
    - no script: n19, where the model wrote invented tool-call markup for 390 s.
  - Sandbox NOT_FIT on FIT gold, N, F3r (6 cases):
    - wrong API guessed: n01 (ImportError), n03 (opened the export as SQLite when it is not one), n05 (AttributeError), n13 (ModuleNotFoundError);
    - script bug: n17 (instantiated `typing.List`);
    - over-strict assertion: n19 (asserted R² > 0.9; the model scored 0.885).
  - Disclosure: n24 (F3) timed out at 900 s with no script. It was scored NOT_FIT, which counts as a catch even though nothing was checked. F3r then checked it for real, and the 13/14 catch count for F3 includes it.
- **A failure mode of tool-less calls** (VERIFIED). 2 of 24 F1 replies on N (n03, n21) contained invented tool-call markup and invented install output, with no tools available. One then gave a verdict and the other gave none. A fit check must never be taken from a model's claim that it "ran" something; only a harness run counts.
- **Sandbox cost** (VERIFIED, this machine).
  - Every one of the 59 packages installed as wheels with `uv sync --no-build`.
  - A cold install (empty cache) took a median of 1.4 s, a 90th percentile of 13.5 s and a maximum of 141.5 s (comparer-mcp, 251 MB). The environment size was a median of 1.6 MB and a 90th percentile of 23.9 MB. A warm reinstall took a median of 0.01 s ([install_costs.py](_scratch/software-discovery/fit/scripts/install_costs.py)).
  - Over 264 sandbox runs (gold and agent checks), a run took a median of 0.08 s, a 90th percentile of 0.76 s and a maximum of 3.33 s ([run_times.py](_scratch/software-discovery/fit/scripts/run_times.py), [output](_scratch/software-discovery/fit/data/run-times-2026-10-13.txt)). The 60 s limit is generous.
- **Sandbox safety and its limits.**
  - `--no-build` makes uv refuse any source distribution: "operations that require building a source distribution will exit with an error" ([uv settings](https://docs.astral.sh/uv/reference/settings/), SOURCED). That matters because 74.81% of the 4,669 malicious PyPI package files Guo et al. studied reached users through source installation ([Guo 2023](https://doi.org/10.1109/ase56229.2023.00135), abstract, SOURCED).
  - A wheel still runs code at interpreter start: `import` lines in `.pth` files "are always run at Python startup" ([site docs](https://docs.python.org/3/library/site.html), SOURCED). Python 3.15 deprecates such lines but still runs them during the deprecation period (same page). So the run itself must be sandboxed.
  - [sandbox.py](_scratch/software-discovery/fit/scripts/sandbox.py) runs it under [bubblewrap](https://github.com/containers/bubblewrap) (LGPL, 9.0k stars, pushed 2026-09-25, used as an external binary). It unshares every namespace, binds `/usr`, the interpreter and the environment read-only, mounts a tmpfs `/tmp`, clears the environment, sets a timeout, and binds the CA store and time zone read-only (amendment 2: pygit2 loads the CA store at import).
  - [probe_sandbox.py](_scratch/software-discovery/fit/scripts/probe_sandbox.py) checks seven properties, and all pass ([output](_scratch/software-discovery/fit/data/probe-sandbox-2026-10-13.txt), VERIFIED):
    - the network is unreachable;
    - the environment and `/usr` are read-only;
    - `~/.ssh` is invisible;
    - a variable set in the parent does not reach the sandbox, whose environment holds only `HOME`, `LANG`, `PATH` and `PWD`;
    - `--no-build` refuses an sdist-only release (docopt 0.6.2).
  - **Not bounded:** memory, process count and system calls. There is no cgroup, rlimit or seccomp filter; the only resource limit is the wall-clock timeout. A build should add rlimits (ASSUMED sufficient for checks this small).
  - One sampled wheel shows why the environment must be throwaway: satusehat-integration installs top-level `queue`, `builder` and `datatype` packages into site-packages, and its `queue` shadows the standard library's (VERIFIED).
  - Alternatives: [anthropics/sandbox-runtime](https://github.com/anthropics/sandbox-runtime) (Apache-2.0, 5.5k stars, pushed 2026-10-13) wraps bubblewrap on Linux and `sandbox-exec` on macOS, and is the portable option. google/nsjail (Apache-2.0) and firejail (GPL-2.0) are heavier (SOURCED, GitHub API 2026-10-13).
- **Reproducibility without third-party text in git.**
  - Committed results keep verdicts, short reasons, hedge flags, timings and exception class names. Raw outputs and the PyPI snapshots stay in `<git-common-dir>/devkit/bench/fit-196-2026-10-13/`, which is never pushed, with their SHA-256 values in [raw_manifest.json](_scratch/software-discovery/fit/data/raw_manifest.json) ([scrub.py](_scratch/software-discovery/fit/scripts/scrub.py)).
  - The PyPI snapshots can be rebuilt from version-pinned URLs, and both hashes reproduce ([refetch_pypi.py](_scratch/software-discovery/fit/scripts/refetch_pypi.py)).
  - Every installed wheel is pinned with its URL and SHA-256 in [wheels.json](_scratch/software-discovery/fit/data/wheels.json).
  - The four gold scripts that had followed README examples (n05, n07, n13, n19) were rewritten. They now share no README line except two import statements, and only short API-call fragments ([readme_overlap.py](_scratch/software-discovery/fit/scripts/readme_overlap.py), [output](_scratch/software-discovery/fit/data/readme-overlap-2026-10-13.txt)).
- **Bounds.**
  - One author wrote every case and gold script, and the arms ran on the same model.
  - The TEST packages are well known, so memory was expected to do well.
  - N is 24 cases, so its intervals are wide.
  - F4, F4r and the cascade are exploratory and were chosen after the N scores were seen.
  - One call per case: sampling variance is not measured.
  - Offline needs only, Linux only (bubblewrap).
  - #218 re-tests the cascade on outside-sourced cases with repeated calls.

## Sources
- Larios Vargas et al. Selecting third-party libraries: The practitioners' perspective. ESEC/FSE 2020. https://arxiv.org/abs/2005.12574 (Table 2)
- Spracklen et al. We Have a Package for You! A Comprehensive Analysis of Package Hallucinations by Code Generating LLMs. USENIX Security Symposium; arXiv 2024. https://arxiv.org/abs/2406.10279 (§5.3, §5.4, §6.2)
- Krishna et al. Importing Phantoms: Measuring LLM Package Hallucination Vulnerabilities. 2025. https://arxiv.org/abs/2501.19012 (§2.2, §4)
- Churilov. The Range Shrinks, the Threat Remains: Re-evaluating LLM Package Hallucinations on the 2026 Frontier-Model Cohort. 2026. https://arxiv.org/abs/2605.17062 (abstract)
- Twist, Harman, Yannakoudakis, Zhang. Library Hallucinations in LLM-Generated Code: A Risk Analysis Grounded in Developer Queries. 2025. https://arxiv.org/abs/2509.22202 (abstract)
- Djiré et al. Evaluating Inference-Time Defenses Against Package Hallucination in LLM-Generated Code. 2026. https://arxiv.org/abs/2608.22652 (abstract)
- Latendresse et al. Is ChatGPT a Good Software Librarian? An Exploratory Study on the Use of ChatGPT for Software Library Recommendations. 2024. https://arxiv.org/abs/2408.05128
- Xu et al. Understanding and Remediating Open-Source License Incompatibilities in the PyPI Ecosystem. ASE 2023. https://arxiv.org/abs/2308.05942
- Wang et al. Correct Code, Vulnerable Dependencies: A Large Scale Measurement Study of LLM-Specified Library Versions. 2026. https://arxiv.org/abs/2605.06279
- Han et al. LibRec: Benchmarking Retrieval-Augmented LLMs for Library Migration Recommendations. 2025. https://arxiv.org/abs/2508.09791 (Table III and §VI-B, read 2026-10-13)
- Zahan et al. Do Software Security Practices Yield Fewer Vulnerabilities? ICSE-SEIP 2023. https://arxiv.org/abs/2210.14884
- Zahan et al. Assumptions to Evidence: Evaluating Security Practices Adoption and Their Impact on Outcomes in the npm Ecosystem. 2025. https://arxiv.org/abs/2504.14026
- Mujahid et al. What are the characteristics of highly-selected packages? A case study on the npm ecosystem. 2022. https://arxiv.org/abs/2204.04562
- He et al. Six Million (Suspected) Fake Stars in GitHub: A Growing Spiral of Popularity Contests, Spams, and Malware. ICSE 2026. https://arxiv.org/abs/2412.13459
- Avelino et al. On the abandonment and survival of open source projects: An empirical investigation. 2019. https://arxiv.org/abs/1906.08058
- Ma et al. Do Coding Agents Reuse Existing Code or Reinvent the Wheel? 2026. https://arxiv.org/abs/2609.35357
- Shi et al. Retrieval Models Aren't Tool-Savvy: Benchmarking Tool Retrieval for Large Language Models (ToolRet). ACL Findings 2025. https://arxiv.org/abs/2503.01763 (Table 5)
- Weller et al. When do Generative Query and Document Expansions Fail? A Comprehensive Study Across Methods, Retrievers, and Datasets. 2023. https://arxiv.org/abs/2309.08541 (abstract)
- Morris et al. Text Embeddings Reveal (Almost) As Much As Text (vec2text). 2023. https://arxiv.org/abs/2310.06816 (abstract)
- Model Context Protocol (MCP) Tool Descriptions Are Smelly! Towards Improving AI Agent Efficiency with Augmented MCP Tool Descriptions. 2026. https://arxiv.org/abs/2602.14878
- Evaluating Skills, Not Just Agents: Agentic Continuous Evaluation of Skills (ACES). 2026. https://arxiv.org/abs/2608.20614
- Claude Code docs (fetched 2026-10-13): https://code.claude.com/docs/en/features-overview, https://code.claude.com/docs/en/mcp, https://code.claude.com/docs/en/skills, https://code.claude.com/docs/en/plugin-evals
- Vercel. AGENTS.md outperforms skills in our agent evals. 2026-01-27. https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals
- Scalekit. MCP vs CLI. 2026-03-11. https://www.scalekit.com/blog/mcp-vs-cli-use
- Arize. MCP vs CLI skills for agents. 2026-05. https://arize.com/blog/mcp-vs-cli-skills-for-agents-what-our-eval-found-and-which-you-should-use/
- Nordic APIs. MCP vs CLI (Smithery's numbers, second-hand). 2026-05-13. https://nordicapis.com/mcp-vs-cli-which-is-better-for-agentic-ai/
- OpenSSF: https://github.com/ossf/scorecard, https://github.com/ossf/scorecard/blob/main/docs/checks.md, https://github.com/ossf/criticality_score, https://github.com/ossf/malicious-packages
- libraries.io SourceRank: https://github.com/librariesio/libraries.io/blob/main/app/models/concerns/source_rank.rb; API: https://libraries.io/api
- CHAOSS Contributor Absence Factor: https://chaoss.community/kb/metric-bus-factor/
- Blue Oak Council licence list: https://blueoakcouncil.org/list
- Never Work in Theory, summary of Abdalkareem et al. (trivial packages): https://neverworkintheory.org/2021/09/17/why-do-developers-use-trivial-packages.html
- PyPI: https://docs.pypi.org/api/, https://policies.python.org/pypi.org/Terms-of-Service/. ODC-By 1.0: https://opendatacommons.org/licenses/by/1-0/. ecosyste.ms open data: https://packages.ecosyste.ms/open-data. Hugging Face storage limits: https://huggingface.co/docs/hub/storage-limits
- Models and tools: https://huggingface.co/Qwen/Qwen3-Reranker-0.6B, https://github.com/xhluca/bm25s
- Jain et al. On Mitigating Code LLM Hallucinations with API Documentation (CloudAPIBench). ICSE-SEIP 2025. https://doi.org/10.1109/icse-seip66354.2025.00027 (abstract)
- Mallen et al. When Not to Trust Language Models: Investigating Effectiveness of Parametric and Non-Parametric Memories. 2022. https://arxiv.org/abs/2212.10511 (abstract)
- Xiong et al. Can LLMs Express Their Uncertainty? An Empirical Evaluation of Confidence Elicitation in LLMs. 2023. https://arxiv.org/abs/2306.13063 (abstract)
- Guo et al. An Empirical Study of Malicious Code In PyPI Ecosystem. ASE 2023. https://doi.org/10.1109/ase56229.2023.00135 (abstract)
- uv settings reference (`no-build`), fetched 2026-10-13: https://docs.astral.sh/uv/reference/settings/. Python `site` module (`.pth` execution): https://docs.python.org/3/library/site.html
- Sandboxes (GitHub API, 2026-10-13): https://github.com/containers/bubblewrap, https://github.com/anthropics/sandbox-runtime, https://github.com/google/nsjail, https://github.com/netblue30/firejail
- Gold sources: https://github.com/vinta/awesome-python (README @ ba49ec7), https://github.com/ualberta-smr/PyMigBench (@ d8ffb98)
- Further sources of the rounds (the Scorecard and deps.dev APIs, Qwen3 and other embedder cards, Tool-DE, Bruch et al., the index stores, Djiré et al., Di Sipio et al., Import2vec, CrossRec, MigrationHelper): [r1-ranking](_scratch/software-discovery/r1-ranking.md), [r1-surface](_scratch/software-discovery/r1-surface.md), [r1-benchmark](_scratch/software-discovery/r1-benchmark.md), [r2-pipeline](_scratch/software-discovery/r2-pipeline.md).

## Follow-ups
- [ ] #172 (waits for bug #180, G5 on dual and LGPL licences): `uv add` guard hook. DONE WHEN a PreToolUse hook refuses a nonexistent, malicious (OSV `MAL-`) or strong-copyleft package with the reason, warns on HOLD and advisories, and calls devkit.reuse's gates (tests with a fake server).
- [ ] #173: `Reuse:` line. DONE WHEN the completion gate refuses a builder hand-back that adds a dependency or a new non-test module without the line, accepts its documented forms, and builder.md names it within budget.
- [ ] #174 (with #30): `dev lit` bypass. DONE WHEN research-auditor.md names `dev lit get`, `dev lit get` prints authors, OA PDF link and full abstract, and a re-run of surface_usage.py shows direct literature API calls in at most 1 run in 20.
- [x] #175: `--from` need text (set D: −0.04 [−0.24, +0.16], §3). DONE WHEN `--from` drops stdlib and project-internal noise, prints its need text, and a fresh frozen set of at least 10 code cases is scored once against the current `--from`, with a paired interval.
- [ ] #211: a `--from` need-text design with a cited note. DONE WHEN the picked design is scored once on a fresh frozen held-out set of at least 20 code cases against #175's `need_from`, with a paired interval.
- [ ] #176: re-run on a newer model or date. DONE WHEN B0 and A3 from another model or a model at least 6 months newer are scored on the frozen TEST snapshot, and a post-cutoff stratum of at least 30 needs is scored once, with rules 10 and 13 re-applied in this note.
- [ ] #177: owner decision on A3 for new product dependencies. DONE WHEN the decision is recorded in §10 and, if A3, the builder prompt and #172 say when to run it.
- [x] #29 / PR #171, merged as 22d7ce9: R4 constants, `dev reuse check`, and name-then-verify text in builder.md, researcher.md, the research skill and HARNESS.md (§10 items 1-2).
- [ ] #196 build half: the F0 fit line. DONE WHEN the builder procedure and the `Reuse:` line require "fit: <package> does <need> via <API>" or "fit: unsure" for each new dependency, the completion gate accepts both forms, and a test covers them (§10 item 9). `dev reuse try` is not part of it and waits for #218.
- [ ] #217: context7 arm F2 on the frozen fit cases. DONE WHEN F2 runs once on both strata and §11 reports it against F0, F1 and F4r.
- [ ] #218: fit cascade on outside-sourced cases with repeated calls. DONE WHEN at least 40 new frozen cases not written by the researcher are scored for F0, F4r and the cascade, 3 runs each, with paired intervals in §11.
- [ ] (not filed, BELIEVED low value) An optional user-invoked `/reuse` skill (`disable-model-invocation: true`) as a front door for the owner. DONE WHEN filed after #172 ships, or dropped.
- [ ] (not filed; covered by #97) A `claude plugin eval` trigger-rate measurement of the reuse rule, with the prompt line alone vs the prompt line plus the hook. DONE WHEN #97 includes a reuse case.
