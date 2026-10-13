# r1-surface: which Claude Code surface carries `dev reuse`, `dev lit` and the research loop

Search coverage: `uv run dev lit status` on 2026-10-13: openalex, crossref, europepmc and arxiv up, s2 DOWN (rate
limited, keyless pool). Claude Code docs, vendor blogs and GitHub API read 2026-10-13. The Smithery benchmark post
returns 404, so its numbers are second-hand (marked). Local transcripts: every harness-ops subagent run under
`~/.claude/projects/` on this machine (2026-10-11 to 2026-10-13).

Scripts (gitignored `.scratch/` of this worktree; synthesis should move them to `scripts/`):
`.scratch/surface_usage.py`, `.scratch/builder_reuse.py`, `.scratch/coldstart.sh`.

## Answer in one paragraph

The leading answer survives for **lit and the research loop**, and only partly survives for **reuse**. Researchers
whose own system prompt names the CLI use it in almost every run (54 of 62 `dev lit search/get`, 60 of 62
`dev lit status`), so always-on prompt text plus a CLI works when the procedure belongs to the agent's own job.
Builders are different. Only 2 of the 6 builder runs that wrote a new module searched for a library first, so the
one-line "Reuse before build" rule fires in a minority of runs. A skill would not fix that: skills fire less
reliably than always-on text (Vercel: never invoked in 56 % of cases), and model-invoked Skill calls are rare in
this machine's transcripts. An MCP server would not fix it either: in Claude Code its schemas are deferred, so it
costs the same decision as a CLI plus a ToolSearch turn, and it buys only cross-client reach. The surface that
changes builder behaviour is a **hook**. That means a PreToolUse check on `uv add` (hallucination, licence and
maintenance guard) plus a reminder when a builder first writes a new non-test module. The harness's own rule
already says a rule broken in a quarter of runs becomes a hook. Separately, the CLI loses traffic through
**missing fields**, not through its surface: 13 of 62 researcher runs and 7 of 11 auditor runs curl OpenAlex or
Crossref directly, bypassing the shared rate limiter, mostly for authors, open-access links or full abstracts.

## Findings

### F1. In Claude Code, an MCP server no longer costs tokens up front (SOURCED)
- With tool search on (the default), "only tool names and server instructions load at session start". Each tool
  description and each server's instructions is truncated at 2,048 characters
  ([MCP docs, "Scale with MCP tool search"](https://code.claude.com/docs/en/mcp)). A plugin can ship a server in
  `.mcp.json`, which starts automatically when the plugin is enabled (same page).
- The features overview rates MCP as "Low until a tool is used". Skills are "Low (descriptions every request)", and
  a hook costs "Zero, unless hook returns additional context"
  ([features overview](https://code.claude.com/docs/en/features-overview)).
- The skill listing's budget "scales at 1% of the model's context window". When the listing overflows, Claude Code
  drops descriptions, starting with the skills invoked least
  ([skills docs](https://code.claude.com/docs/en/skills)).
- *Implication (BELIEVED):* the blog benchmarks that blame MCP's token cost on schema overhead do not carry over
  to Claude Code with tool search. In the Scalekit benchmark, with 43 GitHub tool schemas loaded, MCP used 4 to 32
  times the tokens of the CLI ([Scalekit, 2026-03-11](https://www.scalekit.com/blog/mcp-vs-cli-use)). The cost
  that remains is one ToolSearch turn per deferred tool: 93 ToolSearch calls in 62 harness-ops researcher runs,
  about 1.5 per run (VERIFIED, `surface_usage.py`).

### F2. CLI vs MCP: no consistent winner; both the task and how familiar the API is decide it (SOURCED, vendor evidence)
- **Scalekit** (Claude Sonnet 4, 5 read-only GitHub tasks, n=5 per arm, 75 runs). Median tokens per task were
  1,365 to 9,386 for the CLI and 32,279 to 82,835 for MCP. Success was CLI 25/25 and MCP 18/25, and all 7 MCP
  failures were TCP `ConnectTimeout`s to the remote server, not tool errors
  ([Scalekit](https://www.scalekit.com/blog/mcp-vs-cli-use)).
- **Arize** (Claude Opus 4.6 in the Agent SDK, 25 GitHub tasks × 4 arms × 5 runs = 500 trials, LLM-as-judge).
  Correctness was flat: baseline bash 0.845, MCP 0.834, short skill 0.833, long skill 0.826. On tier-4 analysis
  tasks MCP cost more than 6 times as much as the skills. The short skill (341 lines) beat the long one (2,187
  lines). The author's own caveat: "For well-known CLIs like `gh`, pretraining does much of the work"
  ([Arize, May 2026](https://arize.com/blog/mcp-vs-cli-skills-for-agents-what-our-eval-found-and-which-you-should-use/)).
- **Smithery** (second-hand via [Nordic APIs, 2026-05-13](https://nordicapis.com/mcp-vs-cli-which-is-better-for-agentic-ai/);
  the original post 404s). The study ran 756 runs on Haiku 4.5, GPT 5.4 and Sonnet 4.6. On GitHub REST, native MCP
  scored 91.7 % at a median of 28,838 tokens, against 83.3 % at 82,942 tokens for a CLI with descriptions. With 826
  tools, MCP reached 100 % against 87.5 % for the CLI. Smithery's quoted view: "For local tools like git, docker,
  or ffmpeg, CLI is the native surface and MCP has no business replacing it."
- *Reading (BELIEVED):* MCP wins when an agent must drive many unfamiliar endpoints, each of which is one typed
  call. The CLI wins when results are composed or filtered and the commands are few and documented in the prompt.
  `dev lit` and `dev reuse` are few subcommands whose output an agent pipes and filters, so they sit on the CLI
  side. All three benchmarks are vendor-run on GitHub only, with small n.

### F3. Always-on text beats model-invoked skills at triggering (SOURCED; VERIFIED locally)
- **Vercel** (Next.js 16 API evals; the task count and model are not stated). Pass rates were baseline 53 %, skill
  by default 53 %, skill with explicit instructions 79 %, and an 8 KB docs index in AGENTS.md 100 %. "In 56% of
  eval cases, the skill was never invoked", and the results shifted with wording ("You MUST invoke" vs "Explore
  project first, then invoke") ([Vercel, 2026-01-27](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)).
  This is a first-party benchmark, and it measured docs knowledge, not a tool procedure.
- When a skill does run, it helps. ACES ran 947 paired cases over 58 production skills and found a mean composite
  Skill Lift of 0.213 (95 % CI 0.197 to 0.230), positive in 72.8 % of cases
  ([arXiv 2608.20614](https://arxiv.org/abs/2608.20614)). So the risk is the trigger, not the content.
- The Claude Code docs themselves name "Skill not triggering" as a failure mode. They say to measure it with
  `claude plugin eval` and a `tool_used: Skill` grader. Each case runs 3 times by default against a no-plugin
  baseline, and "the most common first finding is a Δ near zero with the case's `tool_used: Skill` grader
  failing" ([plugin evals docs](https://code.claude.com/docs/en/plugin-evals)).
- Locally, model-invoked Skill calls are rare across every transcript on this machine. `harness-ops:research` was
  model-invoked once and `harness-ops:lead` twice, and no subagent invoked a harness-ops skill except `harness`
  (twice) (VERIFIED, `surface_usage.py`). Caveat: this machine has only a few main sessions, so it is weak
  evidence.

### F4. A CLI named in the agent's own prompt is followed in about 90 % of runs (VERIFIED)
`surface_usage.py` over all transcripts (harness-ops agents date from 2026-10-11, when `dev lit` shipped):

| agent type | runs | `dev lit status` | `dev lit search/get` | WebFetch to a lit API (breach) | curl to a lit API |
|---|---|---|---|---|---|
| harness-ops:researcher (prompt names `dev lit`) | 62 | 60 | 54 | 1 | 13 runs (27 calls) |
| legacy `researcher` (other projects, no `dev lit` rule) | 38 | 0 | 8 | 16 | 15 runs |
| harness-ops:research-auditor (prompt does not name it) | 11 | 0 | 0 | 0 | 7 runs (15 calls) |

- Read with care (BELIEVED), since this is two populations and not an experiment. WebFetch breaches of the lit APIs
  fell from 16 of 38 runs to 1 of 62 once the rule and the CLI existed.
- Bypasses remain. Spot-reading the curl calls (`builder_reuse.py`, the RAW pattern) shows researchers fetching
  fields that `dev lit get` does not print: `authorships`, `open_access`/`locations` (PDF links) and full
  `abstract_inverted_index`. Crossref is curled for author lists. The research-auditor's `tools:` line lists Bash,
  but its prompt never mentions `dev lit`. These calls skip the shared token bucket and cooldown that `lit.py`
  exists for (see its docstring).

### F5. Builders rarely search before they build (VERIFIED, small N)
- 39 harness-ops:builder runs. 8 ran any library lookup (gh search, pypi.org, ecosyste.ms, deps.dev, context7 or
  `dev reuse`), and 4 ran `uv add` (`surface_usage.py`).
- 6 builder runs wrote a new non-test module. Only **2 of the 6** looked for a library before the first such write,
  and 3 of the 6 at any point (`builder_reuse.py`). The 4 that did not were #113, #114, #55 and #85/#54. #113 and
  #114 followed research notes that had already weighed tools (pyscn), so the measured miss rate overstates the
  real gap (BELIEVED). N=6 cannot support a rate, only a direction.
- The harness's own threshold, "a rule broken in a quarter of runs becomes a hook"
  ([agent-harness note §2](../../agent-harness.md)), is met here even after allowing for those two cases.

### F6. The CLI's output size, not its surface, sets the token cost per call (VERIFIED)
Median and p90 result size per call, in characters, over all transcripts:

| call | median | p90 |
|---|---|---|
| `dev lit search/get` (n=525) | 5,127 | 13,547 |
| WebSearch (n=1,916) | 2,947 | 3,754 |
| arxiv MCP (n=1,744) | 2,024 | 6,795 |
| context7 MCP (n=95) | 3,034 | 6,470 |
| `dev lit status` (n=91) | 730 | 2,090 |

- `dev lit` is the largest per call. Its defaults (records per search, snippet length) are a token lever that
  belongs to the CLI, whatever the surface (BELIEVED).
- The code-execution-with-MCP article's case is MCP tools exposed as code files, with results filtered in code
  before they reach the model ("150,000 tokens to 2,000 tokens … 98.7%"). It comes with sandboxing and operational
  overhead ([Anthropic, 2025-11-04](https://www.anthropic.com/engineering/code-execution-with-mcp)).
- A CLI run through Bash already lets the agent do this with `| head` or `| python3 -c` (BELIEVED). The transcripts
  show researchers piping `dev lit` output through `head` and `grep` routinely.

### F7. A long-running server would save under a second per call (VERIFIED)
- One argument for an MCP server is that it keeps the embedding model warm. Measured with `coldstart.sh` in the
  #29 worktree: model2vec potion-base-8M import plus load took 0.56, 0.65 and 0.58 s; `uv run dev --help` took 0.07
  to 0.08 s.
- One GitHub search alone is rate-limited to 30 per minute (#169 pilot), so a warm server saves under 1 s against
  network calls that take seconds. This does not justify a server.

### F8. Existing free MCP servers and skills for package search: none to adopt as the surface (SOURCED via GitHub API, 2026-10-13)

| candidate | stars, last push, licence | what it does | verdict |
|---|---|---|---|
| [ecosyste-ms/mcp](https://github.com/ecosyste-ms/mcp) | 9, 2026-10-09, MIT | package lookup, versions, advisories, dependents. `search_packages` is full-text and "requires local database", a bundled SQLite of *critical* packages | no as surface (Node/npx, keyword only, critical subset). Its bundled `@ecosyste-ms/critical` DB is a candidate offline corpus for embedding (gap G2) |
| [ecosyste-ms/mcp-server](https://github.com/ecosyste-ms/mcp-server) | 3, 2026-10-12, no licence | field-level package and repo metadata, `get_related_packages` | no (no licence) |
| [github/github-mcp-server](https://github.com/github/github-mcp-server) | 33.6k, 2026-10-12, MIT | includes repository search | no: duplicates `gh search repos`, which agents already have. Scalekit's timeouts came from its remote endpoint |
| [SocketDev/socket-mcp](https://github.com/SocketDev/socket-mcp) | 139, 2026-10-13, MIT | supply-chain scores per package. The public server needs an OAuth sign-in, and self-hosting needs an API key | no for default (needs an account); possible opt-in hallucination and typosquat check |
| [upstash/context7](https://github.com/upstash/context7) | 63.0k, 2026-10-12, MIT | docs index. `resolve-library-id` takes a need phrase plus a query | already installed here. One probe ("retry with exponential backoff") returned litl/backoff and aiohttp_retry but not tenacity, plus a JS library: a weak discovery source, N=1 (VERIFIED, one call) |
| [oborchers/mcp-server-pacman](https://github.com/oborchers/mcp-server-pacman) | 11, 2025-04-29, MIT | PyPI, npm and crates search | no: stale. PyPI has had no search API since 2022 (BELIEVED, not checked here) |
| [sammcj/mcp-package-version](https://github.com/sammcj/mcp-package-version) | 121, archived | latest versions | no (archived) |
| ECC `search-first` skill ([affaan-m/ECC](https://github.com/affaan-m/ECC), MIT) | 278k stars (repo) | a skill: search npm, PyPI, MCP, skills and GitHub, then adopt, extend or build | prior art for the skill form. It reports no measured trigger rate or lift; its decision matrix (adopt, extend, compose, build) is worth borrowing as text |

## Against the leading answer (deterministic CLI + short prompt procedure, no MCP)

1. **For reuse, the prompt procedure is the part that fails (F5).** The CLI is right, but "a short procedure in
   the builder prompt" is the weak link. That is the same situation as the read-discipline rule that held only
   once a hook rewrote reads ([agent-harness note §2](../../agent-harness.md)). Recommend instead:
   - (a) a PreToolUse hook on `Bash(uv add *)` that runs the `dev reuse` hallucination, licence and maintenance
     guard on each named package and blocks unknown, too-young or typo-near names;
   - (b) a one-time PostToolUse reminder when a builder Writes its first new non-test `.py` under the source tree,
     naming `dev reuse --from <file>`;
   - (c) a `Reuse:` line in the PR summary that the completion gate checks. That line names either what was
     searched, or the research note that already did it.

   (a) is deterministic. (b) and (c) are cheap. None needs MCP.
2. **The CLI is incomplete, and agents route around it (F4).** Add the fields researchers curl for (authors, OA PDF
   link, full abstract) to `dev lit get`. Name `dev lit get` in the research-auditor's prompt. Otherwise the
   shared limiter that justifies the CLI is bypassed in about a fifth of research runs.
3. **MCP's token penalty is mostly gone in Claude Code (F1),** so "MCP is too expensive" is no longer a valid
   reason. The real reasons against MCP here are these:
   - It needs a second process lifecycle. lit.py already shares its cache and limits through flock, which works
     for MCP too.
   - It is invisible to `uv run dev check`, CI and humans.
   - Its tool descriptions are usually poor: 97.1 % of 856 tools across 103 servers had at least one smell, and
     fixing them changed task success by a median +5.85 pp, regressed 16.67 % of cases and added 67.46 % more
     steps ([arXiv 2602.14878](https://arxiv.org/abs/2602.14878)).
4. **A skill is right only as a user entry point.** A `disable-model-invocation: true` skill such as
   `/harness-ops:reuse <need>` costs nothing until typed (F1 docs) and gives the owner a front door. As a
   model-invoked trigger it is the weakest option (F3). It should not carry the rule.
5. **A subagent is right for the research loop only.** That is already the case: researchers are subagents. Inline
   discovery output is 5 to 14 k characters per `dev lit` call (F6), but builder reuse checks are a few calls, so a
   spawn (system prompt plus CLAUDE.md) costs more than it isolates (BELIEVED).

**What would change it to MCP:**
- (i) The harness must serve clients without a shell or a checkout (claude.ai, Desktop, Codex cloud). Then wrap
  the same devkit functions in a thin plugin `.mcp.json` server and keep the CLI.
- (ii) `dev reuse` grows a stateful index whose load time exceeds about 5 s per call. It is 0.6 s now (F7).
- (iii) A `claude plugin eval` suite shows `dev reuse` used at a higher rate when exposed as a typed MCP tool than
  as a CLI named in the prompt, with the hook held constant. Nobody has measured that for a project-specific CLI.

**What would change it to a model-invoked skill:** an eval with a `tool_used: Skill` grader showing a reuse skill
triggering at least 80 % on natural builder prompts. Vercel's 44 % invocation rate (56 % never invoked) suggests
it will not.

## Gaps seen, not closed
- **G1.** There is no controlled trigger-rate measurement for this harness. Next step: a `claude plugin eval` suite
  of about 10 builder prompts that need a library, with a `tool_used` grader whose `input_match` is `dev reuse`.
  Run it in two arms (prompt line only vs prompt plus hook), 3 runs each. That turns F5's N=6 into a real rate.
- **G2.** Is the `@ecosyste-ms/critical` SQLite (bundled with ecosyste-ms/mcp) a usable offline corpus to embed for
  semantic search? Size, PyPI coverage and licence of the data are unchecked. This belongs to the sources
  sub-question.
- **G3.** Is context7 usable as a `dev reuse` source (HTTP API, keyless limits)? One probe only.
- **G4.** The Smithery original post is unavailable; its numbers are second-hand.
- **G5.** The scripts live in gitignored `.scratch/`. Synthesis must move `surface_usage.py`, `builder_reuse.py`
  and `coldstart.sh` into `_scratch/software-discovery/scripts/` for the VERIFIED labels to hold.

## Sources fetched
- [Claude Code: extend Claude Code (features overview)](https://code.claude.com/docs/en/features-overview)
- [Claude Code: skills](https://code.claude.com/docs/en/skills) (troubleshooting, listing budget, `context: fork`)
- [Claude Code: MCP](https://code.claude.com/docs/en/mcp) (tool search, plugin servers, output limits)
- [Claude Code: plugin evals](https://code.claude.com/docs/en/plugin-evals)
- [Anthropic: code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Vercel: AGENTS.md outperforms skills in our agent evals](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)
- [Scalekit: MCP vs CLI](https://www.scalekit.com/blog/mcp-vs-cli-use)
- [Arize: MCP vs CLI skills for agents](https://arize.com/blog/mcp-vs-cli-skills-for-agents-what-our-eval-found-and-which-you-should-use/)
- [Nordic APIs: MCP vs CLI](https://nordicapis.com/mcp-vs-cli-which-is-better-for-agentic-ai/) (for Smithery's numbers)
- [Model Context Protocol (MCP) Tool Descriptions Are Smelly! Towards Improving AI Agent Efficiency with Augmented MCP Tool Descriptions](https://arxiv.org/abs/2602.14878)
- [Evaluating Skills, Not Just Agents: Agentic Continuous Evaluation of Skills](https://arxiv.org/abs/2608.20614)
- [RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation](https://arxiv.org/abs/2505.03275):
  abstract only. Tool-selection accuracy was 43.13 % vs a 13.62 % baseline when only retrieved tools were shown. It
  supports deferral in general; not used above.
- GitHub API metadata for the repositories in F8, and the READMEs of ecosyste-ms/mcp, ecosyste-ms/mcp-server,
  SocketDev/socket-mcp and the ECC `search-first` SKILL.md.
