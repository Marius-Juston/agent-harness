# Gaps after round 2 (lead, 2026-10-12)

Round 2 settled the three open gaps:
1. Prevention: push a scoped signature map (`dev code map`, 4-8k tokens) and gate exact/renamed clones of existing
   code in the diff (`dev code dupes --diff`; 6 of 7 fires real on repo-L's 109 merges, 0 on H) (r2-prevention).
2. Inventory: runtime coverage per requirement plus software reconnaissance; repo-L's consolidation is ~21.5% on
   paper (12.3% mechanical); going past ~25% needs owner scope decisions D1-D5. Copy code-modernization's consolidate
   prompt, referee and baseline-diff + canary verdict (Apache-2.0), run per module only (r2-feature-inventory).
3. Trigger: composite variant C (growth x2 and +5k LOC, or 100 first-parent commits, or a new smell, or a new large
   churny file); 0.4 s on repo-L; fired about once per 54 commits there, never on H/repo-E (r2-trigger-calibration).

## Stop
Stop after round 2: the remaining gaps (thresholds are judgement on four repos; no real review was run; no test shows a
pushed map changes Claude's behaviour) are closed only by building #113 and running it, not by more search.
Synthesis must: keep VERIFIED claims tied to tracked scripts (move what the numbers rest on out of gitignored
.scratch/ into scripts/ or name it as not reproducible here), keep code names, and file follow-ups.
