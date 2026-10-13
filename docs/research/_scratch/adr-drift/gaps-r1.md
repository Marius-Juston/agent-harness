# Gaps after round 1 (lead, 2026-10-13)

Round 1: [r1-adrs](r1-adrs.md), [r1-taxonomy](r1-taxonomy.md), [r1-tools-against](r1-tools-against.md).

## Findings that change the recommendation
- Semantic (LLM) drift detection is too noisy to gate on (best measured 75% precision; Google's blocking bar is zero
  false positives); agents act on false alerts. Gates stay mechanical; semantic checks, if any, are periodic sweeps.
- Mechanical literal-marker checks are the common ground of the ADR and the counter-case researchers: the one
  measured reversal C1 (rate-limit rule deleted for word budget, restored 8 h later) is caught by an `enforced-by`
  marker check, with or without an ADR around it.
- 17 of 32 measured drift classes would still be missed today; most fixes are small extensions of existing checks
  (dev command options and bare `dev X` spans, board lint Owns paths and met DONE WHEN, floor raise at integrate,
  orient listing new memory, generated HANDOFF State).
- ADR evidence for agents is weak and unreplicated; ADR maintenance in open source decays; project B's own ADRs
  include one amended in place on day one and one cited nowhere outside docs/adr.

## Contradictions
- r1-adrs: ship small ADRs. r1-tools-against: one-line rule + check, no ADR layer. Unresolved: does an ADR document
  add anything the rule + reason + marker does not, for the 5 comment-only decisions and the 5 reversals?

## Owner question added before round 2
- Is there something better than ADRs (QUESTION.md item 3)?

## Round 2 (only the gaps)
- R2a: compare decision mechanisms (executable checks, path-scoped .claude/rules/, hooks, inline rule + reason +
  marker, decision-labelled issues + generated index, ADRs) on the 23 measured decisions and 5 reversals: which
  each would have prevented, context words, drift surface, maintenance, false positives. Argue for the minimum.
- R2b: replay the proposed mechanical checks over both repos' history to measure their false-positive rate and
  the catches, so each recommended check has a measured precision before it becomes a gate (else a sweep or warning).

Stop rule: stop after round 2 unless it changes the recommendation.
