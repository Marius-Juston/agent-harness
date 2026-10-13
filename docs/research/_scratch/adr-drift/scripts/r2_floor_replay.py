"""R2b check 4: replay "integrate writes the min_tests raise the gate prints" over every first-parent commit.

usage: python3 r2_floor_replay.py collect   -> .scratch/r2/floor_counts.json (slow: collects tests at each commit)
       python3 r2_floor_replay.py simulate  -> prints the simulation
Count = tests PASSED in the gate's default run (-m "not slow and not live", xdist -n 8) at each commit, run in a
throwaway detached worktree (.scratch/r2/wt-*) with the repo's current venv; project-B commits after the migration put
agent-harness at the pinned tag first on PYTHONPATH. Env-dependent failures are recorded as `failed`.
Policy simulated, as gate.py states it: notice when floor < 0.90 * passed; integrate then writes 0.95 * passed.
A later commit with selected < floor would have failed the gate: hand-classify as a real test loss or legitimate.
"""

import json
import os
import re
import subprocess
import sys

import r2_common as c

PYS = {"AH": str(c.AH / ".venv/bin/python"), "PB": str(c.PB / ".venv/bin/python")}
OUT = c.SCR / "floor_counts.json"
JUNIT = c.SCR / "floor_junit.xml"
from xml.etree import ElementTree  # noqa: E402 - research script, kept as run


def worktree(name):
    """One throwaway detached worktree per repository under .scratch/r2/wt-*, so tests see their own git toplevel."""
    wt = c.SCR / f"wt-{name.lower()}"
    if not wt.exists():
        c.git(c.REPOS[name][0], "worktree", "add", "--detach", str(wt), c.REPOS[name][1])
    return wt


def collect():
    res = json.load(open(OUT)) if OUT.exists() else {}  # noqa: SIM115 - research script, kept as run
    for name in c.REPOS:
        wt = worktree(name)
        for i, (sha, t, subj) in enumerate(c.history(name)):
            key = f"{name}:{sha}"
            if key in res:
                continue
            c.git(wt, "checkout", "-q", "--detach", "--force", sha)
            c.git(wt, "clean", "-qfdx", "-e", ".venv")
            floor = None
            rt = wt / ".devkit/ratchets.toml"
            if rt.exists():
                m = re.search(r"^min_tests\s*=\s*(\d+)", rt.read_text(), re.M)
                floor = int(m.group(1)) if m else None
            n = fail = None
            tail = ""
            if (wt / "tests").is_dir():
                root = wt if (wt / "devkit").is_dir() else None
                if root is None and name == "PB":
                    m = re.search(r"agent-harness@(v[\d.]+)", (wt / "pyproject.toml").read_text())
                    root = c.tree("AHTAG", c.git(c.AH, "rev-list", "-n1", m.group(1)).strip())
                env = dict(os.environ, PYTHONPATH=f"{wt}:{root}" if root else str(wt))
                p = subprocess.run(
                    [
                        PYS[name],
                        "-m",
                        "pytest",
                        "tests",
                        "-p",
                        "no:cacheprovider",
                        "-n",
                        "8",
                        f"--junitxml={JUNIT}",
                        "-m",
                        "not slow and not live",
                    ],
                    cwd=wt,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=1200,
                )
                tail = (p.stdout + p.stderr).strip()[-160:]
                n = fail = None
                if JUNIT.exists():
                    cases = list(ElementTree.parse(JUNIT).getroot().iter("testcase"))  # noqa: S314 - research script, kept as run
                    bad = [k for k in cases if k.find("failure") is not None or k.find("error") is not None]
                    skip = [k for k in cases if k.find("skipped") is not None]
                    n, fail = len(cases) - len(bad) - len(skip), len(bad)
                    JUNIT.unlink()
            res[key] = dict(
                repo=name,
                idx=i,
                sha=sha[:9],
                t=t,
                subj=subj[:70],
                floor=floor,
                selected=n,
                failed=fail,
                tail=tail[-160:],
                err=None,
            )
            print(name, i, sha[:9], floor, n, fail, tail[-100:], file=sys.stderr)
            json.dump(res, open(OUT, "w"), indent=0)  # noqa: SIM115 - research script, kept as run


def simulate():
    res = json.load(open(OUT))  # noqa: SIM115 - research script, kept as run
    for name in c.REPOS:
        rows = sorted((r for r in res.values() if r["repo"] == name), key=lambda r: r["idx"])
        sim = None
        notices = fails_actual = fails_sim = 0
        print(f"== {name}: idx sha selected actual_floor | simulated floor (raise written at integrate)")
        prev = None
        for r in rows:
            n, fl = r["selected"], r["floor"]
            if n is None:
                print(f"  {r['idx']:>2} {r['sha']} selected=? floor={fl} err={r['err']}")
                continue
            if sim is None:
                sim = fl
            mark = []
            if fl is not None and fl < 0.90 * n:
                notices += 1
                mark.append("notice")
            if fl is not None and n < fl:
                fails_actual += 1
                mark.append("FAIL(actual)")
            if sim is not None and n < sim:
                fails_sim += 1
                mark.append("FAIL(sim)")
            drop = f" drop {prev - n}" if prev is not None and n < prev else ""
            print(f"  {r['idx']:>2} {r['sha']} {n:>5} floor={fl} sim={sim} {' '.join(mark)}{drop} | {r['subj']}")
            if sim is not None and sim < 0.90 * n:
                sim = int(n * 0.95)
            if fl is not None and sim is not None and fl > sim:
                sim = fl  # a hand raise in the real history also counts
            prev = n
        print(
            f"  {name}: commits with the raise notice (actual floors) {notices}; gate failures actual {fails_actual}, "
            f"simulated {fails_sim}"
        )


if __name__ == "__main__":
    {"collect": collect, "simulate": simulate}[sys.argv[1]]()
