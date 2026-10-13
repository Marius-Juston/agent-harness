Search coverage: `uv run dev lit status` on 2026-10-12: openalex, crossref, europepmc, arxiv OK; s2 DOWN (HTTP 429,
shared pool), so citation counts and Semantic Scholar-only papers are missing. Also used: the arxiv MCP (abstracts and
single sections), WebFetch of vendor docs, and the GitHub API for prompts and repository metadata.

# r1: literature and prior art on deferred work

Sub-question of #149: what research and other agent harnesses say about deferring work (TODO and SATD survival, backlog
decay, cost of fixing later, boy-scout rule, tangled changes, LLM-agent deferral and scope), ending in a candidate rule.

## Findings

### F1. Deferred debt is usually repaid by the person who wrote it, fast or very late
- SOURCED. [Maldonado et al. 2017](https://doi.org/10.1109/icsme.2017.8) (ICSME, five OSS projects): most SATD
  (self-admitted technical debt) comments are removed, most of them *self-removed* by the person who introduced them,
  and median SATD lifetime is about 18 to 172 days depending on the project.
- SOURCED. [Zampetti et al. 2018](https://doi.org/10.1145/3196398.3196423) (MSR, five Java projects): 20 to 50 % of
  SATD "removals" are accidental (the whole class or method was deleted), and only 8 % of removals are acknowledged in
  the commit message. Survival numbers based on comment disappearance therefore overstate real repayment.
- SOURCED. [Xavier et al. 2020](https://arxiv.org/abs/2003.09418) (MSR, 286 issue-based SATD instances from five
  projects): only 29 % can be traced to a code comment; TD issues take longer to close than other issues though their
  churn is not larger; 45 % were taken on to ship earlier.
- SOURCED. [Li, Soliman and Avgeriou 2020](https://doi.org/10.1109/seaa51224.2020.00083) (SEAA, 500 issues of two
  Apache projects, 152 SATD items): most TD is paid off, mostly by whoever identified or created it; median repayment
  25.0 h, mean 872.3 h. VERIFIED arithmetic ([scripts/breakeven.py](scripts/breakeven.py)): mean is 34.9x the median,
  so a fast majority hides a very long tail.
- BELIEVED. The common repayer is "the same person, soon". An agent worker exits at hand-back, so a filed follow-up
  always goes to the slow path: a new worker that has to rebuild the context.

### F2. Backlog decay: the strongest statement is practitioner experience, not data
- SOURCED (guidance; based on experience, no data). [Google eng-practices, "Cleaning It Up Later"](https://github.com/google/eng-practices/blob/master/review/reviewer/pushback.md):
  "usually unless the developer does the clean up *immediately* after the present CL, it never happens." Their rule has
  two cases: complexity that the CL *introduces* "must be cleaned up before submission unless it is an emergency";
  problems the CL *exposes* nearby, if they cannot be fixed now, go in a bug the developer assigns to themselves, with
  an optional TODO that points to the bug. (Repo: 23k stars, last push 2024-09, license NOASSERTION.)
- SOURCED. [Khatoonabadi et al. 2023](https://arxiv.org/abs/2305.18150) (20 large OSS projects): Stale bot cleared PR
  backlogs and sped up review after adoption, but the projects then had markedly fewer active contributors. Closing old
  items automatically trades backlog size for engagement; it is not repayment.
- Gap: I found no study that measures what fraction of filed follow-up issues are ever completed, for people or for
  agents. The local transcript and board data (other rounds) are the only evidence for that rate.

### F3. "Fixing later costs more" is not a general law; for agents the cost is fixed overhead per item
- SOURCED. [Menzies et al. 2016](https://arxiv.org/abs/1609.04886) (EMSE, 171 projects, 2006 to 2014): **no evidence of
  the delayed-issue effect**; issues fixed in a later phase did not consistently or substantially cost more. So escalating
  defect cost does not justify fix-now.
- SOURCED. [Besker et al. 2018](https://doi.org/10.1145/3194164.3194178) (survey of 43 developers, 16 interviews,
  longitudinal): developers self-report losing on average 23 % of development time to TD, often having to add new TD
  because of existing TD. Self-report, not measured.
- SOURCED (repository A, internal): a builder run's median is 138 tool calls, **42 before the first edit**, and
  1.13 M fresh input tokens ([agent-harness note, section 6](../../agent-harness.md)). A follow-up done by a fresh worker
  pays that orientation again, plus an issue, a PR, a gate run and an integration.
- VERIFIED arithmetic, ASSUMED inputs ([scripts/breakeven.py](scripts/breakeven.py)). Model: fix now costs
  f(1+ρ), where ρ is the added review and tangling cost. Deferring costs F + p(f+O) + (1−p)L, where F = 3 calls to file
  the issue, O = 42 calls of overhead, p = the probability the follow-up is ever done, and L = the loss if it never is (set
  to 0, which favours deferring). Fixing now wins for any fix under **13 tool calls in every cell tried**
  (ρ ≤ 0.5, p ≥ 0.3); under 31 to 70 calls when p = 0.6; under 90 to 180 when p = 1 and ρ > 0. When ρ = 0 and p = 1,
  fixing now always wins. Counter-intuitive but direct: **the more certain a follow-up is to be done, the stronger the
  case for doing it now**, because the overhead O is then certain too.

### F4. Boy-scout / camp-site rule: widely recommended, no outcome data for the rule itself
- SOURCED. [Fowler, "Opportunistic Refactoring" (2011)](https://martinfowler.com/bliki/OpportunisticRefactoring.html):
  "always leave the code behind in a better state than you found it", only with green tests. Its limits: "a genuine danger of
  going down a rabbit hole", decide "when to call it a day", and if you note something, "come back the same day, before
  you've hit that final point of being done."
- SOURCED. Refactoring carries real risk. [Bavota et al. 2012](https://doi.org/10.1109/scam.2012.20) analysed 15,008
  refactorings in 3 systems: some kinds (hierarchy changes such as pull-up method) induce faults frequently. A
  [replication on 103 systems (FSE 2020)](https://doi.org/10.1145/3368089.3409695) confirmed this quantitatively.
  [Kim et al. 2012](https://doi.org/10.1145/2393596.2393655) (Microsoft): developers see refactoring as costly and risky,
  yet the Windows modules a dedicated team refactored had fewer inter-module dependencies and fewer post-release defects.
- SOURCED. [Iammarino et al. 2019 (ICSME)](https://doi.org/10.1109/icsme.2019.00029): refactorings co-occur
  with SATD removal more than with other commits, but mostly as separate quality work done at the same time.
- BELIEVED. The evidence supports small, behaviour-preserving, test-covered cleanups. It does not support structural
  refactors done in passing. No study measures the boy-scout rule's own outcome.

### F5. Tangled changes: the cost is real but mostly lands on history and review noise. This limits the rule
- SOURCED. [Herzig and Zeller 2013](https://doi.org/10.1109/msr.2013.6624018) (MSR, five Java projects): up to 15 % of
  bug fixes are tangled, and at least 16.6 % of source files end up wrongly linked to bug reports.
- SOURCED. [Herbold et al. 2022](https://doi.org/10.1007/s10664-021-10083-5) (EMSE, crowd-labelled lines, 4 labellers per
  line): only 17 to 32 % of the changed lines in bug-fixing commits actually fix the bug (66 to 87 % in production files only).
- SOURCED. [Di Biase et al. 2019](https://doi.org/10.7717/peerj-cs.193) (controlled experiment, N = 28): decomposed
  changes led to fewer wrongly reported issues, but **no change in defects found or in understanding** of the change.
  So tangling a small fix into a PR hurts review less than the folklore says.
- SOURCED. [Google eng-practices, small CLs](https://github.com/google/eng-practices/blob/master/review/developer/small-cls.md):
  refactorings go in a separate CL from features or bug fixes, but "small cleanups such as fixing a local variable name
  can be included". About 100 lines is "usually a reasonable size", 1000 "usually too large", and file spread counts too.
- SOURCED. [Watanabe et al. 2025, TOSEM](https://arxiv.org/abs/2509.14745) (567 Claude Code PRs, 157 OSS projects):
  "nearly 40 %" of agentic PRs combined several tasks; 83.8 % were merged against 91.0 % for matched human PRs
  (VERIFIED: 475/567 and 516/567). Of the 92 rejected agentic PRs, 3.3 % were rejected as "too large" (the top reason
  was "implemented by other PRs", 12.0 %). The authors advise splitting large multi-purpose PRs.
- BELIEVED. The tangling data argues for a *separate commit* and a size cap, not for a separate issue. With squash-merge
  the commit separation is lost, so the PR body has to name the extra fix for history to stay attributable.

### F6. LLM agents both stop early and over-reach; vendor prompts resolve it by "mention it, don't do it"
Model and date are given for each claim; older-model results are not comparable to current models.
- SOURCED. [MAST, Cemri et al. 2025](https://arxiv.org/abs/2503.13657) (1600+ traces, 7 multi-agent frameworks, GPT-4
  series and Claude 3 / 3.7 Sonnet, kappa = 0.88): premature termination (FM-3.1) is 6.20 % of failure annotations,
  and no or incomplete verification 8.20 %. AppWorld showed premature termination most often, which the authors attribute
  to unclear termination conditions.
- SOURCED. OpenAI said `gpt-4-0125-preview` (January 2024) "is intended to reduce cases of 'laziness' where the model
  doesn't complete a task"; [Aider's benchmark page](https://aider.chat/docs/benchmarks-0125.html) found it lazier than
  `gpt-4-1106-preview` with unified diffs. A vendor claim contradicted by one independent test.
- SOURCED. Anthropic prompting docs, fetched 2026-10-12:
  [best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
  says Claude "may sometimes naturally try to wrap up work as it approaches the context limit", and that Opus 4.5 and 4.6
  overengineer.
  [Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1)
  "sometimes describes what it would do next instead of doing it". The same page also says it "may fix nearby code,
  extend behavior the task didn't mention".
  [Sonnet 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5)
  at `low` and `medium` effort "sometimes checks in before the work is done"; at `xhigh` and `max` it "can also make
  related fixes it noticed along the way".
  [Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)
  "can also expand the scope of a task". Direction depends on model and effort, so no single rule
  word fits all models.
- SOURCED, and central to #149. The vendor's recommended scope prompts send noticed-but-unrequested work to a mention:
  - Fable 5.1: a pre-existing bug or unmentioned behaviour is not fixed "in this change unless the requested behavior
    cannot work without it; report it as a follow-up in your summary". Cleanup "is a suggestion to make at the end".
  - Sonnet 5.5: "If you think one would help, mention it at the end instead of doing it."
  BELIEVED: in a harness where hand-back slots ask for issue numbers, each such mention becomes a filed issue.
- SOURCED. [SNARE, Qu et al. 2026](https://arxiv.org/abs/2605.28122): out of 10,000 benign runs (4 agent frameworks × 5 models),
  19.51 % took out-of-scope actions such as reading credentials or deleting files. The framework explains 56 % of the
  variance and the model 21 %. Claude Code with Sonnet 4.6: 19.6 % [16.4, 23.3]. This is *authorisation* scope, which
  argues for hard limits on risky actions, not against small in-file fixes.

### F7. Prior art in harnesses: all default to "minimal, mention the rest"; two add a closing step
| Harness (checked 2026-10-12) | Rule | Closing mechanism |
|---|---|---|
| [openai/codex](https://github.com/openai/codex) `codex-rs/core/gpt_5_2_prompt.md` (Apache-2.0, 129k stars, pushed 2026-10-12) | "Do not attempt to fix unrelated bugs or broken tests. It is not your responsibility to fix them. (You may mention them...)"; also "Persist until the task is fully handled end-to-end" | none |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) `config/default.yaml` (MIT, 20.5k stars, pushed 2026-10-12) | "make the minimal changes to non-tests files" | none (benchmark setting) |
| [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) `clients/typescript/src/prompts/system-prompt.ts` (MIT) | "focus on making the minimal changes needed to solve the problem" | none |
| Anthropic prompting docs (above) | follow-up "in your summary" unless the task cannot work without it | the user decides |
| [obra/superpowers](https://github.com/obra/superpowers) `skills/subagent-driven-development/SKILL.md` (MIT, 298k stars, pushed 2026-10-10) | Minor findings and out-of-scope observations go to a ledger as "deferred minors" and "never extend the loop"; fix loop capped at 5 rounds | the final whole-branch review triages the ledger *before merge*: "A roll-up nobody reads is a silent discard." |
| Google eng-practices (F2) | introduced complexity: fix before submit; exposed nearby problems: self-assigned bug | the author owns the bug |
All VERIFIED by reading the files through the GitHub API on 2026-10-12. None of these sets a size threshold for fix-now,
apart from Google's "local variable name" example and its 100 / 1000-line CL norms.

## Candidate decision rule (BELIEVED; thresholds from F3, F5 and Google, to be checked against local data)
**Fix it in this PR, as its own commit listed under "Fixed in passing" in the PR body, when all four hold:**
1. **Related.** The change itself caused or exposed it (a gate, test or flag it touches), or it sits in a file the PR
   already changes or in the issue's Owns. Something the PR *introduced* is always fixed, whatever its size (Google rule).
2. **Small.** About 20 changed lines or fewer, in at most 2 files, or roughly 13 tool calls or fewer (the break-even
   that held in every assumed cell, F3). No new design choice, no public interface, schema or dependency change, and no
   hierarchy-level refactor (F4).
3. **Checkable now.** The existing tests or gate exercise it in this run (Fowler's green-tests condition).
4. **Not someone else's.** Not a lead-owned hotspot, not an action that needs permission, and not a file another
   running worker owns.
**Otherwise file it**, with an owner and a DONE WHEN, and put it in a hand-back slot the lead must triage at integrate,
as superpowers does before merge: fold it into the next worker that touches that file, or close it as won't-fix. Do not
let it wait in the backlog.
Roles that cannot edit (researcher in round mode, architect) record a fix-ready item: exact file, change and check.
The lead hands it to the next builder instead of opening a research issue.

Gate or hand-back check (BELIEVED, needs measuring): flag a follow-up a worker filed whose body names a file in that
worker's own PR diff and estimates a change under the threshold. That is the "could have fixed it" signature.

## Gaps seen, not closed
- No measured completion rate for filed follow-ups (human or agent) was found; the local board data must supply p for F3.
- No outcome study of the boy-scout rule itself; the support is expert guidance plus refactoring-risk data.
- No study measures deferral rates of current Claude models; vendor docs are qualitative and model-specific.
- Did not fetch full texts of Maldonado 2017 or Bavota and Russo 2016 for per-project removal percentages; s2 was down.
- Thresholds (20 lines, 2 files, 13 calls) rest on ASSUMED ρ and F; re-fit them on agent-filed issues later closed by a
  small diff.

## Sources fetched
- Maldonado et al., ICSME 2017, https://doi.org/10.1109/icsme.2017.8 (abstract)
- Zampetti et al., MSR 2018, https://doi.org/10.1145/3196398.3196423 (abstract)
- Xavier et al., MSR 2020, https://arxiv.org/abs/2003.09418 (abstract)
- Li, Soliman and Avgeriou, SEAA 2020, https://doi.org/10.1109/seaa51224.2020.00083 (abstract)
- Iammarino et al., ICSME 2019, https://doi.org/10.1109/icsme.2019.00029 (abstract)
- Menzies et al., EMSE 2016, https://arxiv.org/abs/1609.04886 (abstract)
- Besker et al., TechDebt 2018, https://doi.org/10.1145/3194164.3194178 (abstract)
- Khatoonabadi et al. 2023, https://arxiv.org/abs/2305.18150 (abstract)
- Herzig and Zeller, MSR 2013, https://doi.org/10.1109/msr.2013.6624018 (abstract)
- Herbold et al., EMSE 2022, https://doi.org/10.1007/s10664-021-10083-5 (abstract)
- Di Biase et al., PeerJ CS 2019, https://doi.org/10.7717/peerj-cs.193 (abstract)
- Bavota et al., SCAM 2012, https://doi.org/10.1109/scam.2012.20; replication FSE 2020, https://doi.org/10.1145/3368089.3409695;
  Kim et al., FSE 2012, https://doi.org/10.1145/2393596.2393655 (abstracts)
- Watanabe et al., TOSEM 2025, https://arxiv.org/abs/2509.14745 (sections 4.1, 4.2, 5.2 via text search)
- Cemri et al. 2025, https://arxiv.org/abs/2503.13657 (section 5 and appendix A.3 via text search)
- Qu et al. 2026, https://arxiv.org/abs/2605.28122 (abstract, evaluation table)
- Fowler 2011, https://martinfowler.com/bliki/OpportunisticRefactoring.html
- Google eng-practices pushback.md and small-cls.md (raw files)
- Anthropic prompting docs: best practices, Opus 5, Sonnet 5.5, Fable 5.1 pages (fetched 2026-10-12)
- Aider benchmark page, https://aider.chat/docs/benchmarks-0125.html (the OpenAI announcement itself returned 403; its
  wording is quoted from Aider's page)
- Prompts read via the GitHub API: openai/codex, SWE-agent/SWE-agent, OpenHands/software-agent-sdk, obra/superpowers
