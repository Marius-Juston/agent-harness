"""Dry check before scoring: does every snapshot hold an enriched candidate for every shortlist R0/R4 can build after
the M-source exclusion? Prints only need ids that fail, never a metric (so it is not a TEST run).

Usage: uv run --no-project python check_shortlists.py <recall_bench cache> --split test <snap> [<snap> ...]
"""

import json
import sys
from pathlib import Path

CACHE, SPLIT, SNAPS = sys.argv[1], sys.argv[3], sys.argv[4:]
sys.argv = [sys.argv[0], CACHE, "check", "--split", SPLIT]
import r3_score as r3  # noqa: E402 (reads its arguments at import)


def main() -> None:
    split = json.loads((r3.HERE / "data" / "split.json").read_text())
    r3.load_needs(split)
    configs = {c["id"]: c for c in json.loads((r3.HERE / "data" / "configs.json").read_text())["ranking_configs"]}
    for snap in SNAPS:
        bad = []
        for i, d in r3.load(Path(snap), set(split[SPLIT])).items():
            for k in ("R0", "R4"):
                try:
                    r3.rank(r3.cands_of(d), configs[k], "agent", cut=20)
                except RuntimeError:
                    bad.append(f"{i} ({k})")
        print(f"{snap}: {len(bad)} shortlists lack enrichment {bad}")


if __name__ == "__main__":
    main()
