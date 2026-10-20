# Gaps after round 1 (#346)

Inputs: r1-transcripts.md, r1-github-tools.md, r1-dedup.md, r1-conflicts-generation.md, r1-against.md.

## Findings that change the recommendation
- Actions are blocked only on the private consuming project (repo-E); agent-harness is public and its Actions run
  (r1-github-tools). The cron made 10 of 2,354 label changes and integrate re-syncs after each merge
  (r1-transcripts). A local scheduler drops to a warning at most: orient prints "last sweep N h ago" when stale.
- Lead bookkeeping is 32 % of lead turns but at most about 4 % of total spend (r1-transcripts). Only two candidates
  clear r1-against's break-even (~$6.4 per 10-day window) at their upper bound: the integrate retry loop and the
  review relay.
- Integrate retries: 36 of 115 changed nothing; 15 of those were pending checks (#322 shipped). The safe form is
  read-only: show the cached last refusal for an unchanged (head SHA, feedback) key instead of re-running, not a
  merging loop (r1-against: the lead must read the PR and decide --harness-ok; a diagnostic --override re-run
  merged a PR).
- Conflicts: a post-merge merge-tree sweep of open PRs (3.7 ms each) sends all 10 historical send-backs at once
  instead of a median 30.5 min later; a dev pr check against main catches 7 of 19 (r1-conflicts-generation). Owns
  overlap predicts poorly (P 0.29, R 0.37); lock/generated files cause about a third.
- Duplicates: 1.6 % base rate. Stdlib BM25 over title+Context+DONE WHEN, advisory "Related:" line and Refs
  pre-fill only; close duplicates with `gh issue close --duplicate-of` (r1-dedup; r1-against agrees: advisory only).
- Review relay: the reviewer can post one COMMENT review itself (authors cannot approve their own PR). Risk from
  r1-against: an agent on the owner's account can resolve threads, so the unresolved-thread check would certify
  itself; the lead keeps the disposition (link #83, #110).
- New candidates: N1 owner-decision recording (overlaps #226), N2 manual closes after merge (38).

## Contradictions
- Integrate queue ceiling: r1-against $10.3 vs r1-transcripts $2.0-29.9 per window. Same direction; the
  synthesis states the range and that the read-only form is cheap enough to clear the lower break-even.
- Review relay: r1-against $2.3 vs r1-transcripts $3.1-23.9 (different turn counts). Synthesis reconciles by
  stating both bases.

## Weak or unverified
- Head-SHA change on retries rests on commit dates (2 of 115 known exactly).
- 0.64 dedup precision is one rater.
- N2 cause ASSUMED.

## Not built (below threshold or covered)
Generated HANDOFF ($2.3; copies facts, breaks "link where it lives"), PR body (12 % generable; #232/#100),
changelog (#60/#100), claim --worktree ($3.1; `gh issue develop --worktree` exists in gh 2.99), Mergify/Kodiak
(still need a check on the queued head), Projects v2, issue types/fields (org-only).

## Stop
Stop after round 1: saturation. The five sub-questions agree on the ranking, the counter-case narrows rather
than reverses it, and the open items above are measurement precision, not missing answers that would change
which issues to file.
