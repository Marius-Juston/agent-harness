"""Stage-0 arithmetic for #262: per-arm cost by token kind, arm ratios, stage 1 re-priced at the pilot's per-run cost,
and what the decision rule can tell apart in designs that fit the owner's remaining budget.

Run from the repository root: `uv run python -I docs/research/_scratch/researcher-effort/scripts/pilot_arith.py`.
Inputs: `pilot/<arm>/rescored.json` (summarize.py on the owner-run transcripts) and `[tool.devkit.prices]`.
n = 1 trial per arm on one question: every ratio here is one draw, not an estimate of an arm's mean.
"""

import json
import random
import re
import sys
from pathlib import Path

from devkit import config

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import power_and_cost as pc  # noqa: E402 - sibling script, needs the path above

PILOT = HERE.parent / "pilot"
BUDGET_TOTAL, SPENT = 25.00, 2.66  # owner, 2026-11-20: $25 for #262 and #590 together
WEB_SEARCH_USD = 10 / 1000  # Claude API web search, "$10 USD per 1,000 searches" (Pricing page, fetched 2026-11-20)
AUDIT = (0.93, 1.48)  # $ per research-auditor run (power_and_cost.py AUDITOR, SOURCED there)


def by_kind() -> dict:
    prices = config.get().prices
    out = {}
    for arm in "ABC":
        s = json.loads((PILOT / arm / "rescored.json").read_text())
        rows = {}
        for model, u in s["by_model"].items():
            p = prices.get(re.sub(r"-\d{8}$", "", model))
            if not p:
                continue
            rows[model] = {
                "input": u["input"] * p["input"] / 1e6,
                "cache_write_5m": u["cache_write"] * p["cache_write_5m"] / 1e6,
                "extra_if_1h": u["cache_write"] * (p["cache_write_1h"] - p["cache_write_5m"]) / 1e6,
                "cache_read": u["cache_read"] * p["cache_read"] / 1e6,
                "output": u["output"] * p["output"] / 1e6,
                "reported": u["cost_usd_reported"],
            }
        out[arm] = {"cost": s["cost_usd"], "turns": s["num_turns"], "wall_s": s["wall_s"], "models": rows}
    return out


def main() -> None:
    arms = by_kind()
    print("per arm (n = 1 each, Q06): $ total, turns, wall s; priced by kind at [tool.devkit.prices]")
    for arm, a in arms.items():
        print(f"  {arm}: ${a['cost']:.4f}  turns {a['turns']}  wall {a['wall_s']} s")
        for m, r in a["models"].items():
            parts = "  ".join(f"{k} ${v:.4f}" for k, v in r.items() if k != "extra_if_1h")
            print(f"     {m}: {parts}")
    a, b, c = (arms[x]["cost"] for x in "ABC")
    print(f"\nratios: B/A {b / a:.3f}  C/A {c / a:.3f}  C/B {c / b:.3f}")
    opus = {x: arms[x]["models"]["claude-opus-5-5"]["reported"] for x in "ABC"}
    print(
        f"Opus-only $: A {opus['A']:.4f}  B {opus['B']:.4f}  C {opus['C']:.4f} (C's Opus / B's Opus {opus['C'] / opus['B']:.3f})"
    )
    print(f"pilot total ${a + b + c:.4f}; remaining of ${BUDGET_TOTAL:.2f}: ${BUDGET_TOTAL - SPENT:.2f}")

    print("\ncache writes re-priced at 1-hour rates (the trials wrote only 5-minute cache; production writes 1-hour):")
    one_h = {}
    for arm, x in arms.items():
        extra = sum(r["extra_if_1h"] for r in x["models"].values())
        one_h[arm] = x["cost"] + extra
        print(f"  {arm}: ${x['cost']:.4f} -> ${one_h[arm]:.4f} (+{extra / x['cost']:.1%})")
    print(f"  B/A at 1-hour rates {one_h['B'] / one_h['A']:.3f}")

    refetch = PILOT / "refetch.json"
    if refetch.is_file():
        searches = {k: v["web_search_calls"] for k, v in json.loads(refetch.read_text()).items()}
        print("\nHaiku reported minus Haiku by kind, against WebSearch calls x $10 per 1,000 (refetch.json):")
        for arm, x in arms.items():
            h = next((r for m, r in x["models"].items() if "haiku" in m), None)
            if h and arm in searches:
                gap = h["reported"] - h["input"] - h["cache_write_5m"] - h["cache_read"] - h["output"]
                fee = searches[arm] * WEB_SEARCH_USD
                print(f"  {arm}: gap ${gap:.4f}; {searches[arm]} searches = ${fee:.4f}")

    print("\nstage 1 as pre-registered (10 q x 3 trials), re-priced at the pilot's per-run $ (one draw per arm):")
    for lo_hi, aud in zip(("low", "high"), AUDIT, strict=True):
        three = 30 * (a + b + c) + 90 * aud
        two = 30 * (a + b) + 60 * aud
        print(f"  auditor ${aud:.2f} ({lo_hi}): arms A, B, C ${three:,.0f}; arms A, B ${two:,.0f}")
    cap = {x: 2 * arms[x]["cost"] for x in "ABC"}
    print(f"  per-run caps (PROTOCOL.md §6: 2x stage 0's run): A ${cap['A']:.2f}  B ${cap['B']:.2f}  C ${cap['C']:.2f}")

    print("\nwhat fits the remaining budget, arms A and B, one audit per trial:")
    for q, t in ((3, 1), (5, 1), (10, 1)):
        spend = q * t * (a + b) + 2 * q * t * AUDIT[1]
        print(f"  {q} q x {t} trial: ${q * t * (a + b) + 2 * q * t * AUDIT[0]:.2f}-{spend:.2f}")
    audit_pilot = (3 * AUDIT[0], 3 * AUDIT[1])
    print(f"  audit the 3 pilot answers only: ${audit_pilot[0]:.2f}-{audit_pilot[1]:.2f}")

    print("\npower of the 5-point point rule in budget-sized designs (power_and_cost.simulate, seed 262, 400 sims)")
    print("  q  trials  p_base  true B-A | P(B kept)")
    rng = random.Random(262)  # noqa: S311 - a seeded simulation, not a secret
    for q, t in ((3, 1), (5, 1), (10, 3)):
        pc.QUESTIONS, pc.TRIALS = q, t
        for p_base in (0.75, 0.85):
            for delta in (0.0, -0.10):
                kept = sum(pc.simulate(p_base, delta, 0.08, 0.08, rng)[0] > -pc.MARGIN for _ in range(400)) / 400
                print(f"  {q:>2}  {t}       {p_base:.2f}    {delta:+.2f}    | {kept:.2f}")


if __name__ == "__main__":
    main()
