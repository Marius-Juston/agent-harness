"""Score the #176 rerun arms from offline snapshots (rerun PROTOCOL.md rules R4-R12). Metrics and harm follow #169's
r3_score.py; the order of the A2 list is the shipped one (reuse.order: every PASS before every HOLD, then S).

Usage:
  uv run --no-project python score.py <recall_bench cache> a3-input --split tune --snap <dir> --kind base|latest
      --out <a3 input.json>
  uv run --no-project python score.py <recall_bench cache> score --split tune --snap <dir>
      [--a3 <answers.json> ...] [--p1 <top100.json>]
      [--head <pypi top list .jsonl>] [--json <out.json>]
  ... score --split test --confirm-test-run ...   (rule R11: logged in data/TEST_RUNS.log)
  ... score --split test --replay ...   (re-prints the logged TEST run from its snapshot; not a TEST run, not logged)

The B0 and B0W names are read from the snapshot (snapshot.py --names b0=... b0w=...), in the agent's order.
Arms (rule R5): B0 = the agent's first 3 names (tool-less); B1 = B0 without FAIL, UNVERIFIED and NOT FOUND names;
B0W / B1W = the same for the agent with WebSearch and WebFetch; A1 = `dev reuse` search alone (its own top 10);
A2 = search's judged shortlist plus the B0 names (checked, R and S computed), FAIL/UNVERIFIED/NOT FOUND dropped,
shipped order, top 10; A3 = the agent's pick from A2's top 20. M2 needs: the source package is dropped from every
list before the cut (#169 rule 22). Metrics: hit@1/3/10 and MRR@10 on gold (canonical GitHub repository, or
pypi:<name> for L); harm = share of the top 3 that is HOLD, FAIL, UNVERIFIED or NOT FOUND; harm without licence (no
G5); harm without newness (no G3, rule R7). 95% bootstrap: recall_bench.boot (10,000 resamples over needs, seed 169).
"""

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
CACHE, CMD, ARGS = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.argv = [sys.argv[0], CACHE]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)

GOOD = {"pass", "flag"}
LIVE = GOOD | {"HOLD"}
DECIDE = ["B1", "B1W", "A2", "A3", "P1"]  # rule R9: gated arms in order of cost, cheapest first (P1 only if R10)


def opt(name: str, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


def opts(name: str) -> list[str]:
    return [ARGS[i + 1] for i, a in enumerate(ARGS) if a == name]


def norm(s: str) -> str:
    return re.sub(r"[-_.]+", "-", s or "").lower()


_canon: dict[str, str] = {}


def canon(repo: str) -> str:
    r = (repo or "").removeprefix("github.com/").lower()
    if r and r not in _canon:
        info = rb.gh_repo(r)
        _canon[r] = info["name"] if info else r
    return _canon.get(r, r)


def hits(c: dict, gold: set[str]) -> bool:
    """Is candidate c a gold library? Its PyPI name (L gold), its repository, or that repository after a GitHub
    rename. Only repositories whose name part equals a gold repository's are canonicalised (#169 r3_score gold_in)."""
    if f"pypi:{norm(c['name'])}" in gold:
        return True
    r = (c.get("repo") or "").removeprefix("github.com/").lower()
    if not r:
        return False
    return r in gold or (r.split("/")[-1] in {g.split("/")[-1] for g in gold} and canon(r) in gold)


def ckey(c: dict) -> str:
    return (c.get("repo") or "").lower() or f"pypi:{norm(c['name'])}"


def bad(c: dict, skip: tuple[str, ...] = ()) -> bool:
    if c["status"] in ("NOT FOUND", "UNVERIFIED"):
        return True
    return any(v.startswith(("HOLD", "FAIL")) for g, v in (c.get("gates") or {}).items() if g not in skip)


def metrics(top: list[dict], gold: set[str]) -> dict:
    r = next((k + 1 for k, c in enumerate(top[:10]) if hits(c, gold)), 0)
    t3 = top[:3]

    def share(skip: tuple[str, ...]) -> float:
        return sum(bad(c, skip) for c in t3) / len(t3) if t3 else 0.0

    return {
        "hit1": float(r == 1),
        "hit3": float(0 < r <= 3),
        "hit10": float(r > 0),
        "mrr": 1 / r if r else 0.0,
        "harm3": share(()),
        "harm3_nolic": share(("G5",)),
        "harm3_nonew": share(("G3",)),
        "n_named": len(top),
    }


def name_word(n: dict) -> bool:
    """Rule L7 sensitivity: an L need whose text still holds a 4+ letter hyphen part of a gold name ("kifly")."""
    parts = {p for g in n["gold"] for p in re.split(r"[-_./:]+", g.lower()) if len(p) >= 4} - {"pypi"}
    owner = {g.split("/")[0] for g in n["gold"] if "/" in g}
    words = {re.sub(r"[^a-z0-9]", "", w.lower()) for w in " ".join([n["need"], *n["also_described_as"]]).split()}
    return bool((parts - owner) & words)


def excluded(n: dict, c: dict) -> bool:
    if not n.get("exclude"):
        return False
    pkg, src = n["exclude"]
    r = (c.get("repo") or "").removeprefix("github.com/").lower()
    return norm(c["name"]) == pkg or bool(src and r and (r == src or canon(r) == src))


def named(d: dict, n: dict, tag: str) -> list[dict]:
    return [c for c in d["named"].get(tag, {}).get("candidates", []) if not excluded(n, c)]


def a2_list(d: dict, n: dict, cut: int = 10, tags: tuple[str, ...] = ("b0",)) -> list[dict]:
    seen, pool = set(), []
    for c in [*d["search"]["ordered"], *(c for t in tags for c in named(d, n, t))]:
        if c["status"] not in LIVE or excluded(n, c) or ckey(c) in seen:
            continue
        seen.add(ckey(c))
        pool.append(c)
    pool.sort(key=lambda c: -c["score"])
    if d["devkit"]["PASS_BEFORE_HOLD"]:
        pool = [c for c in pool if c["status"] in GOOD] + [c for c in pool if c["status"] == "HOLD"]
    return pool[:cut]


def shortlist_line(k: int, c: dict) -> str:
    """#169 r3_score.py shortlist_line, unchanged."""
    gates = "; ".join(f"{g} {v}" for g, v in (c.get("gates") or {}).items() if not v.startswith(("pass", "n/a")))
    desc = re.sub(r"\s+", " ", c.get("description") or "")[:160]
    return (
        f"{k}. {c['name']} | {c['status'].upper()}{' (' + gates + ')' if gates else ''} | {desc} | "
        f"stars {c.get('stars') if c.get('stars') is not None else '?'} | downloads/month "
        f"{c.get('downloads_month') if c.get('downloads_month') is not None else '?'} | dependents "
        f"{c.get('dependents') if c.get('dependents') is not None else '?'} | last release "
        f"{c.get('last_release') or '?'} | licence {c.get('license') or '?'}"
    )


def load_snap(snap: Path, allowed: set[str]) -> dict[str, dict]:
    out = {}
    for f in snap.glob("*.json"):
        d = json.loads(f.read_text())
        if d["id"] not in allowed:
            sys.exit(f"{d['id']} is not in the {SPLIT} split")
        out[d["id"]] = d
    return out


def answers(paths: list[str]) -> dict:
    out: dict = {}
    for p in paths:
        out |= {k: v for k, v in json.loads(Path(p).read_text()).items() if not k.startswith("_")}
    return out


SPLIT = opt("--split", "tune")
REPLAY = "--replay" in ARGS
if SPLIT == "test" and CMD == "score" and "--confirm-test-run" not in ARGS and not REPLAY:
    sys.exit("TEST is scored once (rule R11): pass --confirm-test-run")


def main() -> None:
    split = json.loads((DATA / "split.json").read_text())
    needs = {n["id"]: n for n in json.loads((DATA / "needs.json").read_text())}
    for n in needs.values():
        n["gold_set"] = set(n["gold"])
    allowed = set(split[SPLIT])
    snap = load_snap(Path(opt("--snap")), allowed)
    if CMD == "a3-input":
        strata = ("L",) if opt("--kind") == "latest" else ("A2", "M2")
        rows = [
            {
                "id": i,
                "need": needs[i]["need"],
                "also_described_as": needs[i]["also_described_as"],
                "shortlist": [shortlist_line(k + 1, c) for k, c in enumerate(a2_list(snap[i], needs[i], cut=20))],
            }
            for i in split[SPLIT]
            if needs[i]["stratum"] in strata and i in snap
        ]
        Path(opt("--out")).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"A3 input ({opt('--kind')}): {len(rows)} needs")
        return
    ids = [i for i in split[SPLIT] if i in snap]
    if SPLIT == "test" and not REPLAY:
        with (DATA / "TEST_RUNS.log").open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} score.py TEST run ({len(ids)} needs), argv={ARGS}\n")
    a3 = answers(opts("--a3")) if opts("--a3") else None
    rows: dict[str, list[dict]] = {}
    misses: dict[str, list[str]] = {}
    pool_hit = []
    for i in ids:
        d, n, gold = snap[i], needs[i], needs[i]["gold_set"]
        pool_hit.append(any(hits(c, gold) for c in d["search"]["pool"]))
        rows.setdefault("A1", []).append(metrics([c for c in d["search"]["kept"] if not excluded(n, c)], gold))
        a2 = a2_list(d, n)
        rows.setdefault("A2", []).append(metrics(a2, gold))
        for tag, raw, gated in (("b0", "B0", "B1"), ("b0w", "B0W", "B1W")):
            if not any(tag in snap[j]["named"] for j in ids):
                continue
            first = named(d, n, tag)[:3]
            rows.setdefault(raw, []).append(metrics(first, gold))
            rows.setdefault(gated, []).append(metrics([c for c in first if c["status"] in LIVE], gold))
            if not rows[gated][-1]["hit3"]:
                misses.setdefault(gated, []).append(f"{i}: named {[c['name'] + ' ' + c['status'] for c in first]}")
        if "p1" in d["named"]:  # rule R10: only when TUNE headroom reached 0.10
            rows.setdefault("P1", []).append(metrics(a2_list(d, n, tags=("b0", "p1")), gold))
        if a3 is not None:
            short = {c["name"]: c for c in a2_list(d, n, cut=20)}
            picked = [short[x] for x in dict.fromkeys(a3.get(i, [])) if x in short]
            rows.setdefault("A3", []).append(metrics(picked, gold))
        if not rows["A2"][-1]["hit3"]:
            misses.setdefault("A2", []).append(f"{i}: top 3 {[c['name'] for c in a2[:3]]}")
    strata = {s: {i for i in ids if needs[i]["stratum"] == s} for s in ("A2", "M2", "L")}
    strata["main (A2+M2)"] = strata["A2"] | strata["M2"]
    if opt("--head"):
        head = {
            json.loads(x)["repo"].lower()
            for x in Path(opt("--head")).read_text().splitlines()[:1000]
            if json.loads(x).get("repo")
        }
        head |= {canon(r) for r in list(head)}
        strata["top-1,000"] = {i for i in strata["main (A2+M2)"] if needs[i]["gold_set"] & head}
        strata["long tail"] = strata["main (A2+M2)"] - strata["top-1,000"]
    if SPLIT == "test":
        strata["main, no TUNE overlap"] = strata["main (A2+M2)"] - set(split["overlaps_tune"])
    strata["main, no #169 overlap"] = strata["main (A2+M2)"] - set(split["overlaps_169"])
    strata["L, no name word"] = {i for i in strata["L"] if not name_word(needs[i])}
    out = [
        f"{SPLIT.upper()} needs scored: {len(ids)} of {len(split[SPLIT])}"
        + (" (REPLAY, not logged)" if REPLAY else ""),
        "",
    ]
    report: dict = {"strata": {}, "decision": {}}
    for sname, sset in strata.items():
        sel = [k for k, i in enumerate(ids) if i in sset]
        if not sel:
            continue
        out += [
            f"## {sname} (n={len(sel)})",
            "",
            "| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR | harm | harm no G5 | harm no G3 | empty |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for arm, ms in rows.items():
            sub = [ms[k] for k in sel]
            h = rb.boot([m["hit3"] for m in sub])
            mean = {key: sum(m[key] for m in sub) / len(sub) for key in sub[0]}
            report["strata"].setdefault(sname, {})[arm] = {**mean, "hit3_ci": h[1:]}
            out.append(
                f"| {arm} | {h[0]:.2f} [{h[1]:.2f}, {h[2]:.2f}] | {mean['hit1']:.2f} | {mean['hit10']:.2f} | "
                f"{mean['mrr']:.2f} | {mean['harm3']:.2f} | {mean['harm3_nolic']:.2f} | {mean['harm3_nonew']:.2f} | "
                f"{sum(m['n_named'] == 0 for m in sub) / len(sub):.2f} |"
            )
        out += ["", "Paired differences [95% CI] (hit@3; harm):"]
        pairs = [(a, b) for a in [*DECIDE, "B0W"] for b in DECIDE if a in rows and b in rows and a != b]
        pairs = [(a, b) for a, b in pairs if (b == "B1") if a == "B0W"] + [
            (a, b) for a, b in pairs if a != "B0W" and DECIDE.index(a) > DECIDE.index(b)
        ]
        for a, b in pairs:
            dh = rb.boot([rows[a][k]["hit3"] - rows[b][k]["hit3"] for k in sel])
            harm_key = "harm3_nonew" if sname == "L" else "harm3"
            dm = rb.boot([rows[a][k][harm_key] - rows[b][k][harm_key] for k in sel])
            # amendment 2 (primary): harm only on needs where both arms named at least one package
            both = [k for k in sel if rows[a][k]["n_named"] and rows[b][k]["n_named"]]
            db = rb.boot([rows[a][k][harm_key] - rows[b][k][harm_key] for k in both]) if both else None
            report["strata"][sname][f"{a} - {b}"] = {
                "hit3": dh,
                harm_key: dm,
                "harm_both_named": db,
                "n_both": len(both),
            }
            out.append(
                f"- {a} - {b}: hit@3 {dh[0]:+.2f} [{dh[1]:+.2f}, {dh[2]:+.2f}]; {harm_key} on needs both named "
                + (f"(n={len(both)}) {db[0]:+.3f} [{db[1]:+.3f}, {db[2]:+.3f}]" if db else "(n=0) n/a")
                + f"; all needs (registered, secondary) {dm[0]:+.3f} [{dm[1]:+.3f}, {dm[2]:+.3f}]"
            )
        if sname in ("main (A2+M2)", "L"):
            for reading in ("both named (primary, amendment 2)", "all needs (registered, secondary)"):
                win = "B1"
                for arm in DECIDE[1:]:
                    key = f"{arm} - {win}"
                    if key not in report["strata"][sname]:
                        continue
                    r = report["strata"][sname][key]
                    harm = r["harm_both_named"] if reading.startswith("both") else r[harm_key]
                    harm_ok = harm is None or harm[0] <= 0  # no need named by both: nothing to compare
                    if r["hit3"][1] > 0 and harm_ok:
                        win = arm
                report["decision"].setdefault(sname, {})[reading] = win
                out += ["", f"Rule R9, harm {reading} (cheapest first {DECIDE}): **{win}**"]
        out.append("")
    p1 = opt("--p1")
    if p1:
        top = json.loads(Path(p1).read_text())
        out += ["## Rule R10 headroom: needs with a gold library in P1's top 100 absent from the A2 pool", ""]
        for sname in ("main (A2+M2)", "L", "all"):
            sset = set(ids) if sname == "all" else strata.get(sname, set())
            extra = []
            for i in sorted(sset):
                d, n = snap[i], needs[i]
                pool = [*d["search"]["pool"], *named(d, n, "b0")]
                index = top.get(i, [])[:100]
                libs = [n["gold_set"]] if n["stratum"] == "L" else [{g} for g in n["gold_set"]]  # L: one library
                if any(any(hits(h, g) for h in index) and not any(hits(c, g) for c in pool) for g in libs):
                    extra.append(i)
            share = len(extra) / len(sset) if sset else 0.0
            report.setdefault("headroom", {})[sname] = [len(extra), len(sset), share]
            out.append(f"- {sname}: {len(extra)}/{len(sset)} = {share:.3f} (threshold 0.10)")
        out.append("")
    out += ["## Agent names that do not exist (G1 NOT FOUND) and needs named nothing", ""]
    for tag in ("b0", "b0w"):
        for sname in ("A2", "M2", "L", "main (A2+M2)"):
            sset = strata.get(sname, set())
            names = [c for i in sset for c in snap[i]["named"].get(tag, {}).get("candidates", [])]
            nf = sum(c["status"] == "NOT FOUND" for c in names)
            empty = sum(not snap[i]["named"].get(tag, {}).get("asked") for i in sset)
            out.append(f"- {tag} {sname}: {nf}/{len(names)} names NOT FOUND; {empty}/{len(sset)} needs named nothing")
        allnames = [c for i in ids for c in snap[i]["named"].get(tag, {}).get("candidates", [])]
        out.append(f"- {tag} all: {sum(c['status'] == 'NOT FOUND' for c in allnames)}/{len(allnames)} names NOT FOUND")
    out.append("")
    pool_l = [h for i, h in zip(ids, pool_hit, strict=True) if i in strata["L"]]
    out += [
        f"Gold anywhere in the search pool: {sum(pool_hit) / len(ids):.2f} (L only: "
        f"{sum(pool_l) / len(pool_l) if pool_l else 0:.2f})",
        "",
        "Misses (gated arms and A2):",
    ]
    for arm, ms in misses.items():
        out += [f"- {arm}: {len(ms)}", *[f"  - {m}" for m in ms]]
    print("\n".join(out))
    if opt("--json"):
        Path(opt("--json")).write_text(json.dumps({"ids": ids, "rows": rows, "report": report}, indent=1, default=str))


if __name__ == "__main__":
    main()
