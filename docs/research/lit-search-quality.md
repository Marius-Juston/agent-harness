# Research: recall of dev lit search, WebSearch and the arxiv MCP on the papers merged notes cite

Status: v1.1 (2026-10-13; research-auditor fixes from PR #214 applied). Issue: #192. Serves #194 (`dev lit search --also`, PR #202) and the discovery lines of the researcher instructions and the research skill.
Labels: VERIFIED = measured here (script named; the private inputs and all raw answers are kept outside git, backed up in `<git-common-dir>/devkit/bench/lit-recall-private-2026-10-13/` (point `LITRECALL_PRIVATE` there to re-score), their SHA-256 in [runs_manifest.json](_scratch/lit-recall/scripts/data/runs_manifest.json) and PROTOCOL amendment 1); SOURCED = cited and fetched; BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit status` on 2026-10-13 showed OpenAlex (with a key), Crossref and Europe PMC up for every run. Semantic Scholar was DOWN all session (HTTP 429, keyless pool, "daily budget spent"). arXiv was throttled intermittently (times EDT): 429s at the first arxiv MCP test and during gold resolution (before 11:00), DOWN at the 11:02 status check, answering the status probes logged around the L0, L2 and L4 TUNE runs (about 11:10-11:23, [status.log](_scratch/lit-recall/scripts/data/runs/status.log) "arxiv answered"), stalling L1 from about 11:24 to 11:38 (shared cooldown streak 9, about 512 s per hold), not answering at the TEST L0 run (11:41, status.log) or for X until about 11:45, then answering X through 11:54. L0 never reached its arXiv fallback (OpenAlex plus Crossref or Europe PMC always filled 20), so L0 is OpenAlex plus that fallback, with no Semantic Scholar results. Also used: Claude Code's WebSearch, the arxiv MCP server 0.8.2, DataCite (for 15 arXiv ids), the GitHub API and PyPI JSON.
Companions: [software-discovery.md](software-discovery.md) (the #169 benchmark this one copies, and the `dev lit` surface choice). Round files: [_scratch/lit-recall/](_scratch/lit-recall/QUESTION.md), with the [PROTOCOL](_scratch/lit-recall/PROTOCOL.md) and its amendments 1-6, and the scripts and public data in [scripts/](_scratch/lit-recall/scripts/score.py).

## 0. Findings in one paragraph
A first pass of 8 model-written queries finds **under a tenth of the papers a merged note ends up citing, whichever tool runs it**. The TEST was run once: 8 notes, 496 cited works.
- `dev lit search -n 20` (L0) found a macro mean of **8.7%** of each note's works at depth 20 (micro 5.4%). The arxiv MCP `search_papers` (X) found **10.2%** (5.2%), and WebSearch (W) **4.5%** (2.8%).
- No arm was shown better than L0 under the pre-registered rule, and X was not shown worse. X - L0 = +1.5 points [-3.7, +7.2] at @20 and +1.5 [-4.3, +8.4] at @10. W - L0 = -2.3 [-5.4, 0.0] at @10, the deepest W reaches. With 8 notes, differences smaller than about 6 points per note cannot be detected. So **no tool change is triggered**.
- `dev lit`'s default sources stay, as tested without Semantic Scholar. L2 and L4 did not beat L0 on TUNE. L1 and L3, the two with arXiv, could not run because arXiv throttled this machine.
- The tools overlap little. X finds 14.6% of the works that have an arXiv id and 0% of the others; L0 finds 4.7% of the others. At the same 20 results per query, L0's top 10 plus X's top 10 beat L0's top 20 by +3.7 points [-0.3, +8.6] and X's top 20 by +2.2 [-2.6, +8.8] (exploratory). The full union of both top 20s finds 14.8% vs L0's 8.7%, but it reads twice as many results.
- For #194, `--also` (LA: 8 queries merged into one list of 20) showed no detectable per-note gain over the first query alone at equal depth (+0.7 points [-3.1, +3.4]) and +2 points per work (micro +2.0 [+0.6, +3.4]). It found 5.1 points [2.1, 8.4] less than running the 8 queries separately.
- In 16 of 20 sampled misses (80%, 95% CI about 58-92%), an automated OpenAlex search for the work's own title returns it in the top 5. So **the gap is mostly the queries, not the index**. Notes reach most of their sources through later, narrower searches and citation chasing, which this benchmark does not measure.

## 1. Question and scope
[QUESTION.md](_scratch/lit-recall/QUESTION.md) asks how well `dev lit search` finds the papers that merged notes cite, compared with the other discovery tools a researcher agent has.
- **Gold:** the papers cited by merged notes in this repository (A1-A4) and in a private consuming project (P01-P09). The private notes appear by code name only, since this repository is public.
- **Queries:** 8 per note, written from the note's pre-research question (its `QUESTION.md`; for P07, which has none, its issue's title and Context section) by a fresh, tool-less `claude -p --model claude-opus-5-5` call.
- **Out of scope:** citation chasing (`dev lit refs`, #193), later rounds, and the precision of what a tool returns.

## 2. Method (VERIFIED; pre-registered, [PROTOCOL.md](_scratch/lit-recall/PROTOCOL.md))
- **Units and gold.** 13 merged notes qualify: each has a pre-research question and cites at least 3 papers ([build_inputs.py](_scratch/lit-recall/scripts/build_inputs.py)). Gold is every work a note names by DOI, arXiv id, PMID or PMCID. [resolve_gold.py](_scratch/lit-recall/scripts/resolve_gold.py) resolves each to one record through OpenAlex, or through DataCite for arXiv ids OpenAlex lacks.
  - Counts ([gold_counts.json](_scratch/lit-recall/scripts/data/gold_counts.json)): 907 cited ids gave 889 works. 4 were dropped by type (dataset, erratum and the like), leaving 885 gold works. 19 DOIs or arXiv ids did not resolve. 9 TEST works have no OpenAlex domain (the `oa:none` group in [results_test.json](_scratch/lit-recall/scripts/data/results_test.json)).
  - Papers cited only by a publisher or PDF link are not gold.
- **Split.** It was frozen before any query existed: stratified by domain, 40/60, seed "192-split".
  - TUNE: A3 P03 P04 P05 P07 (389 works).
  - TEST: A1 A2 A4 P01 P02 P06 P08 P09 (496 works).
  - Domains: CS is A1-A4, P02, P05 and P07. EDU (education, psychology, health) is P01, P03, P04, P06 and P08. SEC is P09.
- **Arms** ([run_arms.py](_scratch/lit-recall/scripts/run_arms.py)). Every arm runs the same 8 query strings unchanged.
  - L0: `dev lit search -n 20 --source auto`.
  - L1-L4: TUNE-only `--source` variants.
  - W: WebSearch through `claude -p --model haiku`, with results read from the tool's own structured answer ([web_arm.py](_scratch/lit-recall/scripts/web_arm.py)). It returns about 9.3 links per query.
  - X: `search_papers` with max_results 20, over MCP stdio ([arxiv_arm.py](_scratch/lit-recall/scripts/arxiv_arm.py)).
  - Exploratory, added in amendment 2 at the lead's request for #194: L0-1 is the first query alone; LA is the first query `--also` the other 7, giving one list of 20.
- **Matching** ([score.py](_scratch/lit-recall/scripts/score.py)) is the same for every arm. A result matches a gold work by any of:
  - an identifier: DOI, arXiv id, PMID, PMCID or OpenAlex id, taken from the record or from its URL;
  - its normalised title, also compared with the note's link text for the work (amendment 3);
  - for results with no ids only, the first author's surname plus the year.
- **Matching audits.**
  - TUNE: a hand audit found all 13 non-identifier matches correct. In a seeded sample of 40 unmatched web results, 1 was a gold work whose OpenAlex title lacks its subtitle; amendment 3 fixed that before TEST.
  - TEST title matches at @20 (L0 2, W 8, X 0) were all correct by hand. One of W's 3 author-year matches was wrong (unit A1). Without it, W's macro@20 is 0.040 and its micro 0.026.
  - TEST unmatched web results: in the seeded sample of 40, 1 is a resource-library page for a gold meta-analysis and 1 possibly a conference document by a gold work's first author. A loose word-overlap screen over every unmatched TEST result ([loose_screen.py](_scratch/lit-recall/scripts/loose_screen.py)) flagged 24 L0, 10 X and 10 W candidate works; by hand, 1 W candidate (the same page) and 1 L0 candidate (a preprint record of a gold field study) are the gold work, X none. So each arm's undercount is about one work (0.2 points micro).
  - Many web results are repository or PDF pages titled by their host name, which no rule can match, so **W's recall is a lower bound**, though the screen suggests not by much.
- **Metric.** Recall@k for a note is the share of its gold works found within rank k of at least one of its 8 queries. The primary metric is macro recall@20, the mean over notes.
- **Intervals.** Paired 95% percentile bootstrap, 10,000 draws. The primary intervals resample notes (8 clusters, so they are wide); secondary intervals resample gold works.
- **Deviations,** all dated before TEST was scored ([TEST_RUNS.log](_scratch/lit-recall/scripts/data/TEST_RUNS.log)):
  - Amendment 4: arXiv throttled this machine's IP, so L1 and L3 are not measured, L\* = L0, and X ran on TEST only.
  - Amendment 5: PRs #190 and #202 merged before the TEST run. All 64 TEST lists were re-run with the merged `lit.py` and came out identical ([check_main_l0.py](_scratch/lit-recall/scripts/check_main_l0.py)).

## 3. TEST results (VERIFIED, one shot)
Scored by [score.py](_scratch/lit-recall/scripts/score.py) `test`; the numbers are in [results_test.json](_scratch/lit-recall/scripts/data/results_test.json). The queries were written by claude-opus-5-5 and WebSearch was driven by claude-haiku-5-5, on 2026-10-13.

| arm | macro@10 | macro@20 | micro@10 | micro@20 | results per query |
|---|---|---|---|---|---|
| **L0** `dev lit search -n 20` | 0.068 | **0.087** | 0.044 | 0.054 | 20 |
| X arxiv MCP `search_papers` | 0.083 | 0.102 | 0.038 | 0.052 | 20 |
| W WebSearch | 0.045 | 0.045 | 0.028 | 0.028 | 9.3 |
| L0-1 first query only (exploratory) | 0.029 | 0.029 | 0.006 | 0.006 | 20, one query |
| LA first query `--also` 7 (exploratory) | 0.026 | 0.036 | 0.014 | 0.026 | 20, one list |
| L0 + X union (exploratory) | 0.124 | 0.148 | 0.069 | 0.087 | 40 |
| L0 + W union (exploratory) | 0.083 | 0.101 | 0.056 | 0.065 | 29 |

Paired differences in percentage points, with 95% intervals over notes and over gold works:

| pair | @10 notes | @10 works | @20 notes | @20 works |
|---|---|---|---|---|
| X - L0 | +1.5 [-4.3, +8.4] | -0.6 [-2.6, +1.4] | +1.5 [-3.7, +7.2] | -0.2 [-2.4, +2.0] |
| W - L0 | -2.3 [-5.4, 0.0] | -1.6 [-3.4, 0.0] | -4.1 [-7.2, -1.2] | -2.6 [-4.6, -0.8] |
| LA - L0-1 | -0.2 [-3.6, +2.2] | +0.8 [-0.2, +2.0] | +0.7 [-3.1, +3.4] | +2.0 [+0.6, +3.4] |
| LA - L0 | -4.2 [-6.9, -1.9] | -3.0 [-4.6, -1.6] | -5.1 [-8.4, -2.1] | -2.8 [-4.4, -1.4] |
| L0+X - L0 | +5.6 [+1.6, +10.9] | +2.4 [+1.2, +3.8] | +6.2 [+2.8, +10.2] | +3.2 [+1.8, +4.8] |
| L0+W - L0 | +1.5 [+0.2, +3.0] | +1.2 [+0.4, +2.2] | +1.4 [+0.2, +2.9] | +1.0 [+0.2, +2.0] |

**Same 20 results per query** ([equal_budget.py](_scratch/lit-recall/scripts/equal_budget.py), exploratory). L0's top 10 plus X's top 10 beat L0's top 20 by +3.7 points [-0.3, +8.6] over notes and +1.4 [+0.2, +2.8] over works, and X's top 20 by +2.2 [-2.6, +8.8] over notes and +1.6 [-0.2, +3.4] over works. The same split with W instead of X gives -0.3 [-2.9, +1.9] against L0's top 20.

**Coverage at @20.** Of the 496 works, no arm found 449. X alone found 15, L0 alone 12 and W alone 4.

Per-note recall@20:

| note | L0 | X | W |
|---|---|---|---|
| A1 | 0.17 | 0.35 | 0.09 |
| A2 | 0.22 | 0.11 | 0.11 |
| A4 | 0.08 | 0.12 | 0.00 |
| P01 | 0.02 | 0.01 | 0.01 |
| P02 | 0.05 | 0.07 | 0.04 |
| P06 | 0.07 | 0.02 | 0.03 |
| P08 | 0.01 | 0.03 | 0.00 |
| P09 | 0.06 | 0.11 | 0.08 |

TUNE macro@20, for the record ([results_tune.json](_scratch/lit-recall/scripts/data/results_tune.json)): L0 0.141, L2 0.139, L4 0.138, W 0.063, L0-1 0.080, LA 0.027.

## 4. Per domain (VERIFIED, TEST, recall@20)
| group (n) | L0 | X | W | L0 + X |
|---|---|---|---|---|
| notes: CS (4) | 0.132 | 0.162 | 0.059 | 0.227 |
| notes: EDU (3) | 0.037 | 0.020 | 0.015 | 0.056 |
| notes: SEC (1) | 0.056 | 0.111 | 0.083 | 0.111 |
| works with an arXiv id (178) | 0.067 | 0.146 | 0.039 | 0.157 |
| works without (318) | 0.047 | 0.000 | 0.022 | 0.047 |
| OpenAlex domain Physical Sciences (202) | 0.059 | 0.099 | 0.025 | 0.119 |
| Social Sciences (166) | 0.048 | 0.024 | 0.024 | 0.060 |
| Life Sciences (95) | 0.074 | 0.011 | 0.042 | 0.084 |
| Health Sciences (24) | 0.000 | 0.042 | 0.000 | 0.042 |

- **X is an arXiv tool.** It is the best single arm on computing notes and nearly useless on education and psychology notes, whose works are mostly journal papers.
- **L0 is the only arm that reaches works without an arXiv id** at any useful rate.
- **Every arm is weakest on EDU notes.** They cite 68-144 works each. In the sampled misses, many of those are specific trials and meta-analyses (§6).

## 5. What the pre-registered rules decide (VERIFIED arithmetic; PROTOCOL rule 10)
- **(a) Name W or X next to `dev lit search`?** Only if it beat L\* = L0 at @10 (and, for X, also at @20) with an interval above 0. X's intervals cross 0 at both depths, and W is lower. **Not triggered.**
- **(b) Change `dev lit`'s defaults?** L\* is L0, so **`dev lit`'s default sources stay**. L2 (`openalex,crossref`) and L4 (`openalex,europepmc`) were below L0 on TUNE. L1 and L3, with arXiv, were not measured.
- **(c) Result: no tool change.** The union rows above are reported as what a combined pass adds: +6.2 points at @20 for L0 + X and +1.4 for L0 + W.

## 6. Misses (PROTOCOL rule 11; VERIFIED, [misses.py](_scratch/lit-recall/scripts/misses.py), [misses_test.json](_scratch/lit-recall/scripts/data/misses_test.json))
- **449 of 496 TEST works were in no arm's top 20 for any query.** Rule 11's causes reduce to one here: every L0 and X list held a full 20 results and the matching rules are shared, so every miss is "not retrieved".
- **The index holds most of them.** For a seeded sample of 20 misses, an automated (not hand) `dev lit` OpenAlex search for each work's own title puts it in the top 5 for 16 (80%, Wilson 95% CI about 58-92%). A narrower query would have found them.
- **The sample leans to education notes.** 18 of the 20 come from EDU notes (P01 7, P08 6, P06 5), which hold most of the misses.
- **What they are** (BELIEVED, from reading the 20 titles): mostly specific studies that a topical first-pass query does not name, such as a randomised trial of one tool, a field experiment, meta-analyses of one intervention, and a medical deskilling study cited as an analogy.
- **How notes reach them** (BELIEVED): through later sub-question searches, citations inside papers they read, and the round structure of the research skill. The benchmark's 8 opening queries do not model this. An agent that expands citations recovers much of such recall in PaSa's ablation (§8).

## 7. Threats to validity
- **Few clusters.** There are only 8 TEST note clusters, so note-level intervals are wide; X - L0 spans 11 points. Work-level intervals ignore that clustering, so the note-level ones are primary.
- **One query syntax.** Every tool got the same strings. A real agent adapts its syntax: quoted phrases and `cat:` filters for the arxiv MCP, `--years` for `dev lit`. That likely understates X and L0 more than W. BELIEVED.
- **Gold bias.** Gold counts only works cited with an identifier. A note cites what its researchers found with these same tools, so tools they used more get credit. The two private notes' coverage lines read here (P02, P09) name OpenAlex, Crossref, Europe PMC, arXiv and WebSearch, so no arm is favoured outright. ASSUMED for the other notes.
- **Source outages.**
  - Semantic Scholar was down, so L0 was effectively OpenAlex (plus Crossref and Europe PMC fill), and an S2-backed run could differ; that is unmeasured.
  - arXiv was down for over an hour, so an agent here would often have had no X at all.
- **One model and date.** The queries came from claude-opus-5-5 on 2026-10-13; another model would write other queries.

## 8. Context from prior work (SOURCED, abstracts)
- **LitSearch** (597 questions about ML and NLP papers) found that commercial search engines and research tools such as Google Search "lag behind the best dense retriever by up to 32 recall points" ([LitSearch: A Retrieval Benchmark for Scientific Literature Search](https://doi.org/10.18653/v1/2024.emnlp-main.840)). That matches W being the weakest arm here.
- **A comparison of 28 academic search systems** found "substantial differences" in precision, recall and reproducibility. It judged Google Scholar "inappropriate as principal search system" for evidence synthesis ([Which academic search systems are suitable for systematic reviews or meta-analyses?](https://doi.org/10.1002/jrsm.1378), Research Synthesis Methods).
- **PaSa** (ACL 2025; its baselines are GPT-4o-era, so its absolute numbers are historical) is an LLM agent that searches, reads papers and expands their citation lists. Removing the citation-expansion action cut its crawler recall by 22.98% on AutoScholarQuery and 32.21% on RealScholarQuery (§5.4 ablation), and the full agent beat Google with GPT-4o-paraphrased queries by 37.78% in recall@20 ([PaSa: An LLM Agent for Comprehensive Academic Paper Search](https://arxiv.org/abs/2501.10120), SOURCED abstract and §5.4). This supports §6: query-only first passes leave most recall to citation chasing.
- **OpenAlex semantic search** (`/works?search.semantic=`) is in beta; OpenAlex's 2026-02-24 pricing post says "we don't recommend using it for sensitive production workflows yet" and gives every API key "$1 of free usage per day" ([New Features and Usage-Based Pricing](https://blog.openalex.org/?p=3953), SOURCED). Its documentation caps it at 50 results per query and 1 request per second ([Semantic search](https://help.openalex.org/api/semantic-search), SOURCED). One probe on 2026-10-13 with this project's free key returned `meta.cost_usd` 0.001, the same as a keyword search, so about 1,000 calls a day fit the free allowance (VERIFIED, one call through `devkit.lit`'s shared client). It is a candidate arm for #213.
- **OpenAlex coverage.** On 16.8 million recent publications shared by OpenAlex, Web of Science and Scopus, OpenAlex had comparable reference numbers and internal coverage, but fewer abstracts ([Reference coverage analysis of OpenAlex compared to Web of Science and Scopus](https://doi.org/10.1007/s11192-025-05293-3), Scientometrics 2025). This fits §6: the misses are in the index.

## 9. Recommendation for this project
1. **Keep `dev lit search` as the first literature tool, with its current defaults.** VERIFIED (rule 10): no arm was shown better, X was not shown worse, and WebSearch was lower at every depth it reaches. With 8 notes, differences under about 6 points per note cannot be detected, and the defaults were tested without Semantic Scholar.
2. **Whether to add the arxiv MCP is left to #213.** At the same 20 results per query, L0's top 10 plus X's top 10 beat L0's top 20 by +3.7 points [-0.3, +8.6] and X's top 20 by +2.2 [-2.6, +8.8]: suggestive, not shown. The full union (+6.2 points [+2.8, +10.2]) reads twice as many results. Restricting it to computing questions is a post hoc reading of §4 (X found nothing outside arXiv), so it is BELIEVED, not tested.
   - Cost: a few seconds per query when arXiv answers; arXiv throttled this machine for long stretches of this session.
   - The tool: the arxiv-mcp-server repository is Apache-2.0, has 3,214 stars, was last pushed 2026-10-08 and is not archived (GitHub API, 2026-10-13). Researcher instructions already name the arxiv MCP after `dev lit`.
3. **`--also` (#194): no detectable per-note gain; +2 points per work.** At one list of 20 it matched the first query alone per note (+0.7 [-3.1, +3.4]) and found 2 more points of works (micro +2.0 [+0.6, +3.4]). Running the phrasings as separate `dev lit search` calls found 5.1 points [2.1, 8.4] more than one `--also` list of 20. So use `--also` when the reading budget is one deduplicated list of 20, and separate calls when it is not. #169's package search differed: three queries round-robin doubled hit@3 over one query, 0.14 to 0.30 ([r1-benchmark F2](_scratch/software-discovery/r1-benchmark.md), TUNE).
4. **Do not judge a research round by first-pass search.** About 90% of what a note cites is in no opening query's top 20 (§6), and PaSa's ablation puts a large share of recall on citation expansion (§8). Recall comes from sub-question searches and citation chasing: the research skill's rounds and `dev lit refs` (#193). BELIEVED; #212 measures it.

## Sources
- LitSearch: A Retrieval Benchmark for Scientific Literature Search. EMNLP 2024. https://doi.org/10.18653/v1/2024.emnlp-main.840 (abstract, via `dev lit get`)
- Which academic search systems are suitable for systematic reviews or meta-analyses? Evaluating retrieval qualities of Google Scholar, PubMed, and 26 other resources. Research Synthesis Methods, 2019 (online). https://doi.org/10.1002/jrsm.1378 (abstract)
- Reference coverage analysis of OpenAlex compared to Web of Science and Scopus. Scientometrics, 2025. https://doi.org/10.1007/s11192-025-05293-3 (abstract)
- He, Huang, Feng, Lin, Zhang, Li, E. PaSa: An LLM Agent for Comprehensive Academic Paper Search. ACL 2025. https://arxiv.org/abs/2501.10120 (abstract, §5.4)
- OpenAlex. New Features and Usage-Based Pricing. 2026-02-24. https://blog.openalex.org/?p=3953
- OpenAlex. Semantic search (API documentation). https://help.openalex.org/api/semantic-search (fetched 2026-10-13)
- arxiv-mcp-server 0.8.2. https://github.com/blazickjp/arxiv-mcp-server (GitHub API and PyPI JSON, 2026-10-13)
- Protocol and data: [PROTOCOL.md](_scratch/lit-recall/PROTOCOL.md), [results_test.json](_scratch/lit-recall/scripts/data/results_test.json) and [split.json](_scratch/lit-recall/scripts/data/split.json), whose SHA-256 is recorded in the protocol.

## Follow-ups
- [ ] #212: trace how a full research round gets its sources: the share of a note's cited works first reached by first-pass search, by later sub-question search, and by citation chasing. DONE WHEN the merged notes of at least 3 research rounds are traced from their scratch files and `dev lit refs` output, with the share per path reported.
- [ ] #213: confirm L0 + arxiv MCP in a pre-registered rerun, with OpenAlex semantic search as a candidate arm. Allow tool-specific query syntax, use a fresh set of at least 8 merged notes, run L1/L3 (`dev lit` with arXiv) while arXiv answers, and run with Semantic Scholar up or keyed. DONE WHEN the union, L1 and L3 are each scored once against L0 with paired note-level intervals, and rule 10 is re-applied in this note.
