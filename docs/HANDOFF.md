# Handoff

At most 40 lines. `uv run dev handoff` (integrate runs it) rewrites the board sections; the rest is hand-written.

## In flight
- #650 in-progress lead on `650-handoff`: docs: this repository keeps docs/HANDOFF.md and docs/questions/OPEN_QUESTIONS.md like rendered projects
- #636 blocked: scripts: owner decides whether lit_bypass.py, the finished #174 re-measure, is deleted, frozen or kept (owner decision, review 3 step 2)
- #625 review harness-ops:builder on `625-split-board`: board: split devkit/board.py (1525 pinned, 1861 lines) into modules so features stop hitting its LOC pin
- #594 blocked: Experiment: query reformulation for research searches (synonyms, field vocabulary, method names) measured with the research eval
- #587 blocked: Research: free text-and-data-mining APIs for paywalled papers, and how many abstract-only claims stay paywalled after open-access sources
- #543 in-progress harness-ops:builder on `543-nice-background`: gate: non-gate heavy work runs at lower CPU priority so a gate keeps its idle speed on a busy machine
- #536 blocked: metrics: optional local OTLP sink for Claude Code telemetry (side calls, documented contract)
- #535 blocked: metrics history: a content-free per-run store that outlives Claude Code's 30-day transcript cleanup
- #501 in-progress: review: this repository requires a reviewer verdict on harness code; a tester follows each feature merge
- #472 review harness-ops:builder on `473-train-admission`: integrate: verify a train's single push of main against branch protection
- #338 blocked: Research: post-release measurements of the v0.1.10 and v0.1.11 changes
... 6 more: `uv run dev board list`

## Just merged
- test(eval-research): a held-out snowball arm (half the gold as seeds, dev lit snowball top 20 at depth 1 and 2) with S2 answering (#619) (#645)
- docs: Re-measure open-access reach with an Unpaywall contact email and a CORE key (#622) (#646)
- fix: agent-harness.md:124 spotlighting link text fails cite-check title match (#616) (#643)
- feat(research-skill): snowball accepted papers with dev lit snowball in the gap pass (#618) (#642)
- feat(integrate): gate the merged tree while Actions run on it, not one after the other (#541) (#639)

## Next
- #534 P1 build, unblocks 2: dev metrics: tokens, wall time and rework per merged issue, by agent type, model and effort
- #590 P1 build, unblocks 1: evals: a known-answer evaluation of the research skill (cited-works recall, claim precision, tokens)
- #301 P1 research: Experiment: probes G1-G3, plugin loaded from the uv pin and the /reload-plugins build switch
- #542 P1 build: gate: board stage leaves the gate, gate gh calls time out, pass fingerprint overlaps the stages
- #608 P1 research: Research: least-privilege GitHub credentials per agent role (workers cannot merge or administer, only integrate can)

## Waiting on the owner
- #625 / PR #640 (board.py split, reviewer merge verdict round 2): a `weakening-ok:` line on #625 for 10 retargeted
  test patches. #636 (delete lit_bypass): a `weakening-ok:` line on #636.
- #610 / #262 pilot and #590 baseline: a working Anthropic API key (the stored one answers 401).
- #619: run `uv run dev eval research --claims 0 --label 619-held-out` (no API spend; workers are refused, #647).
- #587: the library's answers to Q1-Q3 in docs/research/tdm-compliance.md §5 before any institutional access.
- #608: a scratch repository and fine-grained tokens for least-privilege credentials.
- Release v0.1.18 with `uv run dev release prepare 0.1.18` once #640 lands.
