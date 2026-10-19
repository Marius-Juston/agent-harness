"""Relative cost of one subagent run on each 5.x model, holding its token mix fixed.

Prices per MTok from https://platform.claude.com/docs/en/about-claude/pricing (fetched 2026-10-19):
input, 1h cache write, cache read, output. The token mix is repo-E's Opus 5.5 cost share
(r1-usage-logs.md section 1: cache read 48 %, cache write 29 %, output 20 %, uncached input 3 %).
Same tokens on another model is an assumption: a model that takes more turns or thinks more costs more.
"""

PRICES = {  # input, 1h cache write, cache read, output  ($/MTok)
    "opus-5-5": (4, 8, 0.20, 20),
    "sonnet-5-5": (2, 4, 0.10, 10),
    "haiku-5-5 (<=100k prompt)": (0.10, 0.20, 0.01, 0.50),
    "haiku-5-5 (>100k prompt)": (0.50, 1.00, 0.05, 2.50),
    "fable-5-1": (10, 20, 0.25, 50),
    "opus-5-5 fast mode (in/out only)": (8, 16, 0.40, 40),  # ASSUMED: cache scales 2x like input
}
SHARE = {"input": 0.03, "write": 0.29, "read": 0.48, "output": 0.20}  # of Opus 5.5 cost

opus = PRICES["opus-5-5"]
# convert cost shares into token quantities at Opus prices, then re-price
tokens = {k: SHARE[k] / p for k, p in zip(("input", "write", "read", "output"), opus, strict=True)}
for name, p in PRICES.items():
    cost = sum(tokens[k] * price for k, price in zip(("input", "write", "read", "output"), p, strict=True))
    print(f"{name:34s} {cost:6.3f} x Opus 5.5")
# break-even: how many more total tokens can a cheaper model use and still cost no more?
for name in ("sonnet-5-5", "haiku-5-5 (<=100k prompt)", "haiku-5-5 (>100k prompt)"):
    p = PRICES[name]
    cost = sum(tokens[k] * price for k, price in zip(("input", "write", "read", "output"), p, strict=True))
    print(f"{name}: may use {1 / cost:5.1f}x the tokens before costing more than Opus 5.5")
