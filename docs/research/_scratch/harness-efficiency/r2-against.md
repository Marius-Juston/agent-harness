# r2-against: where the leading candidate changes would lose effectiveness (#254, round 2, adversarial seat)

Search coverage: `uv run dev lit status` 2026-10-19: openalex, crossref, europepmc and arxiv ok; s2 DOWN (HTTP 429).
The arxiv MCP timed out on every `get_abstract`, so abstracts came from `uv run dev lit get` and full texts from
arxiv.org/html through WebFetch. WebFetch summarises the page with a small model, so the numbers marked
"(WebFetch, full text)" are SOURCED but were not read line by line.

VERIFIED numbers come from three scripts in the session scratchpad
(`/tmp/claude-1000/<repo-E project dir>/50a10312-0021-4a60-badf-597004abc2f9/scratchpad/r2/`):
- `cost_mix.py`: per-agent cost mix, cache-TTL split, `effort` field and API-call gaps, from repo-E transcripts
  for 2026-10-11..16;
- `power_and_savings.py`: power analysis, Sonnet savings bounds, and TTL and compaction arithmetic;
- `prs60.json` plus the inline counts in §5, for review rounds.

Copy them to `scripts/` if the synthesis cites them.

## 0. Verdicts in one table

| Candidate | Verdict | The main reason |
|---|---|---|
| (a) Reviewers, auditors and lookups on Sonnet 5.5 or Haiku 5.5 | **Reject** for security-reviewer and research-auditor. **Adopt behind eval** for a new lookup or log-reading agent only. | The three review roles are 8 % of spend. Same-token savings are about $55 for the whole window ($0.29 per merged PR). Cheaper tiers catch 12-38 points fewer injected errors in the one benchmark found. |
| (b) Cutting harness text further; brevity rules | **Reject** narration caps and any brevity rule between tool calls. **Adopt** dedupe and conflict removal **behind eval**. **Reject** "budgets only fall" as a standing goal. | Anthropic's reverted length line passed weeks of evals and still cost 3 %. Our evals can only see drops of 33 points or more. Rules moved into skills mostly never load. |
| (c) Compacting `dev lit` / `dev check` output | **Reject** for `dev check` and `cite-check` (nothing to gain). **Adopt behind eval** for `dev lit`, judged on cost per success. | All `dev check` output in the window is worth about $2. All `dev lit` output is about $37. Removing 1,000 tokens is wiped out by 0.54 extra turns. |
| (d) Lowering effort per role | **Reject** for security-reviewer, research-auditor and researcher synthesis. **Adopt behind eval** for mechanical builder issues, chosen per dispatch. | Most roles already run at medium. Effort is the documented lever for edge cases and for self-preference. Anthropic reverted a medium default once already. |
| (e) Review convergence: re-review only the new diff, cap rounds | **Adopt behind eval** for diff-scoped re-review **plus** a periodic full-repository sweep. **Reject** any round cap that ends review while a blocking finding is open. | 31 % of repo-E round-2 verdicts and 3 of 10 round-3+ verdicts still blocked, several on user-safety leaks. |
| (f) Treating one 6-day transcript window as causal | **Reject** as decision evidence. Use it only to rank hypotheses. | Transcripts undercount output tokens 6.4x. The window spans four harness versions. Run-to-run token variance is up to 30x. |

## 1. (a) Moving reviewers, auditors and lookups to Sonnet 5.5 or Haiku 5.5

### Risk
A cheaper reviewer misses defects. For this project the cost of a missed defect is asymmetric: repo-E's
security-reviewer guards user-safety controls. Its round-2+ findings in the window included several user-safety
and privacy defects (§5) (details of this private repository's security findings are redacted here because agent-harness is public; they are in the repo-E PRs).

### Evidence
- **The saving is small (VERIFIED, `cost_mix.py` + `power_and_savings.py`).**
  - Opus-priced transcript cost: research-auditor $77 (4.0 %), security-reviewer $49 (2.6 %), reviewer $28 (1.5 %).
    The total is $1,903.
  - Cache reads cost the same on both models ($0.20/MTok; Sonnet 5.5 post price table,
    `scratchpad/blog/raw/building-with-claude-sonnet-5-5.md`, SOURCED). So Sonnet halves only cache writes, output
    and uncached input.
  - At the same tokens, that saves 33-39 % per role: **$55 for the window, 2.9 % of spend, $0.29 per merged PR**.
  - Transcripts undercount output tokens (§6), so the true figure is somewhat higher. It is bounded by 50 % of the
    review roles' cost, so it stays under about 5 % of spend. BELIEVED bound.
- **Break-even is close.** Sonnet costs the same as Opus once it needs 1.49x the tokens (research-auditor) or 1.63x
  (reviewers). VERIFIED arithmetic.
  - Bai et al. report that runs of the same task vary up to 30x in tokens. They also report that Claude Sonnet 4.5
    used over 1.5 M more tokens than GPT-5 on the same tasks
    ([2604.22750](https://arxiv.org/abs/2604.22750), SOURCED abstract).
  - Model-level token efficiency therefore varies by more than this break-even margin.
- **Cheaper tiers catch fewer errors.** Nguyen et al. injected errors into papers and measured recall
  ([2606.19749](https://arxiv.org/abs/2606.19749), SOURCED: abstract + Table 3 via WebFetch, full text).
  - Under the same OpenAIReview harness, frontier models scored 71.6 % (GPT-5.5) and 68.1 % (Claude Opus 4.7).
  - The four "efficient" models scored 55.8 %, 52.6 %, 45.4 % and 33.0 %.
  - Caveats: this is paper review, not code review; the comparison is across families, not Opus against Sonnet; and
    the paper gives no precision figure.
- **Anthropic's own guidance points away from this move.**
  - "Move down to Sonnet or Haiku for lookups, not for writing code"; "Keep judgment calls on the main model"; "a small
    model that misreads a search result sends the main model after the wrong file"
    (r1-blog-sonnet §3, from "What a task costs on Opus 5.5", SOURCED).
  - Sonnet 5.5 suits "a clear spec and a way to check the result" (same file §1).
  - A security review of a user-data PR is a judgment call with no executable oracle.
- **Self-preference does not argue for a cheaper tier.** Harmful self-preference is strongest when the evaluator is
  wrong as a generator, and long chain-of-thought before the verdict reduces it
  ([2504.03846](https://arxiv.org/abs/2504.03846), SOURCED abstract).
  - That argues for more reasoning in the reviewer, or a different family. It does not argue for a weaker tier of
    the same family.
  - r1-outside's "different model tier or family" lumps the two together. Only "family" is supported.

### Guard that would make it safe
- A pre-registered reviewer eval built from merged repo-E PRs whose defect a later PR fixed.
  - Take the round-2+ blocking findings in §5 as seeds and replay the round-1 head SHAs.
  - Freeze the split and score recall of confirmed blocking defects, plus false blocks on known-clean PRs.
- Use paired arms on the same PRs. Adopt only if Sonnet recall is not lower beyond a pre-set margin (say 5 points),
  with an n chosen from §3's power table.
- Under the owner's rule (memory: pre-registered evals), the simplest unbeaten arm (Opus) stays.

### Verdict
- **Reject** for security-reviewer and research-auditor. The saving is small and the loss is asymmetric for a
  minors' product.
- **Adopt behind eval** for a *new* read-only lookup or log-reading agent: "where is X defined", summarising
  `dev check` failures. There a wrong answer is cheap to spot and the agent does not decide anything.
- Expect little money from it, because such agents do not exist today and their spend would be new.

## 2. (b) Cutting harness text further, falling word budgets, brevity rules

### Risk
- A ratchet that only falls can delete load-bearing lines, and its proxy (word count) has no stopping rule.
- Brevity rules on the agent's own text cut reasoning that happens in the visible channel.

### Evidence
- **The reverted line, read in full** (`scratchpad/blog/E_raw/april-23-postmortem.txt` lines 62-71, SOURCED).
  - The line was "Length limits: keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless
    the task requires more detail."
  - It shipped "After multiple weeks of internal testing and no regressions in the set of evaluations we ran". Then
    "One of these evaluations showed a 3% drop for both Opus 4.6 and 4.7", found by line-by-line ablation, and the
    line was reverted.
  - Two corrections to how round 1 used this:
    - (i) The ablation removed the whole line, so the post does **not** show that final-report caps are safe and only
      inter-tool caps hurt. r1-engineering §21 says "hand-back limits are on the final report only; never cap words
      between tool calls". That split is BELIEVED, not SOURCED.
    - (ii) The 3 % came from one evaluation in a broader set, on Opus 4.6/4.7. It is direction, not an Opus 5.5
      effect size.
- **Our evals cannot see a 3 % drop (VERIFIED, `power_and_savings.py`).**
  - Two-arm test, α = 0.05, power 0.8, base pass rate 70 %:
    - detecting −3 points needs **3,760 trials per arm**;
    - −10 points needs 354;
    - −20 points needs 91.
  - With `dev eval`'s typical 10 or 30 trials per arm, the smallest detectable drop is **57 or 33 points**.
  - "Adopt behind eval" therefore only guards against large regressions. Small, real losses such as Anthropic's 3 %
    pass undetected, exactly as they did inside Anthropic.
- **Round 1 proposes the very thing that was reverted.**
  - r1-outside top-15 #14 recommends a "Narration cap … 'At most one short line between tool calls'" (from
    superpowers).
  - That is the same mechanism as the reverted "≤25 words between tool calls". **Reject.**
- **Moving rules into skills or linked docs usually means they never load.**
  - The Skill tool was called 16 times in 16 lead sessions (r1-usage-logs §4, VERIFIED there).
  - Vercel's evals: a skill was "never invoked" in 56 % of cases. Skill-by-default scored 53 % (the same as no docs),
    skill with explicit instructions 79 %, and an 8 KB docs index inline in AGENTS.md 100 %
    ([Vercel blog](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals), SOURCED; the post gives
    no task count or model).
  - So r1-blog C5 ("progressive disclosure … ~200-word hub SKILL.md plus references/") and the costs-doc advice "move
    instructions from CLAUDE.md to skills" risk turning rules into text that is never read.
- **What the cutting evidence actually says.**
  - Gloaguen et al. find context files add over 20 % cost with no success gain, but "instructions in the context
    files are well followed" and are "useful for specifying non-standard coding practices"
    ([2602.11988](https://arxiv.org/abs/2602.11988), SOURCED abstract).
  - HARNESS.md is almost entirely non-standard practice: uv-only, worktrees, `dev` commands, labels. That is the kind
    of text the paper finds useful.
  - Anthropic's 80 % system-prompt cut removed generic over-constraints for all users (r1-blog C §1). It is not
    evidence about project-specific procedure.
- **Budgets already bind against new evidence.**
  - repo-E CLAUDE.md + HARNESS.md is 600 words, equal to the `context = 600` budget (r1-blog C header).
  - The Opus 5.5 post recommends *adding* a two-sentence stop rule (r1-blog C §2, O2).
  - Under a falling ratchet, that addition forces a deletion or an owner raise. The ratchet's own justification
    (SkillsBench: compact beats comprehensive) does not say that shorter than today is better.

### Guard that would make it safe
- Allow cuts that **remove duplication or conflict** (r1-blog C1/C2), where a hook already enforces the rule.
- Check each cut with the hook-denial count. If the rule was load-bearing, denials rise.
- Never treat a falling count as a goal. Change the ratchet to "a raise needs evidence" without "budgets only go
  down" as an objective.
- Brevity rules for in-run text: none.

### Verdict
- **Reject**: narration caps, and any new brevity rule between tool calls.
- **Adopt behind eval**: dedupe and conflict removal. Use the denial count and pass rate as the guardrail.
- **Reject**: "keep budgets falling" as a standing goal. Keep the cap, drop the downward drive.

## 3. (c) Compacting tool outputs (`dev lit`, `dev check`)

### Risk
Less information per call leads to more calls. On a 100k-170k context, each extra turn costs more than the tokens it
saved.

### Evidence
- **Weinberger & Hozez, re-read in full** ([2607.12161](https://arxiv.org/abs/2607.12161), SOURCED: abstract via `dev lit get`;
  details via WebFetch of the full text). Corrections to r1-outside 2f:
  - **Runs.** 2,908 runs, 2,848 analysed, 103 tasks, 712 per arm. The authors put the effective sample at about
    38-45 tasks per arm. "About 2,900 paired runs" is right in count but overstates independence.
  - **The 38.4 % cut.** It is RTK-ML's *estimated* cut (local BPE tokenizer, no CI). RTK-ML is a research variant
    with query-aware ranking and an embedding sidecar, not plain truncation. The **+6.8 % cost** has a 95 % CI of
    [+2.8, +11.3].
  - **Success did not fall.** Baseline 96.2 %, RTK-ML 97.5 %, and every paired CI crosses zero. r1 implies a quality
    harm inside the Claude Code study. There was none.
  - **The "patch application" harm came from a separate single-shot Sonnet 4.6 harness, not Claude Code**: 27/40
    patches applied raw against 15/40 compressed, and 2/40 against 1/40 resolved. That is small and outside the main
    design.
  - **Models.** Haiku 4.5, Sonnet 5 and Opus 4.8 (plus a replication on Opus 5). Mean turns were only about 4.5 per
    task, against 14-64 median turns for harness agents.
  - **The mechanism that transfers is trajectory change**: RTK-ML took 4.69 turns against a 4.46 baseline. Their bill
    was 44 % cache write and 35 % cache read.
  - So 2607.12161 supports "judge on cost per success". It does **not** predict +6.8 % for a deterministic,
    source-side format change to `dev lit`. A source-side change does not rewrite cached prefixes.
- **There is little to gain (VERIFIED, `power_and_savings.py`, using r1-usage-logs §3-4 totals at chars/4).**
  - `dev check` averages 847 characters per call. All its output for the window, kept for 30 turns, is worth about
    **$2**. `cite-check` is about $2. All `dev lit` output is about **$37**, roughly 2 % of spend.
  - Cutting 1,000 tokens from one result saves $0.014. One extra researcher turn costs about $0.026, so the cut is
    erased by **0.54 extra turns**.
  - With 985 `lit search` calls already paired with 711 `lit get` calls, a search output that drops the snippet
    agents use to triage plausibly raises the `get` rate (BELIEVED).
- **Contrary evidence: reduction without loss.**
  - AgentDiet cut input 39.9-59.7 % and cost 21.1-35.9 % with no performance loss
    ([2509.23586](https://arxiv.org/abs/2509.23586), SOURCED abstract).
  - CORVUS cut input 9-50 % at comparable pass rates ([2607.22711](https://arxiv.org/abs/2607.22711), SOURCED abstract;
    r1 had it as SECONDARY, and its numbers are now confirmed).
  - Both act on trajectory history (stale or duplicated content), not on what a tool prints. Claude Code owns history,
    so the harness cannot use them directly. The harness analogue is the read guard, which already exists.
- **Minification of code hurts**: input −42 %, resolve rate −12 points (GPT-5-mini;
  [2606.01326](https://arxiv.org/abs/2606.01326), SOURCED abstract). This is lossy compression of the content being
  worked on; `lit` metadata is not that.

### Guard
- Compact `dev lit` only as an A/B judged on **cost per accepted research note** and on the `lit get`/`lit search`
  ratio.
- Keep the fields agents demonstrably use. Check which fields later appear in notes, by grepping the transcripts.
- Leave `dev check` alone. Its median output is already a summary.

### Verdict
- **Reject** for `dev check` and `cite-check`.
- **Adopt behind eval** for `dev lit`, with an expected best case of about 1 % of spend.
- The bigger context items are elsewhere: plain `sed`/`grep`/`cat` (14.3 M characters for researchers) and whole
  research notes read with `allow_large`.

## 4. (d) Lowering effort per role

### Current state (VERIFIED, `cost_mix.py`, the `effort` field on each assistant record)
| Role | Effort |
|---|---|
| researcher | high (18,608 calls) |
| research-auditor | high (1,898) |
| builder | medium |
| reviewer | medium |
| security-reviewer | medium |
| tester | medium |
| lead | medium |

The owner's `~/.claude/settings.json` sets `effortLevel: medium`. "Lowering effort" can therefore only mean
researcher or auditor high → medium, or anything → low.

### Risk
Missed edge cases, skipped files and premature "done".

### Evidence
- **Effort buys exactly what research and review need.**
  - "If Claude got it wrong by skipping a file, not running the tests, or bailing on a refactor partway through, pick
    a higher effort level" (r1-blog-sonnet §3, Hallie post, SOURCED there).
  - Terminal-Bench 3.0, Opus 5.5: 36.6 % at low against 65.7 % at max. Security tasks rose the most, from 64 % to
    87 % low → top (r1-blog-sonnet §4; Anthropic internal, 5 attempts per task, per-category values not
    independently checked).
- **Anthropic has already reverted a lower default once.** Their March move from high to medium "was the wrong
  tradeoff" and was reverted on April 7 (postmortem lines 28-43, SOURCED; Opus 4.6 / Sonnet 4.6 era).
- **Less reasoning means more harmful self-preference** in evaluators ([2504.03846](https://arxiv.org/abs/2504.03846), SOURCED abstract).
- **The token saving goes mostly to cache reads**, which are 56 % of researcher cost (VERIFIED). That saving is real
  only if lower effort does not add re-dispatches.
  - "high pays for itself on a task where it saves one retry" (r1-blog-sonnet §3, SOURCED there).
  - Research audit cycles already ran 2-3 rounds on some notes (§5, e.g. PR 684).

### Guard
- Lower effort per dispatch, never per role. Use the per-invocation `effort` parameter on issues the lead labels
  mechanical (renames, regeneration).
- Escalate after one gate failure.
- Measure first-try gate pass and re-dispatches per issue. Pre-register the label rule before looking.

### Verdict
- **Reject** lowering researcher, research-auditor or security-reviewer.
- **Adopt behind eval** for low effort on mechanical builder issues.
- Raising security-reviewer to high (r1-blog-sonnet §4 #1) is the opposite move. It costs little ($49 at medium),
  but it still needs the same recall eval as §1.

## 5. (e) Review convergence rules: re-review only the new diff, cap rounds

### Risk
- Diff-only re-review misses interactions between the fix and untouched code.
- A round cap merges with a blocking finding still open.

### Evidence (VERIFIED, `prs60.json`)
The data is the last 60 merged repo-E PRs (merged 2026-10-15T18:09 to 2026-10-16T16:00, so about one day, not the
whole window).

- **The r1 baseline is overstated.**
  - I reproduced r1's count: 46 reviewed PRs, mean 2.43 review comments, 9 passing in one round.
  - But 13 of the 46 PRs had **two** reviewer types (12 security + code, 1 security + audit), counted in r1 as
    sequential rounds.
  - Counting rounds per reviewer type (the maximum per PR) gives **mean 2.04**, 10/46 (22 %) in one round, and 2 with
    4 or more rounds.
  - The claim "repo-E's review loop converges worse than Anthropic's did before they fixed theirs" therefore holds for
    the one-round share (22 % against 65 %) but **not** for the mean (2.04 against 2.08).
- **Round 2 is mostly a confirmation pass, and it still finds blocking defects.**
  - By verdict head, 48 of 59 round-1 verdicts were "merge after fixes" or "send back".
  - Round 2: 27 pass and **12 of 39 (31 %) still blocked**. Round 3+: **3 of 10 still blocked**.
  - The round-2+ blocks I read include several user-safety or privacy findings on five PRs, among them three
    successive bypasses of one safety gate over four rounds (details of this private repository's security findings are redacted here because agent-harness is public; they are in the repo-E PRs).
    - PR 601: the reviewer revised its own round-1 finding ("time was bounded, but memory is not").
- **Diff-only would have caught most of these, but not all.**
  - Most were introduced by the fix commits, so a diff-scoped re-review that also reads the callers and callees of
    changed symbols would have seen them (BELIEVED, from reading the comment heads only).
  - PR 601's correction was a re-think of round-1 code that diff-only would not revisit.
  - A **round cap would have merged one of those PRs with a known open user-safety defect.**
- **Anthropic's numbers rest on layers repo-E lacks.**
  - Their reviewer was "finding a few more old ones each round": 59 % of repeat flags were on code present since the
    first pass (r1-blog A §1, SOURCED there). Those are real bugs that diff-only deliberately stops looking for.
  - Anthropic compensates with nightly review agents over existing code, staged release behind flags, and one-step
    rollback (same section). repo-E has none of these.
  - Their "after three rounds" rule asks the author to "step back and reconsider the approach". It is not a merge
    cap. r1-blog A #1 reads it correctly; the target "mean ≤1.6" invites a cap.

### Guard
- Diff-scoped re-review must include the changed symbols' references (`dev code refs`).
- Blocking findings never expire with a round count. After round 3, escalate to the lead or owner: re-scope, or
  re-dispatch a fresh builder.
- File pre-existing findings as issues (r1's Introduced/Pre-existing tag), plus a periodic full-repository security
  sweep to replace what diff-only drops.
- Guardrail metric: fix PRs touching the same files within 14 days.

### Verdict
- **Adopt behind eval**: diff-scoped re-review, the no-repeat rule and the flag-the-class rule.
- **Reject**: any cap that merges with an open blocker.
- Restate the target as "fewer confirmation-only rounds" (27 of 39 round-2s were passes). That is where the savings
  are. For example, the builder posts a fix-to-finding map that the reviewer checks in one short pass.

## 6. (f) Treating the 6-day transcript window as causal

- **Transcripts undercount output 6.4x (VERIFIED, `cost_mix.py` + cost-state sums).**
  - Transcript `usage` totals 4.24 M output tokens; the session cost-state records total 26.95 M, plus 7.69 M
    thinking. In a sample of 80 subagent files, 886 of 7,089 assistant messages had more than 2,000 characters of
    content but recorded fewer than 100 output tokens.
  - So r1-usage-logs' per-agent table ("fitted prices on transcript tokens") **understates output-heavy roles**. Its
    "cost share … output 20 %" comes from cost-state and is right overall, but the per-agent split cannot be derived
    from transcripts.
  - Uncached input is also missing: 77 k in transcripts against 17.0 M in cost-state.
  - `dev agent-stats` has the same blind spot if it reads the same `usage` fields (ASSUMED; not checked in
    `devkit/stats.py`).
- **The window is not stationary.**
  - It spans harness v0.1.4-v0.1.7.
  - The review sample in §5 covers one day.
  - The research share (48 %) reflects which issues were open that week.
- **The pre-harness comparison is confounded.** "27.6 M against 4.5 M tokens per builder run" compares a different
  repository, era and task mix. r1 labels it "not a controlled one", and it should not appear as an effect of the
  harness.
- **The outcome measure is coarse.** Merged PRs are counted, not correct ones. The $12 per merged PR includes research
  PRs. Bai et al.'s up-to-30x run-to-run variance ([2604.22750](https://arxiv.org/abs/2604.22750)) means per-role
  medians from about 50-260 runs cannot separate small interventions.
- **Guard.** Use the window to rank hypotheses. Decide on paired or pre-registered A/Bs using cost-state (or OTel)
  cost and a defect-escape metric.

## 7. Round-1 claims that are wrong, overstated or contradictory

1. **The cache TTL contradiction.** Every subagent, reviewers and auditors included, wrote with the 1-hour TTL.
   - Evidence: repo-E `.claude/settings.json` has had `subagentPromptCacheTtl: "1h"` since its first commit
     (b05eec0, 2026-10-11). The template sets it too (`template/.claude/settings.json.jinja:88`).
   - Precedence is env > setting > frontmatter (prompt-caching docs, steps 1-6, SOURCED).
   - VERIFIED: 98.37 M of 98.73 M write tokens (99.6 %) were `ephemeral_1h`.
   - So r1-engineering's line "`experimental.cacheTtl: 1h` on builder, researcher, tester" is true but incomplete.
   - r1-blog-D #6 ("try 5 min for reviewers and auditors") **cannot be done per agent while the setting exists**,
     because frontmatter loses to the setting. It is also worth little: security-reviewer would save about $14 of
     writes but pay about $9 in rewrites for its 63 gaps over 5 minutes (4.0 % of its calls, more than the 1.5 %
     subagent average).
   - **Reject.**
2. **Cache-read multiplier.** r1-blog-D says "On Opus 5.5 and Sonnet 5.5 a read costs 0.05×". That is correct for
   Opus ($0.20 of $4) but **wrong for Sonnet**: $0.20 of $2 = 0.1×, and the Sonnet post says "A cache read costs a
   tenth of the input price". This is why Sonnet saves only on the non-read share (§1).
3. **r1-outside 2f** overstates success harm and run independence, and misplaces the patch-application result (§3).
4. **r1-outside 2g (ClayBuddy, "700k-token output cut accuracy by 70 points")** does not appear in the abstract
   ([2606.19380](https://arxiv.org/abs/2606.19380)). Leave it SECONDARY and keep it out of the note unless the full
   text confirms it.
5. **r1-blog-A baseline** "mean 2.43 rounds" double-counts parallel reviewer types. Correct it to 2.04 per type (§5).
6. **r1-engineering §21** "hand-back limits are on the final report only; never cap words between tool calls" is
   presented as following from the postmortem. The postmortem ablated the whole two-sentence line, so the split is
   BELIEVED (§2).
7. **r1-outside #3 / 6c** "Make the reviewer a different model tier or family". The self-preference evidence supports
   *family* or more reasoning, not a lower *tier* (§1).
8. **r1-outside #14** (narration cap) contradicts r1-engineering §21. The postmortem sides with §21 (§2).
9. **r1-blog-sonnet §1 "expect roughly 30–50% less per review"** is right per review. As spend, it is about 3 % of
   the window (§1). The synthesis should quote the absolute figure.

## 8. Gaps not closed
- Per-agent output and thinking cost cannot come from transcripts. Check whether OTel or `cost-state` can be split
  per subagent. This is the prerequisite for every model and effort A/B.
- There is no same-family Opus-vs-Sonnet code-review recall number. The only tier evidence is cross-family paper
  review (§1).
- Which round-2+ blockers sat outside the fix diff was judged from comment heads, not from the diffs (§5).
- MAST "+15.6 %" and the SWE-Review numbers (27.5 % → 56.9 %) were not re-fetched.
- `devkit/stats.py` was not read to confirm it shares the output undercount.

## Sources fetched this round
- Weinberger & Hozez, 2607.12161 (abstract via `dev lit get`; full text via WebFetch of arxiv.org/html).
- Gloaguen et al., 2602.11988 (abstract).
- Nguyen et al., 2606.19749 (abstract; Table 3 via WebFetch).
- Bai et al., 2604.22750 (abstract).
- Chowdhury et al., 2604.03196 (abstract; confirms 45.20 % against 68.37 % and 60.2 % in the 0-30 % band).
- Son et al., SWE-Router, 2607.00053 (abstract; no numbers in it).
- Chen et al., 2504.03846 (abstract).
- Ge & Assis, ClayBuddy, 2606.19380 (abstract).
- Hrubec & Cito, 2606.01326 (abstract).
- Xiao et al., AgentDiet, 2509.23586 (abstract).
- Lumer et al., 2601.06007 (abstract; confirms 41-80 % and 13-31 %).
- Zheng et al., CORVUS, 2607.22711 (abstract).
- Vercel, "AGENTS.md outperforms skills in our agent evals" (vercel.com/blog).
- Anthropic, "An update on recent Claude Code quality reports" (raw copy `scratchpad/blog/E_raw/april-23-postmortem.txt`).
- Claude Code prompt-caching docs (raw copy `scratchpad/blog/E_raw/docs/prompt-caching.md`).
- Sonnet 5.5 post (raw copy `scratchpad/blog/raw/building-with-claude-sonnet-5-5.md`).
- repo-E transcripts, cost-state records and `gh pr list` (2026-10-19).
