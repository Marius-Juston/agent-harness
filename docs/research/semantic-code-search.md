# Research: semantic code search for agents: repo map, structural, embeddings, BM25 and LSP against dev code find

Status: v1 (2026-11-19). Issue: #495. Serves the build issues in Follow-ups.
Labels: SOURCED = cited and fetched; VERIFIED = measured in this repo (script named, under [_scratch/semantic-code-search/scripts/](_scratch/semantic-code-search/scripts/)); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit status` on 2026-11-19 showed openalex, crossref, europepmc and arxiv up and s2 DOWN (HTTP 429); every `dev lit search` answered from openalex alone, so Semantic Scholar-only records were missed. Also used: `uv run dev reuse check` (PyPI, 2026-11-19), the GitHub REST API, aider's `repomap.py` source at commit 5dc9490, and the Claude Code LSP tool of this session.
Companions: [devkit-reuse-survey.md](devkit-reuse-survey.md) (the house method for weighing a runtime dependency), [software-discovery.md](software-discovery.md) (model2vec potion-base-8M, already the optional `semantic` extra of `dev reuse`), [issue-pr-automation.md](issue-pr-automation.md) (the stdlib BM25 in `devkit/board.py`), [architecture-consolidation.md](architecture-consolidation.md) §6 (the pushed signature map of #122).

## 0. Findings in one paragraph
**Add two stdlib-or-gate-only commands and no embedding dependency.** On 47 *which code does Z* questions replayed from this repository's history (an issue's title asked of the tree before the commit that closed it; the answer is the functions that commit changed), **BM25 over function units outside tests** (devkit's own `bm25`, over name, signature, docstring and body identifiers) put the answer in its 5 one-line cards for **0.53 [0.39, 0.67]** of questions, against **0.17** for one `dev code find` call, at **372 tokens per answered question against 9,563 (26 times fewer)**; the right file was in its top 5 for 0.96. Embeddings did not beat it significantly: the best single model (jina code, 615 MB) +0.04 [−0.13, +0.21], the best fusion (BM25 + bge-small) +0.09 [−0.02, +0.19], for 187 MB of new install (fastembed and onnxruntime), a 65-615 MB model download and an index that took 4-5 minutes (bge-small) to build cold on this machine; model2vec (the existing optional extra) fused with BM25 gave +0.04. An aider-style PageRank repo map answered 0.11: it ranks widely used names, not the ones a task needs. On 22 *what calls Y* questions, **ty's language server, started by devkit with the worktree as its root, found all 92 callers with no false one in 99 tokens per question**, against recall 0.93, precision 0.44 and 629 tokens for `dev code refs`; ast-grep's call patterns were nearly as good (0.98 / 0.99). Claude Code's own LSP tool, also backed by ty here, returned only the declaration from a worker's worktree and once resolved into another agent's worktree. Filed: #545 (`dev code search`, P1), #547 (`dev code refs --exact` through ty, P2, alongside #59's ty gate stage) and #548 (does it cut a whole agent run's tokens). The main limit: every number here is one tool call, not a whole agent run.

## 1. Question and scope
The owner wants agents to find code faster and with fewer tokens (#495), and made **tokens returned per answered question** a primary criterion next to hit rate. Today an agent asks `uv run dev code find <regex>` (a capped `git grep`), `dev code refs <Symbol>` (a word grep) and `dev code outline/show`. This note measures, on this repository, which of the candidate approaches answers three kinds of question best per token: *which code does Z* and *where is X* (a task description to the function to change), and *what calls Y*. Every candidate that would become a runtime dependency of `agent-harness` reaches every pinned project, so each is weighed by its install size, index cost and licence, as [devkit-reuse-survey.md](devkit-reuse-survey.md) does.

Out of scope: an agent-in-the-loop measurement (does a better first answer change a whole run's tokens?); it is the first follow-up. Markdown and docs search: only Python functions and methods are retrieval units.

## 2. Method

### 2.1 Questions with known answers from this repository's history (VERIFIED, [mine.py](_scratch/semantic-code-search/scripts/mine.py))
- **47 localization questions** (*which code does Z / where is X*). For each first-parent commit that changed non-test Python code (106 commits), the question is **the title of the issue it closed**, asked of **the tree before the commit**, and the answer is the set of functions that existed in that tree and that the commit modified. Kept: 1-3 such functions after dropping six registration functions every new command touches (`cli.build`, `cli.run`, `cli.main`, `config.parse`, `gate.stages`, `docs.render`; the rule was fixed before scoring). Asking the parent tree means the answer's docstring cannot echo the issue it was written for. Each tree has 1,692 units on average (functions and methods in every tracked `.py` outside `docs/`). The list is [questions_hist.json](_scratch/semantic-code-search/questions_hist.json).
- **22 caller questions** (*what calls Y*), asked at HEAD (`f4ba1c9`): Y is every top-level function of the six modules the consolidation series (#442-#450) moved code into that 3-15 non-test functions name ([pick_callers.py](_scratch/semantic-code-search/scripts/pick_callers.py) prints the pool; the rule was fixed before scoring). Gold (92 caller functions) is what both ty's language server and a stdlib import-resolving call graph report, plus each of the 22 disagreements judged by reading the line: all 22 were true callers that reach Y through `workflow.Y`, a re-export the consolidation left behind ([eval_callers.py](_scratch/semantic-code-search/scripts/eval_callers.py), `ADJUDICATED`). Import statements are not callers.

### 2.2 Arms
Each localization arm returns a ranked list of functions; the indexed arms print the first 5 as one-line cards (`path:line  name(args)  # first doc line`, about 35 tokens each), the way `dev code outline` prints a file ([eval_nl.py](_scratch/semantic-code-search/scripts/eval_nl.py), [corpus.py](_scratch/semantic-code-search/scripts/corpus.py)).

| arm | what it is | new dependency |
|---|---|---|
| grep | `dev code find` as an agent runs it: the 3 rarest content words of the title as one case-insensitive alternation, output grouped by file, 6 lines per file, 60 lines in all; rank = order of the output lines | none |
| grep-all | the same with every content word of three letters or more | none |
| bm25-head | `devkit.board.bm25` (Lucene idf, k1 1.5, b 0.75) over path words, qualname, signature and the docstring's first paragraph | none (stdlib, already in devkit) |
| bm25-full | the same plus the body's identifiers, attribute names and string literals | none |
| m2v-8M / m2v-32M | model2vec static embeddings potion-base-8M / potion-retrieval-32M, cosine over the full text | model2vec (the existing optional extra) |
| bge-small | fastembed BAAI/bge-small-en-v1.5 (Qdrant's quantized ONNX), cosine over the full text truncated at 512 tokens | fastembed + onnxruntime |
| jina-code | fastembed jinaai/jina-embeddings-v2-base-code, same | fastembed + onnxruntime |
| rrf(bm25+X) | reciprocal-rank fusion (k = 60) of bm25-full and embedding arm X | as X |
| repomap | aider's `get_ranked_tags` reimplemented on stdlib `ast`: a file graph referencer → definer weighted by sqrt(references) with aider's multipliers (×10 mentioned identifier, ×10 long snake/camel name, ×0.1 private, ×0.1 defined in more than 5 files), PageRank personalised on files whose path matches a title word, rank spread over definitions | none (aider itself uses networkx and tree-sitter) |

No arm was tuned on these questions: BM25 keeps devkit's constants, the models are used as published, and RRF's k = 60 is the usual default. **One choice was made after a first look:** after the first lexical run (before any embedding ran), I added the `src` view, which drops tests and eval fixtures from every ranking, because test functions named after the feature crowded the top 5. `dev code refs` already prints tests last. Both views are reported.

Caller arms: `refs` is `uv run dev code refs Y` itself (lines mapped to their enclosing function); `astgrep` is ast-grep 0.51.3 with the patterns `Y($$$)` and `$O.Y($$$)`; `ty` is ty 0.0.107's language server (`textDocument/references` at Y's definition), started by the script with the worktree as its root; `resolver` is the stdlib call graph.

Tokens are characters / 4 (ASSUMED proxy; the arms are compared by ratio, and every arm's output is the same mix of paths and code). Latency is wall time on the owner's WSL2 machine while about 15 other agents ran, so it is an upper bound.

## 3. Findings

### 3.1 Localization: BM25 over function units beats grep 3x on hit rate at 1/26 of the tokens per answer (VERIFIED, [score.py](_scratch/semantic-code-search/scripts/score.py))
47 questions; `src` view for the indexed arms (tests and eval fixtures dropped from the ranking); grep's own output for grep. Top-5 has a Wilson 95 % interval. **Tokens per answer** = all tokens the arm printed over the 47 questions / questions answered in its top 5. Query ms is warm (index and model in memory); §3.2 gives cold costs.

| arm | top-1 | top-5 [95 % CI] | file in top 5 | tokens printed (mean) | tokens per answer | median query ms |
|---|---|---|---|---|---|---|
| grep (`dev code find`, 3 rarest words) | 0.04 | 0.17 [0.09, 0.30] | 0.49 | 1,628 | 9,563 | 241 |
| grep-all (every content word) | 0.00 | 0.00 [0.00, 0.08] | 0.06 | 1,645 | (none answered) | 89 |
| bm25-head | 0.19 | 0.40 [0.28, 0.55] | 0.94 | 197 | 488 | 7 |
| **bm25-full** | **0.23** | **0.53 [0.39, 0.67]** | **0.96** | **198** | **372** | 15 |
| m2v-8M | 0.15 | 0.47 [0.33, 0.61] | 0.89 | 178 | 380 | 10 |
| m2v-32M | 0.19 | 0.43 [0.30, 0.57] | 0.91 | 174 | 409 | 15 |
| bge-small | 0.28 | 0.53 [0.39, 0.67] | 0.91 | 169 | 317 | 26 |
| rrf(bm25+m2v-8M) | 0.26 | 0.57 [0.43, 0.70] | 0.91 | 188 | 327 | 25 |
| rrf(bm25+m2v-32M) | 0.19 | 0.51 [0.37, 0.65] | 0.91 | 186 | 364 | 29 |
| jina-code | 0.23 | 0.57 [0.43, 0.70] | 0.85 | 170 | 296 | 37 |
| rrf(bm25+bge-small) | 0.28 | 0.62 [0.47, 0.74] | 0.96 | 183 | 297 | 45 |
| rrf(bm25+jina-code) | 0.23 | 0.60 [0.45, 0.72] | 0.91 | 184 | 309 | 53 |
| repomap | 0.02 | 0.11 [0.05, 0.23] | 0.45 | 117 | 1,097 | 17 |

The full tables (both views, every paired difference) are in [scores_nl.txt](_scratch/semantic-code-search/scores_nl.txt).

- **BM25 over function units against grep:** +0.36 top-5 [+0.21, +0.51] (paired bootstrap, 10,000 resamples), at **372 tokens per answer against 9,563, 26 times fewer**. Even counting a grep answer anywhere in its capped output (the agent reads all of it), grep answers 12 of 47 at 6,376 tokens per answer. VERIFIED.
- **Grep's failure is the cap, not the words.** The 60-line cap is filled in alphabetical file order, so the right file is often cut (gold file in the first 5 files: 0.49). Alternating every content word fills the cap with common words and answers none. A real agent runs several greps and reads more; this measures one call. VERIFIED (the single-call limit is the main caveat, §5).
- **The body's identifiers help BM25:** bm25-full against bm25-head +0.13 top-5 [−0.04, +0.28], not significant. VERIFIED.
- **Dropping tests matters more than the ranker.** Over every unit (tests included), top-5 falls to 0.28 for bm25-full, 0.32 for m2v-8M, 0.43 for bge-small and 0.40 for rrf(bm25+bge-small): test functions named after the feature fill the top 5. VERIFIED.
- **The right file is almost always in BM25's top 5 (0.96)**, so `search` then `outline` of that file reaches the function on nearly every question. VERIFIED.
- **The ceiling is below 1.** All of bm25-full, m2v-8M, bge-small and repomap miss 9 of 47. In about 6 of them the gold is incidental wiring the commit touched (`_integrate` for a HANDOFF generator, `setup.staleness` for decisions.toml, `workflow.is_harness` for an install guard). BELIEVED (my reading of the 9).

### 3.2 Embeddings and fusion: no significant gain over BM25 at this size, at a large cost (VERIFIED)
- **Alone, no embedding beats BM25:** jina-code (a code-specific model, 0.64 GB in fastembed's model list) − bm25-full +0.04 [−0.13, +0.21]; bge-small 0.00 [−0.15, +0.15]; m2v-8M −0.06 [−0.23, +0.11]; m2v-32M −0.11 [−0.26, +0.04]. The larger static model is not better than the 8M one here. VERIFIED.
- **Fusion helps a little, not significantly:** rrf(bm25+bge-small) − bm25-full **+0.09 [−0.02, +0.19]**; rrf(bm25+jina-code) +0.06 [−0.06, +0.19]; rrf(bm25+m2v-8M) +0.04 [−0.09, +0.17]. The best tokens per answer are jina-code's 296 and rrf(bm25+bge-small)'s 297, against BM25's 372: every indexed arm prints the same 5 cards, so the one that answers more spends less per answer. VERIFIED.
- **The cost is the index, not the query.** A cold one-shot run at HEAD (2,515 units, nothing persisted) takes **1.1-1.4 s for BM25** (0.9-1.0 s of it is parsing with `ast`), **2.5-3.0 s for model2vec** (model load 1.1-1.6 s, encoding 0.4-0.5 s) and **256-309 s for bge-small** (encoding 254-307 s on this CPU, shared with other agents); jina-code's first tree took over 15 minutes and 7.7 GB of resident memory with batches of 4 (a run with batches of 32 was killed). bge-small and jina-code would need a persisted index updated per changed function in every worktree; model2vec and BM25 can rebuild per call. VERIFIED ([bench_cold.py](_scratch/semantic-code-search/scripts/bench_cold.py), 2-3 runs each).
- A difference of +0.09 on 47 questions needs about 4 times as many questions to separate from zero at this variance. BELIEVED (the interval half-width scales with 1/√n: 0.105 · √(47/188) ≈ 0.05).

### 3.3 The repo map ranks the wrong things for a question (VERIFIED; SOURCED contrast)
- The aider-style map answers 0.11 at top 5 (0.45 for the file), −0.43 [−0.60, −0.26] against bm25-full. It ranks the definitions of names used everywhere: `read_guard.out`, `paths.common_dir`, `board.get` and `config.get` are in its top 5 for 21-32 of the 47 questions. References are matched by name, in aider's source as here, so every `dict.get` call counts for `board.get`. An issue title rarely names an identifier exactly, so the personalisation seldom fires. VERIFIED.
- This is not the job aider built it for. Aider fills a token budget with a whole-repository map so that the model knows the shape of the code ([aider repomap.py](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py), Aider-AI, Apache-2.0). On 25 repositories, [Agent Retrieval Bench: Evaluating Repository Context Retrieval for Coding Agents](https://arxiv.org/abs/2607.24882) (Qin and Xie, 2026, arXiv) found RepoMap gave the best budgeted context yield at 8K tokens while embedding models had the best MRR and Recall@20, with "no single retrieval family" dominating. SOURCED. A map belongs in a brief (#122's pushed signature map), not in answering a question. BELIEVED.

### 3.4 Callers: a language server is exact at a sixth of the tokens of `dev code refs` (VERIFIED)
22 targets, 92 gold caller functions. **Tokens per caller found** = all tokens printed / true callers found.

| arm | recall | precision | targets with every caller | tokens printed (mean) | tokens per caller found | median ms (max) |
|---|---|---|---|---|---|---|
| refs (`dev code refs`) | 0.93 | 0.44 | 19/22 | 629 | 161 | 304 (463) |
| ast-grep call patterns | 0.98 | 0.99 | 20/22 | 99 | 24 | 242 (536) |
| **ty language server** | **1.00** | **1.00** | **22/22** | **99** | **24** | **43 (1,941)** |
| stdlib resolver | 0.76 | 1.00 | 13/22 | 21 | 7 | 1,240 (1,734) |

- **ty finds every caller, `dev code refs` prints 6 times the tokens for 93 % of them.** refs' false positives are mentions in docstrings, f-strings and same-named locals or variables (`running`, `worktrees`, `quota`); its misses are callers cut by its 60-line cap. ast-grep's two misses are tests that wrap Y instead of calling it (`real, calls = workflow.regenerate, []`); its one false positive is a method of the same name (`self.quota_cap` in `guard_bash.py`). VERIFIED.
- **ty's 1.00 is partly by construction:** gold is what ty and the resolver agree on plus the 22 ty-only cases, which reading showed all true. The independent check is refs, a lexical superset up to its cap: all 34 refs-only hits in `devkit/` and `plugins/` are docstrings, strings, comments or other names (a `running` local, `quota.usage_poll`, a `guard_bash` method named `quota_cap`), none a caller. VERIFIED (the refs-only hits in tests were not read).
- **The re-exports are why a resolver needs a type checker:** all 22 disputed callers reach Y through `workflow.Y`, which the consolidation left as a re-export. A hand-written import resolver misses them (recall 0.76); ty follows them. VERIFIED.
- **Latency:** ty's first reference query took 141 ms in the final run and up to 1.9 s while it indexed; the server's first start through `uvx` (download included) took 6.0 s, later starts under 0.1 s to answer `initialize`. VERIFIED.

### 3.5 Claude Code's own LSP tool did not work from a worktree in this session (VERIFIED, 6 calls)
The owner's `astral` plugin registers `uvx ty@latest server` as Claude Code's Python language server, which agents reach through the built-in LSP tool. From this worktree-isolated subagent:

- `findReferences` on `merging.deleted_owns` (5 true references) returned **only the declaration** in 3 calls (twice in this worktree, once in the lead's checkout); `incomingCalls` returned "nothing calls this function" in 2 calls. VERIFIED.
- `goToDefinition` on `workflow.deleted_owns` in this worktree's `devkit/train.py` resolved to **another agent's worktree** (`../agent-a73c68da38dcf8d56/devkit/merging.py`). VERIFIED (1 call).
- The session's ty server runs with the lead's checkout as its working directory (`/proc/<pid>/cwd`), one per project, not per worktree. VERIFIED.

So in a harness where every worker has its own worktree, the session-level LSP tool can give a worker no references or another worker's code. A server started by devkit with the worktree as its root (as [eval_callers.py](_scratch/semantic-code-search/scripts/eval_callers.py) does) gave the right answer every time. Why Claude Code's references came back empty is not established (the server may not have indexed yet, or the files may lie outside its workspace). BELIEVED, from 6 calls in one session.

### 3.6 What the literature says (SOURCED)
The literature agrees that retrieval for coding agents has no single winner and that a better first answer saves agent tokens; none of it measures a repository this small or a harness like this one.

- [Agent Retrieval Bench: Evaluating Repository Context Retrieval for Coding Agents](https://arxiv.org/abs/2607.24882) (Qin and Xie, 2026, arXiv; 427 samples, 25 repositories, file level): no retrieval family dominates; the best embedding models (Qwen3-Embedding 4B/8B) lead on MRR and Recall@20, RepoMap on context yield at an 8K budget; logged agent trajectories miss every gold file on 27-35 % of samples. SOURCED (abstract).
- [Small Agents with Semantic Search: Efficient Multilingual Code Localization](https://arxiv.org/abs/2610.05099) (Lasbordes et al., 2026, arXiv): small (under 2B) localization agents with a late-interaction semantic search tool beat the same agents with grep on SWE-bench Lite and Multi-SWE-bench Flash derived tasks, with 44.1 % lower end-to-end latency on CPU and 29.1 % fewer tokens. SOURCED (abstract). Those agents were fine-tuned for the tool; Claude is not.
- [Code Isn't Memory: A Structural Codebase Index Inside a Coding Agent](https://arxiv.org/abs/2606.22417) (Bhola et al., 2026, arXiv): inside one harness with Claude Opus 4.7 fixed, three seeds, adding a structural codebase index gave a large localization gain, a statistically separated resolve gain and a lower cost per solved task, and did not regress against an agentic-grep comparator. SOURCED (abstract; effect sizes not in the abstract).
- [Is Grep All You Need? How Agent Harnesses Reshape Agentic Search](https://arxiv.org/abs/2605.15184) (Sen et al., 2026, arXiv): on 116 LongMemEval questions (conversation memory, not code), grep was generally more accurate than vector retrieval across a custom harness, Claude Code, Codex and Gemini CLI, and the harness and tool-result style mattered as much as the retriever. SOURCED (abstract). Contrary evidence for embeddings, on a different corpus.
- [Retrieval-Oriented Code Representations in Agentic Bug Localization](https://arxiv.org/abs/2607.11046) (Caumartin, Chen and Costa, 2026, arXiv; LCA and SWE-bench Verified): what is indexed matters: LLM-written role summaries beat file paths by up to 40 % Hit@5 with a footprint 10-21 times smaller than raw source. SOURCED (abstract). Matches §3.1, where adding the body's identifiers to BM25 helped (not significantly).
- [LocAgent: Graph-Guided LLM Agents for Code Localization](https://arxiv.org/abs/2503.09089) (Chen et al., 2025, arXiv): a code graph of files, classes and functions with import, call and inheritance edges, searched by an agent, reaches 92.7 % file-level accuracy on its benchmarks. SOURCED (abstract). The same graph is what the ty references in §3.4 give for callers.
- [Demystifying LLM-Based Software Engineering Agents](https://doi.org/10.1145/3715754) (Xia et al., 2025, Proc. ACM Softw. Eng.): Agentless runs a fixed three-phase process, localization then repair then patch validation, with no agent choosing tools, and was the best open-source approach on SWE-bench Lite at $0.70 per issue at the time. SOURCED (abstract). Localization as a cheap, separate first step is the role `dev code search` would play. BELIEVED.

### 3.7 Cost to every downstream project (VERIFIED, [footprint.sh](_scratch/semantic-code-search/scripts/footprint.sh), [bench_cold.py](_scratch/semantic-code-search/scripts/bench_cold.py); SOURCED licences)
A runtime dependency of `agent-harness` reaches every pinned project at its next update. Each candidate installed alone into a bare Python 3.13 project:

| candidate | used by | packages locked | install size | model on disk | licence | last push (GitHub) |
|---|---|---|---|---|---|---|
| stdlib `ast` + `devkit.board.bm25` | bm25 arms, resolver | 0 | 0 | none | (devkit) | |
| model2vec | m2v arms | 25 | 92 MB | 30 MB (potion-base-8M), 125 MB (potion-retrieval-32M) | MIT | 2026-11-11 |
| fastembed (with onnxruntime) | bge-small, jina-code | 30 | 187 MB | 65 MB (bge-small, quantized ONNX), 615 MB (jina code) | Apache-2.0 | 2026-11-18 |
| ty | language server | 1 | 39 MB | none | MIT | 2026-11-19 |
| ast-grep-cli | ast-grep | 1 | 53 MB | none | MIT | 2026-11-19 |

All five pass the reuse gates G1-G6 (`uv run dev reuse check`, 2026-11-19; sqlite-vec too, which no arm needed: 2,515 vectors fit in 2.6-3.9 MB of numpy). VERIFIED. Models download from Hugging Face on first use, which a sandboxed or offline worker may not reach. BELIEVED.

ty may cost nothing extra: #59 (open) adds a ty type-check stage to the gate, which would make ty a gate dependency like ruff. ASSUMED until #59 merges.

## 4. Recommendation for this project
1. **`uv run dev code search "<words>"`: BM25 over function and method units, tests excluded by default** ([#545](https://github.com/Marius-Juston/agent-harness/issues/545), P1). Stdlib `ast` and `devkit.board.bm25`, **no new dependency**, nothing persisted. Bounds from §3.1, to hold on the same 47-question replay: top-5 ≥ 0.50 (measured 0.53), mean output ≤ 250 tokens (measured 198), a cold run ≤ 2 s on this repository (measured 1.1-1.4 s). It complements `find`: `find` stays the tool for a known string, `search` is for a task description. One line in `builder.md`'s navigation step; the other agents wait for #548.
2. **`uv run dev code refs <Symbol> --exact`: callers from a ty language server that devkit starts with the worktree as its root**, grep output as the fallback ([#547](https://github.com/Marius-Juston/agent-harness/issues/547), P2). Bounds from §3.4 on the 22 targets: recall ≥ 0.98, precision ≥ 0.95, mean output ≤ 150 tokens (measured 1.00, 1.00 and 99). Cost to every project: ty (MIT, 1 package, 39 MB), which #59's gate stage would add anyway; until #59 merges, the command falls back to grep where ty is missing. Do not route agents to Claude Code's LSP tool for this in worktrees (§3.5).
3. **No embedding dependency now.** On this evidence an embedding model adds at most +0.09 top-5 (a point estimate whose interval includes 0) over BM25 without tests for 92-187 MB of install, a 30-615 MB model and, for the transformer models, a persisted per-worktree index. model2vec stays the optional `semantic` extra of `dev reuse`; `search` need not read it. Revisit only through #548: if the agent-run experiment shows search pays, re-run the fusion arms on about 190 questions (§3.2).
4. **No repo map for questions.** A pushed signature map remains #122's job (briefs), not a search arm.

Expected effect, if #548 confirms it in agent runs: a localization that today costs a few `find` calls of about 1,600 tokens each starts from 5 cards of about 200 tokens with the right file there 96 % of the time. ASSUMED until #548.

## 5. Limits
- **One tool call, not an agent run.** The grep arm emulates one `dev code find` call with the 3 rarest title words; a real agent picks its own words, runs several calls and reads files. The 26-fold token ratio is per call. #548 measures whole runs.
- **History-derived gold is noisy.** The answer is what a commit changed, which includes incidental wiring (about 6 of the 9 questions every arm missed). The registration-function rule removed the worst of it before scoring.
- **One small repository** (1,692 units per tree on average, one author's naming). The embedding literature's gains are on larger, multi-author code; §3.6.
- **The `src` view was added after the first lexical run** (before any embedding arm ran). Both views are reported, and they rank the arms differently: over every unit, bge-small (0.43) and its fusion (0.40) lead BM25 (0.28). Dropping tests lifts BM25 (+0.25) and jina-code (+0.27) most, so the test filter is part of recommendation 1, not an option.
- **Caller gold leans on ty** (§3.4); the refs cross-check read the source hits, not the test hits, and cannot see a caller beyond refs' 60-line cap.
- Tokens are characters / 4; latencies were taken on a shared, busy machine.
- s2 was down for every literature search, and the papers in §3.6 were read as abstracts.

## Sources
- [Agent Retrieval Bench: Evaluating Repository Context Retrieval for Coding Agents](https://arxiv.org/abs/2607.24882). Qin, Xie. 2026, arXiv:2607.24882.
- [Small Agents with Semantic Search: Efficient Multilingual Code Localization](https://arxiv.org/abs/2610.05099). Lasbordes, Sinha, Sourty, Chatelain, Seddah. 2026, arXiv:2610.05099.
- [Code Isn't Memory: A Structural Codebase Index Inside a Coding Agent](https://arxiv.org/abs/2606.22417). Bhola, Krishnan, Kurmala, NS. 2026, arXiv:2606.22417.
- [Is Grep All You Need? How Agent Harnesses Reshape Agentic Search](https://arxiv.org/abs/2605.15184). Sen, Kasturi, Lumer, Gulati, Subbiah. 2026, arXiv:2605.15184.
- [Retrieval-Oriented Code Representations in Agentic Bug Localization](https://arxiv.org/abs/2607.11046). Caumartin, Chen, Costa. 2026, arXiv:2607.11046.
- [LocAgent: Graph-Guided LLM Agents for Code Localization](https://arxiv.org/abs/2503.09089). Chen et al. 2025, arXiv:2503.09089.
- [Demystifying LLM-Based Software Engineering Agents](https://doi.org/10.1145/3715754). Xia, Deng, Dunn, Zhang. 2025, Proceedings of the ACM on Software Engineering.
- [aider repomap.py](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py). Aider-AI, Apache-2.0, commit 5dc9490 (repository pushed 2026-05-22).
- [model2vec](https://github.com/MinishLab/model2vec). MinishLab, MIT. GitHub repository, pushed 2026-11-11.
- [fastembed](https://github.com/qdrant/fastembed). Qdrant, Apache-2.0. GitHub repository, pushed 2026-11-18.
- [ty](https://github.com/astral-sh/ty). Astral, MIT. GitHub repository, pushed 2026-11-19.
- [ast-grep](https://github.com/ast-grep/ast-grep). MIT. GitHub repository, pushed 2026-11-19.
- Marius-Juston/agent-harness issues [#59](https://github.com/Marius-Juston/agent-harness/issues/59), [#122](https://github.com/Marius-Juston/agent-harness/issues/122) and [#495](https://github.com/Marius-Juston/agent-harness/issues/495).

## Follow-ups
- [ ] `dev code search` ([#545](https://github.com/Marius-Juston/agent-harness/issues/545)) DONE WHEN the 47-question replay gives top-5 ≥ 0.50 at ≤ 250 tokens mean output and a cold run takes ≤ 2 s.
- [ ] `dev code refs --exact` through a worktree-rooted ty server ([#547](https://github.com/Marius-Juston/agent-harness/issues/547)) DONE WHEN the 22 caller targets give recall ≥ 0.98, precision ≥ 0.95 at ≤ 150 tokens mean output, with a grep fallback tested.
- [ ] Agent-run experiment ([#548](https://github.com/Marius-Juston/agent-harness/issues/548)) DONE WHEN this note reports paired differences in localization rate and tokens per correct localization over at least 20 questions × 3 trials per arm, model named.
- [ ] Re-run [eval_nl.py](_scratch/semantic-code-search/scripts/eval_nl.py) when the repository doubles in units (about 5,000 functions) DONE WHEN the BM25 and fusion rows are regenerated and a fusion gain whose interval excludes 0 is filed as a build issue.
