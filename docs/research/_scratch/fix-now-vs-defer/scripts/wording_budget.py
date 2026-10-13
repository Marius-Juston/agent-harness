"""Apply the fix-now wording proposed in docs/research/fix-now-vs-defer.md §6 to copies and count words (#149).

Each edit must match the current file exactly once; the script prints words before and after, against the
budget in .devkit/ratchets.toml (agent 600, skill 650, context 600), using devkit's own counter. Nothing is written.
Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/wording_budget.py`.
"""

import sys
from pathlib import Path

from devkit import frontmatter

ROOT = Path(__file__).resolve().parents[5]
FIX_NOW_LINK = "the fix-now rule (multi-agent.md §3)"

EDITS: dict[str, list[tuple[str, str]]] = {
    "plugins/harness-ops/agents/builder.md": [
        (
            "Edits outside Owns: make one only when the issue needs it end to end, and list it",
            "Edits outside Owns: make one when the issue needs it end to end or it is a fix-now (multi-agent.md §3),"
            " and list it",
        ),
        (
            "what is unverified, and follow-ups filed as issues (`uv run dev board new ...`).",
            "what is unverified, `Fixed in passing`, and follow-ups filed as issues (`uv run dev board new ...`), each"
            " naming the fix-now condition it failed.",
        ),
        (
            "files outside Owns, and follow-up issue numbers.",
            "files outside Owns, fixes in passing, and follow-up issue numbers.",
        ),
    ],
    "plugins/harness-ops/agents/tester.md": [
        (
            "A small defect inside the issue's Owns: fix it.",
            "A small defect inside the issue's Owns, or a fix-now (multi-agent.md §3): fix it.",
        ),
    ],
    "plugins/harness-ops/agents/researcher.md": [
        # added: fix-ready edits batched into one build issue; scripts kept where the note can link them
        (
            'File each important gap you did not close as an issue: `uv run dev board new --type research ... --done "..."`.',
            "File each important gap you did not close as an issue (`uv run dev board new --type research ... --done"
            ' "..."`); batch fix-ready edits (file, change, check) into one build issue.',
        ),
        (
            "check it with a small script in `.scratch/`. That makes it VERIFIED.",
            "check it with a script in `_scratch/<slug>/scripts/`; that makes it VERIFIED.",
        ),
        # cut to pay for it
        (
            "(`docs/research/INDEX.md` unless `[tool.devkit] research_dir` moves it)",
            "(`docs/research/INDEX.md` by default)",
        ),
        ("(CLAUDE.md may name them; for ML: NeurIPS, ICML, ICLR, ACL, EMNLP)", "(CLAUDE.md may name them)"),
    ],
    "plugins/harness-ops/skills/lead/SKILL.md": [
        # added: follow-ups are triaged before integrate; a PR's own defects go back to its worker
        (
            "4. `uv run dev integrate <pr>`;",
            "4. Triage the PR's follow-ups: a fix-now (multi-agent.md §3) or a defect in its own new code returns to the"
            " worker; the rest need a reason.\n5. `uv run dev integrate <pr>`;",
        ),
        (
            "Its last line, `RESULT-JSON`, gives the outcome: on `conflict`, resume the worker it names;",
            "On its `RESULT-JSON` outcome `conflict`, resume the named worker;",
        ),
        ("Researchers fan out wide (no tests, little memory).", "Researchers fan out wide."),
        (
            "`harness-ops:research` skill (researcher rounds, synthesis, audit), not",
            "`harness-ops:research` skill, not",
        ),
        # cut to pay for it: the agent list is a derived fact (agent-surface.md)
        (
            "Agents come from the `harness-ops` plugin, so their types are `harness-ops:builder`, `harness-ops:tester`,\n"
            "  `harness-ops:researcher`, `harness-ops:reviewer`, `harness-ops:research-auditor`, `harness-ops:architect`,"
            " `harness-ops:suite-auditor`. A project's own agents in\n  `.claude/agents/` keep bare names.",
            "Plugin agent types are `harness-ops:<name>` (`docs/generated/agent-surface.md`); a project's own agents in\n"
            "  `.claude/agents/` keep bare names.",
        ),
    ],
    "docs/specs/multi-agent.md": [
        # the one full statement of the rule; every role links here
        (
            "A small edit outside Owns that the issue needs is allowed. Name it in the hand-back.\n",
            "A small edit outside Owns that the issue needs is allowed. Name it in the hand-back.\n"
            "- **Fix now.** A defect or gap found while working goes in this PR, under `Fixed in passing` in its body,\n"
            "  when all four hold: (1) at most 20 changed lines in at most 2 files; (2) the files are in the PR's diff or\n"
            "  Owns, or this change exposed it; (3) no design choice, new interface or dependency, or owner approval, and\n"
            "  the gate checks it; (4) no other open item or hotspot holds the file. A defect the PR itself introduced is\n"
            "  fixed whatever its size. Otherwise file it and name the condition that failed.\n",
        ),
    ],
    "template/.devkit/HARNESS.md.jinja": [
        (
            "Work without an issue does not exist. Each issue has a measurable DONE WHEN.",
            "Work without an issue does not exist. Each issue has a measurable DONE WHEN. A small fix found on the way\n"
            f"  belongs to the current issue: {FIX_NOW_LINK}.",
        ),
    ],
}
BUDGET = {"agents": 600, "skills": 650, "template": 600, "specs": 10**6}  # specs: no budget


def count(rel: str, text: str) -> int:
    if rel.endswith(".jinja"):  # context budget: HARNESS.md plus the template CLAUDE.md that imports it
        return frontmatter.words(text) + frontmatter.words((ROOT / "template/CLAUDE.md.jinja").read_text())
    return frontmatter.words(frontmatter.split(text)[1])


bad = 0
for rel, edits in EDITS.items():
    text = (ROOT / rel).read_text()
    before = count(rel, text)
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            print(f"{rel}: expected 1 match, found {n}: {old[:60]!r}")
            bad += 1
            continue
        text = text.replace(old, new)
    after = count(rel, text)
    budget = next(v for k, v in BUDGET.items() if f"/{k}/" in f"/{rel}")
    flag = "OK" if after <= budget else "OVER"
    print(f"{rel}: {before} -> {after} words (budget {budget}) {flag}")
    bad += after > budget
sys.exit(1 if bad else 0)
