#!/usr/bin/env python3
"""Tally decisions made in agent-harness issue/PR comments and where (if anywhere) they are recorded in git (#220 r1).

Usage (from the repository root):  python3 docs/research/_scratch/adr-drift/scripts/r1_adr_decisions.py [comments.json]
Without an argument it fetches every issue/PR conversation comment since 2026-09-29 with `gh api --paginate`.

The decision table below is hand-coded (one rater, 2026-10-13) from the 106 comments left after dropping claim,
heartbeat and release markers and short "PR ready"/"Merged" notices. A decision is a comment that chooses among
options or sets a rule for later work (owner directive, lead decision, deliberate trade-off). Bug reports, review
fix requests, data and un-adopted proposals are excluded. For each decision the script checks that the comment id
exists, and that each `record` path contains its marker (git grep), so "recorded" is mechanical, not asserted.
"""

import json
import subprocess
import sys

# id, issue, comment id, who, decision, record = (path, marker) or None, issue body updated?, status
D = [
    (
        "D01",
        23,
        6110078863,
        "owner",
        "research API keys: setup section in template + every repo",
        ("docs/SETUP.md", "Research API keys"),
        True,
        "adopted",
    ),
    (
        "D02",
        9,
        6111552912,
        "lead",
        "lit contact only from config; no personal data in external queries; guard flags emails",
        None,
        False,
        "pending",
    ),
    (
        "D03",
        35,
        6110963017,
        "lead",
        "TLS failure = unverified (TLS), never retried insecurely",
        ("devkit/citecheck.py", "unverified (TLS)"),
        None,
        "adopted",
    ),
    (
        "D04",
        38,
        6125956401,
        "lead",
        "a complete hand-back closes its issue (dev pr writes Closes #N)",
        ("devkit/workflow.py", "gets `Closes #N`"),
        None,
        "adopted",
    ),
    (
        "D05",
        57,
        6126159088,
        "lead",
        "security scan is not a separate billed job",
        (".github/workflows/ci.yml", "bill one runner"),
        None,
        "adopted",
    ),
    (
        "D06",
        54,
        6126326802,
        "lead",
        "completion gate caps refusals at 2; giving up is a board block",
        ("plugins/harness-ops/scripts/completion_gate.py", "Capped at CAP refusals"),
        None,
        "adopted",
    ),
    (
        "D07",
        84,
        6126807476,
        "lead",
        "one auditor cycle by default, second only on 'return'; 2-source rule",
        None,
        False,
        "pending",
    ),
    (
        "D08",
        141,
        6137869528,
        "owner",
        "repo_metrics.py frozen, superseded by dev consolidation",
        ("scripts/repo_metrics.py", "FROZEN (#141)"),
        None,
        "adopted",
    ),
    (
        "D09",
        147,
        6137398939,
        "reviewer",
        "configurable default branch and gate test path belong to #147",
        None,
        False,
        "pending",
    ),
    (
        "D10",
        171,
        6140300028,
        "lead",
        "hold #171: search may not become the prompt default before #169 decides",
        ("docs/research/software-discovery.md", "recall aid"),
        None,
        "adopted",
    ),
    (
        "D11",
        29,
        6147090475,
        "lead",
        "ship R4; search is a recall aid; prompts say name-then-verify",
        ("devkit/reuse.py", "optional recall aid"),
        None,
        "adopted",
    ),
    (
        "D12",
        174,
        6147198151,
        "lead",
        "restore 'shared rate limits: never curl' in researcher/auditor prompts",
        ("plugins/harness-ops/agents/researcher.md", "never curl"),
        None,
        "adopted",
    ),
    (
        "D13",
        172,
        6147370383,
        "lead",
        "uv add guard must not refuse OR-licensed or LGPL packages",
        ("devkit/reuse.py", "weak copyleft"),
        None,
        "adopted",
    ),
    (
        "D14",
        176,
        6147371000,
        "lead",
        "add arm B0 + WebSearch and a 'latest library' prompt set",
        None,
        False,
        "pending",
    ),
    (
        "D15",
        175,
        6148305708,
        "lead",
        "evaluate --from on a new pre-registered held-out set >= 20 cases",
        ("docs/research/software-discovery.md", "set D"),
        None,
        "adopted",
    ),
    (
        "D16",
        170,
        6148183623,
        "lead",
        "cross-repo dispatch: prompt names the worktree, cd-prefix every command",
        ("plugins/harness-ops/skills/lead/SKILL.md", "prefix every command with cd"),
        None,
        "adopted",
    ),
    (
        "D17",
        211,
        6149788941,
        "lead",
        "agent-written need is the primary candidate, fresh held-out set",
        None,
        False,
        "pending",
    ),
    (
        "D18",
        196,
        6151242889,
        "lead",
        "build half implements only F0; dev reuse try waits for #218",
        ("docs/research/software-discovery.md", "provisional until #218"),
        False,
        "adopted",
    ),
    (
        "D19",
        113,
        6136888705,
        "auditor",
        "report files that fail ast.parse instead of skipping them",
        ("devkit/consolidation.py", "fail to parse"),
        None,
        "adopted",
    ),
    (
        "D20",
        142,
        6136976612,
        "builder",
        "architect read-only by guard; only the lead resets the trigger",
        ("plugins/harness-ops/skills/lead/SKILL.md", "Only the lead resets the trigger"),
        None,
        "adopted",
    ),
    (
        "D21",
        7,
        6106944452,
        "builder",
        "Python-literal globs (.*, .?nv) no longer read as .env",
        ("plugins/harness-ops/scripts/guard_bash.py", "regexes far more often"),
        None,
        "adopted",
    ),
    (
        "D22",
        45,
        6125972031,
        "builder",
        "unknown/combined git-config flags count as writes (fail closed)",
        ("plugins/harness-ops/scripts/guard_bash.py", "Unknown flags count as writes"),
        None,
        "adopted",
    ),
    (
        "D23",
        103,
        6126830024,
        "lead",
        "a builder may stop when its issue is status:blocked",
        ("plugins/harness-ops/scripts/completion_gate.py", "def blocked"),
        None,
        "adopted",
    ),
]

# Contradictions or reversals of a recorded decision seen in the same window (each checked by hand; see r1-adrs.md §4).
C = [
    (
        "C1",
        "researcher.md 'shared, rate-limited client' rule dropped by 4d61af0 (#171, word budget), restored by 10a846b (#174)",
    ),
    (
        "C2",
        "#171 first build made `dev reuse` search the prompt default; reversed by #169's pre-registered TEST (D10, D11)",
    ),
    (
        "C3",
        "D07 (auditor: second cycle only on 'return') diverges from research SKILL.md (again on 'return' or 'accept after fixes')",
    ),
    ("C4", "D18 narrowed #196's DONE WHEN in a comment; the issue body still asks for 'docs lookup or a 5-line check'"),
    ("C5", "D11's comment on #29 called stale by the lead 6 minutes later (comment 6147210954)"),
]


def comments(path: str | None) -> dict[int, dict]:
    if path:
        with open(path) as fh:
            raw = fh.read()
    else:
        raw = subprocess.run(
            ["gh", "api", "--paginate", "repos/{owner}/{repo}/issues/comments?per_page=100&since=2026-09-29T00:00:00Z"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    out, dec, i = [], json.JSONDecoder(), 0
    while rest := raw[i:].lstrip():
        obj, n = dec.raw_decode(rest)
        out += obj
        i = len(raw) - len(rest) + n
    return {c["id"]: c for c in out}


def owner_directives() -> list[int]:
    """Issues whose Context section opens with an owner directive ("Owner ...")."""
    import re

    raw = subprocess.run(
        ["gh", "issue", "list", "--state", "all", "--limit", "500", "--json", "number,body"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(
        i["number"] for i in json.loads(raw) if re.search(r"## Context\s*\n+\s*(Owner|OWNER)", i["body"] or "")
    )


def recorded(rec: tuple[str, str] | None) -> bool:
    if rec is None:
        return False
    path, marker = rec
    return subprocess.run(["git", "grep", "-q", "-F", marker, "--", path]).returncode == 0


def main() -> None:
    cs = comments(sys.argv[1] if len(sys.argv) > 1 else None)
    substantive = [
        c
        for c in cs.values()
        if not any(m in c["body"] for m in ("devkit:claim", "devkit:heartbeat", "devkit:release"))
    ]
    print(f"comments since 2026-09-29: {len(cs)}; without claim/heartbeat/release markers: {len(substantive)}")
    missing = [d[0] for d in D if d[2] not in cs]
    if missing:
        sys.exit(f"comment ids not found: {missing}")
    rows = [(d, recorded(d[5])) for d in D]
    bad = [d[0] for d, ok in rows if d[5] is not None and not ok]
    if bad:
        sys.exit(f"record marker not found: {bad}")
    n = len(rows)
    rec = sum(ok for _, ok in rows)
    pending = [d[0] for d, ok in rows if not ok]
    by = {}
    for d, _ in rows:
        by[d[3]] = by.get(d[3], 0) + 1
    print(f"decisions made in comments: {n} (by {by})")
    print(f"recorded in a tracked file: {rec}/{n}; only in GitHub comments: {n - rec} {pending}")
    print(
        f"comment-only and pending with the issue body not updated: {sum(1 for d, ok in rows if not ok and d[6] is False)}"
    )
    print(f"scope changed by comment without editing DONE WHEN: {[d[0] for d, _ in rows if d[6] is False]}")
    od = owner_directives()
    print(f"issues whose Context opens with an owner directive: {len(od)} {od}")
    print(f"contradictions / reversals in the window: {len(C)}")
    for c in C:
        print("  ", c[0], c[1])


if __name__ == "__main__":
    main()
