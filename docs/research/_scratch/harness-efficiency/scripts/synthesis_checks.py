"""Arithmetic behind the numbers the #254 synthesis (docs/research/harness-efficiency.md) states itself.

Usage: python3 synthesis_checks.py   (stdlib only; no transcripts needed)

Inputs are copied from the round files, each named next to its constant:
- list prices: platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-19 (1-hour cache writes, since every
  repo-E request wrote the 1-hour cache, r2-against.md section 7);
- window totals: scripts/cost_segments.py (cost records summed per process segment, and the transcript-priced
  estimate);
- modelled cost by agent type, output corrected: r2_cost_phases.py, rerun 2026-10-19 after the per-segment fix;
- review-loop costs: r2-review-convergence.md F4;
- fixed-component costs: r2-surface-audit.md F1 (scripts/component_cost.py).
"""

import math
from statistics import NormalDist

# $/M tokens: input, 1-hour cache write, cache read, output (pricing page, 2026-10-19)
PRICES = {
    "opus-5-5": (4.0, 8.0, 0.20, 20.0),
    "sonnet-5-5": (2.0, 4.0, 0.10, 10.0),
    "haiku-5-5 <=100k": (0.10, 0.20, 0.01, 0.50),
    "haiku-5-5 >100k": (0.50, 1.0, 0.05, 2.50),
    "fable-5-1": (10.0, 20.0, 0.25, 50.0),
}
KINDS = ("input", "write", "read", "output")

# modelled cost by agent type, $ (r2_cost_phases.py line "modelled cost all agents"; it has no uncached input)
MODELLED = {
    "researcher": 1223,
    "builder": 558,
    "lead": 384,
    "research-auditor": 100,
    "security-reviewer": 72,
    "general-purpose": 39,
    "reviewer": 39,
    "tester": 28,
}
TOTAL_MODELLED = 2444
LAST_RECORD = 2231  # the old total: each session's last cost record only
COST_RECORDS = 2756  # cost records summed per process segment (cost_segments.py), a lower bound
ESTIMATE = 2928  # cost_segments.py transcript-priced estimate, which also covers a process with no record
MERGED_PRS = 188  # repo-E only; agent-harness PRs worked from the same sessions are not in the denominator


def ratio(model: str, share: dict[str, float]) -> float:
    """Cost of an Opus 5.5 token mix (cost shares) re-priced on another model, as a multiple of Opus."""
    opus = dict(zip(KINDS, PRICES["opus-5-5"], strict=True))
    other = dict(zip(KINDS, PRICES[model], strict=True))
    return sum(share[k] / opus[k] * other[k] for k in KINDS)


def n_per_arm(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Two-sided two-proportion test, normal approximation."""
    za, zb = NormalDist().inv_cdf(1 - alpha / 2), NormalDist().inv_cdf(power)
    pbar = (p1 + p2) / 2
    num = za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil((num / (p1 - p2)) ** 2)


def min_detectable(p1: float, n: int) -> float:
    """Smallest drop from p1 that n trials per arm detect at alpha 0.05, power 0.8 (search in 0.5-point steps)."""
    d = 0.005
    while n_per_arm(p1, p1 - d) > n:
        d += 0.005
    return d


print("1. Sonnet 5.5 vs Opus 5.5 price per token kind:")
for k, o, s in zip(KINDS, PRICES["opus-5-5"], PRICES["sonnet-5-5"], strict=True):
    print(f"   {k:6s} {s / o:.2f}x")
mixes = {
    "window (cost records per segment: read 42, write 28, output 20, input 10 %)": {
        "input": 0.10,
        "write": 0.28,
        "read": 0.42,
        "output": 0.20,
    },
    "researcher (r2 rerun: read 42, write 32, output 27 %)": {
        "input": 0.0,
        "write": 0.32,
        "read": 0.42,
        "output": 0.27,
    },
    "lead (r2 rerun: read 62, write 23, output 15 %)": {"input": 0.0, "write": 0.23, "read": 0.62, "output": 0.15},
}
for name, mix in mixes.items():
    print(f"   {name}: " + ", ".join(f"{m} {ratio(m, mix):.3f}x" for m in PRICES if m != "opus-5-5"))

print(
    f"\n0. Window: ${LAST_RECORD} last records, ${COST_RECORDS} per segment, ${ESTIMATE} estimated; per merged PR "
    f"${COST_RECORDS / MERGED_PRS:.1f}-{ESTIMATE / MERGED_PRS:.1f}"
)
print("\n2. Spend by agent type, output corrected (share of the $2,444 modelled):")
for at, c in MODELLED.items():
    print(f"   {at:18s} ${c:5d} {c / TOTAL_MODELLED:5.1%}")
review = sum(MODELLED[a] for a in ("research-auditor", "security-reviewer", "reviewer"))
print(f"   three review roles ${review} = {review / TOTAL_MODELLED:.1%}")

print("\n3. Same-token Sonnet 5.5 savings at the live price (every kind is 0.5x):")
guard = MODELLED["security-reviewer"] + MODELLED["research-auditor"]
for name, c in (
    ("review roles", review),
    ("security-reviewer + research-auditor", guard),
    ("researcher", MODELLED["researcher"]),
):
    s = c * (1 - ratio("sonnet-5-5", mixes["researcher (r2 rerun: read 42, write 32, output 27 %)"]))
    print(f"   {name}: ${s:.0f} = {s / TOTAL_MODELLED:.1%} of spend, ${s / MERGED_PRS:.2f} per merged PR")
print("   break-even: Sonnet may use 2.0x the tokens before it costs more")

print("\n4. Fixed components and lead compaction, as shares of the corrected total (estimate to records):")
lead_artifact = 3251 * 12_900 * 0.20 / 1e6  # 3,251 lead calls re-reading the 12.9k-token Artifact schema
for name, lo, hi in (
    ("Artifact + Agent schemas in workers (component_cost.py; Skill kept)", 74 + 2.5, 116 + 4),
    ("  plus Artifact in lead calls (enableArtifact: false)", 74 + 2.5 + lead_artifact, 116 + 4 + lead_artifact),
    ("lead compaction at 150-400k (r2_cost_phases.py F)", 384 - 226, 384 - 206),
):
    print(f"   {name}: ${lo:.0f}-{hi:.0f} = {lo / ESTIMATE:.1%}-{hi / COST_RECORDS:.1%}")

print("\n5. Review loop (r2-review-convergence F4):")
rounds, fixes, lead = 167, 281, 49
print(
    f"   reviews ${rounds}, fix resumes ${fixes}, lead ${lead}: ${rounds + fixes + lead} = "
    f"{(rounds + fixes + lead) / ESTIMATE:.1%}-{(rounds + fixes + lead) / COST_RECORDS:.1%} (a lower bound)"
)
extra_round = 0.36 + 1.27 + 2 * 0.13
print(f"   one extra review-and-fix round ${extra_round:.2f}; pre-PR self-review at $0.72 breaks even when it saves")
print(f"   a round on {0.72 / extra_round:.0%} of PRs")

print("\n6. Power of a two-arm pass-rate eval, base 70 %, alpha 0.05, power 0.8:")
for drop in (0.03, 0.10, 0.20):
    print(f"   detect -{drop * 100:.0f} points: {n_per_arm(0.70, 0.70 - drop)} trials per arm")
for n in (10, 30, 100):
    print(f"   with {n} trials per arm the smallest detectable drop is {min_detectable(0.70, n) * 100:.1f} points")
