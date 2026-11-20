# Research: a known-answer evaluation of research (`uv run dev eval research`)

Status: v0.9 (2026-11-19). Issue: #590. The baseline run on main has not been made yet (§4). Serves #584, #9 and any change to the research skill, its sources or its tools: each gets measured the way the gate's changes are.
Labels: VERIFIED = measured in this repo (test or script named); SOURCED = cited and fetched; BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit search` on 2026-11-20 printed `discovery 2026-11-20: OK (openalex, s2 answered)` (one query on benchmarks of deep research agents); `dev lit get` then saw an s2 HTTP 429. The design reuses #192, so it needed little new literature.
Companions: [lit-search-quality.md](lit-search-quality.md) (#192: the gold, the queries, the matching rules and the metric this eval reuses), [harness-efficiency.md](harness-efficiency.md) §10 (paired comparison of `dev eval` runs). Code: `devkit/research_eval.py`. Question set: `evals/research/questions.yaml` and `evals/research/gold.json`, built by [build_gold.py](_scratch/research-eval/build_gold.py).

## 0. Findings in one paragraph
- `uv run dev eval research` scores a fixed set of 7 questions whose answer notes are merged here. The notes cite 137 works in total. VERIFIED (`tests/test_evals.py::test_the_research_question_set_has_six_questions_with_queries_notes_and_gold`).
- Most of the eval needs no model. It replays the 8 opening queries of each question through `dev lit search -n 20`, makes one snowball hop (`dev lit refs` and `cited-by`) from the first 5 results, and reports recall@10, recall@20, snowball recall and the tokens an agent reads. Every HTTP answer is recorded once in a cassette under the state dir and replayed after that, so a rerun on the same code makes no calls and gets the same numbers. VERIFIED (`test_research_eval_replays_its_cassette_so_a_rerun_makes_no_call_and_a_change_shows_in_compare`).
- Only the claim audit needs an agent. It runs one `claude -p` call per sampled claim, 2 claims per question by default, so 14 calls per run. A spend cap stops it (`--max-usd`, default $5). VERIFIED (`test_research_eval_stops_auditing_at_the_spend_cap`).
- **The baseline is not recorded yet.** The worktree's command guard refused `uv run dev eval research`, apparently because it reads the word `eval` as the shell's `eval` (§4).

## 1. What a run measures
Every number is per question, and the record stores per-question rows, so two runs can be paired by question.
- **recall@10 and recall@20**: the share of the note's cited works that any of its 8 queries ranks in the top k. This is #192's metric, at #192's L0 setting (`--source auto`, 20 results). VERIFIED (`test_research_eval_fake_run_records_recall_claim_support_and_tokens_per_question`).
- **snowball recall**: the works found in the top 20 plus the works one hop away. The hop chases `refs` and `cited-by`, 20 works each, from the first 5 distinct results that have an OpenAlex id, taken in round-robin rank order across the queries. #192 found that a first pass misses about 90% of what a note cites. It found most of those misses in the index under their own titles, and it left citation chasing unmeasured ([lit-search-quality.md](lit-search-quality.md) §6 and §9 item 4). This metric is the first number for the snowball path. BELIEVED: one hop from 5 seeds is a cheap proxy, not the full research rounds.
- **read tokens**: the characters of `dev lit` output an agent would read for all of this, divided by 4. No tokenizer is called, so the count stays offline. It is an estimate and is only comparable between runs of this eval.
- **supported share**: the share of the sampled claims that the auditor marks supported. Unreachable and unparsed verdicts are left out of the denominator. The sample is seeded per question, and a claim is a line of the note that links a web source, outside its Sources list. The auditor prompt uses the research-auditor agent's verdicts (supported, partly, unsupported, unsourced) and its rule that papers are fetched with `dev lit get`. `--notes DIR` audits candidate notes from a research run instead of the merged ones.
- **audit tokens and cost**: the billed tokens and dollars of the auditor calls, read from each call's own transcript (`evals.parse`).

**Prior art (SOURCED, abstracts via `dev lit get`).**
- BrowseComp-Plus replaces live web search APIs with a fixed corpus. Its authors say dynamic, opaque APIs "hinder fair comparisons and reproducibility" ([BrowseComp-Plus](https://arxiv.org/abs/2508.06600)). The cassette here does the same job for `dev lit`'s sources: it freezes them.
- DeepResearch Bench scores retrieval by "effective citation count and overall citation accuracy" ([DeepResearch Bench](https://arxiv.org/abs/2506.11763)). The two halves here play the same parts: cited-works recall and the share of claims supported.

`--compare A B` gives B minus A for each metric, as the mean paired difference over questions with a seeded 95% percentile bootstrap interval. VERIFIED (the replay test above shows a snowball drop of -0.333 [-0.333, -0.333] when the hop is switched off).

## 2. The question set
- **A1-A4** are #192's four public units: architecture-consolidation, fix-now-vs-defer, software-discovery and test-suite-design. Their gold and queries are copied unchanged from the [lit-recall data](_scratch/lit-recall/scripts/data/runs_manifest.json), so the eval's L0 numbers can be checked against #192's per-note table. The private P units cannot be used, because this repository is public.
- **B5-B7** are decision-records-and-drift, harness-efficiency and issue-pr-automation. These are the other merged notes that have a pre-research `QUESTION.md` and cite at least 3 works by DOI or arXiv id.
  - Their queries were written on 2026-11-19 by a fresh, tool-less `claude -p --model claude-opus-5-5`. It used #192's fixed [query prompt](_scratch/lit-recall/scripts/data/query_prompt.md) and runner (`run_agent.py --batch 1`) and saw the question only.
  - Their gold came from [build_gold.py](_scratch/research-eval/build_gold.py), which uses #192's resolver: all 59 cited ids resolved, none dropped. VERIFIED (the `counts` key of `evals/research/gold.json`).
- **Limits.**
  - 7 questions are few. #192 put the smallest detectable per-note difference at about 6 points with 8 notes ([lit-search-quality.md](lit-search-quality.md) §0), and this set is smaller. BELIEVED: the eval will show large changes, such as a source added or the snowball changed, and not small ones.
  - Every question is from computing, which is where X (the arxiv MCP) did best in #192.
  - The gold is what the notes cited, so it carries #192's gold bias ([lit-search-quality.md](lit-search-quality.md) §7).
  - A1-A4's gold is frozen as of 2026-10-13. Later edits to those notes are not in it.

## 3. Cost and determinism
- **Deterministic part.**
  - About 56 search calls plus 70 chase calls per run, each followed only while sources fall short. All of them are live only the first time; after that they replay. VERIFIED by count in the fake run; the real count depends on fallback.
  - A failure (a source down or throttled) is recorded and replays as the same failure. A rerun therefore sees the sources the recording saw. To re-record, delete `<state>/evals/research/cassette/`.
  - The record counts live, replayed, missing and failed calls, so a comparison shows whether new answers were fetched.
- **Agent part.**
  - 14 auditor calls per run at the defaults. The auditor gets only `WebFetch` and `uv run dev lit get`, up to 12 turns, and no MCP servers.
  - `--claims 0` skips it. `--model` picks the auditor's model. `--max-usd` caps the spend and records how many claims were skipped.
  - The auditor uses whatever credentials `claude` finds in the environment the caller sets. The eval never reads a key itself.
- ASSUMED, until the baseline: a Sonnet-class auditor costs on the order of $0.05-0.30 per claim, so a default run costs a few dollars.

## 4. Baseline on main: not yet run
The DONE WHEN item "a baseline run on current main is recorded" is open. The builder's sandbox refused `uv run dev eval research --offline --claims 0 --label probe` with "this command runs a string through eval". The refusal is a pattern match on the subcommand name, and the command was not worked around. The lead or owner runs, once the guard allows it:
```bash
uv run dev eval research --label baseline-main --claims 2 --model sonnet --max-usd 5
```
Then add the printed table here as §4, labelled VERIFIED with the record's name, and check that A2's recall@20 is close to #192's L0 value. An exact match is not expected: the index has changed since 2026-10-13, and Semantic Scholar may answer now.

## Sources
- Du, Xu, Zhu, Wang, Mao. DeepResearch Bench: A Comprehensive Benchmark for Deep Research Agents. 2025. https://arxiv.org/abs/2506.11763 (abstract)
- Chen, Ma, Zhuang, et al. BrowseComp-Plus: A More Fair and Transparent Evaluation Benchmark of Deep-Research Agent. 2025. https://arxiv.org/abs/2508.06600 (abstract)
- [lit-search-quality.md](lit-search-quality.md): #192's protocol, gold, queries and results.
- [harness-efficiency.md](harness-efficiency.md): the paired-run comparison of `dev eval`.
