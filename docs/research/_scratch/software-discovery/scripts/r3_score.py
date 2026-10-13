"""Round 3 (#169): score every pre-registered arm on TUNE from offline snapshots (r3-tuning.md, PROTOCOL.md 4, 9-17).

Usage:
  uv run --no-project python r3_score.py <recall_bench cache> score --a1 <snap> --a2 <snap_a2> --b0 <answers.json>
      [--a3 <a3 answers.json>] [--arm P1=<snap_p1> ...] [--choose] [--json <out.json>]
  uv run --no-project python r3_score.py <recall_bench cache> a3-input --a2 <snap_a2> --config R? --out <input.json>

Arms: R0-R7 on the A1 pool (PR #171 recall) and on the A2 pool (A1 + the B0 names, always shortlisted); B0 and B1
(r3-tuning.md "Declared"); A3 (agent re-rank of A2's top 20); each --arm (an index's names added to the A2 pool,
not forced). TUNE only: a TEST need in any snapshot stops the run. Metrics: hit@3 (95% bootstrap, 10,000 resamples,
seed 169), hit@1, hit@10, MRR@10, harm = share of the top 3 that is HOLD, FAIL, unverified or unknown; paired hit@3
differences; strata A/P and head/tail (head_flags.json).
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = sys.argv[1]
CMD = sys.argv[2]
ARGS = sys.argv[3:]
sys.argv = [sys.argv[0], CACHE]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)

GOOD = {"pass", "flag"}
FLOOR = 0.20


def opt(name: str, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


def opts(name: str) -> list[str]:
    return [ARGS[i + 1] for i, a in enumerate(ARGS) if a == name]


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


_canon: dict[str, str] = {}


def canon(repo: str) -> str:
    """owner/name after GitHub renames (cached GitHub API answers), lower case."""
    r = repo.removeprefix("github.com/").lower()
    if r and r not in _canon:
        info = rb.gh_repo(r)
        _canon[r] = info["name"] if info else r
    return _canon.get(r, r)


def gold_in(repos: set[str], gold: set[str]) -> set[str]:
    """The gold repositories among `repos`, without canonicalising every repository: exact match, plus canonicalised
    repositories whose name part equals a gold repository's name part (a rename that keeps the name, the usual case)."""
    raw = {r.removeprefix("github.com/").lower() for r in repos if r}
    parts = {g.split("/")[-1] for g in gold}
    return (raw & gold) | {canon(r) for r in raw if r.split("/")[-1] in parts and r not in gold} & gold


def in_gold(repos: set[str], gold: set[str]) -> bool:
    return bool(gold_in(repos, gold))


def cand_repo(c: dict, raw: bool = False) -> str:
    r = (c.get("repo") or "").removeprefix("github.com/").lower()
    if not r and c.get("ecosystem") == "pypi":
        j = rb.pypi(c["name"])
        r = (rb.pypi_repo(j["info"]) or "") if j else ""
    return r if raw else canon(r) if r else f"pypi:{norm(c['name'])}"


def load(snap: Path, tune: set[str]) -> dict[str, dict]:
    out = {}
    for f in snap.glob("*.json"):
        d = json.loads(f.read_text())
        if d["id"] not in tune:
            sys.exit(f"{d['id']} is not a TUNE need: round 3 scores TUNE only")
        out[d["id"]] = d
    return out


def adoption(c: dict) -> tuple[int, int]:
    return (c.get("dependents") or -1, c.get("downloads_month") or -1)


def rank(cands: list[dict], cfg: dict, force: str = "", n: int = 10, cut: int | None = None) -> list[dict]:
    """replay.py rank(), plus: candidates tagged `force` (the agent's names) always join the shortlist."""
    cands = [c for c in cands if c["status"] != "NOT_FOUND"]
    if cfg["shortlist"] == "prior2n":
        short = sorted(cands, key=lambda c: -c["prior"])[: 2 * n]
    else:
        best = max((c["relevance"] for c in cands), default=0.0)
        by_r = sorted(cands, key=lambda c: -c["relevance"])[:n]
        near = [c for c in cands if c["relevance"] >= best - FLOOR]
        by_a = sorted(near, key=adoption, reverse=True)[:n]
        short = by_r + by_a
    if force:
        short += [c for c in cands if force in c.get("found_by", [])]
    seen, uniq = set(), []
    for c in short:
        k = c.get("repo") or c["name"]
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    if any(c["status"] == "NOT_ENRICHED" for c in uniq):
        raise RuntimeError("the snapshot lacks a candidate this config shortlists")
    kept = [dict(c) for c in uniq if c["status"] in GOOD | {"HOLD"}]
    for c in kept:
        c["S"] = c["relevance"] + cfg["lambda"] * c["quality"]
    if cfg["tier_delta"] is not None and kept:
        top_r = max(c["relevance"] for c in kept)
        tier = sorted([c for c in kept if c["relevance"] >= top_r - cfg["tier_delta"]], key=lambda c: -c["quality"])
        rest = sorted([c for c in kept if c["relevance"] < top_r - cfg["tier_delta"]], key=lambda c: -c["S"])
        order = tier + rest
    else:
        order = sorted(kept, key=lambda c: -c["S"])
    if cfg["pass_before_hold"]:
        order = [c for c in order if c["status"] in GOOD] + [c for c in order if c["status"] == "HOLD"]
    return order[: cut or n]


def metrics(repos: list[str], gold: set[str], harm: list[bool]) -> dict:
    r = next((k + 1 for k, nm in enumerate(repos[:10]) if nm in gold), 0)
    return {"hit1": float(r == 1), "hit3": float(0 < r <= 3), "hit10": float(r > 0), "mrr": 1 / r if r else 0.0,
            "harm3": sum(harm[:3]) / len(harm[:3]) if harm else 0.0}


def find_named(d: dict, name: str) -> dict | None:
    """The A2-snapshot candidate for an agent-named PyPI package (add_named.py), or None."""
    for c in d["candidates"]:
        if "agent" in c.get("found_by", []) and norm(c["name"]) == norm(name):
            return c
    j = rb.pypi(name)
    r = (rb.pypi_repo(j["info"]) or "") if j else ""
    for c in d["candidates"]:
        if "agent" in c.get("found_by", []) and r and (c.get("repo") or "").removeprefix("github.com/") == r:
            return c
    return None


def b_arms(d: dict, names: list[str], gold: set[str]) -> tuple[dict, dict]:
    rows = []
    for nm in names[:3]:
        c = find_named(d, nm)
        st = c["status"] if c else "NOT_FOUND"
        repo = cand_repo(c) if c and st != "NOT_FOUND" else f"pypi:{norm(nm)}"
        rows.append((repo, st))
    b0 = metrics([r for r, _ in rows], gold, [st not in GOOD for _, st in rows])
    keep = [(r, st) for r, st in rows if st in GOOD | {"HOLD"}]
    b1 = metrics([r for r, _ in keep], gold, [st == "HOLD" for _, st in keep])
    return b0, b1


def table(rows: dict[str, list[dict]], ids: list[str], base: list[str], strata: dict[str, set[str]]) -> list[str]:
    out = ["| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR | harm share of top 3 | "
           + " | ".join(f"hit@3 minus {b} [95% CI]" for b in base) + " | "
           + " | ".join(f"hit@3 {s} (n={len(v)})" for s, v in strata.items()) + " |",
           "|---" * (6 + len(base) + len(strata)) + "|"]
    for k, rs in rows.items():
        n = len(rs)
        h3 = rb.boot([r["hit3"] for r in rs])

        def avg(key: str, rs: list[dict] = rs, n: int = n) -> float:
            return sum(r[key] for r in rs) / n
        diffs = []
        for b in base:
            if b == k or b not in rows:
                diffs.append("-")
                continue
            d = rb.boot([a["hit3"] - c["hit3"] for a, c in zip(rs, rows[b], strict=True)])
            diffs.append(f"{d[0]:+.2f} [{d[1]:+.2f}, {d[2]:+.2f}]")
        st = [sum(r["hit3"] for i, r in zip(ids, rs, strict=True) if i in v) / max(1, len(v)) for v in strata.values()]
        out.append(f"| {k} | {h3[0]:.2f} [{h3[1]:.2f}, {h3[2]:.2f}] | {avg('hit1'):.2f} | {avg('hit10'):.2f} | "
                   f"{avg('mrr'):.2f} | {avg('harm3'):.2f} | " + " | ".join(diffs) + " | "
                   + " | ".join(f"{x:.2f}" for x in st) + " |")
    return out


def display(c: dict) -> str:
    return c["name"]


def shortlist_line(k: int, c: dict) -> str:
    gates = "; ".join(f"{g} {v}" for g, v in (c.get("gates") or {}).items() if not v.startswith(("pass", "n/a")))
    desc = re.sub(r"\s+", " ", c.get("description") or "")[:160]
    return (f"{k}. {display(c)} | {c['status'].upper()}{' (' + gates + ')' if gates else ''} | {desc} | "
            f"stars {c.get('stars') if c.get('stars') is not None else '?'} | downloads/month "
            f"{c.get('downloads_month') if c.get('downloads_month') is not None else '?'} | dependents "
            f"{c.get('dependents') if c.get('dependents') is not None else '?'} | last release "
            f"{c.get('last_release') or '?'} | licence {c.get('license') or '?'}")


def main() -> None:
    split = json.loads((HERE / "data" / "split.json").read_text())
    tune = set(split["tune"])
    configs = {c["id"]: c for c in json.loads((HERE / "data" / "configs.json").read_text())["ranking_configs"]}
    text = {n["id"]: n for n in rb.load_text_needs()}
    a2 = load(Path(opt("--a2")), tune)
    if CMD == "a3-input":
        cfg = configs[opt("--config")]
        b0in = {n["id"]: n for n in json.loads((HERE / "data" / "b0_tune_input.json").read_text())}
        out = []
        for i in split["tune"]:
            top = rank(a2[i]["candidates"], cfg, force="agent", cut=20)  # the n=10 shortlist, up to 20 shown
            out.append({**b0in[i], "shortlist": [shortlist_line(k + 1, c) for k, c in enumerate(top)]})
        Path(opt("--out")).write_text(json.dumps(out, indent=1))
        print(f"A3 input: {len(out)} needs, config {cfg['id']}")
        return
    if CMD == "headroom":
        index = json.loads(Path(opt("--index")).read_text())
        rows = []
        for i in split["tune"]:
            gold = text[i]["gold"]
            pool = gold_in({c.get("repo") or "" for c in a2[i]["candidates"]}, gold)
            top = gold_in({h["repo"] for h in index[i][:100]}, gold)
            rows.append((i, bool(pool), bool(top), sorted(top - pool)))
        k = len(rows)
        extra = [r for r in rows if r[3]]
        only = [r for r in rows if r[2] and not r[1]]
        print(f"TUNE needs {k}; gold in the A2 pool {sum(r[1] for r in rows)}; gold in index top 100 "
              f"{sum(r[2] for r in rows)}")
        print(f"headroom (PROTOCOL 16): needs with a gold repository in index top 100 absent from the A2 pool: "
              f"{len(extra)}/{k} = {len(extra) / k:.3f}")
        print(f"needs whose only recalled gold is from the index: {len(only)}/{k} = {len(only) / k:.3f}")
        for r in extra:
            print(f"- {r[0]}: {', '.join(r[3])}")
        return
    a1 = load(Path(opt("--a1")), tune)
    ids = [i for i in split["tune"] if i in a1 and i in a2]
    heads = json.loads((HERE / "data" / "head_flags.json").read_text())
    strata = {"A": {i for i in ids if text[i]["stratum"] == "A"}, "P": {i for i in ids if text[i]["stratum"] == "P"},
              "head": {i for i in ids if heads.get(i)}, "tail": {i for i in ids if not heads.get(i)}}
    answers = json.loads(Path(opt("--b0")).read_text())
    extra = {kv.split("=", 1)[0]: load(Path(kv.split("=", 1)[1]), tune) for kv in opts("--arm")}
    a3 = json.loads(Path(opt("--a3")).read_text()) if opt("--a3") else None
    rows: dict[str, list[dict]] = {}
    pool_hit = {"A1": [], "A2": []}
    per_need: dict[str, dict] = {}
    for i in ids:
        gold = text[i]["gold"]
        raw = rank(a1[i]["candidates"], configs["R0"])
        rows.setdefault("A1 = R0, uncanonicalised (round-1 check)", []).append(
            metrics([cand_repo(c, raw=True) for c in raw], gold, [c["status"] == "HOLD" for c in raw]))
        for pool, snap, force in (("A1", a1, ""), ("A2", a2, "agent")):
            cands = snap[i]["candidates"]
            pool_hit[pool].append(in_gold({c.get("repo") or "" for c in cands}, gold))
            for k, cfg in configs.items():
                top = rank(cands, cfg, force)
                rows.setdefault(f"{pool} {k}", []).append(
                    metrics([cand_repo(c) for c in top], gold, [c["status"] == "HOLD" for c in top]))
        b0, b1 = b_arms(a2[i], answers.get(i, []), gold)
        rows.setdefault("B0", []).append(b0)
        rows.setdefault("B1", []).append(b1)
        for name, snap in extra.items():
            for k, cfg in configs.items():
                top = rank(snap[i]["candidates"], cfg, "agent")
                rows.setdefault(f"{name} {k}", []).append(
                    metrics([cand_repo(c) for c in top], gold, [c["status"] == "HOLD" for c in top]))
        if a3 is not None:
            cfg = configs[a3["_config"]]
            short = {display(c): c for c in rank(a2[i]["candidates"], cfg, "agent", cut=20)}
            picked = [short[nm] for nm in a3.get(i, []) if nm in short]
            rows.setdefault(f"A3 (agent re-rank of A2 {cfg['id']} top 20)", []).append(
                metrics([cand_repo(c) for c in picked], gold, [c["status"] == "HOLD" for c in picked]))
        per_need[i] = {k: v[-1]["hit3"] for k, v in rows.items()}
    base = ["B1", "A1 R0"]
    order = list(rows)
    lines = [f"TUNE needs scored: {len(ids)} of {len(split['tune'])}",
             f"gold anywhere in the A1 pool {sum(pool_hit['A1']) / len(ids):.2f}; in the A2 pool "
             f"{sum(pool_hit['A2']) / len(ids):.2f}", ""]
    lines += table({k: rows[k] for k in order}, ids, base, strata)
    print("\n".join(lines))
    if "--choose" in ARGS:
        def key(k: str) -> tuple:
            m = sum(r["hit3"] for r in rows[f"A2 {k}"]) / len(ids)
            return (-round(m, 6), configs[k]["complexity"], k)
        ranked = sorted(configs, key=key)
        print("\nchoice order on the A2 pool (hit@3, then complexity, then id): " + ", ".join(ranked))
    if opt("--json"):
        Path(opt("--json")).write_text(json.dumps({"ids": ids, "rows": rows, "per_need": per_need}, indent=1))


if __name__ == "__main__":
    main()
