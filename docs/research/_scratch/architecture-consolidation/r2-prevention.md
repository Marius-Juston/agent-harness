Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc, arxiv OK; s2 DOWN (HTTP 429, shared
pool, also rate-limited during every search). arXiv full texts were read from arxiv.org/html with curl; GitHub facts from
api.github.com on 2026-10-12. Repo code names: repo-L (the game server, app dir written `app/`), H (agent-harness).

# R2, gap 1: prevention. Give agents a map of existing code, and catch new near-duplicates at the gate

## Findings

### P1. A pushed list of interfaces helps reuse; more source, or reading alone, does not
- RepoReuse ([arXiv:2609.35357](https://arxiv.org/abs/2609.35357), Sep 2026; OpenCode with Qwen3.7-plus, Table 5).
  "Interface memory" is the module, name, signature, return value and behaviour of each earlier function, put into the
  requirement. With it, self-reuse rose from 30.0% to 67.8% and C_dup fell from 65.1% to 46.7%. Pasting the full
  source of earlier work gave 29.2% self-reuse and 70.7% C_dup, worse than giving nothing. SOURCED.
- The map came at a cost. Agents opened 13.5 files per turn instead of 27.7. Repository recall fell from 55.5% to
  35.1%, and repository reuse from 58.9% to 50.5%, because the note listed only the agent's own functions. The
  authors add that interface memory also *asks* for reuse, so the map and the request cannot be separated. SOURCED
  (same section). Consequence (BELIEVED): a map has to cover the whole repository, or the part the task touches, not
  only recent work. Otherwise it swaps one kind of reuse for another.
- Reading is not the bottleneck for code the agent wrote itself. Every configuration read at least 98.9% of its own
  earlier targets, yet self-reuse was only 67.0-86.4%. For repository code, reuse tracks exploration: configurations
  that read at least 900 lines per turn reached 47-61% repository recall, against 35-42% for those reading under 700
  lines. SOURCED ([same paper, §4.2](https://arxiv.org/abs/2609.35357)). Consequence (BELIEVED): a pull tool such as
  grep or `dev code find` fixes only the finding half. The decision half needs a push (the map) plus a check after
  the fact (P5).
- FeatLens ([arXiv:2609.26480](https://arxiv.org/abs/2609.26480), Sep 2026, preprint) builds a feature index from
  natural language to functions, then ranks it with personalized PageRank. On DevEval with GPT-5-mini it called more
  of the true dependencies than CodexGraph (DIR@1 53.58% vs 46.27%), kept Pass@1 comparable, and wrote shorter code
  (101.1 vs 167.7 LOC). SOURCED. It is a research prototype, not a maintained tool.
- Telling the model to be minimal does not work. Across 28 SWE-bench Verified systems the median patch had 121.78%
  more total changes than the developer's patch. Prompting and minimality baselines shrank patches only by losing 49
  to 217 resolved instances ([arXiv:2608.13292](https://arxiv.org/abs/2608.13292), Aug 2026). SOURCED. So a "reuse
  existing code" line in CLAUDE.md is not enough on its own (BELIEVED).

### P2. Navigation and map tools: evidence is about answering questions and resolving issues, not about reuse
| tool | license / activity (API, 2026-10-12) | evidence | fit for H |
|---|---|---|---|
| [aider](https://github.com/Aider-AI/aider) repo map (tree-sitter tags + PageRank, `--map-tokens`, 1k default) | Apache-2.0, 49.5k stars, last push 2026-05-22 | design only. The [docs](https://aider.chat/docs/repomap.html) and the [2023 post](https://aider.chat/2023/10/22/repomap.html) claim it "helps aider write new code that respects and utilizes existing libraries" but publish no ablation. SOURCED (no numbers found) | runs without an API key via `--show-repo-map` (VERIFIED, P3). It is heavy (108 packages) and ranks by global reference count, which favours hubs over the small helpers that get re-implemented (BELIEVED) |
| [RepoGraph](https://github.com/ozyyshr/RepoGraph) ([arXiv:2410.14684](https://arxiv.org/abs/2410.14684), v2 Mar 2025) | Apache-2.0, 305 stars, last push 2025-04-01 (stale) | SWE-bench Lite resolve rate rose by 2.00-2.66 points in all four hosts, e.g. Agentless 27.33 to 29.67 and SWE-agent 18.33 to 20.33 (GPT-4o). SOURCED. Reuse was not measured. Model-dated | prior art only |
| [universal-ctags](https://github.com/universal-ctags/ctags) | GPL-2.0, 7.3k stars, pushed 2026-10-11 | none on agents. aider replaced it with tree-sitter for richer signatures ([post](https://aider.chat/2023/10/22/repomap.html)). SOURCED | not installed here. Python's `ast` already gives full signatures |
| [Serena](https://github.com/oraios/serena) (LSP-backed MCP) | **GPL-3.0-or-later** application, SolidLSP part MIT ([README](https://github.com/oraios/serena)). 30.2k stars, pushed 2026-10-12 | only agents rating themselves on about 20 tasks (README quotes). No controlled reuse result. SOURCED | strong for find-references and rename. The GPL application is fine to run as a separate tool, but its code cannot be vendored into an MIT template (BELIEVED) |
| [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) ([arXiv:2603.27277](https://arxiv.org/abs/2603.27277)) | MIT, 46.4k stars, pushed 2026-10-13 | across 31 repos, answer quality was 83% against 92% for a file-exploring agent, at 10x fewer tokens and 2.1x fewer tool calls. SOURCED (abstract). It trades accuracy for tokens and does not measure reuse | optional, since a native binary writes to the agent config |
| [claude-context](https://github.com/zilliztech/claude-context) | MIT, 12.6k stars, pushed 2026-07-14 | none fetched | needs an OpenAI embedding key and a Milvus/Zilliz endpoint ([README](https://github.com/zilliztech/claude-context)), so code leaves the machine. Reject as a default (BELIEVED) |
| [code-index-mcp](https://github.com/johnhuang316/code-index-mcp) | MIT, 1.0k stars, pushed 2026-07-27 | none | tree-sitter symbols and ripgrep. It overlaps `dev code` |
| [Repository Intelligence Graph](https://arxiv.org/abs/2601.10112) | paper (CMake only) | with Claude Code, Cursor and Codex on 8 repositories, mean accuracy rose 12.2% and completion time fell 53.9%. Gains were 6.6% for single-language repositories and 17.7% for multi-language ones. SOURCED | evidence that a deterministic, precomputed map helps commercial agents. The map is of build structure, not functions |

No study found measures whether a ranked repo map (aider-style) lowers duplication. The only reuse-outcome evidence is
P1 (RepoReuse, FeatLens). SOURCED as absent in the sources searched; the s2 outage limits this.

### P3. Measured here: map size, and what a ranked map leaves out
- Full interface map: the `dev code outline` format, top-level and class-level defs, with signature and first doc line,
  tests excluded (`scripts/mapsize.py`). VERIFIED.
  - H (`devkit plugins scripts`): 404 defs, 40.8k chars, about 10.2k tokens at chars/4. Public names only: 337 defs,
    about 8.7k tokens. That fits in a session-start push.
  - repo-L `app/`: 6,819 defs, about 185k tokens. Public only: 5,235 defs, about 150k tokens. The whole repo: 7,868
    defs, about 205k tokens. That does not fit, so it must be scoped to the packages a task touches or ranked.
  - A map is about 10% of the source characters in both repos (ratio 0.106-0.126).
- aider ranked map on scratch clones, without chat files, so the ranking is global. VERIFIED (commands below).
  - repo-L: 1k tokens showed 60 defs from 36 files, 4k showed 215 defs from 109 files, 8k showed 565 defs from 235
    files. That is at most 0.8%, 2.7% and 7.2% of the 7,868 non-test defs; aider also counts tests, so the true
    non-test share is lower. Wall time was 14.9 s cold and 7-9 s warm.
  - H: 88 defs at 1k and 350 at 4k. The 8k map (668 defs) already includes tests and the template.
  - Consequence (BELIEVED): on a large repository a global ranked map shows almost none of the leaf helpers that P5
    finds re-implemented (`_alive`, `budget`, `_plan_ids`). A task-scoped map, of the packages named in the issue
    and their imports, is the better default.
- How agents navigate today: Claude Code transcripts for repo-L (406 jsonl files), counted with
  `scripts/navcount.py`. VERIFIED.
  - Bash commands containing grep, rg or git grep: 16,450. `dev code` calls: 777 (show 502, outline 227, refs 48,
    find 0). So `dev code` is about 4.5% of search calls. Write: 2,418, Edit: 3,160, LSP: 26.
  - Caveat: the grep count includes pipes through grep. Even so, agents search with raw grep, not the capped tool.

### P4. H's `dev code` compared with these tools
- `dev code outline|show|refs|find` is a capped pull interface ([devkit/code.py](https://github.com/Marius-Juston/agent-harness/blob/main/devkit/code.py)).
  It gives a per-file outline (Python `ast`, Markdown headings), a symbol body, word-grep references and a regex
  grep, all through `git grep`. VERIFIED by reading the module.
- It has no repository-wide or task-scoped map, no ranking, no "is there already something like this?" query, no
  semantic or type-aware references (refs is a word grep, so it misses aliases and attribute calls), and no non-Python
  outline.
- Against P1 the gap is the push: nothing hands an agent the interfaces of the code its task touches. Adding
  `dev code map <paths> [--budget N]` (stdlib `ast`, the same format as `outline`, scoped to packages) would cost
  about 10% of the source size (P3). It keeps the current principle of capped output and no new dependency (BELIEVED).

### P5. Gate check: pyscn clones limited to the diff, backtested on 120 repo-L merges
- pyscn 1.32.4 has no diff or baseline mode (`analyze`/`check --help`). VERIFIED. The wrapper
  `scripts/backtest.py` (about 50 lines) does this:
  1. runs `pyscn analyze --select clones --json` on the merged tree;
  2. keeps a pair only when one fragment is at least 50% lines added by the merge and the other fragment is untouched
     (new copies existing).
- Speed: the median whole-repo pass was 3.3 s (max 7.1 s) on repo-L (305k Python lines) and 0.15 s on H. That is
  cheap enough for every gate. VERIFIED.
- Results with pyscn defaults (similarity ≥0.65, types 1, 2 and 4, tests excluded). VERIFIED.
  - Over the last 120 first-parent merges of repo-L, 109 touched Python (median 1,003 Python lines added). 25 of
    them (23%) fired, with 65 pairs: 47 type-4, 15 type-2 and 3 type-1.
  - H's last 30 merges (28 with Python): 0 fired.
- Precision. I reviewed every type-1/2 pair and every type-4 pair at similarity ≥0.8 by hand
  (`scripts/showpair.py`); the verdicts are BELIEVED.
  - Type-1/2: 6 of 18 were real reuse misses: a copied `_text` helper across two scripts, `_alive` re-implementing
    `workflow.alive`, an identical `budget()`, a repeated character query, repeated database-URL loading, and a
    repeated cast-walking loop. Nine were declarative boilerplate (SQLAlchemy row classes, pydantic config models) and
    three were dispatch or validator patterns.
  - Type-4 ≥0.8: 3 of 11 real (a second `/proc/meminfo` reader, a second `_plan_ids`, the cast walk again), 2
    partial, and 4 false positives from similar-shaped validators.
  - Type-4 <0.8: I skimmed 17 of 36. At most 1 was real; most were row classes and query patterns.
- Candidate rules (`scripts/rules.py`). VERIFIED counts, BELIEVED precision.

| rule | merges fired (of 109) | pairs | actionable |
|---|---|---|---|
| pyscn default | 25 (23%) | 65 | low (mostly type-4 noise) |
| types 1-2 | 13 (12%) | 18 | 6/18 (33%) |
| **types 1-2, new fragment not a `class` body** | **7 (6%)** | **7** | **6/7 (86%)** |
| types 1-2, or type-4 ≥0.8, not a class body | 13 (12%) | 18 | 9-11/18 (50-61%) |

- Recall is unmeasured. The bold rule missed the meminfo and `_plan_ids` re-implementations, which only type-4 caught.
- Established precedent: SonarQube's built-in "Sonar way" gate fails when duplication on new code is above 3.0%.
  It ignores changes under 20 new lines, and on a pull request "new code" is what changed against the target
  ([Sonar docs](https://docs.sonarsource.com/sonarqube-server/latest/quality-standards-administration/managing-quality-gates/introduction-to-quality-gates/)).
  SOURCED. It needs a SonarQube server, so it is not uv-native.

### P6. Recommendation for #113 (BELIEVED, built on P1-P5)
1. **Push a scoped map.** Add `dev code map`, and have `board brief` (or the worker prompt) append the map of the
   packages the issue names. Budget it at about 4-8k tokens; the whole map for repos under about 10k tokens, as with
   H. Use stdlib `ast`, not aider (heavy, global ranking) or an MCP server (extra install; GPL or cloud for some).
2. **Gate check, warn first.** Add `dev code dupes --diff <base>`: pyscn clones filtered to new-vs-existing pairs.
   - Fail on type-1/2 pairs whose new fragment is a function or block, not a class body. The backtest suggests
     about 1 in 16 merges fires, with about 6 in 7 actionable.
   - Report type-4 ≥0.8 as a warning in the PR body.
   - Print both locations, so the fix is "import the old one".
3. Do not count on prompts ("reuse existing code") or bigger context (full source) on their own (P1, RECAP).
4. Measure it: replay the bold rule on new merges for 4 weeks and log precision. Use RepoReuse-style C_dup on an
   eval scenario before and after the map push (`dev eval` exists).

## Gaps seen, not closed
- Recall of the gate rule: no labelled set of "should have reused" PRs. A seeded test (re-implement 20 known helpers
  under new names, then check what fires) would measure it.
- No experiment here shows that a pushed map changes Claude's reuse on H or repo-L. P1 is one paper, one model and one
  harness, with the map confounded with an explicit reuse request.
- repo-R and repo-E were not backtested. repo-E has no package code (r1-tools-prior-art).
- pyscn on non-Python code: jscpd (MIT, r1) would be the language-agnostic fallback. Its diff filtering is untested.
- The precision verdicts are one reviewer's judgement on 29 pairs, not double-coded.

## Sources fetched
- RepoReuse, Ma et al. [arXiv:2609.35357](https://arxiv.org/abs/2609.35357) (HTML §4.2, §4.4, Tables 4-5, App. A)
- FeatLens [arXiv:2609.26480](https://arxiv.org/abs/2609.26480) (HTML abstract, §4.3, §5.1)
- RECAP / patch verbosity [arXiv:2608.13292](https://arxiv.org/abs/2608.13292) (abstract)
- RepoGraph [arXiv:2410.14684](https://arxiv.org/abs/2410.14684) (HTML abstract, Table 5)
- Repository Intelligence Graph [arXiv:2601.10112](https://arxiv.org/abs/2601.10112) (HTML abstract, contributions)
- Codebase-Memory [arXiv:2603.27277](https://arxiv.org/abs/2603.27277) (abstract via `dev lit get`)
- aider [repo map docs](https://aider.chat/docs/repomap.html), [tree-sitter repo map post](https://aider.chat/2023/10/22/repomap.html)
- SonarQube [quality gates](https://docs.sonarsource.com/sonarqube-server/latest/quality-standards-administration/managing-quality-gates/introduction-to-quality-gates/)
- GitHub API and READMEs: Aider-AI/aider, Aider-AI/grep-ast, universal-ctags/ctags, oraios/serena,
  DeusData/codebase-memory-mcp, zilliztech/claude-context, johnhuang316/code-index-mcp, ozyyshr/RepoGraph,
  ludo-technologies/pyscn (README, mcp/README), kucherenko/jscpd, pmd/pmd, sourcegraph/scip-python

Commands (on scratch clones, read-only on the originals):
`uvx --python 3.12 --from aider-chat aider --show-repo-map --map-tokens {1024,4096,8192} --no-gitignore
--no-check-update --no-analytics --model gpt-4o --yes-always` (aider 0.86.2);
`python3 -I scripts/backtest.py <clone> 120 bt_L.jsonl` (pyscn 1.32.4);
`python3 -I scripts/rules.py <clone> bt_L.jsonl`; `mapsize.py <repo> <dirs>`; `navcount.py < transcripts`.
