"""Freeze the #176 rerun needs, gold, split and agent inputs (rerun PROTOCOL.md rules R1-R3). Run once, before any run.

Usage: uv run --no-project python build_needs.py <recall_bench cache_dir> <latest_needs.json from build_latest.py>

Strata, none of them in #169's split (../../scripts/data/split.json):
- A2 (fresh awesome-python): every eligible subcategory of awesome-python@ba49ec7 (build_gold.parse, #169's rule) not
  among #169's 120 drawn needs, minus the section "Miscellaneous" (no specific need, as #169 drops "General",
  "Others" and "Miscellaneous" subcategories). need = the label (#169's q0); also_described_as = the list's own section
  description. Nothing is paraphrased by hand or by a model. Id = the label, as in #169.
- M2 (fresh PyMigBench): every eligible migration source (recall_bench.load_migration_needs, #169's rule) not among
  #169's 40 sampled. need = the source package's PyPI summary; also = its keywords (as #169). Opaque id M2-xx; the
  source package is excluded from every arm's answers before scoring (#169 rule 22).
- L (latest): build_latest.py's needs (post-cutoff gold, masked summary). Opaque id L-xxx.
Split: within each stratum, ids sorted, shuffled with random.Random("176-split"), the first round(0.4 n) are TUNE
and the rest TEST. `overlaps_tune` lists TEST needs whose gold shares a repository with a TUNE need's gold;
`overlaps_169` lists needs whose gold shares a repository with any #169 need's gold.
Writes data/needs.json, data/split.json, data/inputs/<split>_<base|latest>.json and prints the SHA-256 of each.
"""

import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE.parent.parent / "scripts"
DATA = HERE.parent / "data"
sys.path.insert(0, str(OLD))
LATEST = Path(sys.argv[2])
sys.argv = [sys.argv[0], sys.argv[1]]
import build_gold as bg  # noqa: E402
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)


def norm(s: str) -> str:
    return re.sub(r"[-_.]+", "-", s).lower()


def main() -> None:
    out = DATA / "split.json"
    if out.exists():
        sys.exit(f"{out} exists: the rerun split is frozen.")
    old_split = json.loads((OLD / "data" / "split.json").read_text())
    old_ids = set(old_split["tune"]) | set(old_split["test"])
    old_gold = set().union(*(n["gold"] for n in rb.load_text_needs()))
    old_gold |= set().union(*(n["gold"] for n in rb.load_migration_needs(sample=True)))
    needs: list[dict] = []
    md = (OLD / "data" / "awesome-python@ba49ec7.md").read_text()
    for n in bg.parse(md):
        if n["id"] in old_ids or n["id"] == "Miscellaneous":
            continue
        needs.append(
            {
                "id": n["id"],
                "stratum": "A2",
                "need": n["q0"],
                "also_described_as": [n["desc"]] if n["desc"] else [],
                "gold": sorted(rb.canon(n["gold"])),
                "source": f"awesome-python@ba49ec7 {n['id']}",
            }
        )
    fresh_m = [m for m in rb.load_migration_needs(sample=False) if m["id"] not in old_ids]
    for k, m in enumerate(sorted(fresh_m, key=lambda m: m["id"]), 1):
        kw = m["keywords"].replace(",", " ").strip()
        needs.append(
            {
                "id": f"M2-{k:02d}",
                "stratum": "M2",
                "need": m["summary"],
                "also_described_as": [kw] if kw else [],
                "gold": sorted(m["gold"]),
                "exclude": [norm(m["pkg"]), (m["src_repo"] or "").lower()],
                "source": m["id"],
            }
        )
    for k, n in enumerate(json.loads(LATEST.read_text()), 1):
        repo = rb.canon([n["repo"]])
        needs.append(
            {
                "id": f"L-{k:03d}",
                "stratum": "L",
                "need": n["need"],
                "also_described_as": n["also_described_as"],
                "gold": sorted(repo) + [f"pypi:{norm(p)}" for p in n["packages"]],
                "source": n["repo"],
                "first_upload": n["first_upload"],
                "repo_created": n["repo_created"],
                "stars_at_fetch": n["stars_at_fetch"],
            }
        )
    split: dict = {"tune": [], "test": []}
    for s in ("A2", "M2", "L"):
        ids = sorted(n["id"] for n in needs if n["stratum"] == s)
        random.Random("176-split").shuffle(ids)  # noqa: S311 (split, not security)
        k = round(0.4 * len(ids))
        split["tune"] += sorted(ids[:k])
        split["test"] += sorted(ids[k:])
    by_id = {n["id"]: n for n in needs}
    tune_gold = set().union(*(by_id[i]["gold"] for i in split["tune"]))
    split["overlaps_tune"] = sorted(i for i in split["test"] if set(by_id[i]["gold"]) & tune_gold)
    split["overlaps_169"] = sorted(i for i in by_id if set(by_id[i]["gold"]) & old_gold)
    split["counts"] = {
        f"{part}_{s}": sum(by_id[i]["stratum"] == s for i in split[part])
        for part in ("tune", "test")
        for s in ("A2", "M2", "L")
    }
    split["rules"] = __doc__.split("Strata")[1].split("Writes")[0].strip()
    files = {
        DATA / "needs.json": json.dumps(needs, indent=1, ensure_ascii=False) + "\n",
        out: json.dumps(split, indent=1, ensure_ascii=False) + "\n",
    }
    for part in ("tune", "test"):
        for kind, strata in (("base", ("A2", "M2")), ("latest", ("L",))):
            rows = [
                {"id": i, "need": by_id[i]["need"], "also_described_as": by_id[i]["also_described_as"]}
                for i in split[part]
                if by_id[i]["stratum"] in strata
            ]
            files[DATA / "inputs" / f"{part}_{kind}.json"] = json.dumps(rows, indent=1, ensure_ascii=False) + "\n"
    (DATA / "inputs").mkdir(parents=True, exist_ok=True)
    for f, body in files.items():
        f.write_text(body)
        print("sha256", hashlib.sha256(body.encode()).hexdigest(), f.relative_to(DATA))
    print(
        json.dumps(split["counts"]),
        "overlaps_tune",
        len(split["overlaps_tune"]),
        "overlaps_169",
        len(split["overlaps_169"]),
    )


if __name__ == "__main__":
    main()
