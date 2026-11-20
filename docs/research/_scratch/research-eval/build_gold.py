"""Build evals/research/gold.json for `dev eval research` (#590): the cited works of each question's known answer note.

Usage: uv run python docs/research/_scratch/research-eval/build_gold.py

A1-A4 are #192's public gold, copied unchanged (gold_A.json plus each work's link texts from alt_titles_A.json), so
this eval's L0 numbers can be checked against lit-search-quality.md. B5-B7 are resolved here by the same code
(lit-recall's `ids_in` and `resolve_gold.resolve`: OpenAlex batches through the shared `devkit.lit` client, DataCite
for arXiv ids OpenAlex lacks). Run once; the questions and their notes are in evals/research/questions.yaml.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "docs/research/_scratch/lit-recall/scripts"))

import yaml  # noqa: E402
from common import ids_in, load  # noqa: E402
from resolve_gold import resolve  # noqa: E402

from devkit import lit  # noqa: E402


def main() -> None:
    spec = yaml.safe_load((ROOT / "evals/research/questions.yaml").read_text())
    gold_a, alt_a = load("gold_A.json"), load("alt_titles_A.json")
    out, counts = {}, {}
    c = lit.client()
    for q in spec["questions"]:
        qid = q["id"]
        if qid in gold_a:
            out[qid] = [{**g, "alt": alt_a.get(qid, {}).get(g["key"], [])} for g in gold_a[qid]]
            counts[qid] = {"gold": len(out[qid]), "from": "lit-recall gold_A.json"}
            continue
        raw = {k: sorted(v) for k, v in ids_in((ROOT / q["note"]).read_text()).items()}
        works, cnt = resolve(c, raw)
        out[qid] = [{**w, "alt": []} for w in works]
        counts[qid] = cnt
        print(qid, cnt)
    path = ROOT / "evals/research/gold.json"
    path.write_text(
        json.dumps({"_how": __doc__.split("\n\n")[1].replace("\n", " "), "counts": counts, **out}, indent=1)
    )
    print(path, {k: len(v) for k, v in out.items()})


if __name__ == "__main__":
    main()
