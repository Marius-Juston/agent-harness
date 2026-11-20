# Handoff

At most 40 lines. `uv run dev handoff` (integrate runs it) rewrites the board sections; the rest is hand-written.

## In flight
- #594 blocked: Experiment: query reformulation for research searches (synonyms, field vocabulary, method names) measured with the research eval
- #587 blocked: Research: free text-and-data-mining APIs for paywalled papers, and how many abstract-only claims stay paywalled after open-access sources
- #536 blocked: metrics: optional local OTLP sink for Claude Code telemetry (side calls, documented contract)
- #535 blocked: metrics history: a content-free per-run store that outlives Claude Code's 30-day transcript cleanup
- #501 blocked: review: this repository requires a reviewer verdict on harness code; a tester follows each feature merge
- #338 blocked: Research: post-release measurements of the v0.1.10 and v0.1.11 changes
- #269 blocked: Experiment: a Haiku 5.5 read-only lookup agent against Opus Explore on questions with known answers
- #268 blocked: Experiment: builder fresh-context self-review of its diff before dev pr, paired and pre-registered
- #264 blocked: Experiment: premature stops of tester and issue-mode researcher; extend completion_gate if >= 5 %
- #262 review harness-ops:researcher on `262-research-effort-model`: Experiment: researcher at effort medium and a Sonnet 5.5 search phase vs Opus high
- #124 blocked: Experiment: does a pushed signature map change Claude's code reuse
- #9 in-progress harness-ops:builder on `9-oa-sources`: dev lit and cite-check: ERIC, Unpaywall, CORE and PubMed sources plus a retraction check

## Just merged
- ci(scripts): owner decides whether lit_bypass.py, the finished #174 re-measure, is deleted, frozen or kept (owner decision, review 3 step 2) (#636) (#653)
- feat(board): split devkit/board.py (1525 pinned, 1861 lines) into modules so features stop hitting its LOC pin (#625) (#640)
- docs(handoff): refresh board sections at session end (#650) (#652)
- docs: this repository keeps docs/HANDOFF.md and docs/questions/OPEN_QUESTIONS.md like rendered projects (#650) (#651)
- test(eval-research): a held-out snowball arm (half the gold as seeds, dev lit snowball top 20 at depth 1 and 2) with S2 answering (#619) (#645)

## Next
- #534 P1 build, unblocks 2: dev metrics: tokens, wall time and rework per merged issue, by agent type, model and effort
- #590 P1 build, unblocks 1: evals: a known-answer evaluation of the research skill (cited-works recall, claim precision, tokens)
- #301 P1 research: Experiment: probes G1-G3, plugin loaded from the uv pin and the /reload-plugins build switch
- #542 P1 build: gate: board stage leaves the gate, gate gh calls time out, pass fingerprint overlaps the stages
- #608 P1 research: Research: least-privilege GitHub credentials per agent role (workers cannot merge or administer, only integrate can)

## Waiting on the owner
- #610 / #262: key replaced. Run the pilot with `run_pilot.sh` in the 262 worktree ($13 cap); then the #590 baseline.
- #619: rerun `uv run dev eval research --claims 0 --label 619-held-out` once S2 is healthy (34 of 56 failed).
- #587: the library's answers to Q1-Q3 in docs/research/tdm-compliance.md §5 before any institutional access.
- #608: a scratch repository and fine-grained tokens for least-privilege credentials.
- Release v0.1.18: #640 has landed; `uv run dev release prepare 0.1.18` when the owner says go.
