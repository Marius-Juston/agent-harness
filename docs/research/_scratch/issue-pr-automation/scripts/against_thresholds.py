"""Break-even thresholds for the #346 candidates (r1-against). Pure arithmetic plus git counts; no network.

Inputs and where they come from:
- LEAD_TURN: mean modelled cost of a lead turn that issues a Bash call, $0.077 (command-sequences.md §1).
- PR_COST: $14.7-15.6 per merged PR, all roles (harness-efficiency.md §2); 15.0 used.
- WINDOW_SPEND: $2,252 modelled over the 10-day harness window (command-sequences.md §0).
- FIX_SHARE: share of non-merge commits on devkit/workflow.py with a fix/bug subject (surface_growth.py).
- Counts from command-sequences.md §2 and this note's gh queries (see r1-against.md).
Run from the repository root.
"""

import subprocess

LEAD_TURN = 0.077
PR_COST = 15.0
WINDOW_SPEND = 2252.0
FIX_SHARE = 6 / 27  # workflow.py, surface_growth.py


def git_count(*args):
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
    return len([line for line in out.splitlines() if line.strip()])


# 1. Cost of building one candidate: one PR plus expected follow-up fix PRs.
fix_per_feature = FIX_SHARE / (1 - FIX_SHARE)
build = PR_COST * (1 + fix_per_feature)
print(f"fix commits per feature commit on workflow.py: {fix_per_feature:.2f}")
print(f"build cost incl. expected fixes: ${build:.1f}")
for horizon in (1, 2, 3, 6):
    s = build / horizon
    print(f"  payback in {horizon} window(s) of 10 days needs >= ${s:.1f}/window ({s / WINDOW_SPEND:.2%} of spend)")

# 2. Measured savings ceilings (command-sequences.md counts x lead turn cost).
ceilings = {
    "(1) integrate retries, all 134 removed (upper bound)": 134 * LEAD_TURN,
    "(2) review relay n-gram, 10 x 3 lead calls": 10 * 3 * LEAD_TURN,
    "(9) claim --worktree, about 10 split dispatches": 0.85,  # command-sequences.md §3.4
    "(6) PR/issue body edit loops, 107 runs (all roles)": 6.0,  # command-sequences.md §3.4
}
for k, v in ceilings.items():
    print(f"ceiling {k}: ${v:.2f}/window")

# 3. Base rates for alarm-style candidates.
issues_total, dups = 235, 2  # gh issue list --state all; 'Duplicate of' closing comments (#10, #37)
base = dups / issues_total
print(f"\nduplicate base rate: {dups}/{issues_total} = {base:.2%}")
for recall, fpr in ((0.9, 0.01), (0.9, 0.02), (0.8, 0.05), (0.8, 0.10)):
    ppv = recall * base / (recall * base + fpr * (1 - base))
    alarms_per_100 = 100 * (recall * base + fpr * (1 - base))
    print(
        f"  recall {recall:.0%}, false-alarm rate {fpr:.0%} per filing: PPV {ppv:.0%}, "
        f"{alarms_per_100:.1f} alarms per 100 filings"
    )

integrates, conflicts = 365, 8  # command-sequences.md §2 integrate outcomes
print(f"\nintegrate conflict outcomes: {conflicts}/{integrates} = {conflicts / integrates:.1%}")
FIX_RESUME = 1.27  # builder fix resume, harness-efficiency.md §4
print(f"  ceiling if every conflict's fix resume were avoided: ${conflicts * FIX_RESUME:.2f}/window")

# 4. How often the hand-written artefacts change (agent-harness history).
print("\ncommits touching CHANGELOG.md:", git_count("log", "--format=%h", "--", "CHANGELOG.md"))
print("commits touching docs/HANDOFF.md:", git_count("log", "--format=%h", "--", "docs/HANDOFF.md"))
print("release tags:", len([t for t in subprocess.run(["git", "tag"], capture_output=True, text=True).stdout.split()]))
