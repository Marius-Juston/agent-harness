"""Pre-registration arithmetic for #262: what the full experiment costs, and what its decision rule can tell apart.

Run: `python3 -I docs/research/_scratch/researcher-effort/scripts/power_and_cost.py` (stdlib only, seeded).

Every input is printed. Cost inputs are SOURCED from r2-research-and-lead-cost.md (repo-E, 2026-10-11..16) and from
`uv run dev agent-stats --cost` on this machine (2026-11-19); accuracy inputs are ASSUMED (no audited-claim rate per
note has been recorded in a machine-readable form yet), so the power table is a range over them, not a point.
"""

import random
import statistics

# --- cost of the full design -------------------------------------------------------------------------------------
QUESTIONS, ARMS, TRIALS = 10, 3, 3
RUNS = QUESTIONS * ARMS * TRIALS
# $ per researcher run: repo-E round-1 median, round-2 median, all-researcher mean (r2-research-and-lead-cost.md §1).
RESEARCHER = {"r1 median": 2.84, "r2 median": 3.72, "all-run mean": 4.47}
# $ per research-auditor run: repo-E below/above median note size (same file §1.6); this machine's one run.
AUDITOR = {"local (1 run)": 0.93, "repo-E": 1.48}


def cost_table() -> None:
    print(
        f"full design: {QUESTIONS} questions x {ARMS} arms x {TRIALS} trials = {RUNS} researcher runs + {RUNS} audits"
    )
    for rk, r in RESEARCHER.items():
        for ak, a in AUDITOR.items():
            print(f"  researcher {rk:>12} ${r:.2f} + auditor {ak:>13} ${a:.2f}: ${RUNS * (r + a):,.0f}")
    two = QUESTIONS * 2 * TRIALS
    print(
        f"two arms only (A, B): {two} runs + {two} audits: ${two * (2.84 + 0.93):,.0f} to ${two * (4.47 + 1.48):,.0f}"
    )


# --- power of the decision rule --------------------------------------------------------------------------------
CLAIMS = 10  # sampled per trial output by the auditor
MARGIN = 0.05


def simulate(p_base: float, delta: float, q_sd: float, t_sd: float, rng: random.Random) -> tuple[float, float]:
    """One experiment: per-question base accuracy, per-(arm, trial) noise, binomial claims. Returns the B-A point
    estimate and a one-sided 95 % lower bound from a question bootstrap of the per-question mean differences."""
    diffs = []
    for _ in range(QUESTIONS):
        pq = min(0.99, max(0.01, rng.gauss(p_base, q_sd)))
        arm_means = []
        for d in (0.0, delta):
            accs = []
            for _ in range(TRIALS):
                p = min(0.99, max(0.01, rng.gauss(pq + d, t_sd)))
                accs.append(sum(rng.random() < p for _ in range(CLAIMS)) / CLAIMS)
            arm_means.append(statistics.fmean(accs))
        diffs.append(arm_means[1] - arm_means[0])
    point = statistics.fmean(diffs)
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(400))
    return point, boots[int(0.05 * len(boots))]


def power_table(sims: int = 400) -> None:
    rng = random.Random(262)  # noqa: S311 - a seeded simulation, not a secret
    print(
        f"\npower: {QUESTIONS} questions, {TRIALS} trials/arm, {CLAIMS} claims/trial, margin {MARGIN:.2f}, {sims} sims"
    )
    print("  p_base  q_sd  t_sd  true B-A | P(point > -margin)  P(lower bound > -margin)  median bound width")
    for p_base in (0.75, 0.85):
        for t_sd in (0.0, 0.08):
            for delta in (0.0, -0.05, -0.10):
                pts, lows = [], []
                for _ in range(sims):
                    pt, lo = simulate(p_base, delta, 0.08, t_sd, rng)
                    pts.append(pt)
                    lows.append(lo)
                p_point = sum(p > -MARGIN for p in pts) / sims
                p_low = sum(lo > -MARGIN for lo in lows) / sims
                width = statistics.median(p - lo for p, lo in zip(pts, lows, strict=True))
                print(
                    f"  {p_base:.2f}    0.08  {t_sd:.2f}  {delta:+.2f}    | {p_point:>8.2f}"
                    f"            {p_low:>8.2f}                 {width:.3f}"
                )


def savings() -> None:
    """What the vendor's cost ratios would save on the researcher line of the measured window (harness-efficiency.md:
    researchers $1,157 of $2,314 modelled), if quality and turns held. Ratios: Optimizing for cost and intelligence,
    footnotes 3 and 8, fetched 2026-11-19."""
    role, total = 1157, 2314
    print("\nresearcher line at vendor cost ratios (same tasks, quality not modelled):")
    for name, ratio in (
        ("medium vs high, SWE-bench Pro (about 70 % of the cost)", 0.70),
        ("medium vs high, defect search (15 % less)", 0.85),
        ("Opus high orchestrator + Sonnet subagents vs Opus medium alone, defect search ($139 vs $200)", 139 / 200),
        ("Opus high orchestrator + Sonnet subagents vs Opus high alone, defect search ($139 vs $236)", 139 / 236),
    ):
        saved = role * (1 - ratio)
        print(f"  {name}: ratio {ratio:.3f}, saves ${saved:,.0f} ({saved / total:.1%} of all spend)")


if __name__ == "__main__":
    cost_table()
    power_table()
    savings()
