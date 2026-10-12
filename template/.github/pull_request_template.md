Closes #<issue>   <!-- "Refs #<issue>" if some DONE WHEN item is not met -->

## What changed
<!-- files; harness files touched (.devkit/, .claude/, .github/workflows/, CLAUDE.md, pyproject.toml)? -->

## Evidence
<!-- numbers, test names, sources; label claims VERIFIED / SOURCED / BELIEVED / ASSUMED -->

## Verdict vs DONE WHEN
<!-- one line per DONE WHEN item: met / not met, with the evidence -->

## Not verified / follow-ups
<!-- each follow-up filed: `uv run dev board new ... --done "..."` (issue numbers here) -->

## Checks
- [ ] `uv run dev check` passed on the PR tip (`uv run dev pr` refuses otherwise)
- [ ] research: `uv run dev cite-check` passes for changed notes; INDEX regenerated
- [ ] new behaviour has tests; a found bug has a regression test
- [ ] follow-ups filed as issues with DONE WHEN
