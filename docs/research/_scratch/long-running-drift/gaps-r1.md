# Gaps after round 1 (lead, 2026-10-19)

Read from the hand-backs of r1-memory, r1-prose-vs-behaviour, r1-platform-and-skew, r1-outside and r1-against.

## Findings that change the recommendation
- **Version skew is the costliest drift on record.** repo-E's agents ran plugin 0.1.4 for 162 h while the pin moved
  v0.1.5 to v0.1.9; 0 sessions ran v0.1.9; 92 builder spawns ran without the completion gate and 27 `uv add` calls
  without the reuse guard (r1-platform-and-skew). Cause: Claude Code's per-user marketplace registry, not #163.
  `doctor` warned but exited 0 and was never run. A loaded-root check in the plugin's own SessionStart/SubagentStart
  hooks fired at 10 of 10 session starts on replay. The marketplace installs `main`'s HEAD under the manifest version
  string (the 2026-10-19 update installed 0c0610e labelled 0.1.9), so the check must compare content, not the string.
- **Instruction files barely drift mechanically; specs and docstrings do** (r1-prose-vs-behaviour): 3 break episodes
  in 990 merges of instruction files (all false), 94 % same-merge co-fix; L3 specs: 52 drift episodes fixed after a
  median 90.7 days, mostly by deleting the specs. An LLM sweep reached precision 0.75 (0.87 on drifted docs, 0.22 on
  maintained instruction files), recall 7 of 9. #229's held-out replay: 0 true events, 1 false positive.
- **Memory**: 8 of 34 checkable entries stale within 3 weeks, half within a day; 6 never corrected; no verified harm
  from a stale entry, but one real scope leak: a user-wide rule stored per project, so 170 public commits carried a
  trailer the owner had forbidden (r1-memory). 91 % of subagents carry the lead's MEMORY.md index, against the docs.
  Listing every entry at orient adds nothing (the index is already loaded).
- **Warnings decay** (r1-against): repo-E's board-lint count at session start rose 72 to 138; "suite audit due" was
  shown 80 times and never acted on. Token cost of drift output is small ($0.69/week for orient); maintenance cost
  and alert fatigue are the real costs. Deleting stale docs and regenerating beats maintaining them.
- **Outside** (r1-outside): context files change ~2 commits/month and grow x1.4; referential rot in 23 % of repos;
  stale context flips 17-91 % of correct answers unless the model is told when the evidence stopped applying;
  intentional negative references ("removed X, do not reintroduce") are the largest false-positive class.

## Contradictions
- #233 (list every memory entry): r1-memory and r1-against both reject it as written; both support printing only
  flagged entries. Resolved: re-scope #233.
- #229 as a gate: r1-prose-vs-behaviour finds 0 true events in the held-out replay; r1-outside favours deterministic
  existence checks. Resolved: keep #229 a warning, skip research notes and negative references; no gate.

## Weak or single-source
- Every hand label has one rater (all four empirical files).
- Months-scale memory drift is unobservable here: transcripts are kept 30 days and no entry is older than 23 days.
- The LLM sweep ran once, with one model and one prompt.

## Stop decision
Stop after round 1. The open items are experiments or probes on this harness (G1 plugin-in-wheel probe, G2 the
reload that switched builds, a held-out recall study of the LLM sweep with planted claims and two models, months-scale
memory data from another machine), not literature questions; another round would not change the ranking:
1 loaded-root skew check (gate at session start), 2 provenance and user scope for memory, 3 warnings only when new or
rising, 4 advisory LLM sweep for specs and docstrings with delete-or-regenerate as the default fix, 5 #229 as a
warning. The synthesis files each as an issue with a measurement.
