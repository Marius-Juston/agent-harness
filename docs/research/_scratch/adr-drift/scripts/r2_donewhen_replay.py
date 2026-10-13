"""R2b check 3: DONE WHEN state vs merged work, replayed with each issue's body as it was at each moment.

usage: python3 r2_donewhen_replay.py   (needs data/{ah,pb}_edits.json from r2_issue_edits.py)
3a "all boxes ticked, issue still open": at each first-parent commit, every open non-epic issue whose DONE WHEN boxes
   are all [x]. Prints each flagged issue, its flagged span and how it ended.
3b "a partial PR (Refs #n) says an item is met; the issue's box stays unticked": met items are parsed from the PR
   body (a `- [x]` line, or a line with met/MET that does not say "not met"), matched to the issue's box by word
   overlap (Jaccard >= 0.25, best match). The box state is read 1 h after the merge and at close/now.
"""

import bisect
import datetime
import json
import re

import r2_common as c


def ts(s):
    return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None


BOX = re.compile(r"^\s*- \[( |x|X)\] (.*)$", re.M)
WORD = re.compile(r"[a-z0-9_.-]{3,}")


def done_when(body):
    m = re.search(r"## DONE WHEN\s*\n(.*?)(\n## |\Z)", body or "", re.S)
    return [(a.lower() == "x", t.strip()) for a, t in BOX.findall(m.group(1))] if m else []


def words(s):
    return set(WORD.findall(s.lower()))


def jac(a, b):
    a, b = words(a), words(b)
    return len(a & b) / max(1, len(a | b))


for name, pre in (("AH", "ah"), ("PB", "pb")):
    hist = c.history(name)
    issues = {i["number"]: i for i in json.load(open(c.DATA / f"{pre}_issues.json"))}  # noqa: SIM115 - research script, kept as run
    edits = {int(k): v for k, v in json.load(open(c.DATA / f"{pre}_edits.json")).items()}  # noqa: SIM115 - research script, kept as run

    def body_at(n, t):
        e = edits.get(n, {}).get("edits") or []  # noqa: B023 - research script, kept as run
        if not e:
            return issues[n]["body"]  # noqa: B023 - research script, kept as run
        times = [ts(x[0]) for x in e]
        k = bisect.bisect_right(times, t) - 1
        return e[max(k, 0)][1]

    # 3a
    flagged = {}
    checks = 0
    for k, (sha, t, _) in enumerate(hist):  # noqa: B007 - research script, kept as run
        for n, i in issues.items():
            if i["title"].startswith("Epic") or ts(i["created_at"]) > t or (i["closed_at"] and ts(i["closed_at"]) <= t):
                continue
            dw = done_when(body_at(n, t))
            checks += 1
            if dw and all(x for x, _ in dw):
                flagged.setdefault(n, []).append(k)
    print(f"== {name} 3a: {checks} (open issue, commit) checks; {len(flagged)} issues flagged all-ticked-but-open")
    for n, ks in sorted(flagged.items()):
        i = issues[n]
        end = f"closed {i['closed_at'][:16]} ({edits.get(n, {}).get('stateReason')})" if i["closed_at"] else "OPEN"
        print(f"   #{n} commits {ks[0]}..{ks[-1]} ({len(ks)}) {end} | {i['title'][:70]}")

    # 3b
    prs = json.load(open(c.DATA / f"{pre}_prs.json"))  # noqa: SIM115 - research script, kept as run
    n_refs = n_parse = 0
    print(f"== {name} 3b: partial PRs")
    for p in prs:
        m = re.match(r"\s*Refs #(\d+)", p["body"])
        if not m or int(m.group(1)) not in issues:
            continue
        n_refs += 1
        n = int(m.group(1))
        tm = ts(p["merged_at"])
        met = [t for a, t in BOX.findall(p["body"]) if a.lower() == "x"]
        met += [
            l
            for l in p["body"].splitlines()  # noqa: E741 - research script, kept as run
            if re.search(r"\bmet\b", l, re.I)
            and not re.search(r"not met|unmet", l, re.I)
            and not l.lstrip().startswith("- [")
        ]
        if not met:
            print(f"   PR {p['number']} -> #{n}: no parseable met item")
            continue
        n_parse += 1
        before = done_when(body_at(n, tm))
        if not before:
            print(f"   PR {p['number']} -> #{n}: issue has no DONE WHEN boxes at merge")
            continue
        i = issues[n]
        t_end = ts(i["closed_at"]) - 1 if i["closed_at"] else 2**40
        after1, final = done_when(body_at(n, tm + 3600)), done_when(body_at(n, t_end))
        for item in met:
            j, best = max((jac(item, b), idx) for idx, (_, b) in enumerate(before))
            if j < 0.25:
                print(f"   PR {p['number']} -> #{n}: met item matched no box (best {j:.2f}): {item[:70]}")
                continue
            st = lambda dw: ("x" if dw[best][0] else " ") if best < len(dw) else "?"  # noqa: B023, E731 - research script, kept as run
            state = "OPEN" if not i["closed_at"] else f"closed {(edits.get(n, {}).get('stateReason') or '')}"
            print(
                f"   PR {p['number']} -> #{n} box{best + 1}/{len(before)} J={j:.2f} at-merge[{st(before)}] +1h[{st(after1)}] "
                f"at-close/now[{st(final)}] issue {state} | {before[best][1][:60]}"
            )
    print(f"   {name}: {n_refs} partial PRs; {n_parse} with a parseable met item")
