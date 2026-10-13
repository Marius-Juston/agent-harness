# Research: agent harness design for a multi-agent research and implementation repo

Status: v1 (2026-10-11), the evidence behind this repository's design.
Section 9 added 2026-10-12 (#61).
Labels: SOURCED = cited and fetched; VERIFIED = measured (in the earlier repositories named below, from their own transcripts and records, which are private); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: not recorded (written before `uv run dev lit status` existed; the evidence is mostly the earlier repositories' own records).
Companions: [../specs/multi-agent.md](../specs/multi-agent.md) (the protocol this note justifies).

## 0. Findings in one paragraph
The harness is built from mechanisms measured on the agents of two earlier private repositories of the author, called here **repository A** (a software project: about 400 transcripts, 300 merged tickets) and **repository B** (a robotics project: about 140 issues across machines), plus the published evidence on agent guidance. Seven design rules follow:
1. Keep always-loaded text short and procedural, and enforce what matters with hooks and checks rather than prose.
2. Declare every fact once and derive the rest, with a check that fails when a pointer dangles.
3. Never let a check that ran nothing report a pass.
4. Answer whole-file reads of long files with outlines, by rewriting the call rather than refusing it.
5. Coordinate across machines through GitHub issues with a race-safe claim, not through ticket files in git.
6. Never take a worker's word for a green gate: re-gate the merged tree, and make budgets only tighten.
7. Measure the surface from transcripts, and delete guidance that changes nothing.

## 1. What guidance helps (SOURCED)
- [SkillsBench](https://arxiv.org/abs/2602.12670): curated skills raise pass rates by +16.6 points on average. Compact skills give +19.0, while comprehensive documentation gives +0.7. Self-generated skills land below no skills (−8.1 on Claude Code). Skills hurt when they prescribe one heavy pipeline with no lightweight fallback. Two or three skills beat four or more.
- [Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988): context files do not raise task success and add over 20 % inference cost. Their instructions are followed, but repository overviews do not help, while non-standard practices do.
- [IFScale](https://arxiv.org/abs/2507.11538): instruction adherence decays as instructions are added, with a bias toward earlier ones.
- [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): start with 20 to 50 tasks drawn from real failures, grade outcomes, isolate trials and read transcripts.
- *Design:* CLAUDE.md holds rules only, within a word budget (`.devkit/ratchets.toml`). There are three skills (lead, research, harness), each starting with its must-hold rules. Agent files state rules, not overviews.

## 2. Enforcement beats instruction (VERIFIED in repository A)
- A rule rewritten as a bounded-loop instruction took unbounded wait loops from 52 of 87 builder runs to 0 of 10. Yet the read discipline stated in its AGENTS.md was still broken: whole-file `cat` of modules over 300 lines was 58 % of all `cat` output, until a PreToolUse hook rewrote such reads into outlines.
- The builder definition changed 35 times in 11 days, and nothing checked any of those changes.
- *Design:* the plugin's `read_guard.py` (rewrite, not refuse), `guard_bash.py` and `edit_guard.py` (refuse with the fix), `lint_after_edit.py` (ruff findings at edit time), and the `harness` skill's rule that a rule broken in a quarter of runs becomes a hook.
- A probe of v0.1.4 `guard_bash.py` with 8 destructive commands blocked only `git push --force`. `git reset --hard`, `checkout -- .`, `clean -fdx`, `rm -rf ../<sibling>`, `stash drop`, `worktree remove --force` and `branch -D` all passed, and sibling worktrees share one stash and one parent directory (VERIFIED by a downstream project's probe script; 8 of 8 blocked after #52, `tests/test_hooks.py`). [cc-safety-net's rule list](https://ccsafetynet.com/docs/reference/blocked-commands) (MIT, Node) blocks the same commands, allows temp paths and `restore --staged`, and lets dynamic `rm` targets through by default (SOURCED). *Design:* the same rules in `guard_bash.py`, which already parses nested commands, instead of a Node dependency. Recursive `rm` is checked against the worktree the session started in, not the cwd after a `cd`, and a one-file `checkout -- <file>` stays allowed (BELIEVED: it touches only the agent's own worktree).
- [Claude Code hooks](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails): a command hook that crashes, times out or exits with a code other than 0 or 2 lets the action through by default; `"onFailure": "block"` (v2.1.295+) makes the failure act as exit 2 (SOURCED). *Design:* set on the policy guards `guard_bash.py` and `edit_guard.py`. `read_guard.py` and `spawn_guard.sh` stay fail-open: a crash there should not stop reads or spawns (BELIEVED).

## 3. Drift (VERIFIED in repository A; SOURCED)
- Repository A's drift audit found eleven classes of hand-declared facts repeated in N places. The fix was the same each time: declare once, derive the rest, and make the check a walk over a registry.
- Twice a gate printed "passed" after selecting no tests. A bad git base was read as "nothing changed", and a split argument deselected every test id. [pytest's exit code 5](https://docs.pytest.org/en/stable/reference/exit-codes.html) ("no tests collected") was mapped to "nothing to run".
- *Design:* `devkit/ran.py`: every check reports how much it examined, and zero fails. pytest exit 5 fails. Generated references (the agent-surface reference and the research INDEX) are diffed in the gate. Dangling paths and unknown `uv run dev` commands in any prose fail the gate. Across projects, the same rule is why this repository exists: the devkit is a pinned dependency, the agents, skills and hooks are one plugin, and the scaffold is a Copier template updated three-way, so no project holds a copy that can drift.

## 4. Gaming and integrity (SOURCED; VERIFIED in repository A)
- [METR on reward hacking](https://metr.org/blog/2025-06-05-recent-reward-hacking): frontier models stubbed validators and monkey-patched equality in 30.4 % of RE-Bench runs. [ImpossibleBench](https://arxiv.org/abs/2510.20270) makes the cheat rate measurable.
- [ImpossibleBench](https://arxiv.org/abs/2510.20270) (SOURCED; GPT-5, o3, Claude Opus 4.1 and others, 2025) makes tests contradict the spec, so any pass is a cheat. Test modification was the primary cheating strategy of Claude Opus 4.1. Read-only tests prevented those edits but not special-casing. An explicit abort option (`flag_for_human_intervention`) cut cheating on Impossible-SWEbench from 54 % to 9 % for GPT-5 and from 49 % to 12 % for o3, with a much smaller effect for Claude Opus 4.1.
- Repository A's harness audit: the gate that judges a branch is the branch's own code, and a gate record is a forgeable file. A same-user signature cannot fix this; re-derivation can.
- *Design:* `dev integrate` re-runs the gate on the merged tree in a throwaway worktree. CI re-runs it on GitHub's machines. PRs touching harness paths need the lead to read the diff (`--harness-ok`). Ratchets refuse a raised budget without a signed `[[raise]]`. The gate flags removed asserts and tests and new skips in the diff against main (`devkit/weaken.py`) unless the owner wrote `weakening-ok:` in the issue, and the builder's way out is `uv run dev board block`. The marker is auditable, not unforgeable (BELIEVED): agents post with the owner's credentials, so the reviewer checks who wrote it.

## 5. Coordination across machines (VERIFIED in repositories A and B)
- Repository A kept tickets as files in git. It needed custom merge drivers, migration renumbering and an integrator folding log fragments, about 28k lines of dev tooling, and conflicts still concentrated on the board files.
- Repository B used GitHub issues and a Project board: a claim records host, agent and branch, cards with no update for 6 h are flagged STALE, and dependencies are links. It worked across machines with no merge conflicts on coordination state.
- *Design:* `devkit/board.py` uses GitHub issues, with status as one label and DONE WHEN, Owns and Blocked-by sections in the body. Claims are race-safe: post a claim, re-read, the earliest live claim wins.

## 6. Tokens (VERIFIED in repository A)
- Builder median per run: 138 tool calls, 42 before the first edit, 1.13 M fresh input tokens and 43 M cached. Cache reads dominate cost, not the model choice. 85 % of responses made one tool call, and 13 to 18 % were chained reads that needed no earlier result.
- [SWE-agent](https://arxiv.org/abs/2405.15793): a capped, windowed file viewer beats raw shell reads.
- *Design:* symbol navigation (`uv run dev code`), read rewriting, one-line spawn prompts carried by `board brief`, 25-line hand-backs, a "batch independent calls" rule, a 1-hour subagent prompt cache (`subagentPromptCacheTtl`), a capped Bash output (`bashOutputMaxChars`), and `skillOverrides` turning off unrelated skills.

## 7. Research errors (SOURCED; BELIEVED)
- Fabricated or mismatched references are a known failure of LLM writing. Repository B's lead found its first explanations wrong several times, so its rules require re-checking a root cause with data and labelling every claim.
- *Design:* `uv run dev cite-check` resolves every arXiv id (batched [arXiv API](https://info.arxiv.org/help/api/index.html) query), DOI ([Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)) and GitHub repository, and fails a link whose text does not match the record's title. The `research-auditor` re-fetches the sources behind the recommendation. The `research` skill runs rounds until saturation, with one researcher arguing against the leading answer.

## 8. Backoff against shared APIs (SOURCED; BELIEVED)
- Retries without jitter re-collide: clients that failed together retry together. Of the schedules compared in [Exponential Backoff And Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) (AWS, 2015), "Full Jitter" (a random sleep over the whole capped exponential window) did the least work, and plain exponential backoff was "the clear loser"; jittered backoff "should be considered a standard approach for remote clients".
- A server's own wait comes first. Retry-After is delay-seconds or an HTTP-date ([RFC 9110, 10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-retry-after)). GitHub answers a primary or secondary rate limit with 403 or 429 ([rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)) and asks clients to honour retry-after, else x-ratelimit-reset when x-ratelimit-remaining is 0, else to wait at least a minute and then exponentially longer ([best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)).
- *Design (BELIEVED):* `devkit/backoff.py` serves `dev lit`, `dev cite-check` and the gh wrapper: full jitter (base 2 s, cap 60 s), a hint honoured as a lower bound plus a random 0-20 % so the waiters it releases do not wake together, and, in `dev lit`, a cooldown shared through the bucket file that doubles on each throttle within 10 minutes and resets on a 200. gh prints no headers, so its wait is 60 s plus jitter.

## 9. The distribution name and dependency confusion (SOURCED; VERIFIED)
- PyPI's `agent-harness` is an unrelated project (homepage `github.com/dschonholtz/agent_harness`), at 0.1.10 on 2026-10-12, above this harness's 0.1.4 (VERIFIED, `curl -s https://pypi.org/pypi/agent-harness/json`).
- uv respects `[tool.uv.sources]` but no other tool does ("Sources are only respected by uv"), and `--no-sources` makes uv ignore the table, per [uv's dependency docs](https://docs.astral.sh/uv/concepts/projects/dependencies/) (SOURCED). uv's default `first-index` strategy exists "to prevent dependency confusion attacks" across indexes, and pinning a package to an index is also a `tool.uv.sources` entry, per [uv's index docs](https://docs.astral.sh/uv/concepts/indexes/) (SOURCED), so an index restriction has the same `--no-sources` hole.
- With a bare `agent-harness` requirement and a git source, `uv lock` takes the git tag, but `uv lock --no-sources` took PyPI's 0.1.10 and `uv export --no-sources` printed `agent-harness==0.1.4`, which pip would fetch from PyPI. With a PEP 508 direct reference (`agent-harness @ git+…@v0.1.4`) all three resolved the git tag, and a `path` source still overrides the reference, which keeps the template's `harness_path` test override working (VERIFIED, uv 0.12.23, scratch projects, 2026-10-12).
- *Design:* the rendered dev group names the git tag directly and keeps the `[tool.uv.sources]` entry; `tests/test_template.py` asserts both, after `copier update` as well. A rename was not needed (BELIEVED): it would break every existing consumer's requirement for no gain over the direct reference.

## 10. Agent and skill frontmatter (SOURCED; VERIFIED; BELIEVED)
Fields and options are from the [subagent frontmatter reference](https://code.claude.com/docs/en/sub-agents#supported-frontmatter-fields) and the [skill frontmatter reference](https://code.claude.com/docs/en/skills#frontmatter-reference), both read 2026-10-12 (#153).
- **Unknown keys.** Claude Code "ignores a field it doesn't recognize without reporting an error" (SOURCED, skills page). *Design:* `AGENT_KEYS` and `SKILL_KEYS` in `devkit/checks.py` are exactly the two documented tables, and the gate fails any other key and any value outside a field's documented options (`effort`, `isolation`, `memory`, `color`, `maxTurns`, `context`, `shell`). `version`, in neither table, left the allowlist; `disallowed-tools`, `background` and `shell` joined it.
- **Plugin-ignored fields.** `hooks`, `mcpServers`, `permissionMode` and `initialPrompt` are ignored for plugin subagents (SOURCED). The gate already flags them.
- **`isolation: worktree`** runs the subagent in a temporary worktree "branched by default from your default branch rather than the parent session's `HEAD`", removed if it makes no changes (SOURCED). *Design:* builder, tester, and now architect and suite-auditor, so the read-only reviews read `main`, not the lead's checkout mid-integration (BELIEVED).
- **`maxTurns`.** At the limit Claude Code returns the output marked partial, and the subagent can be resumed (SOURCED). Assistant turns per run in one downstream project's transcripts, p50/p90/max (VERIFIED, per-run `messages` of `devkit.stats.collect()`, 2026-10-12): builder 40/86/148 (31 runs), researcher 62/96/114 (59), research-auditor 32/42/49 (11), reviewer 12/31/31 (6). *Design (BELIEVED):* a cap about twice the observed maximum stops a runaway loop (§2) without cutting a normal run: builder and tester 300, researcher 250, architect 200 (no runs yet), research-auditor and suite-auditor 120, reviewer 80.
- **`effort`** overrides the session level, and a per-spawn `effort` overrides the field (SOURCED). *Design (BELIEVED):* `high` on every agent: `xhigh` and `max` depend on the model, and the lead can raise one spawn.
- **`color`**, one of eight display colours (SOURCED). *Design:* one per agent, so parallel workers are told apart in the task list; it costs no context.
- **Not adopted (BELIEVED).** `memory` adds a per-agent directory and enables Write and Edit (SOURCED), which breaks the read-only agents and duplicates the board and the notes. `skills` preloads a skill's full text into every spawn (SOURCED); no agent needs a whole skill, and the word budgets would pay for it. `omitClaudeMd` drops the project's CLAUDE.md (SOURCED), whose rules every worker must follow. `background`: fork mode already backgrounds spawns. Architect fan-out: listing `Agent` in a subagent's `tools` lets it spawn down to the depth limit (three layers by default), and "any type list inside the parentheses is ignored" (SOURCED), so a read-only architect could spawn a builder. Fan-out to Explore waits for a spawn guard that refuses other types from read-only agents.
- **Skill arguments.** `$ARGUMENTS` expands to all arguments; named `arguments` map positions with shell-style quoting, so an unquoted multi-word question would fill only the first name (SOURCED). *Design:* the research skill takes its question as `$ARGUMENTS`, with `argument-hint: "[question]"`.
- **`when_to_use`** is appended to `description` in the listing and shares its 1,536-character cap (SOURCED). *Design:* each skill's trigger sentence moved there; the description says what the skill does.
- **Injection.** A failing `` !`cmd` `` "aborts the entire skill invocation", and the docs advise appending `|| true` (SOURCED). *Design:* the lead skill preloads `dev status` that way, and the gate fails an inline injection that does not end in `|| true`.
- **Portability.** claude.ai uploads and the Skills API accept only `name`, `description`, `license`, `compatibility`, `metadata` and `allowed-tools`, and fail hard on any other key (SOURCED). These skills already rely on injection, which works only in Claude Code, so the Claude Code fields cost no portability (BELIEVED). `${CLAUDE_SKILL_DIR}` applies once a skill bundles files; none does yet.

## 11. Recommendation
Adopted as built (BELIEVED, to be measured with `uv run dev agent-stats` and evals):
- Word budgets: 600 per agent, 650 per skill, 600 for CLAUDE.md, only tightening.
- Up to 12 concurrent subagents. The spawn guard holds builders and testers below 6 GB free memory and refuses all spawns at the quota cap (95 % weekly by default).
- One gate per machine, cached by tree; CI runs the same `dev check`.

## Sources
See the inline links. The measurements of repositories A and B come from their own research notes and transcripts (private), read on 2026-10-11. The Claude Code behaviour the plugin relies on (namespaced plugin agents and skills, the frontmatter fields plugin agents ignore, plugin hooks and mods in `hooks/hooks.json`) is from the [plugin components reference](https://code.claude.com/docs/en/plugins/components) and the [plugin manifest reference](https://code.claude.com/docs/en/plugins-reference).

## Follow-ups
- [ ] Scorecard baseline: `uv run dev agent-stats --json` saved after the first 20 worker runs of a project. DONE WHEN a table of medians per agent type is committed.
- [ ] Micro-scenario evals: 10 scenarios from the first real failures. DONE WHEN each runs 3 trials through `claude -p --agent` and the pass rate is recorded.
- [ ] Output compaction for long grep results (a prototype in repository A removed 39 % of large grep results). DONE WHEN an A/B over 20 runs shows fewer tokens and no re-read rise.
