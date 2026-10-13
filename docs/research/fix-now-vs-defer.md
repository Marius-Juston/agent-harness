# Research: fixing small things now instead of filing follow-ups

Status: v1 (2026-10-13), audit fixes applied. Issue: #149. Serves the wording of the builder, tester, researcher and lead instructions.
Labels: SOURCED = cited and fetched; VERIFIED = measured here (script named; the sibling repositories are private, so their numbers reproduce only on the owner's machine); BELIEVED = judgment; ASSUMED = unchecked premise.
Search coverage: `uv run dev lit status` on 2026-10-12 (round 1) and 2026-10-13 (synthesis and audit fixes) showed openalex, crossref, europepmc and arxiv up and s2 DOWN (rate limited, skipped), so Semantic Scholar-only papers and citation counts are missing. Also used: the arXiv MCP (abstracts and single sections), WebFetch of vendor docs, the OpenAlex abstract of Di Biase et al., the GitHub API for other harnesses' prompts, and read-only `gh` on the sibling repositories. One round; the stop reason is in [gaps-r1](_scratch/fix-now-vs-defer/gaps-r1.md).
Companions: [agent-harness.md](agent-harness.md) (per-run overhead of a fresh worker, §6). Round files: [_scratch/fix-now-vs-defer/](_scratch/fix-now-vs-defer/QUESTION.md). Repositories appear under code names, as in the round files: **H** = this repository, **repo-L** = a large agent-run game server with a file board, **repo-R** = a robotics simulation research repository, **repo-E** = a research-first product repository that consumes this harness.

## 0. Findings in one paragraph
Agents file many follow-ups, and most of them state no reason. Few are small fixes that could have been made in place. In 2026-10-03 to 2026-10-13 transcripts (Opus 5.5 in 89% of main-session and 94% of subagent model fields), agents filed 640 items while working on another issue. 493 of them are from repo-L, whose sessions stopped on 2026-10-05; there, 74% are still open after 8 to 10 days. The other repositories' items are under 2 days old at the snapshot, so their open shares (69 to 87%) say little yet. Follow-ups that closed did so within 1.4 days (median 3.1 h), and their closing changes were ordinary tickets: only 4 of 143 measured closing diffs were 30 lines or fewer. Separately, one rater reading 137 follow-ups in H, repo-E and repo-R judged about 12% small, and about 3% small and free of any rule that blocked a fix in place. The clearest is an H review finding in a PR's own new code, filed four minutes before that PR merged. The share with no stated reason runs from 31% (repo-R) to 67% (repo-L); in H it is 43%. The cause is structural. Every role has a "file it" path and a hand-back slot for issue numbers, and only the tester has a "fix it" path. The literature does not show that later is more expensive (no delayed-issue effect in 171 projects). Debt is mostly repaid by its author, but an agent worker exits at hand-back. A small change cost a median of 2 tool calls in place, against a median of 20 calls of orientation for a fresh Opus 5.5 worker. **Recommendation:** a single fix-now rule in `multi-agent.md` §3, linked from every role. Fix the issue in this PR, as its own `fix-now:` commit, when it is at most 20 lines in at most 2 files, is in the PR's diff or Owns or was exposed by it, is mechanical, and sits in a file no other item holds. A defect the PR introduced is always fixed. The lead triages follow-ups before `dev integrate`, and every filed follow-up names the condition that failed. The thresholds are ASSUMED; #155 re-measures per repository.

## 1. Question and scope
The question ([QUESTION.md](_scratch/fix-now-vs-defer/QUESTION.md)): why do agents in a harness-run repository file a follow-up issue for work they could finish in the same change, and what rule makes them fix it now without breaking scope discipline? The scope is how often this happens, what the deferred work cost, which instructions cause it, the literature and prior art, and a rule with thresholds. Out of scope: changing the instructions themselves (#154).

## 2. How often agents defer, and what it cost (VERIFIED)
Numbers come from [measure_deferral.py](_scratch/fix-now-vs-defer/scripts/measure_deferral.py) unless marked (stdlib, read-only `gh`, outputs in gitignored `.scratch/`). The issue and PR snapshot is from 2026-10-13. Items closed after it (H #136, #145, #146) still count as open. The script reads this machine's Claude Code transcripts: 327 main and 659 subagent files at the first run, 664 at the rerun, with the same 640 items. A follow-up is an item created by a `board new`, `gh issue create` or `gh api` call during work on another, known issue. The models come from [models_in_window.py](_scratch/fix-now-vs-defer/scripts/models_in_window.py), which scans the same files. Opus 5.5 wrote 89.1% of main-session and 93.5% of subagent model fields, Opus 5 10.6% of main-session fields, and Sonnet 5.5 3.9% of subagent fields. These results describe these models only. Method: [r1-measured](_scratch/fix-now-vs-defer/r1-measured.md).

| repo | follow-ups | still open | at least 7 days old | no stated reason |
|---|---|---|---|---|
| repo-L | 493 | 366 (74%) | 493; 126 (26%) closed within 7 days | 332 (67%) |
| repo-E | 75 (10 are throwaway `[test]` items) | 57 (76%) | 0 (max age 1.9 days) | 35 (47%) |
| H | 46 | 40 (87%) | 0 (max age 1.8 days) | 20 (43%) |
| repo-R | 26 | 18 (69%) | 0 (max age 3.4 days) | 8 (31%) |
| all | 640 | 481 (75%) | 493 | 395 (62%) |

- **Who files.** VERIFIED: subagents filed 600 of the 640: builders and testers 399, researchers 172, the architect 8, other subagents 21. The lead filed 40. In one H session on 2026-10-13, 25 were filed during other work.
- **How long they stay open.** VERIFIED: the only aged cohort is repo-L, and its sessions stopped on 2026-10-05. Its 74% open means work stopped there; it is not evidence that follow-ups are never done. So p = 0.26, the share closed within 7 days, is a repo-L lower bound on the chance that a follow-up gets done. Follow-ups that closed merged their closing change a median of 3.1 h after filing (IQR 1.2 to 10.8 h, max 32.4 h).
- **Closed follow-ups were tickets, not small fixes (measured diffs).** VERIFIED: closing diffs had a median of 1,072 net lines (IQR 551 to 1,665). Net excludes generated docs, locks, handoffs, logs, board files and research scratch. 4 of 143 (3%) were at most 30 lines, and 10 of 143 (7%) at most 100. 67 closing changes closed several items at once, so per-item sizes there are upper bounds.

| repo | closed follow-ups, median net lines | of those <= 30 | all merged changes, median | of those <= 30 |
|---|---|---|---|---|
| repo-L | 1,145 (n=124) | 0% | 1,099 (n=159) | 6% |
| repo-R | 431 (n=7) | 0% | 156 (n=64) | 16% |
| H | 173 (n=6) | 1 of 6 | 267 (n=38) | 13% |
| repo-E | 68 (n=6) | 3 of 6 | 78 (n=53) | 40% |

- **The four small closed follow-ups were each blocked by a rule.** VERIFIED (PR files):
  - H [#136](https://github.com/Marius-Juston/agent-harness/issues/136) (25 net lines, 5 files) was filed by a researcher whose Owns were docs only.
  - repo-E #73 (5 lines) needed a permission-settings edit ("harness file, owner decision").
  - repo-E #182 (2 lines) was found by a research audit.
  - repo-E #175 (20 lines) was filed by the lead from a research round.
- **Open follow-ups judged small (one rater, not measured).** BELIEVED: one rater read 147 items in H, repo-E and repo-R and estimated their size. Dropping repo-E's 10 throwaway `[test]` items leaves 137. Of these, about 17 (12.4%) look 30 lines or fewer. About 4 or 5 (2.9 to 3.6%) are also free of any parallel-ownership, role, harness-file or owner-decision rule. Examples:
  - [#145](https://github.com/Marius-Juston/agent-harness/issues/145): a reviewer finding in PR #142's own new allowlist (`git -c core.fsmonitor=<cmd>` runs a command). It was filed at 02:39Z and the PR merged at 02:43Z.
  - [#47](https://github.com/Marius-Juston/agent-harness/issues/47): a constant in a file the origin PR touched, filed 2 minutes before that PR merged.
  - repo-R #163: a crash that the origin PR's new guard causes.
  - Most of the rest were right to wait. [#146](https://github.com/Marius-Juston/agent-harness/issues/146) waited because "#114 avoided editing those lines while #142 is open".
- **The fix could usually still go in the open PR.** VERIFIED: 532 of 618 (86%) were filed before the origin PR merged. The closing change touched the origin PR's code or config files in 92 of 142 cases (65%), and its Owns in 75 of 142 (53%). repo-L's broad lanes inflate both figures. For H alone they are 1 of 5 and 0 of 5.
- **Stated reasons.** VERIFIED: the "no stated reason" column counts items where no wording category matched in the filing text or the two messages before it. This is a crude regex, so some reasons are missed. Where a reason shows (multi-label, all 640): research gap or protocol follow-up 19%, owner decision 9%, size or "later" 7%, parallel work or hotspot 7%, found incidentally 3%, scope or Owns 2%. Verbatim reasons: [r1-measured](_scratch/fix-now-vs-defer/r1-measured.md) F7.

The owner perceives frequent small deferrals. Both the measured closing diffs (3%) and the one-rater reading (about 3%) put small, unblocked deferrals low. The larger, measured problem is follow-ups with no stated reason, 31 to 67% per repository. BELIEVED: the fix for both is the same. Name a fix-now path, and add a triage step that makes every deferral state its reason.

## 3. Why: the instructions that push filing
SOURCED (local files read 2026-10-12/13); the causal reading is BELIEVED. Details: [r1-instruction-causes](_scratch/fix-now-vs-defer/r1-instruction-causes.md) F1.
- **Every role can file; only the tester can fix.** The only fix-now rule in the surface is in [tester.md](../../plugins/harness-ops/agents/tester.md): "A small defect inside the issue's Owns: fix it".
  - [builder.md](../../plugins/harness-ops/agents/builder.md) allows an edit outside Owns "only when the issue needs it end to end", and an incidental find never meets that. Its PR body and hand-back ask for "follow-ups filed as issues" and "follow-up issue numbers". No role has a "fixed in passing" slot.
  - [researcher.md](../../plugins/harness-ops/agents/researcher.md) says "File each important gap you did not close as an issue", and in round mode a researcher may not edit.
- **The channel.** BELIEVED: several rules leave a new issue as the only legal move for a fix found in passing. They are:
  - "Work without an issue does not exist" ([HARNESS.md](../../template/.devkit/HARNESS.md.jinja)).
  - Owns, and hotspots that belong to the lead ([multi-agent.md](../specs/multi-agent.md) §3).
  - "The lead never builds" ([lead skill](../../plugins/harness-ops/skills/lead/SKILL.md)).
  - One PR per issue.

  None of these rules is wrong on its own. The gap is that none of them names the cheap case.
- **Gates.** VERIFIED: before #150, the weaken check flagged turning `xfail_strict` on as a weakening. At tip `da5575a` it no longer does ([xfail_strict_probe.py](_scratch/fix-now-vs-defer/scripts/xfail_strict_probe.py)). A floor raise passes without approval, while a lowered floor is refused ([floor_raise_probe.py](_scratch/fix-now-vs-defer/scripts/floor_raise_probe.py)). The gate only prints the raise as a note.
- **The model layer.** Anthropic's guidance points both ways, depending on model and effort (quotes cited below; the causal reading is BELIEVED):
  - SOURCED: [Prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5): the model "can also expand the scope of a task". It suggests saying so "in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it".
  - SOURCED: [Prompting Claude Sonnet 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5): a sample prompt ends "If you think one would help, mention it at the end instead of doing it". The same page says that at `xhigh` and `max` effort the model "can also make related fixes it noticed along the way".
  - SOURCED: [Prompting Claude Fable 5.1](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1): "report it as a follow-up in your summary".
  - SOURCED: [Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5): a list of non-blocking decisions at the end of a turn is called unwanted.

  In a harness, "mention it" plausibly becomes `board new`. No transcript-level test separates the model-layer cause from the instruction-layer cause.

## 4. What the literature and other harnesses say
Details: [r1-literature](_scratch/fix-now-vs-defer/r1-literature.md).
- **Who repays deferred debt, and when.**
  - SOURCED: Most self-admitted technical debt is removed by the person who introduced it, but the median lifetime is about 18 to 172 days, depending on the project ([An Empirical Study on the Removal of Self-Admitted Technical Debt](https://doi.org/10.1109/icsme.2017.8), Maldonado et al., ICSME 2017).
  - SOURCED: Only one study supports "soon". In 152 debt items from the Hadoop and Camel issue trackers, debt was mostly paid by whoever identified or created it, with a median of 25.0 h and a mean of 872.3 h ([Identification and Remediation of Self-Admitted Technical Debt in Issue Trackers](https://doi.org/10.1109/seaa51224.2020.00083), Li, Soliman and Avgeriou, SEAA 2020). VERIFIED with [breakeven.py](_scratch/fix-now-vs-defer/scripts/breakeven.py): the mean is 34.9 times the median.
  - SOURCED: 20 to 50% of debt "removals" are accidental ([Was self-admitted technical debt removal a real removal?](https://doi.org/10.1145/3196398.3196423), Zampetti et al., MSR 2018).
  - BELIEVED: "by its author" has two sources. An exited agent worker cannot be that author.
- **Later is not shown to cost more.** SOURCED: Menzies et al. found no delayed-issue effect in 171 projects ([Are Delayed Issues Harder to Resolve?](https://arxiv.org/abs/1609.04886), EMSE 2016). The case for fixing now therefore rests on the agent's fixed overhead per item.
- **What a small change costs, measured.** VERIFIED with [edit_cost.py](_scratch/fix-now-vs-defer/scripts/edit_cost.py) (Opus 5.5, 2026-10):
  - Workers on merged H and repo-E PRs over 20 lines (n = 43) made a median of 20 tool calls before their first file edit (IQR 14 to 66), and 76 per issue in all.
  - For 14 of the 22 merged PRs of 1 to 20 net lines (most made by the lead in its main session), the calls from the branch's first mention to the PR-open command were a median of 2 (IQR 1 to 4, max 6). This is an estimate: edits made through Bash and reads made before the branch was named are not counted.
  - The older figure of 42 calls before the first edit comes from repository A ([agent-harness.md](agent-harness.md) §6), which records no model or date.
- **The break-even point.** VERIFIED arithmetic; F and ρ are ASSUMED ([breakeven.py](_scratch/fix-now-vs-defer/scripts/breakeven.py)).
  - Fixing now costs f(1+ρ), where ρ is the added review and tangling cost.
  - Deferring costs F + p(f+O), where F = 3 calls to file and p is the chance the follow-up is ever done. No value is lost if it never is, which favours deferring.
  - At the repo-L lower bound p = 0.26, fixing now wins below 11 to 19 calls with O = 42, and below 7 to 11 with O = 20 (ρ from 0.5 to 0).
  - At p = 1, fixing now always wins when ρ = 0. With O = 42 it wins below 180 calls at ρ = 0.25 and below 90 at ρ = 0.5; with O = 20, below 92 and 46.
  - The measured in-place cost (median 2, max 6) is under every one of these points.
- **Tangling: separate commits, not separate issues.**
  - SOURCED: Up to 15% of bug fixes are tangled, and at least 16.6% of source files end up wrongly linked to bug reports ([The impact of tangled code changes](https://doi.org/10.1109/msr.2013.6624018), Herzig and Zeller, MSR 2013).
  - SOURCED: In a controlled experiment (N = 28), decomposition reduced wrongly reported issues but changed neither the defects found nor the understanding of the change. The authors still conclude that "commits belonging to different concepts should be separated" ([The effects of change decomposition on code review](https://doi.org/10.7717/peerj-cs.193), Di Biase et al., PeerJ CS 2019).
  - SOURCED: Google keeps refactorings in their own change, but "small cleanups such as fixing a local variable name can be included", and about 100 lines is "usually a reasonable size" ([Small CLs](https://github.com/google/eng-practices/blob/master/review/developer/small-cls.md), Google eng-practices).
  - Two studies of agentic PRs:
    - SOURCED: Claude Code PRs from 2025-02-24 to 2025-04-30 (older models, not comparable): 85 of 213 sampled agentic PRs (39.9%) had several objectives, against 12.2% of human PRs. 83.8% of the agentic PRs were merged, against 91.0% of matched human PRs ([On the Use of Agentic Coding](https://arxiv.org/abs/2509.14745), Watanabe et al., TOSEM 2025).
    - SOURCED: of 9,799 human-reviewed agentic PRs, rejections were clear agent failures in 35.7% of cases, workflow constraints in 31.2%, and had no observable rationale in 33.1% ([Why Are Agentic Pull Requests Merged or Rejected?](https://arxiv.org/abs/2605.22534), Peralta et al., 2026 preprint).
  - BELIEVED: these findings support a separate commit with a size cap. `dev integrate` merges with `gh pr merge --merge` (`devkit/workflow.py`), so each commit survives in history (VERIFIED).
- **Boy-scout rule: recommended, no outcome data.** SOURCED: Fowler advises leaving code better than you found it, with green tests, and coming "back the same day" for what you noted ([Opportunistic Refactoring](https://martinfowler.com/bliki/OpportunisticRefactoring.html), Fowler 2011). Some refactoring kinds induce faults ([When Does a Refactoring Induce Bugs?](https://doi.org/10.1109/scam.2012.20), Bavota et al., SCAM 2012). The rule should therefore cover small, test-covered fixes, not structural refactors.
- **Closing the loop.**
  - SOURCED: Google: "usually unless the developer does the clean up *immediately* after the present CL, it never happens". A change that introduces new complexity "must be cleaned up before submission". "If the CL exposes surrounding problems and they can't be addressed right now", the developer files a bug and assigns it to themselves ([Handling pushback](https://github.com/google/eng-practices/blob/master/review/reviewer/pushback.md), Google eng-practices).
  - SOURCED: [obra/superpowers](https://github.com/obra/superpowers) (MIT) triages a ledger of deferred minors before merge: "A roll-up nobody reads is a silent discard."
  - SOURCED: [openai/codex](https://github.com/openai/codex), [SWE-agent](https://github.com/SWE-agent/SWE-agent) and [OpenHands](https://github.com/OpenHands/software-agent-sdk) say "minimal changes, mention the rest".
  - BELIEVED, from reading the sources above: none of them sets a numeric threshold.

## 5. The fix-now rule
The rule is BELIEVED; its numbers are ASSUMED. **Fix it in this PR when all four conditions hold:**
1. **Small.** At most 20 changed lines in at most 2 files.
2. **Here.** The files are in the PR's diff or Owns, or this change exposed the defect.
3. **Mechanical.** No design choice, new interface or dependency, and no owner approval; the gate checks it.
4. **Free.** No other open item, and no hotspot, holds the file.

The fix goes in as its own commit with the prefix `fix-now:`, and the PR body lists it under `Fixed in passing`.

- A defect that the PR itself introduced is always fixed, whatever its size (Google's rule).
- Anything else is filed, and the issue names the condition that failed.
- Roles that cannot edit batch fix-ready edits (file, change, check) into one build issue.

The bounds and their basis:
- 20 lines and 2 files sit well below Google's 100-line norm. The measured in-place cost of changes this size (median 2 calls) is under every break-even point.
- The rule keeps the existing protections: disjoint Owns (condition 4), evidence before design (3), the weaken and ratchet gates (3), and a record per change (the commit and the PR body).
- Expected effect, BELIEVED as an upper bound: at most the roughly 3% of follow-ups judged small and unblocked, plus #145-type review findings. That 3% was judged at 30 lines with no file limit. Even #136's PR touched 5 files, so it would not qualify. The larger expected effect is on the follow-ups with no stated reason, through the triage step.

## 6. Recommendation: exact wording per role
The exact replacements are in [wording_budget.py](_scratch/fix-now-vs-defer/scripts/wording_budget.py). The script asserts that each old string matches exactly once and counts words with devkit's counter. VERIFIED at tip `da5575a`:

| file | change | words now -> after (budget) |
|---|---|---|
| `docs/specs/multi-agent.md` and its template copy | §3 gains a **Fix now** bullet: the rule of §5, including the `fix-now:` commit; the only full statement | 1,594 -> 1,700 (none) |
| `template/.devkit/HARNESS.md.jinja` | after "Each issue has a measurable DONE WHEN.": "A small fix found on the way belongs to the current issue: the fix-now rule (multi-agent.md §3)." | 566 -> 583 (600) |
| builder.md | outside-Owns edits allowed "when the issue needs it end to end or it is a fix-now"; the PR body adds `Fixed in passing`, and each follow-up names "the fix-now condition it failed"; the reply adds "fixes in passing" | 419 -> 438 (600) |
| tester.md | "A small defect inside the issue's Owns, or a fix-now (multi-agent.md §3): fix it." | 229 -> 234 (600) |
| researcher.md | the gaps line adds "batch fix-ready edits (file, change, check) into one build issue"; scripts go in `_scratch/<slug>/scripts/`. Paid for by cutting the ML venue list and the `research_dir` clause | 599 -> 598 (600) |
| lead SKILL.md | new step 2.4 (below). Cuts: the agent-type list (derived in `agent-surface.md`), two parentheticals, and a shorter `RESULT-JSON` sentence | 647 -> 649 (650) |

**Integrate-time triage step** (lead skill, step 2, before `uv run dev integrate`):
> 4. Triage the PR's follow-ups: a fix-now (multi-agent.md §3) or a defect in its own new code returns to the worker; the rest need a reason.

The lead still never builds; it resumes the worker. H's own `CLAUDE.md` does not import HARNESS.md, so #154 adds the pointer there too. Not recommended: loosening Owns or letting the lead edit, because most parallel-work deferrals were right (#146).

## 7. Limits
- The H, repo-E and repo-R follow-ups are under 4 days old. The only aged cohort is repo-L, whose work stopped. The small-size shares come from one rater, and the counts cover one machine and 10 days. #155 sets baselines per repository.
- The thresholds and the break-even inputs F and ρ are ASSUMED. The in-place cost measurement covers 14 PRs, most made by the lead.
- No controlled test shows that the wording changes behaviour. #159 is the planted-defect eval.

## Sources
- Maldonado et al. An Empirical Study on the Removal of Self-Admitted Technical Debt. ICSME 2017. https://doi.org/10.1109/icsme.2017.8 (abstract)
- Zampetti et al. Was self-admitted technical debt removal a real removal? MSR 2018. https://doi.org/10.1145/3196398.3196423 (abstract)
- Li, Soliman, Avgeriou. Identification and Remediation of Self-Admitted Technical Debt in Issue Trackers. SEAA 2020. https://doi.org/10.1109/seaa51224.2020.00083 (abstract)
- Menzies et al. Are Delayed Issues Harder to Resolve? Revisiting Cost-to-Fix of Defects throughout the Lifecycle. EMSE 2016. https://arxiv.org/abs/1609.04886 (abstract)
- Herzig, Zeller. The impact of tangled code changes. MSR 2013. https://doi.org/10.1109/msr.2013.6624018 (abstract)
- Di Biase et al. The effects of change decomposition on code review: a controlled experiment. PeerJ CS 2019. https://doi.org/10.7717/peerj-cs.193 (full abstract via OpenAlex and Europe PMC)
- Bavota et al. When Does a Refactoring Induce Bugs? An Empirical Study. SCAM 2012. https://doi.org/10.1109/scam.2012.20 (abstract)
- Watanabe et al. On the Use of Agentic Coding: An Empirical Study of Pull Requests on GitHub. TOSEM 2025. https://arxiv.org/abs/2509.14745 (data collection, results and threats sections via text search)
- Peralta et al. Why Are Agentic Pull Requests Merged or Rejected? An Empirical Study. arXiv preprint, 2026. https://arxiv.org/abs/2605.22534 (abstract)
- Fowler. Opportunistic Refactoring. 2011. https://martinfowler.com/bliki/OpportunisticRefactoring.html
- Google eng-practices: Small CLs, https://github.com/google/eng-practices/blob/master/review/developer/small-cls.md; Handling pushback, https://github.com/google/eng-practices/blob/master/review/reviewer/pushback.md
- Anthropic prompting docs (fetched 2026-10-12/13): https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5, https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5, https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5, https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1
- Harness prompts read through the GitHub API on 2026-10-12: https://github.com/obra/superpowers, https://github.com/openai/codex, https://github.com/SWE-agent/SWE-agent, https://github.com/OpenHands/software-agent-sdk
- Further sources of the round (Xavier et al. 2020, Besker et al. 2018, Khatoonabadi et al. 2023, Herbold et al. 2022, Kim et al. 2012, Iammarino et al. 2019, MAST, SNARE): [r1-literature](_scratch/fix-now-vs-defer/r1-literature.md).

## Follow-ups
- [ ] #154 (blocked by #153, which rewrites the same frontmatter): apply §6. DONE WHEN wording_budget.py's edits are applied, every budget holds, and the rendered template passes its gate.
- [ ] #155: re-measure per repository 2 to 4 weeks after #154. DONE WHEN the note gains a before/after table per repository (baselines in the issue's comments).
- [ ] #159: a planted-defect `dev eval` scenario, with and without the rule. DONE WHEN the fix rate is reported for both arms.
- [ ] (not filed, BELIEVED low value) The gate's "raise min_tests to N" note could say that the raise is a fix-now. DONE WHEN the note names it, or the item is closed as won't-fix.
