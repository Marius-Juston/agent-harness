"""R2b check 1: replay the proposed `dev` command check (names, subcommands, flags, positionals; `uv run dev X` and
bare `dev X` spans) over every first-parent commit of agent-harness and project B, each against its own parser, and
over every issue body against the parser of the day it was filed. Read-only.

usage: python3 r2_cli_replay.py            -> .scratch/r2/cli_repo.json, .scratch/r2/cli_issues.json, summary
Parser per commit: the commit's own devkit/ if it has one, else agent-harness at the tag pinned in pyproject.toml.
"""

import bisect
import collections
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import r2_common as c

PY = str(c.AH / ".venv/bin/python")
WORKER = str(Path(__file__).with_name("r2_cli_worker.py"))
EXT = (".md", ".jinja", ".sh", ".yml", ".yaml", ".json", ".toml", ".py")
GATE_EXT = (".md", ".sh", ".yml", ".json", ".jinja")  # what check_dev_commands reads today


def devroot(name, sha):
    t = c.tree(name, sha)
    if (t / "devkit/cli.py").exists():
        return t
    py = t / "pyproject.toml"
    m = py.exists() and re.search(r"agent-harness@(v[\d.]+)", py.read_text())
    if not m:
        return None  # no devkit at all (agent-harness's initial commit)
    pin = m.group(1)
    sha_ah = c.git(c.AH, "rev-list", "-n1", pin).strip()
    return c.tree("AHTAG", sha_ah)


def run(root, docs, key):
    inp, out = c.SCR / f"in_{key}.json", c.SCR / f"out_{key}.json"
    json.dump(docs, open(inp, "w"))  # noqa: SIM115 - research script, kept as run
    subprocess.run([PY, "-I", WORKER, str(root), str(inp), str(out)], check=True)
    return json.load(open(out))


def skip(f):
    return (
        f.startswith(("docs/generated/", "tests/", "template/tests/"))
        or "/_scratch/" in f
        or f.endswith(("INDEX.md", "uv.lock"))
        or not f.endswith(EXT)
    )


def repo_pass():
    rows = []
    for name in c.REPOS:
        for i, (sha, ts, subj) in enumerate(c.history(name)):  # noqa: B007 - research script, kept as run
            t = c.tree(name, sha)
            docs = [
                [f, (t / f).read_text(errors="replace")]
                for f in c.files(name, sha)
                if not skip(f) and (t / f).is_file()
            ]
            root = devroot(name, sha)
            if root is None:
                continue
            r = run(root, docs, f"{name}")
            for x in r["findings"]:
                rows.append(dict(repo=name, idx=i, sha=sha[:9], ts=ts, **x))
            print(name, i, sha[:9], r["hits"], "hits", len(r["findings"]), "findings", file=sys.stderr)
    return rows


def issue_pass():
    rows = []
    for name, pre in (("AH", "ah"), ("PB", "pb")):
        hist = c.history(name)
        times = [h[1] for h in hist]
        for iss in json.load(open(c.DATA / f"{pre}_issues.json")):  # noqa: SIM115 - research script, kept as run
            t0 = int(datetime.datetime.fromisoformat(iss["created_at"].replace("Z", "+00:00")).timestamp())
            k = max(0, bisect.bisect_right(times, t0) - 1)
            if iss["closed_at"]:
                t1 = int(datetime.datetime.fromisoformat(iss["closed_at"].replace("Z", "+00:00")).timestamp()) + 600
                k1 = max(0, bisect.bisect_right(times, t1) - 1)
            else:
                k1 = len(hist) - 1
            for when, kk in (("create", k), ("close/now", k1)):
                root = devroot(name, hist[kk][0]) or devroot(name, hist[kk + 1][0])
                r = run(root, [[f"#{iss['number']}", iss["body"] or ""]], "iss")
                for x in r["findings"]:
                    rows.append(
                        dict(repo=name, number=iss["number"], state=iss["state"], when=when, at=hist[kk][0][:9], **x)
                    )
    return rows


def summarize(rows):
    """Unique findings (repo, file, kind, span, detail), first and last commit index seen."""
    u = {}
    for r in rows:
        k = (r["repo"], r["src"], r["kind"], r["prefix"], r["span"], r["detail"])
        a = u.setdefault(k, dict(first=r["idx"], last=r["idx"], first_sha=r["sha"], n=0, link=False))
        a["link"] |= bool(re.search(r"#\d+|/issues/\d+", r.get("text", "")))
        a["last"] = max(a["last"], r["idx"])
        a["n"] += 1
    return u


if __name__ == "__main__":
    rows = repo_pass()
    json.dump(rows, open(c.SCR / "cli_repo.json", "w"), indent=0)  # noqa: SIM115 - research script, kept as run
    irows = issue_pass()
    json.dump(irows, open(c.SCR / "cli_issues.json", "w"), indent=0)  # noqa: SIM115 - research script, kept as run
    heads = {n: len(c.history(n)) - 1 for n in c.REPOS}
    u = summarize(rows)
    print("\n# unique repo findings (repo kind prefix ext gate? first..last@head src | span | detail)")
    for k, a in sorted(u.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[1]["first"])):
        repo, src, kind, prefix, span, detail = k
        gate = "G" if kind in ("K1", "K2") and prefix == "uv" and src.endswith(GATE_EXT) else "-"
        live = "LIVE" if a["last"] == heads[repo] else "gone"
        print(
            f"{repo} {kind} {prefix:4} {gate} {'L' if a['link'] else '-'} {a['first']:>2}..{a['last']:>2} {live} {a['first_sha']} {src} | {span[:90]} | {detail}"
        )
    print("\n# issue-body findings (repo #n state kind prefix | span | detail)")
    for r in irows:
        print(
            f"{r['repo']} #{r['number']} {r['state']} {r['when']} {r['kind']} {r['prefix']} | {r['span'][:90]} | {r['detail']}"
        )
    cnt = collections.Counter((k[0], k[2]) for k in u)
    print("\nunique repo findings by repo/kind:", dict(sorted(cnt.items())))
    print(
        "issue findings by repo/when/kind:",
        dict(sorted(collections.Counter((r["repo"], r["when"], r["kind"]) for r in irows).items())),
    )
    print(
        "issues with a finding, by repo/when:",
        dict(sorted(collections.Counter(k[:2] for k in {(r["repo"], r["when"], r["number"]) for r in irows}).items())),
    )
    print(
        "issues checked:",
        {n: len(json.load(open(c.DATA / f"{p}_issues.json"))) for n, p in (("AH", "ah"), ("PB", "pb"))},  # noqa: SIM115 - research script, kept as run
    )
