# Gaps after round 2 (lead, 2026-10-19)

Read from the hand-backs of r2-opus55-handling, r2-review-convergence, r2-research-and-lead-cost, r2-surface-audit
and r2-against.

## Findings that changed the recommendation in round 2
- Review loop: the round-1 "2.43 rounds, 20 % one-round" figure sums roles and counts confirmations. Per role 80 %
  of series pass after one review-and-fix round (code 91 %, audit 87 %, security 66 %), mean 1.81. The cost is in fix
  resumes ($281) more than reviews ($167); long series are all security deny-list/parser gates; 43 % of failing later
  rounds are bugs the previous fix introduced and 25 % missed instances of a flagged class. Lead change: reviewer
  leaves a runnable probe per bug class, builder commits it as a test (r2-review-convergence). Round cap 3 must never
  merge with an open blocker (r2-against).
- Fixed start-up cost: tool schemas, not harness text. The Artifact schema is ~12.9k tokens (46 % of a builder's first
  call) and the Skill tool + listing ~5k, both nearly unused by workers; `disallowedTools` saves ~18k tokens per call
  (r2-surface-audit).
- Lead: mean context 372k, compactions fire at 501-966k; compacting at 200-300k saves 42-48 % of lead cost
  (r2-research-and-lead-cost). Re-orientation cost after compaction is unmeasured.
- Effort, not model, is the per-role lever for Opus 5.5; builder-medium with high re-dispatch after a gate failure
  (r2-opus55-handling). Sonnet for review roles saves ~3 % of spend at a recall risk on user-safety gates: reject for
  security-reviewer and research-auditor (r2-against).
- Prescribed commands refused: `dev pr … | tail` 105/106 refused; cross-repo `cd <abs> &&` 146 refusals
  (r2-surface-audit). Read-guard `cd` bypass is small (3 long-file cases); persisted tool-output re-reads are the
  bigger read cost ($52) (r2-research-and-lead-cost).
- Brevity: reject narration caps and "budgets only fall" as a goal; our evals cannot detect a 3-point drop
  (r2-against). Cut duplicated and hook-enforced prose behind an eval (r2-surface-audit).
- Tool-output compaction: low value except `dev lit` (82 % of returned papers never touched afterwards).
  Tool-result clearing would cost more under 1-hour caching.

## Contradictions left for the synthesis to resolve from the source
- Sonnet 5.5 cache-read price: r2-opus55-handling cites the pricing page at $0.10/M vs Opus $0.20/M; r2-against and
  r2-research-and-lead-cost assume equal. Re-fetch the pricing page; it moves the Sonnet saving estimates.
- Hand-back caps: 25 / 20 / 30 lines in different files (r2-surface-audit).

## Stop decision
Stop after round 2. The open items (Sonnet or medium-effort researchers, lead compaction threshold, self-review
before PR, probe-per-class reviews, cutting prose, per-agent output cost via OpenTelemetry) are experiments on this
harness, not literature questions: another research round would not change the recommendation, it needs the
experiments. The synthesis files each as an issue with a pre-registered measure (cost per merged issue with cache
writes priced apart, review rounds per role, post-merge defect rate).
