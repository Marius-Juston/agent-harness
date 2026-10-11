# Research: agent harness design for a multi-agent research and implementation repo

Status: v1 (2026-10-11), the evidence behind this repository's design.
Labels: SOURCED = cited and fetched; VERIFIED = measured (in the earlier repositories named below, from their own transcripts and records, which are private); BELIEVED = judgment; ASSUMED = unchecked premise.
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

## 3. Drift (VERIFIED in repository A; SOURCED)
- Repository A's drift audit found eleven classes of hand-declared facts repeated in N places. The fix was the same each time: declare once, derive the rest, and make the check a walk over a registry.
- Twice a gate printed "passed" after selecting no tests. A bad git base was read as "nothing changed", and a split argument deselected every test id. [pytest's exit code 5](https://docs.pytest.org/en/stable/reference/exit-codes.html) ("no tests collected") was mapped to "nothing to run".
- *Design:* `devkit/ran.py`: every check reports how much it examined, and zero fails. pytest exit 5 fails. Generated references (the agent-surface reference and the research INDEX) are diffed in the gate. Dangling paths and unknown `uv run dev` commands in any prose fail the gate. Across projects, the same rule is why this repository exists: the devkit is a pinned dependency, the agents, skills and hooks are one plugin, and the scaffold is a Copier template updated three-way, so no project holds a copy that can drift.

## 4. Gaming and integrity (SOURCED; VERIFIED in repository A)
- [METR on reward hacking](https://metr.org/blog/2025-06-05-recent-reward-hacking): frontier models stubbed validators and monkey-patched equality in 30.4 % of RE-Bench runs. [ImpossibleBench](https://arxiv.org/abs/2510.20270) makes the cheat rate measurable.
- Repository A's harness audit: the gate that judges a branch is the branch's own code, and a gate record is a forgeable file. A same-user signature cannot fix this; re-derivation can.
- *Design:* `dev integrate` re-runs the gate on the merged tree in a throwaway worktree. CI re-runs it on GitHub's machines. PRs touching harness paths need the lead to read the diff (`--harness-ok`). Ratchets refuse a raised budget without a signed `[[raise]]`.

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

## 8. Recommendation
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
