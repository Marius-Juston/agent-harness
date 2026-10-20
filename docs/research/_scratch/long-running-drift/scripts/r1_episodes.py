"""Group r1_claims_replay.py events into drift episodes (doc, break commit) and attribute each fix. Read-only.

usage: python3 -I r1_episodes.py REPO REF OUT.json EPISODES.jsonl
An episode = every BREAK of one doc at one first-parent commit. Fix = the latest of its claims' FIX events (or OPEN at
HEAD, censored). The fixing change is the newest non-merge commit in (fix^1, fix] that touched the doc; agent = its
author is Claude or its message has a Claude Co-Authored-By trailer. `ctx` holds the doc lines that carry the claims
at the break commit (private text: the output stays in .scratch/, never in a committed file).
"""

import collections
import hashlib
import json
import subprocess
import sys

repo, ref, src, dst = sys.argv[1:5]
d = json.load(open(src))  # noqa: SIM115 - research script


def git(*a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout


full = {}
for ln in git("log", "--first-parent", "--format=%H", ref).splitlines():
    full[ln[:10]] = ln
eps = collections.defaultdict(list)
for e in d["events"]:
    if e["ev"] in ("FIX", "OPEN"):
        eps[(e["doc"], e["idx"])].append(e)
out = []
for (doc, idx), es in sorted(eps.items(), key=lambda kv: (kv[0][1], kv[0][0])):
    e0 = es[0]
    opened = [e for e in es if e["ev"] == "OPEN"]
    fixed = [e for e in es if e["ev"] == "FIX"]
    fix = max(fixed, key=lambda e: e["fix_idx"]) if fixed and not opened else None
    rec = dict(
        repo=d["label"],
        key=f"{idx}-{hashlib.sha1(doc.encode()).hexdigest()[:8]}",  # noqa: S324 - research script
        doc=doc,
        dtype=e0["dtype"],
        idx=idx,
        sha=e0["sha"],
        ts=e0["ts"],
        subj=e0["subj"],
        born_ts=min(e["born_ts"] for e in es),
        toks=sorted({f"{e['kind']}:{e['tok']}" for e in es}),
        hows=sorted({e.get("how", "OPEN") for e in es}),
        n=len(es),
    )
    if fix:
        rec.update(
            fix_idx=fix["fix_idx"],
            fix_ts=fix["fix_ts"],
            fix_subj=fix["fix_subj"],
            latency_d=(fix["fix_ts"] - e0["ts"]) / 86400,
            latency_merges=fix["fix_idx"] - idx,
        )
        fsha = full[fix["fix_sha"]]
        commits = git("log", "--format=%H%x09%an%x09%s", f"{fsha}^1..{fsha}", "--", doc).splitlines() or [
            git("log", "-1", "--format=%H%x09%an%x09%s", fsha)
        ]
        h, an, s = commits[0].split("\t", 2)
        body = git("log", "-1", "--format=%B", h)
        rec.update(
            fix_commit=h[:10],
            fix_author=an,
            fix_commit_subj=s[:120],
            fix_agent=an.lower().startswith("claude") or "co-authored-by: claude" in body.lower(),
        )
    else:
        rec.update(open_d=(d["last_ts"] - e0["ts"]) / 86400, open_merges=d["n_commits"] - 1 - idx)
    text = git("show", f"{full[e0['sha']]}:{doc}")
    toks = {e["tok"] for e in es}
    rec["ctx"] = [ln.strip()[:240] for ln in text.splitlines() if any(t in ln for t in toks)][:6]
    out.append(rec)
with open(dst, "w") as f:
    for r in out:
        f.write(json.dumps(r) + "\n")
print(
    d["label"], "episodes", len(out), "fixed", sum("fix_idx" in r for r in out), "open", sum("open_d" in r for r in out)
)
