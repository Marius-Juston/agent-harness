"""Replay the ranking configurations of data/configs.json on a `dev reuse` snapshot (run_reuse_snapshot.py), offline.

Usage: uv run python replay.py <snapshot_dir> <recall_bench cache_dir> [--configs R0,R1] [--b0 <answers.json>]

R0 is PR #171 as built, so R0 on the snapshot is arm A1. Gold comes from recall_bench (canonical GitHub names). Only
needs in split.json's TUNE list are scored; a TEST need in the snapshot stops the run (PROTOCOL.md step 5).
Metrics (PROTOCOL.md step 4): hit@3 primary with a 95% bootstrap interval (10,000 resamples, seed 169), hit@1,
hit@10, MRR@10, and the share of top-3 results that are HOLD; paired hit@3 differences against R0.

--b0 scores arm B0 (the agent's own top 3 from memory): a JSON object {need id: [3 PyPI names]}. Each name is looked
up on PyPI (JSON API) for its GitHub repository; a name PyPI does not know counts as harm (does not exist).
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP, CACHE = Path(sys.argv[1]), sys.argv[2]
sys.argv = [sys.argv[0], CACHE, *sys.argv[3:]]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)

GOOD = {"pass", "flag"}


def repo_name(c: dict) -> str:
    return (c.get("repo") or "").removeprefix("github.com/").lower()


def adoption(c: dict) -> tuple[int, int]:
    return (c.get("dependents") or -1, c.get("downloads_month") or -1)


def rank(cands: list[dict], cfg: dict, n: int = 10) -> list[dict]:
    if cfg["shortlist"] == "prior2n":
        short = sorted(cands, key=lambda c: -c["prior"])[: 2 * n]
    else:
        best = max((c["relevance"] for c in cands), default=0.0)
        by_r = sorted(cands, key=lambda c: -c["relevance"])[:n]
        near = [c for c in cands if c["relevance"] >= best - 0.20]
        by_a = sorted(near, key=adoption, reverse=True)[:n]
        seen, short = set(), []
        for c in by_r + by_a:
            if c["name"] not in seen:
                seen.add(c["name"])
                short.append(c)
    if any(c["status"] == "NOT_ENRICHED" for c in short):
        raise RuntimeError("the snapshot lacks a candidate this config shortlists")
    kept = [c for c in short if c["status"] in GOOD | {"HOLD"}]
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
    return order[:n]


def metrics(top: list[str], gold: set[str], holds: list[bool]) -> dict:
    r = next((k + 1 for k, nm in enumerate(top) if nm in gold), 0)
    return {
        "hit1": float(r == 1),
        "hit3": float(0 < r <= 3),
        "hit10": float(r > 0),
        "mrr": 1 / r if r else 0.0,
        "hold3": sum(holds[:3]) / max(1, min(3, len(holds))),
    }


def table(rows: dict[str, list[dict]], base: str | None) -> None:
    print(
        "| arm | n | hit@3 [95% CI] | hit@1 | hit@10 | MRR | HOLD share of top 3 | hit@3 minus "
        + (base or "-")
        + " [95% CI] |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for k, rs in rows.items():
        n = len(rs)
        h3 = rb.boot([r["hit3"] for r in rs])
        d = (
            rb.boot([a["hit3"] - b["hit3"] for a, b in zip(rs, rows[base], strict=True)])
            if base and k != base
            else None
        )

        def avg(key: str, rs: list[dict] = rs, n: int = n) -> float:
            return sum(r[key] for r in rs) / n

        print(
            f"| {k} | {n} | {h3[0]:.2f} [{h3[1]:.2f}, {h3[2]:.2f}] | {avg('hit1'):.2f} | {avg('hit10'):.2f} | "
            f"{avg('mrr'):.2f} | {avg('hold3'):.2f} | "
            + (f"{d[0]:+.2f} [{d[1]:+.2f}, {d[2]:+.2f}]" if d else "-")
            + " |"
        )


def main() -> None:
    split = json.loads((HERE / "data" / "split.json").read_text())
    configs = {c["id"]: c for c in json.loads((HERE / "data" / "configs.json").read_text())["ranking_configs"]}
    if "--configs" in sys.argv:
        configs = {k: configs[k] for k in sys.argv[sys.argv.index("--configs") + 1].split(",")}
    text = {n["id"]: n for n in rb.load_text_needs()}
    snaps = {}
    for f in SNAP.glob("*.json"):
        d = json.loads(f.read_text())
        if d["id"] not in split["tune"]:
            sys.exit(f"{d['id']} is not a TUNE need: replay scores TUNE only")
        snaps[d["id"]] = d
    ids = [i for i in split["tune"] if i in snaps]
    print(f"TUNE needs in the snapshot: {len(ids)}/{len(split['tune'])}")
    rows: dict[str, list[dict]] = {}
    for i in ids:
        gold = text[i]["gold"]
        cands = snaps[i]["candidates"]
        recalled = {repo_name(c) for c in cands}
        rows.setdefault("recall: any gold in the pool", []).append(
            {**metrics([], gold, []), "hit10": float(bool(gold & recalled))}
        )
        for k, cfg in configs.items():
            top = rank([dict(c) for c in cands], cfg)
            rows.setdefault(k, []).append(
                metrics([repo_name(c) for c in top], gold, [c["status"] == "HOLD" for c in top])
            )
    if "--b0" in sys.argv:
        answers = json.loads(Path(sys.argv[sys.argv.index("--b0") + 1]).read_text())
        for i in ids:
            names = answers.get(i, [])[:3]
            repos, missing = [], []
            for nm in names:
                j = rb.pypi(nm)
                missing.append(j is None)
                r = rb.pypi_repo(j["info"]) if j else None
                info = rb.gh_repo(r) if r else None
                repos.append(info["name"] if info else (r or f"pypi:{nm}"))
            m = metrics(repos, text[i]["gold"], missing)
            m["hold3"] = sum(missing) / max(1, len(names))  # harm here = names PyPI does not know
            rows.setdefault("B0 agent memory (harm = name not on PyPI)", []).append(m)
    pool = rows.pop("recall: any gold in the pool")
    print(
        f"gold anywhere in the recalled pool (ceiling for every config): {sum(r['hit10'] for r in pool) / len(pool):.2f}\n"
    )
    table(rows, "R0" if "R0" in rows else None)
    digest = hashlib.sha256((HERE / "data" / "configs.json").read_bytes()).hexdigest()
    print(f"\nconfigs.json sha256 {digest}")


if __name__ == "__main__":
    main()
