"""Run one arm on one split and save every raw answer (PROTOCOL.md rules 5, 8, 9).

Usage: uv run python run_arms.py <arm> <tune|test>      arm: L0 L1 L2 L3 L4 (dev lit), X (arxiv MCP), W (WebSearch)

Output per unit: PRIV/runs/<arm>/<unit>.json (every unit: third-party result titles can hold words the public
repository's scrub forbids; `manifest.py` commits their SHA-256), as
{query: {"results": [{title, year, ids, url}], "notes": [...]}}. Resumable: a query already saved is skipped.

L arms call `devkit.lit.Client.search` exactly as `dev lit search -n 20 --source <spec>` does. A source in a shared
throttle cooldown other than Semantic Scholar is waited out (common.patient_fetch) and the search repeated, so a
config is not scored on an arXiv or Crossref cooldown; Semantic Scholar, DOWN for the day, is left to fail as it
does for every agent (its note is kept).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from common import DATA, HERE, PRIV, load, patient_fetch

from devkit import lit

L_SPECS = {"L0": "auto", "L1": "all", "L2": "openalex,crossref", "L3": "openalex,arxiv", "L4": "openalex,europepmc"}
N = 20


def queries_for(unit: str) -> list[str]:
    qs = load("queries_P.json", private=True) if unit.startswith("P") else load("queries_A.json")
    return qs[unit]


def out_path(arm: str, unit: str) -> Path:
    return PRIV / "runs" / arm / f"{unit}.json"  # all raw answers: result titles can hold words test_scrub forbids


def paper_json(p: lit.Paper) -> dict:
    return {"title": p.title, "year": p.year, "ids": {k: v for k, v in p.ids.items() if v}, "url": "", "src": p.source}


def run_lit(arm: str, c: lit.Client, q: str) -> dict:
    sources, fallback = lit.plan(L_SPECS[arm])
    for _ in range(6):
        papers, notes = c.search(q, sources, N, "", fallback=fallback)
        held = [
            s for s in lit.SOURCES if s != "s2" and any(n.startswith(f"{s}:") and "rate limited" in n for n in notes)
        ]
        if not held:
            break
        for s in held:
            patient_fetch(c, s, lit.search_url(s, q, N))
    return {"results": [paper_json(p) for p in papers], "notes": notes}


def also_lit():
    """PR #202's devkit/lit.py (amendment 2), saved by `git show 53a78f9:devkit/lit.py` to LITRECALL_ALSO_LIT."""
    import importlib.util
    import os

    path = os.environ.get("LITRECALL_ALSO_LIT", str(HERE.parents[4] / ".scratch" / "pr202" / "lit_also.py"))
    spec = importlib.util.spec_from_file_location("lit_also", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lit_also"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_also(la, c, qs: list[str]) -> dict:
    sources, fallback = la.plan("auto")
    for _ in range(6):
        papers, notes = c.search(qs[0], sources, N, "", fallback=fallback, also=tuple(qs[1:]))
        held = [
            s for s in la.SOURCES if s != "s2" and any(n.startswith(f"{s}:") and "rate limited" in n for n in notes)
        ]
        if not held:
            break
        for s in held:
            for q in qs:
                patient_fetch(c, s, la.search_url(s, q, N))
    return {"results": [paper_json(p) for p in papers], "notes": notes}


def main() -> None:
    arm, split = sys.argv[1], sys.argv[2]
    if arm == "LA":
        la = also_lit()
        c = la.client()
        for u in load("split.json")[split]:
            p = out_path(arm, u)
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                continue
            qs = queries_for(u)
            res = run_also(la, c, qs)
            p.write_text(json.dumps({" | ".join(qs): res}, indent=1, ensure_ascii=False))
            print(f"LA {u} {len(res['results'])} {res['notes']}", flush=True)
        return
    units = load("split.json")[split]
    if arm == "X":
        for u in units:
            p = out_path(arm, u)
            p.parent.mkdir(parents=True, exist_ok=True)
            inp = PRIV / f"x_in_{u}.json"
            inp.write_text(json.dumps(dict.fromkeys(queries_for(u))))
            subprocess.run(
                ["uv", "run", "--no-project", "--with", "mcp", "python", str(HERE / "arxiv_arm.py"), str(inp), str(p)],
                check=True,
            )
        return
    if arm == "W":
        for u in units:
            p = out_path(arm, u)
            p.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["uv", "run", "--no-project", "python", str(HERE / "web_arm.py"), json.dumps(queries_for(u)), str(p)],
                check=True,
            )
        return
    c = lit.client()
    status_before = lit.render_status(c.status()).strip().splitlines()[-1]
    for u in units:
        p = out_path(arm, u)
        p.parent.mkdir(parents=True, exist_ok=True)
        done = json.loads(p.read_text()) if p.exists() else {}
        for q in queries_for(u):
            if q in done:
                continue
            done[q] = run_lit(arm, c, q)
            p.write_text(json.dumps(done, indent=1, ensure_ascii=False))
            srcs = sorted({r["src"] for r in done[q]["results"]})
            print(f"{arm} {u} {len(done[q]['results']):2d} {','.join(srcs)} | {q}", flush=True)
    status_after = lit.render_status(c.status()).strip().splitlines()[-1]
    log = DATA / "runs" / "status.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(f"{arm} {split} before: {status_before} | after: {status_after}\n")
    print(re.sub(r"\s+", " ", status_after))


if __name__ == "__main__":
    main()
