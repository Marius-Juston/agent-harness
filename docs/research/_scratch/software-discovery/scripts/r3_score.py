"""Round 3 (#169): score every pre-registered arm on TUNE from offline snapshots (r3-tuning.md, PROTOCOL.md 4, 9-17).

Usage:
  uv run --no-project python r3_score.py <recall_bench cache> score --a1 <snap> --a2 <snap_a2> --b0 <answers.json>
      [--a3 <a3 answers.json>] [--arm P1=<snap_p1> ...] [--choose] [--json <out.json>]
  uv run --no-project python r3_score.py <recall_bench cache> a3-input --a2 <snap_a2> --config R? --out <input.json>

  uv run --no-project python r3_score.py <cache> score --split test --confirm-test-run ... (PROTOCOL 5: logged in
      data/TEST_RUNS.log; TEST answers whose ids are opaque are mapped back with --ids data/b0_test_ids.json)

Amendment 5 item 22: on M (migration) needs the source package (same PyPI name, or the same GitHub repository) is
removed from every candidate list and every agent answer before ranking or scoring, for every arm alike.

Arms: R0-R7 on the A1 pool (PR #171 recall) and on the A2 pool (A1 + the B0 names, always shortlisted); B0 and B1
(r3-tuning.md "Declared"); A3 (agent re-rank of A2's top 20); each --arm (an index's names added to the A2 pool,
not forced). TUNE only: a TEST need in any snapshot stops the run. Metrics: hit@3 (95% bootstrap, 10,000 resamples,
seed 169), hit@1, hit@10, MRR@10, harm = share of the top 3 that is HOLD, FAIL, unverified or unknown; paired hit@3
differences; strata A/P and head/tail (head_flags.json).
"""

import json
import re
import sys
import time
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


SPLIT = opt("--split", "tune")
if SPLIT in ("test", "code_test") and CMD == "score" and "--confirm-test-run" not in ARGS:
    sys.exit("TEST is scored once (PROTOCOL.md step 5): pass --confirm-test-run")
EXCL: dict[str, tuple[str, str]] = {}  # need id -> (source PyPI name, normalised; source repo), M needs only


def load(snap: Path, allowed: set[str]) -> dict[str, dict]:
    out = {}
    for f in snap.glob("*.json"):
        d = json.loads(f.read_text())
        if d["id"] not in allowed:
            sys.exit(f"{d['id']} is not in the {SPLIT} split")
        out[d["id"]] = d
    return out


def excluded(i: str, c: dict) -> bool:
    """Amendment 5 item 22: is this candidate the migration source of need i?"""
    if i not in EXCL:
        return False
    pkg, src = EXCL[i]
    r = (c.get("repo") or "").removeprefix("github.com/").lower()
    # a renamed copy of the source repository keeps its name part in the usual case: canonicalise only those
    same = bool(r and src and r.split("/")[-1] == src.split("/")[-1])
    return norm(c.get("name") or "") in (pkg, norm(src)) or bool(r and src and (r == src or (same and canon(r) == src)))


def cands_of(d: dict) -> list[dict]:
    return [c for c in d["candidates"] if not excluded(d["id"], c)]


def load_needs(split: dict) -> dict[str, dict]:
    """id -> {gold, stratum}; fills EXCL for M needs."""
    if SPLIT == "code_test":
        return {c["id"]: {**c, "gold": rb.canon(c["gold"]), "stratum": "C"} for c in rb.CODE_CASES}
    text = {n["id"]: n for n in rb.load_text_needs()}
    out = {i: text[i] for i in split[SPLIT] if i in text}
    if SPLIT == "test":
        mig = {n["id"]: n for n in rb.load_migration_needs(sample=False)}
        for i in split["test"]:
            if i in mig:
                out[i] = mig[i]
                EXCL[i] = (norm(mig[i]["pkg"]), (mig[i]["src_repo"] or "").lower())
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
    return {
        "hit1": float(r == 1),
        "hit3": float(0 < r <= 3),
        "hit10": float(r > 0),
        "mrr": 1 / r if r else 0.0,
        "harm3": sum(harm[:3]) / len(harm[:3]) if harm else 0.0,
    }


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


def nolic_harm(c: dict | None) -> bool:
    """Secondary harm (amendment 5 item 21): unknown, unverified, or HOLD/FAIL on a gate other than the licence G5."""
    if c is None or c["status"] in ("NOT_FOUND", "UNVERIFIED"):
        return True
    return any(v.startswith(("HOLD", "FAIL")) for g, v in (c.get("gates") or {}).items() if g != "G5")


def arm_metrics(top: list[dict], gold: set[str]) -> dict:
    m = metrics([cand_repo(c) for c in top], gold, [c["status"] == "HOLD" for c in top])
    m["harm3_nolic"] = sum(nolic_harm(c) for c in top[:3]) / len(top[:3]) if top else 0.0
    return m


def b_arms(d: dict, names: list[str], gold: set[str]) -> tuple[dict, dict, list]:
    rows = []
    for nm in names:
        c = find_named(d, nm)
        if (c is not None and excluded(d["id"], c)) or (d["id"] in EXCL and norm(nm) == EXCL[d["id"]][0]):
            continue  # amendment 5 item 22: the source package is not an answer
        st = c["status"] if c else "NOT_FOUND"
        repo = cand_repo(c) if c and st != "NOT_FOUND" else f"pypi:{norm(nm)}"
        rows.append((repo, st, c))
    rows = rows[:3]
    b0 = metrics([r for r, _, _ in rows], gold, [st not in GOOD for _, st, _ in rows])
    b0["harm3_nolic"] = sum(nolic_harm(c) for _, _, c in rows) / len(rows) if rows else 0.0
    keep = [(r, st, c) for r, st, c in rows if st in GOOD | {"HOLD"}]
    b1 = metrics([r for r, _, _ in keep], gold, [st == "HOLD" for _, st, _ in keep])
    b1["harm3_nolic"] = sum(nolic_harm(c) for _, _, c in keep) / len(keep) if keep else 0.0
    return b0, b1, rows


def table(rows: dict[str, list[dict]], ids: list[str], base: list[str], strata: dict[str, set[str]]) -> list[str]:
    out = [
        "| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR | harm share of top 3 | harm without licence | "
        + " | ".join(f"hit@3 minus {b} [95% CI]" for b in base)
        + " | "
        + " | ".join(f"hit@3 {s} (n={len(v)})" for s, v in strata.items())
        + " |",
        "|---" * (7 + len(base) + len(strata)) + "|",
    ]
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
        out.append(
            f"| {k} | {h3[0]:.2f} [{h3[1]:.2f}, {h3[2]:.2f}] | {avg('hit1'):.2f} | {avg('hit10'):.2f} | "
            f"{avg('mrr'):.2f} | {avg('harm3'):.2f} | {avg('harm3_nolic'):.2f} | "
            + " | ".join(diffs)
            + " | "
            + " | ".join(f"{x:.2f}" for x in st)
            + " |"
        )
    return out


def display(c: dict) -> str:
    return c["name"]


def shortlist_line(k: int, c: dict) -> str:
    gates = "; ".join(f"{g} {v}" for g, v in (c.get("gates") or {}).items() if not v.startswith(("pass", "n/a")))
    desc = re.sub(r"\s+", " ", c.get("description") or "")[:160]
    return (
        f"{k}. {display(c)} | {c['status'].upper()}{' (' + gates + ')' if gates else ''} | {desc} | "
        f"stars {c.get('stars') if c.get('stars') is not None else '?'} | downloads/month "
        f"{c.get('downloads_month') if c.get('downloads_month') is not None else '?'} | dependents "
        f"{c.get('dependents') if c.get('dependents') is not None else '?'} | last release "
        f"{c.get('last_release') or '?'} | licence {c.get('license') or '?'}"
    )


def miss_cause(top: list[dict], gold: set[str], pool: list[dict]) -> str:
    """PROTOCOL 8: not recalled / recalled but ranked low / gated out."""
    hits = [c for c in pool if c["status"] != "NOT_FOUND" and in_gold({c.get("repo") or ""}, gold)]
    if not hits:
        return "not recalled"
    if all(c["status"] in ("FAIL", "UNVERIFIED") for c in hits):
        return "gated out (" + ", ".join(sorted({c["name"] + " " + c["status"] for c in hits}))[:120] + ")"
    return "recalled but ranked low (" + ", ".join(sorted({c["name"] + " " + c["status"] for c in hits}))[:120] + ")"


def main() -> None:
    split = json.loads((HERE / "data" / "split.json").read_text())
    allowed = set(split[SPLIT])
    configs = {c["id"]: c for c in json.loads((HERE / "data" / "configs.json").read_text())["ranking_configs"]}
    text = load_needs(split)
    idmap = json.loads(Path(opt("--ids")).read_text()) if opt("--ids") else {}  # opaque id -> need id

    def answers_of(path: str) -> dict:
        raw = json.loads(Path(path).read_text())
        return {idmap.get(k, k): v for k, v in raw.items()}

    if SPLIT == "code_test":  # set C: `dev reuse --from` alone, R0 vs R4 (PROTOCOL 7: never tuned on)
        a1 = load(Path(opt("--a1")), allowed)
        ids = [i for i in split["code_test"] if i in a1]
        with (HERE / "data" / "TEST_RUNS.log").open("a") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%S')} r3_score.py TEST run #1 for set C ({len(ids)} cases, "
                f"`dev reuse --from`, R0 and R4), argv={ARGS}\n"
            )
        rows: dict[str, list[dict]] = {}
        lines = []
        for i in ids:
            for k in ("R0", "R4"):
                top = rank(a1[i]["candidates"], configs[k])
                rows.setdefault(f"A1 {k} (--from)", []).append(arm_metrics(top, text[i]["gold"]))
                if k == "R4":
                    m = rows["A1 R4 (--from)"][-1]
                    lines.append(
                        f"- {i}: query `{a1[i]['queries'][0]}`; top 3 {[c['name'] for c in top[:3]]}; "
                        + ("hit@3" if m["hit3"] else miss_cause(top, text[i]["gold"], a1[i]["candidates"]))
                    )
        print("\n".join([f"C cases scored: {len(ids)}", *table(rows, ids, ["A1 R0 (--from)"], {}), "", *lines]))
        return
    a2 = load(Path(opt("--a2")), allowed)
    if CMD == "a3-input":
        cfg = configs[opt("--config")]
        b0in = json.loads(Path(opt("--b0-input", str(HERE / "data" / "b0_tune_input.json"))).read_text())
        out = []
        for n in b0in:
            i = idmap.get(n["id"], n["id"])
            top = rank(cands_of(a2[i]), cfg, force="agent", cut=20)  # the n=10 shortlist, up to 20 shown
            out.append({**n, "shortlist": [shortlist_line(k + 1, c) for k, c in enumerate(top)]})
        Path(opt("--out")).write_text(json.dumps(out, indent=1))
        print(f"A3 input: {len(out)} needs, config {cfg['id']}")
        return
    if CMD == "headroom":
        index = json.loads(Path(opt("--index")).read_text())
        rows = []
        for i in split[SPLIT]:
            gold = text[i]["gold"]
            pool = gold_in({c.get("repo") or "" for c in a2[i]["candidates"]}, gold)
            top = gold_in({h["repo"] for h in index[i][:100]}, gold)
            rows.append((i, bool(pool), bool(top), sorted(top - pool)))
        k = len(rows)
        extra = [r for r in rows if r[3]]
        only = [r for r in rows if r[2] and not r[1]]
        print(
            f"{SPLIT} needs {k}; gold in the A2 pool {sum(r[1] for r in rows)}; gold in index top 100 "
            f"{sum(r[2] for r in rows)}"
        )
        print(
            f"headroom (PROTOCOL 16): needs with a gold repository in index top 100 absent from the A2 pool: "
            f"{len(extra)}/{k} = {len(extra) / k:.3f}"
        )
        print(f"needs whose only recalled gold is from the index: {len(only)}/{k} = {len(only) / k:.3f}")
        for r in extra:
            print(f"- {r[0]}: {', '.join(r[3])}")
        return
    a1 = load(Path(opt("--a1")), allowed)
    ids = [i for i in split[SPLIT] if i in a1 and i in a2]
    if SPLIT == "test":
        with (HERE / "data" / "TEST_RUNS.log").open("a") as fh:
            fh.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%S')} r3_score.py TEST run (A and M, {len(ids)} needs), argv={ARGS}\n"
            )
    heads = json.loads((HERE / "data" / "head_flags.json").read_text())
    strata = {
        s: {i for i in ids if text[i]["stratum"] == s}
        for s in ("A", "P", "M")
        if any(text[i]["stratum"] == s for i in ids)
    }
    strata |= {"top-1,000": {i for i in ids if heads.get(i)}, "long tail": {i for i in ids if not heads.get(i)}}
    if SPLIT == "test":
        strata["no TUNE overlap"] = {i for i in ids if i not in set(split["overlaps_tune"])}
    answers = answers_of(opt("--b0"))
    extra = {kv.split("=", 1)[0]: load(Path(kv.split("=", 1)[1]), allowed) for kv in opts("--arm")}
    a3 = answers_of(opt("--a3")) if opt("--a3") else None
    rows: dict[str, list[dict]] = {}
    pool_hit = {"A1": [], "A2": []}
    per_need: dict[str, dict] = {}
    misses: dict[str, list[str]] = {}
    show = configs if SPLIT == "tune" else {k: configs[k] for k in ("R0", "R4")}
    for i in ids:
        gold = text[i]["gold"]
        if SPLIT == "tune":
            raw = rank(a1[i]["candidates"], configs["R0"])
            rows.setdefault("A1 = R0, uncanonicalised (round-1 check)", []).append(
                {
                    **metrics([cand_repo(c, raw=True) for c in raw], gold, [c["status"] == "HOLD" for c in raw]),
                    "harm3_nolic": sum(nolic_harm(c) for c in raw[:3]) / len(raw[:3]) if raw else 0.0,
                }
            )
        for pool, snap, force in (("A1", a1, ""), ("A2", a2, "agent")):
            cands = cands_of(snap[i])
            pool_hit[pool].append(in_gold({c.get("repo") or "" for c in cands}, gold))
            for k, cfg in show.items():
                if pool == "A2" and SPLIT == "test" and k != "R4":
                    continue
                top = rank(cands, cfg, force)
                rows.setdefault(f"{pool} {k}", []).append(arm_metrics(top, gold))
                if (pool, k) == ("A2", "R4") and not rows[f"{pool} {k}"][-1]["hit3"]:
                    misses.setdefault("A2 R4", []).append(f"{i}: {miss_cause(top, gold, cands)}")
        b0, b1, named = b_arms(a2[i], answers.get(i, []), gold)
        rows.setdefault("B0", []).append(b0)
        rows.setdefault("B1", []).append(b1)
        if not b1["hit3"]:
            why = "not named" if not any(r in gold for r, _, _ in named) else "gated out"
            misses.setdefault("B1", []).append(
                f"{i}: {why} (B0 named {[c['name'] if c else '?' for _, _, c in named]})"
            )
        if a3 is not None:
            cfg = configs[json.loads(Path(opt("--a3")).read_text())["_config"]]
            short = {display(c): c for c in rank(cands_of(a2[i]), cfg, "agent", cut=20)}
            picked = [short[nm] for nm in a3.get(i, []) if nm in short]
            rows.setdefault("A3", []).append(arm_metrics(picked, gold))
            if not rows["A3"][-1]["hit3"]:
                inshort = [c for c in short.values() if in_gold({c.get("repo") or ""}, gold)]
                misses.setdefault("A3", []).append(
                    f"{i}: "
                    + (
                        "on the shortlist but not picked in the top 3 (" + ", ".join(c["name"] for c in inshort) + ")"
                        if inshort
                        else "not on the shortlist: " + miss_cause([], gold, cands_of(a2[i]))
                    )
                )
        for name, snap in extra.items():
            for k, cfg in show.items():
                if SPLIT == "test" and k != "R4":
                    continue
                top = rank(cands_of(snap[i]), cfg, "agent")
                rows.setdefault(f"{name} {k} (exploratory)" if SPLIT == "test" else f"{name} {k}", []).append(
                    arm_metrics(top, gold)
                )
        per_need[i] = {k: v[-1]["hit3"] for k, v in rows.items()}
    base = ["B1", "A1 R0"] + (["A2 R4"] if SPLIT == "test" else [])
    lines = [
        f"{SPLIT.upper()} needs scored: {len(ids)} of {len(split[SPLIT])}",
        f"gold anywhere in the A1 pool {sum(pool_hit['A1']) / len(ids):.2f}; in the A2 pool "
        f"{sum(pool_hit['A2']) / len(ids):.2f}",
        "",
    ]
    lines += table(rows, ids, base, strata)
    if SPLIT == "test":
        lines += ["", "Paired hit@3 differences within strata [95% CI]:"]
        for sname, sset in strata.items():
            sel = [k for k, i in enumerate(ids) if i in sset]
            for a, b in (("A2 R4", "B1"), ("A3", "B1"), ("A3", "A2 R4"), ("A1 R4", "A1 R0")):
                if a in rows and b in rows:
                    d = rb.boot([rows[a][k]["hit3"] - rows[b][k]["hit3"] for k in sel])
                    lines.append(f"- {sname} (n={len(sel)}): {a} minus {b} {d[0]:+.2f} [{d[1]:+.2f}, {d[2]:+.2f}]")
        lines += ["", "Harm share of the top 3, paired differences [95% CI]:"]
        for a, b in (("A2 R4", "B1"), ("A3", "B1"), ("A3", "A2 R4")):
            if a in rows:
                for key in ("harm3", "harm3_nolic"):
                    d = rb.boot([x[key] - y[key] for x, y in zip(rows[a], rows[b], strict=True)])
                    lines.append(f"- {a} minus {b}, {key}: {d[0]:+.3f} [{d[1]:+.3f}, {d[2]:+.3f}]")
        lines += ["", "Misses (PROTOCOL 8):"]
        for arm, ms in misses.items():
            lines.append(f"- {arm}: {len(ms)} misses")
            lines += [f"  - {m}" for m in ms]
    print("\n".join(lines))
    if "--choose" in ARGS:

        def key(k: str) -> tuple:
            m = sum(r["hit3"] for r in rows[f"A2 {k}"]) / len(ids)
            return (-round(m, 6), configs[k]["complexity"], k)

        ranked = sorted(configs, key=key)
        print("\nchoice order on the A2 pool (hit@3, then complexity, then id): " + ", ".join(ranked))
    if opt("--json"):
        Path(opt("--json")).write_text(
            json.dumps({"ids": ids, "rows": rows, "per_need": per_need, "misses": misses}, indent=1)
        )


if __name__ == "__main__":
    main()
