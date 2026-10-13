# Gaps after round 1 (lead, 2026-10-12)

## Findings that shape the recommendation
- Experts: minimise complexity (Ousterhout), not LOC; tidy continuously (Fowler, Beck); restructure incrementally with
  characterisation tests, strangler fig, parallel change; enforce boundaries in the gate (r1-expert-guidance).
- Agent code drifts: duplicated logic rises over a session (13.8% -> 50.8% by turn 5); a map of existing functions cut
  redundancy 65.1% -> 46.7% and LOC 34%; complexity +42% persists after Cursor adoption (r1-empirical).
- Reuse: pyscn (MIT) + vulture + import-linter/tach + deptry cover the measurements; Anthropic code-modernization
  (Apache-2.0) has the verdict design to copy (r1-tools-prior-art).
- Safety: small green steps, refactor-only PRs, behaviour inventory, golden-master/differential tests, mutation on
  changed code; 4-7% of LLM refactorings ship silently broken (r1-against-and-safety).
- Data: mechanical removal ceiling in repo-L is ~18-25%, not 80%; separating signals were dormant subtrees, in-repo copies
  of a shared dependency, function-level imports, one-off scripts, large churny files (r1-sibling-metrics).

## Contradictions
- Owner's 100k -> 20k vs measured 18-25% mechanical ceiling: the rest needs feature-level decisions (keep / drop with
  owner approval / unused), which experts say must not be "full parity" by default.
- LOC as goal (owner) vs LOC as a misleading payoff measure (Microsoft, Ousterhout): use LOC as a report, not the gate.

## Open, for round 2
1. Prevention: how to give agents a map of existing code (aider repo-map, ctags, `dev code outline`), evidence it works.
2. Feature inventory beyond static reachability, and whether a design-level proposal on repo-L (paper only) can show
   reductions beyond the 25% mechanical ceiling; read code-modernization's scripts and pyscn's skills for reuse.
3. Trigger calibration: replay candidate signals over the git history of the four repos; when would each have fired,
   and how noisy is it?
