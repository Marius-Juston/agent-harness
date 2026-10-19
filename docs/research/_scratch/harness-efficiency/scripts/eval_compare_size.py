"""How many scenarios `dev eval --compare` needs before two tied arms can be called non-inferior (#82, note §10).

Usage: uv run python docs/research/_scratch/harness-efficiency/scripts/eval_compare_size.py
Both arms get the same per-scenario result (3/3 or 2/3, 3 trials), so the true difference is 0. The script prints the
one-sided 95 % lower bound of devkit.evals.compare (scenario bootstrap with Jeffreys draws, seed 82, 10,000 draws)
for each number of scenarios, and whether it clears the default margin of 0.05.
"""

from devkit import evals


def record(n: int, passes: int, trials: int = 3) -> dict:
    return {"results": {f"s{i}": {"passes": passes, "trials": trials, "cost_usd": 1.0} for i in range(n)}}


if __name__ == "__main__":
    print("passes/3  scenarios  lower bound  non-inferior at 0.05")
    for passes in (3, 2):
        for n in (10, 20, 30, 40, 50, 60, 80, 100):
            c = evals.compare(record(n, passes), record(n, passes))
            print(f"{passes:>8}  {n:>9}  {c['pass_lower']:>+11.3f}  {c['verdict'].startswith('non-inferior')}")
