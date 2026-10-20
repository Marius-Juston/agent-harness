"""The shares and modelled savings quoted in command-sequences.md, recomputed from report.json (#321).

Usage: python3 savings.py report.json
"""

import json
import sys

with open(sys.argv[1]) as fh:
    r = json.load(fh)
d = r["detectors"]
turn = {k: v[0] for k, v in r["bash_turn_usd_mean_median"].items()}  # mean $ of a Bash call's turn, per role
total = r["modelled_usd"]["all"]["all"]
t = r["transcripts"]


def n(key):
    return d[key]["n"] if key in d else 0


print(f"window {r['window']}, transcripts {sum(v for k, v in t.items() if not k.startswith('_') for v in v.values())}")
print(f"turns {sum(t['_turns'].values())}, tool calls {sum(t['_calls'].values())}, Bash {sum(t['_bash'].values())}")
print(f"modelled spend ${total:,.0f}; mean Bash turn $ per role {turn}")

# 3.1 integrate waits for pending checks
waits = n("G checks wait + integrate in one call") + n("G checks read -> integrate in a later call (<=3)")
calls = n("D dev integrate: all")
split = n("G checks read -> integrate in a later call (<=3)")
pend = n("G integrate refused for unfinished feedback: pending check")
piped = n("D dev integrate | tail") + n("D dev integrate | grep")
save = (split + pend) * turn["lead"]
print(
    f"3.1 waits {waits}/{calls} = {waits / calls:.1%} of integrate calls; in one call {n('G checks wait + integrate in one call')}"
    f"/{waits} = {n('G checks wait + integrate in one call') / waits:.0%}; piped {piped}/{calls}; turns saved"
    f" {split}+{pend} = {split + pend}, ${save:.2f} = {save / total:.2%} of spend"
)

# 3.2 board new --help lookups
help_new = d["K dev board new --help: all"]
print(
    f"3.2 board new --help {help_new['n']} calls in {help_new['transcripts']} transcripts, turn $ {help_new['save_usd_upper']:.2f};"
    f" help->use {n('K dev --help -> same command (<=3 calls)')}"
)

# 3.3 GitHub REST API curl piped into python
gh = d["A json filter <- curl (GitHub REST API)"]
researchers = sum(v.get("researcher", 0) for k, v in t.items() if not k.startswith("_"))
print(
    f"3.3 GitHub API curl|python {gh['n']}, researcher {gh['roles'].get('researcher', 0)} over {researchers} researcher runs"
    f" = {gh['roles'].get('researcher', 0) / researchers * 100:.0f} per 100; errors {gh['err_rate'] * gh['n']:.0f}"
    f" ({gh['err_rate']:.1%}), error turns ${gh['err_rate'] * gh['n'] * turn['researcher']:.2f}"
)

# rejected: grep -n -> sed -n, and its one-call alternative
pairs, hit = d["C grep -n -> sed -n, same file"], d["C grep -n -> sed -n, same file, window holds a hit"]
neg, pos = (
    d["C one-call grep -C instead: net saving <= 0"],
    d["C one-call grep -C instead: net saving > 0"],
)
print(
    f"C grep->sed pairs {pairs['n']} (turn $ {pairs['save_usd_upper']:.0f} = {pairs['save_usd_upper'] / total:.1%}),"
    f" window holds a hit {hit['n']}; one-call -C loses in {neg['n']}/{hit['n']} = {neg['n'] / hit['n']:.0%}"
    f" (net ${neg['save_usd_upper']:.0f}), wins in {pos['n']} (net ${pos['save_usd_upper']:.0f})"
)
# rejected: dispatch prep, gh json filters, outline windows, body loops, persisted read-backs
one, sep = n("H worktree add + board claim in one call"), n("H worktree add + board claim in separate calls (<=3)")
print(f"H dispatch prep {one + sep}, in one call {one / (one + sep):.0%}; split turns ${sep * turn['lead']:.2f}")
print(
    f"A gh --json {n('A gh --json: all')}, with --jq {n('A gh --json with built-in --jq/-q')}"
    f" = {n('A gh --json with built-in --jq/-q') / n('A gh --json: all'):.0%}; json filters {n('A json filter: all')},"
    f" from curl {sum(v['n'] for k, v in d.items() if k.startswith('A json filter <- curl'))}"
)
o = n("B outline: all")
print(
    f"B outline {o}: -> sed -n same file {n('B outline -> sed -n window, same file')}"
    f" ({n('B outline -> sed -n window, same file') / o:.0%}), -> show {n('B outline -> dev code show')},"
    f" -> Read {n('B outline -> Read, same file')}"
)
j = d["J body read -> edit -> body write (<=4 calls)"]
print(
    f"J body loops {j['n']}, {j['calls_per']} calls, err {j['err_rate']:.1%}, ~${(j['calls_per'] - 1) * j['n'] * turn['builder']:.2f}"
)
rb = {k: v["n"] for k, v in d.items() if k.startswith("E persisted -> read back by")}
print(
    f"E persisted {n('E persisted output: all')}, read back {sum(rb.values())}, by Read {rb.get('E persisted -> read back by Read', 0)}"
)
