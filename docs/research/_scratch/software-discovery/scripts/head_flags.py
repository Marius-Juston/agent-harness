"""PROTOCOL.md amendment 14: flag each need whose gold includes a top-1,000 PyPI package by downloads (the head).

Usage: uv run python head_flags.py <recall_bench cache_dir> <pypi_index.py output, sorted by downloads>
Writes data/head_flags.json: {need id: true|false}. A package counts through its GitHub repository (pypi_index.py's
`repo`), matched to the gold's canonical repository names.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE, INDEX = sys.argv[1], Path(sys.argv[2])
sys.argv = [sys.argv[0], CACHE]
import recall_bench as rb  # noqa: E402 (reads the cache argument at import)


def main() -> None:
    rows = [json.loads(line) for line in INDEX.read_text().splitlines()[:1000]]
    head = {r["repo"] for r in rows if r["repo"]}
    head |= {(rb.gh_repo(r) or {}).get("name", r) for r in list(head)}
    flags = {n["id"]: bool(n["gold"] & head) for n in rb.load_text_needs()}
    flags |= {n["id"]: bool(n["gold"] & head) for n in rb.load_migration_needs(sample=False)}
    flags |= {c["id"]: bool(rb.canon(c["gold"]) & head) for c in rb.CODE_CASES}
    (HERE / "data" / "head_flags.json").write_text(json.dumps(flags, indent=1, sort_keys=True) + "\n")
    split = json.loads((HERE / "data" / "split.json").read_text())
    for part in ("tune", "test", "code_test"):
        ids = split[part]
        print(f"{part}: {sum(flags[i] for i in ids)}/{len(ids)} needs have a top-1,000 gold package")


if __name__ == "__main__":
    main()
