"""r2-against checks: (1) trials per arm to detect a pass-rate drop; (2) Sonnet savings bound for review roles;
(3) 5m-vs-1h TTL for short review runs. Inputs are from cost_mix.py output (repo-E transcripts 2026-10-11..16)."""

from math import ceil, sqrt
from statistics import NormalDist

z = NormalDist().inv_cdf


def n_per_arm(p1, d, alpha=0.05, power=0.8):
    p2 = p1 - d
    return ceil((z(1 - alpha / 2) + z(power)) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / d**2)


for p1 in (0.5, 0.7, 0.9):
    print(
        f"base {p1:.0%}: n/arm to detect -3pp {n_per_arm(p1, 0.03)}, -10pp {n_per_arm(p1, 0.10)}, -20pp {n_per_arm(p1, 0.20)}"
    )
# MDE at n=10 and n=30 per arm, base 70%
for n in (10, 30, 100):
    d = (z(0.975) + z(0.8)) * sqrt(2 * 0.7 * 0.3 / n)
    print(f"n={n}/arm: minimum detectable drop at base 70% ~ {d:.0%}")
# (2) Sonnet bound: cache reads same price; everything else half.
roles = {
    "research-auditor": (77, 0.34),
    "security-reviewer": (49, 0.22),
    "reviewer": (28, 0.23),
}  # ($opus, cache-read share)
total = 1903
save = 0
for r, (c, rd) in roles.items():
    s = c * (1 - rd) * 0.5  # same tokens, best case
    save += s
    be = 1 / (rd + (1 - rd) * 0.5)  # token multiplier at which Sonnet costs the same
    print(f"{r}: same-token saving ${s:.0f} ({s / c:.0%}); break-even token multiplier {be:.2f}x")
print(f"all three: ${save:.0f} = {save / total:.1%} of transcript-priced spend; per merged PR (188) ${save / 188:.2f}")
# (3) 5m TTL for security-reviewer: writes 77% of $49 at $8/M -> $5/M; gaps>5m 63, assume avg context 30k rewritten at $5/M
w = 49 * 0.77
print(
    f"security-reviewer 5m TTL: write saving ${w * (1 - 5 / 8):.1f}, rewrite cost of 63 gaps x 30k x $5/M ${63 * 30e3 * 5 / 1e6:.1f}"
)
# (4) tool-output compaction arithmetic (Opus 5.5 list prices; chars/4 = tokens; r1-usage-logs §3-4 totals)
W, R = 8 / 1e6, 0.20 / 1e6


def kept_cost(tokens, later_turns):  # written once to cache, then read on every later turn
    return tokens * W + tokens * later_turns * R


for name, chars, calls in (("dev check", 0.56e6, 661), ("dev cite-check", 0.7e6, 560), ("dev lit", 10.5e6, 1696)):
    t = chars / 4
    print(
        f"{name}: {chars / calls:.0f} chars/call; whole output for the window kept 30 turns = ${kept_cost(t, 30):.0f}"
    )
extra_turn = 100e3 * R + 300 * 20 / 1e6  # resend ~100k cached context + ~300 output tokens
cut = kept_cost(1000, 30)
print(
    f"one extra researcher turn ${extra_turn:.3f}; cutting 1,000 tokens from one result saves ${cut:.3f}; "
    f"break-even = {cut / extra_turn:.2f} extra turns per 1,000 tokens cut"
)
