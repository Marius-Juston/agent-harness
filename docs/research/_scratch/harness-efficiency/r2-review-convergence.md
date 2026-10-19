# r2: how the review loop converges, and what would make it converge faster without missing defects

Round mode, #254 round 2. Search coverage: `dev lit status` 2026-10-19: openalex, s2, crossref, europepmc and arxiv
all answered (discovery OK); none down. The evidence here is mostly measured, not searched.

Data: every PR merged on or after 2026-10-11 in repo-E (188) and Marius-Juston/agent-harness (73), from
`gh pr view`, plus the 213 review-agent transcripts (reviewer, security-reviewer, research-auditor, old and
`harness-ops:` names) under `~/.claude/projects/<repo-E project dir>/*/subagents/`. agent-harness's own project
directory has no subagent transcripts, so its 73 PRs show only the 19 reviewed from repo-E sessions. Every agent ran
on Opus 5.5. Script: [review_convergence.py](review_convergence.py), with subcommands `fetch`, `rounds`, `comments`,
`classify`, `misses`, `lead`, `sample` and `codes`; the cache goes in a scratch directory and is not committed.

A **round** is one review pass. A review agent is spawned once, then resumed by SendMessage. A round ends with its
SubagentHandback; a coordinator message that arrives before the handback is a correction inside the round, not a new
round. The **series** for one PR and one reviewer role (code, security or audit) is that role's rounds on that PR,
in time order.

## Findings

**F1. The round-1 claim (mean 2.43 rounds, 20 % one-round passes, 46 PRs) reproduces exactly, but it measures the
wrong thing. Per reviewer role, the loop converges about as well as Anthropic's after their fix. The exception is
security review of adversarial filters.** VERIFIED ([review_convergence.py](review_convergence.py) `comments`,
`rounds`)
- Round 1's method: over the 60 most recently created merged repo-E PRs, count comments that start with five review
  prefixes. That gives 46 of 60 PRs, mean 2.43, 9 one-comment PRs (20 %) and 9 PRs with 4 or more. The method adds the
  code, security and audit comments on one PR together, and it counts the comments that confirm fixes. Over all 188
  merged repo-E PRs, the same kind of count gives 90 reviewed PRs with a mean of 1.93 verdict comments.
- From the transcripts: 141 merged PRs had at least one agent review round (repo-E 122, agent-harness 19), in
  169 (PR, role) series and 310 rounds. Each series averaged 1.89 rounds, and 65 series (38 %) had a single round.
- On Anthropic's measure, "passing after one review and fix round" (here: one round, or two rounds where the second
  finds nothing blocking), **135 of 169 series converge (80 %, 95 % CI 73-85 %)**. By role: code 43/47 (91 %), audit
  48/55 (87 %), security 44/67 (66 %, CI 54-76 %). The mean number of rounds to the first pass verdict is 1.81. Seven
  series (4 %) needed 5 or more rounds, all of them security.
- Anthropic reports 65 % → 78 % passing after one round, 8.9 % → 1.5 % needing five or more rounds, and a mean of
  2.08 → 1.32 reviews to pass. SOURCED ([claude.dev](https://claude.dev/blog/solving-code-review-at-anthropic/),
  figure caption; a before/after comparison on internal PRs with no N or interval given). The definitions differ:
  their findings clear on their own when a later commit fixes them, while here a confirmation pass is a full agent
  round. So the comparison is only indicative.
- The slow series cluster. The six longest (one with 12 security rounds, one with 7, four with 5 each) hold 33 of the 151 later rounds (22 %). Every one is a hand-written deny-list or parser gate in
  repo-E's safety or credential code (details of this private repository's security findings are redacted here because agent-harness is public; they are in the repo-E PRs). All 25 rounds after a third round
  were security rounds. Later code rounds were 18 (78 % pass) and later audit rounds were 32 (81 % pass).

**F2. Most rounds after the first are confirmations. When a later round does find something, it is mostly a bug the
previous fix introduced, not old code.** VERIFIED counts ([review_convergence.py](review_convergence.py) `rounds`);
the categories are hand-coded by one coder (`sample` prints the material, `codes` the tallies).
- Verdict by round: round 1 returned "merge after fixes" or "send back" in 157 of 169 series (93 %), with a median of
  6 findings (a heuristic count; mean 6.4). Round 2 passed in 74 of 104 (71 %). Of the 151 later rounds, 96 (64 %)
  were confirmations that found nothing blocking.
- Hand-coding all 51 later rounds that did not pass (4 rounds that belonged to another PR excluded):
  - **introduced by the previous fix push: 22 (43 %, CI 31-57 %)**: regressions, new code added to satisfy a finding,
    or one reviewer's requested fix breaking another reviewer's rule. Example: a code-review fix removed a limit that
    the security review then flagged.
  - missed earlier, same class: 13 (25 %, CI 16-39 %). Another instance of a class already flagged, or a partial fix.
    Examples: four rounds on one PR of the same validation gap, and repeated instances of one leak class on another
    (details of this private repository's security findings are redacted here because agent-harness is public; they are in the repo-E PRs).
  - missed earlier, unrelated: 6 (12 %).
  - minor residue only: 8 (16 %).
  - new input from the owner or the lead: 2 (4 %).
  - **pre-existing on main: 0 (0 %, CI 0-7 %).**
- So Anthropic's git-blame result, that "about 59%" of repeated flags were on code present since the first pass,
  SOURCED ([claude.dev](https://claude.dev/blog/solving-code-review-at-anthropic/)), does not carry over. Here the
  analogous share (missed earlier, either kind) is 37 %, and the largest group is fix-introduced bugs.
- **Repeats:** none of the 51 rounds repeated a posted finding verbatim. Three re-flagged a finding whose fix was
  partial, which is legitimate. Cross-reviewer duplicates happen: on one PR the code and security reviewers flagged
  the same gap. Class findings also do not travel to sibling PRs: a class found on one PR recurred on its sibling.

**F3. Over half of round-1 findings need not block, and almost none is something `dev check` already enforces.**
Hand-coded, VERIFIED tallies (`codes`) on 60 random round-1 finding items (seed 11; codes listed under "Codes"):
- introduced and substantive (a correctness, security or claim error worth fixing before merge): 27 (45 %, CI 33-58 %);
- introduced but minor (wording, docs, latent low-severity): 22 (37 %);
- PR body or process (cited SB ids, Owns, superseded text): 5 (8 %);
- follow-up or future scope: 4 (7 %);
- pre-existing: 2 (3 %);
- **enforced by `dev check` (ruff, format, cite-check, budgets): 0 (CI 0-6 %).** The keyword pass over all 1,018
  round-1 items flags 3 % as gate-related, and the sample shows those are false positives such as "format" in another
  sense.

Of the non-pass verdicts, 44 of 212 (21 %) say in their first lines that the fixes are small or do not block. A further
round followed 31 of those. It passed 68 % of the time, but in about 4 of the 10 that did not pass it found a real
problem (a fix-introduced hole on two PRs, a same-class gap on two others). Skipping re-review after "small
fixes" is therefore not free for security review. VERIFIED (`classify`) plus my reading of those 10.

**F4. What the loop costs: reviews are cheap; the fix resumes they trigger are not.** VERIFIED (`rounds`, `lead`).
Prices are the per-M fits from [r1-usage-logs.md](r1-usage-logs.md).
- Review rounds: 310 rounds, $167 in total.
  - Round 1: mean $0.72, 1.00 M input tokens, 24 tool calls.
  - Round 2: mean $0.36, 0.50 M input.
  - Audit rounds cost the most: mean $0.97 and 1.64 M tokens per round.
  - Median wall time per round is 2.2 min (p90 7.6 min).
- Fix resumes, meaning a worker resumed with review or audit findings: builders 121 at a mean of $1.27 (3.8 M
  input), researchers 73 at $1.70 (6.0 M), testers 2. That totals **$281, more than all the reviews**.
- Lead: 373 lead calls that spawn or resume a reviewer or post a verdict, $49 of input-side cost (about $0.13 per call
  at the lead's context size).
- The review-and-fix loop therefore costs about $497, **about 22 % of the window's $2,231**.
- Marginal costs:
  - an extra review-and-fix round: about $1.9 (re-review $0.36, fix resume $1.27, at least 2 lead calls $0.26);
  - a confirmation-only round: about $0.55;
  - the 25 rounds after a third round: about $8 of review, plus their fix resumes and lead calls, about $45 in all.
- Latency is small. From the first review to merge, the median is 0.5 h for multi-round series and 0.2 h for
  single-round ones.
- Caveat: transcripts log `output_tokens` at stream start, so output is priced from the visible text and tool input
  (characters/4, thinking excluded). Costs are therefore lower bounds; input-side tokens are exact.

**F5. Defects that escaped review: no reverts, and a traced post-merge defect rate of 6-9 % of reviewed PRs. Extra
rounds did not prevent escapes.** VERIFIED (`misses`, an SZZ-style blame, plus my check of each candidate)
- Reverts on main since 2026-10-11: none. The only "Revert" commit (repo-E e473f75) is inside a branch: a fix moved
  to its own PR.
- Method: blame the code lines that each merged fix of a `type:bug` issue changed, on main just before the fix, and
  map the blamed commits to their PRs. This gave 57 candidate pairs. SZZ-style tracing is known to over-blame
  incidental edits, and SZZ variants need an oracle to judge accuracy. SOURCED
  (Rosa et al. 2021, [Evaluating SZZ Implementations Through a Developer-Informed Oracle](https://doi.org/10.1109/icse43902.2021.00049)). I therefore checked each pair against the bug
  issue's text.
- Confident escapes from reviewed PRs, with the bug filed after the merge: 9 of 141 (6 %, CI 3-12 %); 13 of 141 (9 %)
  if all the UI and model-wrapper candidates count (four more PRs).
  - repo-E: per-PR detail is omitted because the repository is private (`misses` prints it). By kind: same-class
    defects past three reviewed safety gates, grading and parsing errors found on live runs, an infrastructure
    batch limit found by an eval, an event-loop bug from a playtest, and seven layout defects from a visual review.
  - agent-harness #171 (2 code rounds): #179, #180 and #215 (licence-gate bugs found in later use).
- Not counted: #687 was filed during review and deferred, which is a known gap rather than a miss. Bugs in the initial
  agent-harness import (#1, #2) and in unreviewed PRs were also left out.
- **Who found the escapes:** live smoke runs or playtests 3, property tests or evals 3, research probes 1, a visual
  review 1 cluster, and later use or audit 4. No later review round found one.
- The three gates with the most rounds still leaked same-class bugs. Their extra
  rounds bought little that the class-level property tests did not.
- This fits the field evidence. Review coverage and participation correlate with fewer post-release defects in Qt,
  VTK and ITK (MSR 2014, up to 2 and 5 extra defects per component when low). SOURCED
  (McIntosh et al. 2014, [The impact of code review coverage and code review participation on software quality: a case study of the qt, VTK, and ITK projects](https://doi.org/10.1145/2597073.2597076)). But a replication on Qt and Chrome found the
  review measures unstable, and models without them fit as well or better. SOURCED
  (Krutauz et al. 2020, [Do Code Review Measures Explain the Incidence of Post-Release Defects?](https://arxiv.org/abs/2005.09217)).
- Limits: the window is 3-8 days after each merge and covers only bugs that got an issue and a merged fix. Treat
  6-9 % as a lower bound.

**F6. Outside evidence on what review finds.** The share of non-blocking findings in F3 is the usual one. In 759
defects from nine industrial and 23 student reviews, 75 % did not affect visible functionality; they were
evolvability issues. SOURCED (Mäntylä & Lassenius 2008, [What Types of Defects Are Really Discovered in Code Reviews?](https://doi.org/10.1109/tse.2008.71)). In industry, 73.8 % of
LLM review comments were resolved, but PR closure time rose from 5 h 52 min to 8 h 20 min, and developers reported
unnecessary corrections. SOURCED (Cihan et al. 2025, [Automated Code Review In Practice](https://arxiv.org/abs/2412.18531); 3 projects, 4,335 PRs,
1,568 reviewed automatically). Anthropic notes that "noise costs more when the author is an agent": an agent fixes
every nit, "which means another commit and another round of review". SOURCED
([claude.dev](https://claude.dev/blog/solving-code-review-at-anthropic/)). F4's fix-resume cost is that mechanism,
measured here.

## The proposed changes, each with its expected effect from this data

| # | Change | What it targets (data) | Expected effect | Label |
|---|---|---|---|---|
| 1 | **Class battery.** The reviewer names each finding's class and leaves a runnable probe (a generator or table). The builder commits it as a property or table test and runs it, together with all earlier rounds' probes, before pushing a fix. This merges "flag the class" with a battery pattern one long security series ended with. | Fix-introduced 22/51 and same-class 13/51 later blocking rounds (69 %); two same-class escapes | If it removes half of those rounds: about 17 rounds and about $33 per window. More important, it closes the escape route of F5. Deterministic probes cost almost no tokens. | BELIEVED |
| 2 | **Round cap at 3.** A fourth round is not spawned. The reviewer returns "reconsider the approach" (for example allowlist instead of deny-list, or an owner rule), and the lead decides with the owner. | 9 security series, 25 rounds after the 3rd, about $45 in all (F4) | Series with 5 or more rounds go from 4 % to 0. About $45 per window, about 2 % of spend. Escape risk is not raised, because those PRs escaped anyway (F5). | BELIEVED |
| 3 | **Severity tags and a bar.** Every finding is tagged Blocking, Nit, Pre-existing or Follow-up, and the summary leads with the counts. Only Blocking findings need a fix before merge. Nits are capped at 3 and go into the same fix push or are dropped. Pre-existing and Follow-up findings become issues. A code or audit verdict with no Blocking finding is "merge"; security keeps a re-review on any code change. | 55 % (CI 42-67 %) of round-1 items need not block; 21 % of non-pass verdicts are self-described minor; later code and audit rounds pass 78-81 % | Fewer fix resumes, at $1.27-1.70 each. If code and audit stop re-reviewing fixes that were only Nit or Follow-up (about 40 of their 50 later rounds pass), that saves about $17 of review plus the matching resumes. Smaller fix pushes also give fewer fix-introduced bugs. | BELIEVED |
| 4 | **Builder self-check before the PR (fresh context).** A subagent reviews `git diff main...HEAD` before `dev pr`, and Important findings are fixed inside the live builder. | Round 1 asks for fixes in 93 % of series | Break-even: a pre-PR review costs about $0.72, and each external fix cycle it avoids saves about $1.9, so it pays only if it makes at least about 38 % of PRs pass round 1 clean (VERIFIED arithmetic on F4's means). Unknown; run it as a pre-registered trial (builder A/B on comparable issues), not a default. | ASSUMED, test |
| 5 | **Diff-only re-review**: `git diff <last-reviewed-sha>..HEAD` plus the open findings and the earlier probes. | Later rounds are already half the cost of round 1; 28 % already say "delta"; delta rounds cost $0.29 against $0.36 | Tokens: at most about $7 per window. Keep it as the rule, because fix-introduced bugs live in the delta. Pair it with re-running earlier probes (item 1), so delta-only does not miss regressions. | VERIFIED cost, BELIEVED rule |
| 6 | **No repeats; dedupe across reviewers.** The second reviewer gets the findings already posted on the PR. | 0 verbatim repeats in 51 rounds; 1 cross-reviewer duplicate | About zero rounds saved. It costs one line in the lead's prompt. | VERIFIED count |
| 7 | **Skip what `dev check` enforces.** | 0 of 60 sampled items (CI 0-6 %) | About zero. Do not spend prompt words on it; reviewer.md already says "the tests already ran". | VERIFIED |
| 8 | **Introduced vs pre-existing tag**, with pre-existing findings filed, never blocking (unless fix-now). | Pre-existing 3 % of round-1 items, 0 % of later blocking rounds | Few rounds saved. Its value is in triage and blame, so fold it into item 3's tags rather than adding a separate rule. | VERIFIED count |

Guardrails, so that faster convergence does not hide misses:
- **Escape rate:** keep `misses` (traced post-merge defects per reviewed PR) at 6-9 % or below over the next 100
  reviewed PRs, and reverts at 0.
- **Rounds:** track per (PR, role): one-round convergence (target security ≥ 80 %, from 66 %) and series with 5 or
  more rounds (target 0).
- **Cost:** track fix-resume cost per merged PR, the biggest lever ($281 in this window).
- These metrics belong in `dev agent-stats`. The parsing in `rounds` (handback segmentation, report-named PR, verdict
  regex) is the part worth porting.

## Gaps seen, not closed
- **Hand-coding.** Only one coder; no second rater, so no agreement (kappa) figure. The round-1 item sample is
  n = 60, so the CIs are wide.
- **Mis-assignment.** Combined reviews ("(A) PR #429 ... (B) PR #351") sometimes credit a round to the wrong PR:
  4 of the 55 later non-pass rounds, about 3 % of rounds overall.
- **Verdict regex.** Spot-checked on 40 reports; the one error found was fixed. The findings count is a heuristic,
  shown as approximate.
- **Effects are untested.** No proposal's effect is measured here. Items 1, 3 and 4 need a pre-registered trial with
  a frozen split of issues, the effect measured on rounds, fix-resume cost and `misses`, and the simplest arm that is
  not beaten winning (owner rule).
- **agent-harness.** Its own sessions keep no subagent transcripts, so its review loop (19 of 73 PRs reviewed from
  repo-E) is under-sampled.
- **Lead cost.** Only the lead calls that spawn, resume or post are counted. The calls that read handbacks and relay
  findings to builders are not, so the lead overhead is a lower bound.
- **Reviewer precision.** Not measured: there is no record of which findings were wrong or intentional, as Anthropic
  records author responses. This is the round-1 proposal "measure reviewer noise by author response", still open.

## Codes (for `sample` output)
- Round-1 items 1-60:
  - substantive (A): 1, 5, 8, 12, 13, 14, 17, 18, 19, 21, 24, 27, 30, 31, 33, 35, 36, 37, 38, 41, 42, 44, 45, 54,
    56, 57, 60;
  - minor (B): 4, 6, 7, 9, 10, 16, 23, 26, 28, 29, 32, 34, 39, 40, 43, 47, 48, 49, 50, 52, 55, 58;
  - process (C): 2, 3, 15, 46, 53;
  - pre-existing (D): 20, 22;
  - follow-up (E): 11, 25, 51, 59.
- Later non-pass rounds `###` 1-55:
  - fix-introduced: 4, 6, 9, 10, 11, 13, 14, 15, 30, 31, 32, 34, 36, 37, 38, 39, 42, 44, 45, 48, 49, 50;
  - same class: 3, 7, 12, 17, 18, 19, 20, 24, 25, 35, 43, 52, 53;
  - missed, other: 26, 33, 40, 46, 47, 55;
  - minor only: 1, 2, 5, 22, 23, 29, 51, 54;
  - new input: 8, 21;
  - excluded (round belongs to another PR): 16, 27, 28, 41.

## Sources fetched
- Anthropic, "How the Claude Code team does code review" (2026-10-15), markdown copy fetched 2026-10-19:
  https://claude.dev/blog/solving-code-review-at-anthropic/
- Mäntylä & Lassenius, What Types of Defects Are Really Discovered in Code Reviews?, IEEE TSE 2008 (abstract):
  https://doi.org/10.1109/tse.2008.71
- McIntosh, Kamei, Adams & Hassan, The impact of code review coverage and code review participation on software
  quality, MSR 2014 (abstract): https://doi.org/10.1145/2597073.2597076
- Krutauz, Dey, Rigby & Mockus, Do code review measures explain the incidence of post-release defects?, EMSE 2020
  (abstract): https://arxiv.org/abs/2005.09217
- Cihan et al., Automated Code Review In Practice, ICSE-SEIP 2025 (abstract): https://arxiv.org/abs/2412.18531
- Rosa et al., Evaluating SZZ Implementations Through a Developer-Informed Oracle, ICSE 2021 (abstract):
  https://doi.org/10.1109/icse43902.2021.00049
- Measured: [review_convergence.py](review_convergence.py) on `gh` data for both repositories (fetched 2026-10-19)
  and the repo-E transcripts. The lead and agent prompts it quotes come from `plugins/harness-ops/agents/reviewer.md`,
  `research-auditor.md` and `skills/lead/SKILL.md` §2 in this worktree, and from repo-E's
  `.claude/agents/security-reviewer.md` and `.claude/skills/lead/SKILL.md` (rule: "Push after a fix: review the new
  head again").
