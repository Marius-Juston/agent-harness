# Starting a session

A fresh Claude Code session in this repository starts or continues the work with one prompt. The SessionStart hook
already injects the handoff, board and machine state; the prompt tells the session what to do with them and when it
may stop. Paste it as is. Project facts live in CLAUDE.md and the owner's brief, never here.

## The default: run the board until it needs the owner

```text
/goal You are the lead for this repository on this machine: follow the harness-ops:lead skill and CLAUDE.md. Work from the repository, not from memory: run `uv run dev orient` and read docs/HANDOFF.md and the owner's brief linked from CLAUDE.md. Then loop: integrate every PR in review (a reviewer or research-auditor first where the lead skill says so); dispatch startable items from `uv run dev board next` in priority order, research before the builds that need it, broad questions through the harness-ops:research skill until its stop rule holds, and a reuse search before any build; file an issue for every gap, defect or open decision you find. Never run a research round while `uv run dev lit search` or `uv run dev lit status` reports DEGRADED. Done when the transcript shows all of: (1) `uv run dev board next` lists nothing startable, every open item being blocked on the owner (recorded in docs/questions/OPEN_QUESTIONS.md), on another machine's live claim, or on an outside dependency named in the issue — or the quota cap stopped new spawns; (2) no worker is still running, and each finished worker's PR is integrated or left open with its reason commented; (3) docs/HANDOFF.md is current and landed through `uv run dev integrate`.
```

## Focused: only some issues

Replace the loop with the issue numbers and keep the stop rule:

```text
/goal You are the lead (harness-ops:lead skill, CLAUDE.md). Take only issues #A, #B and #C, plus the issues they need or file, to integrated PRs. Done when each of them is closed by an integrated PR or blocked on the owner with the question in docs/questions/OPEN_QUESTIONS.md, no worker is running, and docs/HANDOFF.md is current and integrated.
```

## Without /goal

`continue` (or `/harness-ops:lead`) runs one pass of the lead routine and stops at the first point that needs a
decision. Use it for a short session or when you want to steer each step.

## Notes
- The stop rule is written to be checked from the transcript, so state numbers and command output in replies.
- A session at the quota cap ends itself: it commits, pushes branches, updates HANDOFF and stops.
- Several machines can each run this prompt at once: claims on the board keep them off each other's issues.
