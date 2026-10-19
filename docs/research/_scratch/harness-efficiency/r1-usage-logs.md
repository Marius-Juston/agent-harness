# r1: how the harness was used in repo-E (transcripts, 2026-10-11 to 2026-10-16)

Round mode, written by the lead. All numbers are VERIFIED on the local Claude Code transcripts of repo-E
(`~/.claude/projects/<repo-E>/`: 16 lead sessions, 644 subagent transcripts) with the two scripts in this folder:
`transcript_stats.py <project-dir>` (counts, tools, commands, errors, tokens by agent type) and
`extra_stats.py <project-dir>` (price fit, context sizes, cd targets, call gaps, isolation refusals).
Harness version in use: agent-harness v0.1.4-v0.1.7 plugin, model claude-opus-5-5 for every agent.
Outcome in the same window: 188 merged PRs in repo-E (`gh pr list --state merged`, mergedAt >= 2026-10-11,
+268k/-12k lines), plus agent-harness PRs run from repo-E sessions.

## 1. Spend and where it goes
- Client cost records (`cost-state`) sum to **$2,231 API-equivalent** (subscription use, so not billed), 99 % Opus 5.5.
  About **$12 per merged PR**.
- Fitted per-M-token prices from the cost records (least squares over sessions): input 4.09, output 20.05,
  cache read 0.20, cache write 7.95. Cache write at about 2x input means 1-hour-TTL writes; cache read at 0.05x input.
- **Cost share: cache read 48 %, cache write 29 %, output 20 %, uncached input 3 %.** Cost is context size x turns,
  plus new content written once.
- By agent type (fitted prices on transcript tokens; $1,869 of the $2,231 is attributable):

| agent | share | runs | median turns | median tokens/run | end-of-run context median / p90 |
|---|---|---|---|---|---|
| researcher (harness-ops + older `researcher`) | **48 %** | 259 | 64 | 6.9 M | 171k / 301k |
| builder | 21 % | 141 | 55 | 4.5 M | 111k / 238k |
| lead (main sessions) | 20 % | 16 | — | — | average context per call 368k |
| research-auditor | 4 % | 59 | 32 | 2.2 M | 103k / 150k |
| security-reviewer | 2.6 % | 105 | 14 | 0.4 M | 43k / 68k |
| reviewer | 1.5 % | 49 | 18 | 0.65 M | 52k / 78k |
| tester | 1.1 % | 10 | 59 | 4.7 M | 129k / 197k |

- Every one of 668 spawns inherited the model (Opus 5.5); none set `model:` to Sonnet or Haiku. In the earlier
  pre-harness repository, 40 of 402 spawns did.
- Subagents start at a median **26k-token** context (system prompt, tools, CLAUDE.md, agent body) and end near 100-170k.
- Comparison, not a controlled one: the pre-harness repository's builders used a median 27.6 M tokens per run
  (192 runs) against 4.5 M here.

## 2. Cache TTL is right as set
- Gaps between consecutive API calls: subagents 94 % under 1 min, 4 % 1-5 min, **1.5 % over 5 min**; lead 78 %, 17 %, 4.5 %.
- At 5-minute TTL each >5-min gap would rewrite the whole context (~140k subagent, ~370k lead). Subagents: ~65 M
  tokens of rewrites (~$330) vs ~$250 saved on cheaper writes, so about break-even; lead: 1 h clearly wins.
  Keep `cacheTtl: 1h`.

## 3. What fills context (tool-result characters)
- Totals: Bash 72 M chars, Read 19 M, WebFetch 5.4 M, WebSearch 3.7 M, arxiv MCP 6.9 M.
- `dev` subcommands by output: `lit` 10.5 M chars (search 985 calls, get 711), `code` 2.8 M, `board` 2.0 M,
  `cite-check` 0.7 M, `check` 0.56 M.
- Researcher results: plain Bash 36 % (`sed -n` windows 7.0 M, `grep` 3.9 M, `cat` 3.4 M), `dev lit` 16 %, Read 15 %.
  Builder results: plain Bash 67 % (`sed` 4.8 M, `grep` 2.0 M, `cat` 1.5 M), Read 10 %, `dev code` 7 %.
- Research notes are large: 36 notes in repo-E, median 78 KB (~19k tokens), max 169 KB. Researchers and auditors
  extending or auditing one read it with `allow_large` (106 such reads) or `sed` windows; the largest single results
  were whole notes of 80-120 KB and PDFs up to 276 KB.
- The Read tool is little used (1,301 calls vs 28,911 Bash); the Grep tool was never used. Agents navigate with
  `grep`/`sed`/`cat` in Bash.

## 4. Commands and workflow
- Top `uv run dev` subcommands: lit search 985, lit get 711, check 661, cite-check 560, board new 556,
  code outline 533, pr 411, board brief 409, integrate 297, lit status 252, board claim 164, code show 164,
  reuse check 110. `code refs` 11, `code find` 27 (barely used).
- gh: pr view 712, issue view 659, pr diff 415, pr comment 241, issue comment 229, pr checks 161.
- git: log 1,247, diff 989, status 986, commit 896, show 603, fetch 511, worktree 497.
- **96 % of Bash calls are compound** (`&&`, `;`, `|`). **51 % start with `cd`**: 11,006 into a worktree other than
  the agent's own cwd, 3,638 elsewhere, 266 into its own cwd.
  - Researchers run without `isolation` in the main checkout and `cd` into their issue worktree on every call
    (3,933 + 247 auditor).
  - Builders spawned with `isolation: worktree` in repo-E but working on agent-harness `cd` into the other repo's
    worktree (1,611 calls).
- Effects of the `cd` habit:
  - 685 refusals: "This agent is isolated in the worktree … too complex to verify". Harness issue #205 covers part of this.
  - The read guard's whole-file rewrite skips any command containing `&`, so `cd X && cat file` passes
    unrewritten (123 cases).
  - Allowlist entries such as `Bash(uv run dev check*)` do not match `cd X && uv run dev check`, so these calls go to
    the auto-mode classifier.
- Waiting: 295 `sleep` and 209 `until` loops in Bash, plus 59 Monitor calls.
- Skills: the Skill tool was called 16 times in 16 sessions (lead 5, research 4, harness 3, claude-api 3).

## 5. Errors (1,368 Bash, 135 Edit, 59 Write, 69+54+44+22+17 arxiv MCP, 63 WebFetch)
- Edit/Write before Read: **164** "File has not been read yet". Agents read with `sed`/`cat`, which does not
  register as a read, then Edit fails and costs a Read plus a retry.
- Subagent wrote a report file: 12 "Subagents should return findings as text".
- Agent spawn: 12 concurrent-limit refusals, 9 unknown agent type (plugin not loaded or old name), 3 quota gate.
- Guard blocks: 45 plugin `guard_bash` (mostly `git branch -D`), 16 project-guard (pip, pkill, `--no-verify`).
- `dev pr` failed 121 times, mostly the isolation refusal above (a heredoc summary from a `cd`-prefixed call).
- arxiv MCP: 53 timeouts on `get_abstract`, 69 errors on `get_paper_outline`, 15 "PDF conversion requires the pdf
  extra"; Read of PDFs: 22 "pdftoppm is not installed".
- WebFetch: 21 certificate errors, 7 web.archive.org refusals.

## 6. Owner interaction
- 908 owner prompts across 16 lead sessions, 11 `/goal`, 9 `/reload-plugins`, 4 `/reload-skills`, 2 `/compact`;
  6 compactions in total. 108 AskUserQuestion calls (7 rejected).

## 7. Candidate changes the data points at (for the synthesis to weigh against the sources)
1. Research is half of all spend: cap note size (split notes into per-section files or an index plus parts),
   give researchers `code outline`/section reads of notes instead of `allow_large`, and shrink `dev lit` output
   (fields agents use, one line per paper).
2. Give researchers and auditors their worktree as cwd (`isolation: worktree` or a spawn-time cwd) so no call needs
   `cd`; route cross-repo builds to a session in that repository. Expected: most of the 685 refusals and 14k `cd`
   prefixes gone, allowlist matches restored.
3. Make the read guard parse the last command after a leading `cd …&&`.
4. Teach the "Read before Edit" rule where agents read with `sed`, or move agents to Read with offset/limit.
5. Model routing: try Sonnet 5.5 or Haiku 5.5 for security-reviewer/reviewer/auditor-style read-only passes and for
   literature triage, measured against Opus on the same PRs (pre-registered; see the eval rules in the harness).
6. Lead context: average 368k per call; `/compact` was used twice. A lead that delegates reading and keeps only
   hand-backs, plus compaction at natural breaks, cuts its 20 %.
7. Replace `sleep`/`until` polling with Monitor or background-task notifications.
8. Fix tool gaps that cost retries: poppler for PDF reads, the arxiv MCP pdf extra.

## Errata (lead, after round 2)
- Output tokens: transcripts record about 4.2 M output tokens, but the cost records report about 27 M (r2-against);
  the gap is likely thinking tokens absent from transcript usage. The cost shares in §1 come from the cost records
  and stand; the per-agent table in §1 uses transcript tokens and **understates output-heavy roles** (the lead most).
- Cache TTL: `subagentPromptCacheTtl` in settings overrides agent frontmatter, so every subagent wrote 1-hour cache
  (r2-against), consistent with the fitted write price. §2 stands.
- Sonnet 5.5 cache reads are $0.10/M, not the same as Opus (r2-opus55-handling).
