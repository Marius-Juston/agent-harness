Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc, arxiv OK; s2 DOWN (rate limited). No literature search was needed for this sub-question: its evidence is H's own agent surface (read at the branch tip of `149-fix-now-vs-defer`), three H issues read with `gh`, two probes of devkit code, and Anthropic's published prompting guidance (WebFetch).

# r1: which instructions cause deferral

Sub-question of #149: which rules in H's agent surface, and in the system prompts agents run under, push a worker
towards filing a follow-up issue instead of fixing, what each rule protects, and wording that keeps the protection
but adds a fix-now path.

## Findings

### F1. Every role except the tester lacks a fix-now path, while every role has a file-it path
VERIFIED by reading the files at the branch tip (line numbers as of 2026-10-12):

| # | Rule (file:line) | Pushes towards | Protects | Fix-now path today |
|---|---|---|---|---|
| 1 | "Work without an issue does not exist." (`template/.devkit/HARNESS.md.jinja`:11) | `board new` for anything the issue did not name | one record of all work; measurable DONE WHEN | none |
| 2 | "Stay inside your issue's Owns"; outside edits "only when the issue needs it end to end" (`plugins/harness-ops/agents/builder.md`:9, :22) | filing adjacent fixes | parallel builders on disjoint files (`board wave`), no merge conflicts | only edits the issue *needs*; "nearby and cheap" is excluded by wording |
| 3 | "Hotspots ... belong to the lead: a worker asks for the change in its hand-back" (`docs/specs/multi-agent.md`:83) | hand the fix to the lead | shared registries and configs edited by one agent | the lead (see 4) |
| 4 | "Never build while workers run. The lead only plans, dispatches and integrates." (`plugins/harness-ops/skills/lead/SKILL.md`:9; HARNESS.md.jinja:12-14; multi-agent.md:10) | the lead turns every handed-up fix into an issue | lead context and attention; one integrator; no lead edits racing workers | resume the worker "for fixes" of PR comments (lead SKILL.md:23); nothing for reviewer findings or hotspot requests |
| 5 | "One PR per issue" (multi-agent.md:84); "Split an item when its DONE WHEN would need two different measurements" (lead SKILL.md:31) | a second change gets a second issue | reviewable PRs; `Closes #N` means every DONE WHEN met | none |
| 6 | Tester: "A small defect inside the issue's Owns: fix it. Otherwise ... xfail(strict=True) and file the defect" (`plugins/harness-ops/agents/tester.md`:15) | filing outside Owns | tester scope | **yes, inside Owns**: the only explicit fix-now rule in the surface |
| 7 | Researcher round mode "write only the scratch file" (`plugins/harness-ops/agents/researcher.md`:12); researcher may not "edit code" (multi-agent.md:11); "File each important gap you did not close as an issue" (researcher.md:32); synthesis "files follow-up issues for the gaps it left open" (`plugins/harness-ops/skills/research/SKILL.md`:46) | research findings that are a two-line config change become issues | research and implementation stay separate; researchers run no tests | none (by design) |
| 8 | Architect "never edit[s]" and files one issue per step (`plugins/harness-ops/agents/architect.md`:7, :12) | issues | owner decides cuts | none (by design) |
| 9 | Builder: "If a design choice has no note, stop and ask the lead for a researcher" (builder.md:13) | a research issue for small choices | evidence before design | none; "design choice" is undefined, so a mechanical fix can read as one |
| 10 | Hand-back slots: builder "follow-up issue numbers" (builder.md:30) and PR body "follow-ups filed as issues" (builder.md:27); researcher "the issues filed" (researcher.md:35) | a slot that asks for issue numbers | the lead sees deferred work | tester's slot is the exception: "defects found (fixed here, or issue numbers)" (tester.md:18); no role has a "fixed in passing" slot |

BELIEVED: rules 1, 2, 4 and 5 combine into one channel. A builder may not take the fix (2), so it hands it up (3); the
lead may not build (4); the fix has no DONE WHEN in the current issue (5); so the only legal move left is a new issue (1).
None of these rules is wrong on its own; the gap is that no rule names the cheap case.

### F2. Two gate behaviours make a fix-now path harder than filing (VERIFIED)
- **The weaken check is direction-blind on test config.** `devkit/weaken.py` lists `xfail_strict` among config keys that
  "select, deselect or relax tests" and flags any added line with one, unless the issue records the owner's
  `weakening-ok:`. Probe [scripts/xfail_strict_probe.py](scripts/xfail_strict_probe.py) feeds a diff that adds
  `xfail_strict = true` to `pyproject.toml`; `weaken.findings` returns `['pyproject.toml:1: test config edit:
  xfail_strict = true']`. Turning strict xfail on is a tightening, yet a worker cannot land it without the owner. This is
  one of the two edits in H #136 (below), so that follow-up could not have been a fix-now even with a fix-now rule.
- **Floor raises are free but only advertised.** `devkit/checks.py:ratchet_raises` refuses a lowered `[tests]` floor
  without an owner-approved `[[raise]]` and accepts a raised one. Probe [scripts/floor_raise_probe.py](scripts/floor_raise_probe.py): `min_tests`
  759 -> 874 returns `[]`; 759 -> 700 returns the refusal. The gate prints `raise min_tests to N (M passed)` as a note
  (`devkit/gate.py:floor_problems`), not as a problem, and `.devkit/ratchets.toml` is in no ordinary issue's Owns, so the
  note is a standing invitation to file (rule 2) rather than to edit.

### F3. Three H follow-ups from 2026-10-13 map onto specific rules (VERIFIED with `gh issue view`, `gh pr view`)
- **H #136** "pytest config: xfail_strict on and min_tests raised to the gate's own suggestion". Context cites the
  research note `docs/research/test-suite-design.md` §3 (filed from research #112: rule 7). DONE WHEN is two config
  values; Owns `pyproject.toml`, `.devkit/ratchets.toml`, `template/`. Blocked from fix-now by F2 (weaken) as well.
- **H #145** "guard_bash: read-only agents may not pass git -c". Context: "Found reviewing PR #142": the allowlist that
  PR #142 adds accepts `git -c core.fsmonitor=<cmd> status`, which runs an arbitrary command. Issue created
  2026-10-13T02:39; PR #142 merged 2026-10-13T02:43, four minutes later. A defect in the PR's own new code was filed
  rather than sent back (rules 4 and 5; the reviewer's "merge after fixes" verdict in
  `plugins/harness-ops/agents/reviewer.md`:19 exists but was not used). This is the clearest case of deferral that the
  current wording already allows the lead to avoid.
- **H #146** "suite-auditor: read-only enforcement and listings after #142". Context: "#114 avoided editing those lines
  while #142 is open". Rule 2 and the hotspot rule did their job here: two open PRs on one file. Deferral was right;
  only the form (a new blocked issue rather than "rebase and add it once #142 merges") is open to question.

### F4. Anthropic's published guidance pushes the same way, and calls it opt-in
- SOURCED: the cross-model guide's "Overeagerness" section says Opus 4.5 and 4.6 "have a tendency to overengineer" and
  offers an opt-in sample, "If you're seeing this undesired behavior": "Only make changes that are directly requested or
  clearly necessary ... Don't add features, refactor code, or make 'improvements' beyond what was asked. A bug fix
  doesn't need surrounding code cleaned up." The same guide's migration list says to "dial back" anti-laziness
  prompting because "Claude 4.6 models are more proactive and may overtrigger"
  ([Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), Anthropic, fetched 2026-10-12).
- SOURCED: the Opus 5 guide says the model "can also expand the scope of a task, adding steps that weren't requested",
  and for narrow tasks suggests: "If the request seems mistaken or a better approach exists, say so in a sentence and
  continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and
  stop short of actions that are clearly beyond what was asked." The same guide warns that a review prompt saying
  "be conservative" may be followed "literally" and report less
  ([Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5), Anthropic, fetched 2026-10-12).
  BELIEVED: in a harness the durable way to "say so in a sentence" is `board new`, so a model trained to flag rather
  than widen, plus rules 1-5, yields an issue per flag.
- SOURCED: the Opus 5.5 guide lists, as an unwanted end of turn for unattended agents, "a list of decisions for the user
  when, by your own account, none of them blocks the rest of the work", and says the stops worth keeping are "the ones
  where nothing can move without them"
  ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5), Anthropic, fetched 2026-10-12).
  BELIEVED: a follow-up issue for a non-blocking two-line fix is the board form of that same unwanted stop.
- SOURCED (third party, not an Anthropic publication): a capture of the Claude Code system prompt "as of Dec 9 2025"
  carries the same lines ("Only make changes that are directly requested or clearly necessary", "Don't add features,
  refactor code, or make 'improvements' beyond what was asked", "A bug fix doesn't need surrounding code cleaned up")
  ([Claude Code System Prompt](https://blog.thepete.net/claude-code-system-prompt/), blog.thepete.net, captured 2025-12-09).
  It could not be checked against an official copy or against the Claude Code version agents run today.
- SOURCED: Anthropic's own long-running harness had the agent "work on only one feature at a time" after it "tended to
  try to do too much at once", and ends each session "leaving the environment in a clean state"
  ([Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), Justin Young, Anthropic Engineering, 2025-11-26).
- BELIEVED: two layers (model-level "beyond what was asked" and harness-level Owns/one-issue rules) stack, and neither
  defines "clearly necessary". The protection is real: Anthropic reports scope expansion on Opus 5, so removing the
  limits risks scope creep. The fix is a narrow, named exception, not a looser rule.

### F5. Word budgets constrain the wording (VERIFIED by `wc -w` on frontmatter-stripped bodies; devkit's own counter may differ slightly)
builder.md 409 of 600, tester.md 205 of 600, researcher.md 599 of 600, lead SKILL.md 646 of 650, HARNESS.md.jinja plus
CLAUDE.md.jinja 566 of 600 (`.devkit/ratchets.toml` defaults). The researcher and lead changes must replace words, not
add them; the builder change has room.

## Proposed wording (BELIEVED; the line bound is ASSUMED until the frequency/cost round measures closed follow-up diffs)

**Fix-now rule (one rule, referenced by every role).** In HARNESS.md.jinja, after "Work without an issue does not exist":
> A small fix found while working is part of the current issue, not new work: fix it in this PR when it is under
> ~20 changed lines, sits in a file the PR already touches or under its Owns, needs no new design choice or owner
> approval, and no other in-flight item owns the file. Name it under "Fixed in passing" in the PR body. File an issue
> only when one of those fails.

This keeps every protection in F1: Owns and hotspots still bound the files (rules 2, 3), "no new design choice" keeps
research-first (rule 9), "no owner approval" keeps the weaken and ratchet gates, and the PR body keeps the record (rule 1).

**Builder** (builder.md:22, replaces the sentence, roughly word-neutral):
> Edits outside Owns: make one when the issue needs it, or when it is a fix-now (HARNESS rules) in a file no other worker
> holds; list it in the hand-back.

and in the reply slot (builder.md:30): "files outside Owns, fixed in passing, and follow-up issue numbers".

**Tester** (tester.md:15): widen "inside the issue's Owns" to "inside the issue's Owns or a fix-now", keep the xfail-and-file
branch for the rest. It is already the model for the other roles.

**Lead** (lead SKILL.md, replace rather than add): in step 2, "A reviewer finding in the PR's own new code is fixed
before integrate: resume the worker. File it only when it needs a design choice or another Owns." In step 3, change
"New work found" to "New work found (not a fix-now: resume the worker that holds the file instead)". This targets the
H #145 pattern without letting the lead build.

**Researcher** (no edit right added; replace words in researcher.md:32): "File each important gap you did not close as
an issue; put mechanical follow-ups (a config value, a flag) in one issue, not one each." Keeps research and code
separate (rule 7) and cuts issue count instead.

**Gate** (devkit, needs its own issue and tests): make `weaken` direction-aware for tightening keys (`xfail_strict = true`
added, or a stricter `fail_under`), and offer the floor raise the gate already computes as an applied change in a
fix-now, not only a note (F2).

A check the lead can run: count PR-body "Fixed in passing" lines against follow-up issues per PR in `agent-stats` or
the hand-back, before and after the wording change.

## Gaps not closed
- The ~20-line bound is ASSUMED. It should come from the distribution of diff sizes of agent-filed issues that were later
  closed (the frequency/cost sub-question), not from judgment.
- Whether the model-level "beyond what was asked" text is still in the Claude Code system prompt that subagents get
  today was not checked against an official source; only a 2025-12-09 third-party capture was fetched.
- Only three H issues were read by hand; no count of how many of the about 25 follow-ups of the 2026-10-12/13 session
  each rule produced. The table in F1 says which rule *can* cause filing, not how often each one did.
- No controlled test that the proposed wording changes behaviour; `dev eval` with a with/without scenario (one builder
  issue with a planted two-line adjacent defect) would measure it.
- The reviewer finding in #145 may have been deferred by the lead's explicit choice rather than by any rule; the
  lead's transcript for that session was not read.

## Sources fetched
- [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), Anthropic docs, fetched 2026-10-12.
- [Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5), Anthropic docs, fetched 2026-10-12.
- [Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5), Anthropic docs, fetched 2026-10-12.
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), Justin Young, Anthropic Engineering, 2025-11-26.
- [Claude Code System Prompt](https://blog.thepete.net/claude-code-system-prompt/), blog.thepete.net, captured 2025-12-09 (third-party capture).
- H issues #136, #145, #146 and PR #142, read with `gh` on 2026-10-12/13.
- H files at the branch tip: `plugins/harness-ops/agents/*.md`, `plugins/harness-ops/skills/lead/SKILL.md`,
  `plugins/harness-ops/skills/research/SKILL.md`, `template/.devkit/HARNESS.md.jinja`, `template/CLAUDE.md.jinja`,
  `docs/specs/multi-agent.md`, `devkit/weaken.py`, `devkit/checks.py`, `devkit/gate.py`, `devkit/board.py`.
