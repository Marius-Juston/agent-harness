"""Fix-now vs. defer (#149): recompute cited ratios and the break-even fix size.

Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/breakeven.py`.

Costs are in agent tool calls. Inputs other than O are ASSUMED.
fix now : f * (1 + rho)                      rho = added review/tangle cost per unit of fix
defer   : F + p * (f + O) + (1 - p) * L      F = filing cost, p = P(follow-up ever done),
                                             O = fresh-worker overhead, L = loss if never done
break-even f* solves f(1+rho) = F + p(f+O) + (1-p)L  ->  f* = (F + pO + (1-p)L) / (1 + rho - p)
"""

# cited ratios
print("agentic PR merge rate 475/567 =", round(475 / 567 * 100, 2), "%")
print("human PR merge rate 516/567 =", round(516 / 567 * 100, 2), "%")
print("SATD-I mean/median repay time 872.3/25.0 =", round(872.3 / 25.0, 1), "x")

O = 42  # median tool calls before first edit per builder run, repository A (agent-harness note, section 6)
F = 3  # ASSUMED: calls to file one issue with a DONE WHEN
for rho in (0.0, 0.25, 0.5):
    for p in (0.26, 0.3, 0.6, 1.0):  # 0.26: measured share of follow-ups >= 7 days old that closed (126/493)
        L = 0  # ignore lost value: the most defer-friendly case
        denom = 1 + rho - p
        if denom <= 0:
            print(f"rho={rho:<4} p={p:<4} L=0  -> fix now always wins")
            continue
        f_star = (F + p * O + (1 - p) * L) / denom
        print(f"rho={rho:<4} p={p:<4} L=0  -> fix now if fix < {f_star:6.1f} tool calls")
