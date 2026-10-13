#!/usr/bin/env python3
"""Map r1's 23 comment-born decisions and 5 reversals to the mechanism that would hold each one (#220 r2).

Usage (from the agent-harness repository root): python3 -I docs/research/_scratch/adr-drift/scripts/r2_mechanisms.py

The holder of each decision is hand-coded (one rater, 2026-10-13; BELIEVED). What is mechanical: every `held_now`
evidence is a (path, literal) pair that must be present in the tree (git grep -F), so "already held by a test or
hook" is checked, not asserted. Context-word arithmetic at the end uses the word counts quoted in r2-mechanisms.md.

Holder codes:
  test  - an executable check (unit test, gate check, import contract) that fails when the decision is broken
  hook  - a Claude Code hook that refuses or rewrites the agent action the decision forbids
  mark  - a rule sentence (+ reason) in the file agents read, kept by a gate check that a literal stays in that path
  board - a scope/sequence decision whose home is the issue body (DONE WHEN); not a durable rule
"""

import subprocess

# id, holder, held_now = (path, literal) proving a test/hook already holds it, or None; note
D = [
    ("D01", "mark", None, "SETUP.md research-keys section; template-owned, so the render test copies it"),
    ("D02", "test", None, "lit privacy: a unit test that a query with an e-mail is refused; pending, comment-only"),
    (
        "D03",
        "test",
        ("tests/test_checks.py", "unverified (TLS)"),
        "TLS failure is unverified, never retried insecurely",
    ),
    ("D04", "test", ("tests/test_workflow.py", "Closes #33"), "dev pr writes Closes #N"),
    ("D05", "mark", None, "ci.yml: security steps in one billed job (comment 'bill one runner', no test)"),
    (
        "D06",
        "test",
        ("tests/test_hooks.py", "test_completion_gate_refuses_at_most_twice_then_records_a_board_block"),
        "refusal cap 2",
    ),
    ("D07", "mark", None, "audit cycles: never written to research SKILL.md (C3)"),
    ("D08", "mark", None, "repo_metrics.py FROZEN docstring: the file itself carries the rule"),
    ("D09", "board", None, "scope moved to #147"),
    ("D10", "board", None, "hold #171 until #169 decides (temporary)"),
    ("D11", "mark", None, "prompts say name-then-verify; search is a recall aid"),
    ("D12", "hook", None, "never curl/WebFetch the index APIs (C1); text only, no test, no hook today"),
    ("D13", "test", ("tests/test_reuse.py", "LGPL-3.0-only"), "uv add guard admits OR/LGPL"),
    ("D14", "board", None, "add arm B0 + WebSearch (pending experiment)"),
    ("D15", "board", None, "pre-registered held-out set >= 20 (recorded in the note)"),
    (
        "D16",
        "test",
        ("tests/test_plugin.py", "prefix every command with cd <abs path> &&"),
        "a prose-marker test already",
    ),
    ("D17", "board", None, "primary candidate (pending)"),
    ("D18", "board", None, "F0 only; DONE WHEN not edited (C4)"),
    ("D19", "test", ("tests/test_consolidation.py", "failed to parse"), "unparsable files reported"),
    ("D20", "hook", ("tests/test_hooks.py", "harness-ops:architect"), "architect read-only by guard_bash"),
    ("D21", "test", ("tests/test_hooks.py", "is not a .env glob (issue #7)"), "Python-literal globs are not .env"),
    (
        "D22",
        "test",
        ("tests/test_hooks.py", "git config --get --unset core.hooksPath"),
        "combined git-config flags = write",
    ),
    (
        "D23",
        "test",
        ("tests/test_hooks.py", "test_completion_gate_lets_a_builder_stop_on_a_blocked_item"),
        "stop when blocked",
    ),
]

# reversal, what happened, mechanisms that would have prevented it (BELIEVED unless the note says VERIFIED)
C = [
    ("C1", "rule text deleted for word budget", {"mark", "test", "hook(harm only)", "adr(only via enforced-by)"}),
    ("C2", "evidence reversal before merge (legitimate)", set()),
    ("C3", "comment-born default never written to the skill", {"board-lint(warning)"}),
    ("C4", "scope narrowed in a comment, DONE WHEN unchanged", {"board-lint(warning)"}),
    ("C5", "comment called stale 6 minutes later (self-corrected)", set()),
]


def present(path: str, literal: str) -> bool:
    return subprocess.run(["git", "grep", "-q", "-F", literal, "--", path]).returncode == 0


def main() -> None:
    bad = [d[0] for d in D if d[2] and not present(*d[2])]
    if bad:
        raise SystemExit(f"held_now evidence not found: {bad}")
    by = {}
    for d in D:
        by.setdefault(d[1], []).append(d[0])
    held = [d[0] for d in D if d[2]]
    print(f"decisions: {len(D)}; by cheapest holder: " + "; ".join(f"{k} {len(v)} {v}" for k, v in sorted(by.items())))
    print(f"already held by a test or hook today (evidence found in tree): {len(held)} {held}")
    durable_prose = [d[0] for d in D if d[1] in ("mark", "hook") and not d[2]]
    print(f"durable rules held only by prose today: {len(durable_prose)} {durable_prose}")
    for c in C:
        print(f"  {c[0]}: {c[1]} -> prevented by: {', '.join(sorted(c[2])) or 'nothing should (or needs to)'}")
    prevented = {m for c in C for m in c[2]}
    for m in ("mark", "test", "hook(harm only)", "path-rule", "decision-index", "adr", "board-lint(warning)"):
        n = sum(1 for c in C if any(x.startswith(m.split("(")[0]) for x in c[2]))
        print(f"  reversals a {m} mechanism would have prevented: {n}/5")
    _ = prevented
    # Context arithmetic (words): an always-loaded decision index of one ~15-word line per durable decision,
    # against the 600-word context budget; project B's five ADR bodies (wc -w, 2026-10-13).
    adr = [199, 444, 451, 184, 688]
    print(f"project B ADR bodies: total {sum(adr)} words, mean {sum(adr) / len(adr):.0f}")
    for n in (len(durable_prose) + len([d for d in D if d[1] in ("mark", "hook")]) - len(durable_prose), 23):
        print(
            f"index of {n} decisions x 15 words = {n * 15} words = {100 * n * 15 / 600:.0f}% of a 600-word context budget"
        )


# r1's recorded (path, literal) markers plus C1's original literal. Survivorship caveat: 17 of these 18 literals were
# picked from today's text, so a reworded-then-dropped literal is invisible here; R2b replays this properly.
MARKERS = [
    ("docs/SETUP.md", "Research API keys"),
    ("devkit/citecheck.py", "unverified (TLS)"),
    ("devkit/workflow.py", "gets `Closes #N`"),
    (".github/workflows/ci.yml", "bill one runner"),
    ("plugins/harness-ops/scripts/completion_gate.py", "Capped at CAP refusals"),
    ("scripts/repo_metrics.py", "FROZEN (#141)"),
    ("docs/research/software-discovery.md", "recall aid"),
    ("devkit/reuse.py", "optional recall aid"),
    ("plugins/harness-ops/agents/researcher.md", "never curl"),
    ("plugins/harness-ops/agents/researcher.md", "shared, cached, rate-limited client"),
    ("devkit/reuse.py", "weak copyleft"),
    ("docs/research/software-discovery.md", "set D"),
    ("plugins/harness-ops/skills/lead/SKILL.md", "prefix every command with cd"),
    ("devkit/consolidation.py", "fail to parse"),
    ("plugins/harness-ops/skills/lead/SKILL.md", "Only the lead resets the trigger"),
    ("plugins/harness-ops/scripts/guard_bash.py", "regexes far more often"),
    ("plugins/harness-ops/scripts/guard_bash.py", "Unknown flags count as writes"),
    ("plugins/harness-ops/scripts/completion_gate.py", "def blocked"),
]


def marker_replay() -> None:
    """Commits in history where a marker check would have failed: the literal present in the parent, absent after."""
    fails = []
    for path, lit in MARKERS:
        log = subprocess.run(
            ["git", "log", "--all", "--format=%h", "-S", lit, "--", path], capture_output=True, text=True
        )
        for h in log.stdout.split():
            show = lambda rev: subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True).stdout  # noqa: B023, E731 - research script, kept as run
            if lit in show(f"{h}^") and lit not in show(h):
                fails.append((h, path, lit))
    print(f"marker replay: {len(MARKERS)} markers, {len(fails)} commits would fail the check: {fails}")


if __name__ == "__main__":
    main()
    marker_replay()
