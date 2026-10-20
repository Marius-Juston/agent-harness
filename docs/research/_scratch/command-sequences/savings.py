"""The shares and modelled savings quoted in command-sequences.md, recomputed from sequences.py reports (#321).

Usage: python3 savings.py report-harness.json [report.json]
The first report is the harness-run repositories (repo-E and H); the optional second one, every project, adds the
non-harness baseline (L1). Units: n counts occurrences of a sequence; detector A counts Bash calls.
"""

import json
import sys

with open(sys.argv[1]) as fh:
    r = json.load(fh)
d = r["detectors"]
t = r["transcripts"]
turn = {k: v[0] for k, v in r["bash_turn_usd_mean_median"].items()}  # mean $ of a Bash call's turn, per role
total = r["modelled_usd"]["all"]["all"]
runs = {}
for k, v in t.items():
    if not k.startswith("_"):
        for role, x in v.items():
            runs[role] = runs.get(role, 0) + x


def n(key):
    return d[key]["n"] if key in d else 0


def proj(key, p):
    return d[key]["projects"].get(p, 0) if key in d else 0


print(f"window {r['window']}, transcripts {sum(runs.values())} {runs}")
print(f"turns {sum(t['_turns'].values())}, tool calls {sum(t['_calls'].values())}, Bash {sum(t['_bash'].values())}")
print(f"modelled spend ${total:,.0f}; mean Bash turn $ per role {turn}")

# 3.1 integrate waits for pending checks
one = n("G checks wait + integrate in one call")
split = n("G checks read -> integrate in a later call (<=3)")
calls = n("D dev integrate: all")
pend = n("G integrate refused for unfinished feedback: pending check")
piped = n("D dev integrate | tail") + n("D dev integrate | grep")
s31 = (split + pend) * turn["lead"]
print(
    f"3.1 waits {one + split}/{calls} = {(one + split) / calls:.1%}; one call {one}/{one + split} = {one / (one + split):.0%};"
    f" piped {piped}/{calls}; turns saved {split}+{pend} = {split + pend}, ${s31:.2f} = {s31 / total:.2%}"
)

# 3.2 board new --help lookups
hn = d["K dev board new --help: all"]
s32 = hn["save_usd_upper"]
print(
    f"3.2 board new --help {hn['n']} calls in {hn['transcripts']}/{sum(runs.values())} transcripts"
    f" = {hn['transcripts'] / sum(runs.values()):.0%}, turn ${s32:.2f}; help->use"
    f" {n('K dev --help -> same command (<=3 calls)')}"
)

# 3.3 GitHub REST API curl piped into python, counted once per Bash call
g = d["A json filter (python -c json<stdin) <- curl (GitHub REST API)"]
gr = g["roles"].get("researcher", 0)
gerr = round(g["err_rate"] * g["n"])
s33 = gerr * turn["researcher"]
allgh = n("A curl to the GitHub REST API: all calls")
rl = n("A curl to the GitHub REST API: rate-limit message or 403 in output")
print(
    f"3.3 GitHub API curl|python {g['n']} calls, researcher {gr} over {runs['researcher']} runs"
    f" = {gr / runs['researcher'] * 100:.0f} per 100; failing {gerr} ({g['err_rate']:.1%}), ${s33:.2f};"
    f" GitHub API curls {allgh}, rate-limit message or 403 in output {rl}"
)
print(f"savings 3.1-3.3 ${s31 + s32 + s33:.2f} = {(s31 + s32 + s33) / total:.2%} of spend")

# rejected: grep -n -> sed -n and its one-call alternative
pairs, hit = d["C grep -n -> sed -n, same file"], d["C grep -n -> sed -n, same file, window holds a hit"]
neg = d["C one-call grep -C instead: net saving <= 0"]
pos = d["C one-call grep -C instead: net saving > 0"]
long_ = n("C grep -n -> sed -n, same file, grep output over 800 chars")
print(
    f"C pairs {pairs['n']} (turn ${pairs['save_usd_upper']:.0f} = {pairs['save_usd_upper'] / total:.1%});"
    f" grep output >800 chars {long_}; window holds a parsed hit {hit['n']}; -C loses {neg['n']}/{hit['n']}"
    f" = {neg['n'] / hit['n']:.0%} (repo-E {proj('C one-call grep -C instead: net saving <= 0', 'repo-E')}/"
    f"{proj('C grep -n -> sed -n, same file, window holds a hit', 'repo-E')}); wins {pos['n']},"
    f" at most ${pos['save_usd_upper']:.0f}"
)
o1, o2 = n("H worktree add + board claim in one call"), n("H worktree add + board claim in separate calls (<=3)")
print(f"H dispatch prep {o1 + o2}, one call {o1 / (o1 + o2):.0%}; split {o2}, ${o2 * turn['lead']:.2f}")
jq = n("A gh --json with built-in --jq/-q")
fil = [(k, v) for k, v in d.items() if k.startswith("A json filter (") and "<- " in k]
print(
    f"A gh --json {n('A gh --json: all')}, --jq {jq} = {jq / n('A gh --json: all'):.0%}; json filter calls"
    f" {n('A json filter: all calls')} (python {n('A json filter (python -c json<stdin): all calls')},"
    f" jq {n('A json filter (jq): all calls')}); from curl {sum(v['n'] for k, v in fil if '<- curl' in k)},"
    f" from gh {sum(v['n'] for k, v in fil if '<- gh' in k)}"
)
for k, v in sorted(fil, key=lambda kv: -kv[1]["n"]):
    if "<- curl" in k:
        print(f"   {v['n']:>4} err {v['err_rate']:.1%} {dict(list(v['roles'].items())[:3])} {k}")
o = n("B outline: all")
print(f"B outline {o}: sed -n {n('B outline -> sed -n window, same file')}, show {n('B outline -> dev code show')}")
j = d["J body read -> edit -> body write (<=4 calls)"]
print(
    f"J body loops {j['n']}, {j['calls_per']} calls, err {j['err_rate']:.1%},"
    f" ~${(j['calls_per'] - 1) * j['n'] * turn['builder']:.2f}"
)
rb = {k: v["n"] for k, v in d.items() if k.startswith("E persisted -> read back by")}
print(
    f"E persisted {n('E persisted output: all')}, read back {sum(rb.values())},"
    f" by Read {rb.get('E persisted -> read back by Read', 0)}"
)
print(
    f"F bare sleep {n('F bare sleep')} err {d['F bare sleep']['err_rate']:.1%}; dev pr | tail {n('D dev pr | tail')}"
    f" err {d['D dev pr | tail']['err_rate']:.0%}"
)
print(
    f"pytest direct repo-E {proj('D pytest direct (not dev test)', 'repo-E')} vs dev test {proj('D dev test: all', 'repo-E')}"
)
for role in ("lead", "builder", "researcher", "reviewer"):
    top = r["ngrams_top_by_score_per_role"][role][0]
    print(f"top n-gram {role}: {top['seq']} n = {top['n']}")

if len(sys.argv) > 2:  # the non-harness baseline
    with open(sys.argv[2]) as fh:
        a = json.load(fh)
    da = a["detectors"]
    print(
        f"baseline L1: spend ${sum(a['modelled_usd']['L1'].values()):,.0f}, transcripts"
        f" {sum(a['transcripts']['L1'].values())}, dev steps {sum(1 for k in da if k.startswith('D dev') and da[k]['projects'].get('L1'))},"
        f" pytest direct {da['D pytest direct (not dev test)']['projects'].get('L1', 0)},"
        f" grep->sed pairs {da['C grep -n -> sed -n, same file']['projects'].get('L1', 0)}"
    )
