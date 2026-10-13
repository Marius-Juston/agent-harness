"""#175 TEST: old vs new `dev reuse --from` need text on the frozen held-out set D (PROTOCOL.md amendment 6).

Usage (from an agent-harness checkout at the #175 branch, so `devkit` is the new module):
  git show f1cfce7:devkit/reuse.py > <scratch>/reuse_old.py
  uv run --with model2vec python run_code_175.py <code_cases_175.json> <scratch>/reuse_old.py <out_prefix>
      --confirm-test-run
  uv run python run_code_175.py <code_cases_175.json> --verify-only   (fetch and hash-check every case; no search)

Each case's helper text is third-party code, so it is not stored: it is fetched from raw.githubusercontent.com at
the pinned parent commit, cut as build_code_cases_175.helper_text does, and must match `text_sha256` (else exit).
Arms (both through the same, current `reuse.search`: R4 order, n = 10, pypi, model2vec when it loads):
  old  need_from of main @ f1cfce7 (name, every docstring, comments, all imports) on the case's text
  new  need_from of #175 (name, the docstring's first sentence, third-party imports; own modules from `files`)
Metrics: hit@3 (primary), hit@1, hit@10, MRR@10; paired hit@3 difference new - old with a 95% percentile bootstrap
(10,000 resamples of cases, seed 175). A candidate is gold when its PyPI name (normalised) is the gold's or its
repository is the gold repository. Writes <out_prefix>.json (per case) and <out_prefix>.md (the report).
"""

import hashlib
import importlib.util
import json
import random
import re
import sys
import time
from pathlib import Path

import build_code_cases_175 as build  # beside this script

from devkit import reuse

HERE = Path(__file__).resolve().parent
LOG = HERE / "data" / "TEST_RUNS.log"
PROTOCOL = HERE.parent / "PROTOCOL.md"
N, DRAWS, SEED = 10, 10_000, 175


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def helper_text(case: dict) -> str:
    """The case's helper text, fetched at its pinned commit; exits when it is not the text that was frozen."""
    text = build.helper_text(build.raw(case["repo"], case["parent"], case["path"]), case["symbol"])
    got = hashlib.sha256((text or "").encode()).hexdigest()
    if got != case["text_sha256"]:
        sys.exit(f"{case['id']}: fetched text SHA-256 {got} is not the frozen {case['text_sha256']}")
    return text


def rank(cands: list[dict], case: dict) -> int | None:
    """1-based rank of the first gold candidate, None when none of the n shown is gold."""
    for i, c in enumerate(cands, 1):
        repo = (c["repo"] or "").removeprefix("github.com/").lower()
        if (c["ecosystem"] == "pypi" and norm(c["name"]) == norm(case["gold_pypi"])) or (
            case["gold_repo"] and repo == case["gold_repo"]
        ):
            return i
    return None


def boot(xs: list[float], rng: random.Random) -> tuple[float, float]:
    n = len(xs)
    means = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(DRAWS))
    return means[int(0.025 * DRAWS)], means[int(0.975 * DRAWS) - 1]


def metrics(ranks: list[int | None]) -> dict:
    n = len(ranks)
    return {
        "hit@1": sum(r is not None and r <= 1 for r in ranks) / n,
        "hit@3": sum(r is not None and r <= 3 for r in ranks) / n,
        "hit@10": sum(r is not None for r in ranks) / n,
        "mrr@10": sum(1 / r for r in ranks if r) / n,
    }


def main() -> None:
    if "--verify-only" in sys.argv:
        for case in json.loads(Path(sys.argv[1]).read_text())["cases"]:
            helper_text(case)
            print(f"ok {case['id']}", flush=True)
        return
    cases_path, old_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    digest = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    frozen = digest in PROTOCOL.read_text()
    if "--dev" in sys.argv:  # a pipeline check on a hand-made, non-TEST case file; never on set D
        if frozen:
            sys.exit("--dev never runs on the frozen set D")
    elif not frozen:
        sys.exit(f"{cases_path} (SHA-256 {digest}) is not the set frozen in PROTOCOL.md amendment 6")
    elif "--confirm-test-run" not in sys.argv:
        sys.exit("set D is a TEST set, run once (PROTOCOL.md amendment 6): pass --confirm-test-run")
    elif "set D" in LOG.read_text():
        sys.exit("set D was already run (data/TEST_RUNS.log): a re-run must be declared in PROTOCOL.md first")
    spec = importlib.util.spec_from_file_location("reuse_old", old_path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    if frozen:
        with LOG.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} run_code_175.py TEST run #1 for set D (#175, old vs new "
                    f"--from), cases SHA-256 {digest}\n")  # fmt: skip
    cases = json.loads(cases_path.read_text())["cases"]
    encoder, why = reuse.load_encoder()
    client = reuse.client()
    rows = []
    for k, case in enumerate(cases, 1):
        own = reuse.own_modules(case["files"], case["path"])
        text = case["text"] if "--dev" in sys.argv else helper_text(case)
        needs = {
            "old": old.need_from(text, case["symbol"]),
            "new": reuse.need_from(text, case["symbol"], own),
        }
        row = {"id": case["id"], "gold": case["gold_pypi"], "gold_repo": case["gold_repo"]}
        for arm, need in needs.items():
            rep = reuse.search(client, [need], "pypi", N, encoder, why)
            cands = [
                {"name": c.name, "ecosystem": c.ecosystem, "repo": c.repo, "status": c.status, "score": c.score}
                for c in rep["candidates"]
            ]
            row[arm] = {"need_words": len(need.split()), "rerank": rep["rerank"], "rank": rank(cands, case), "top": cands,
                        "notes": rep["notes"]}  # fmt: skip
        rows.append(row)
        print(f"{k}/{len(cases)} {case['id']}: old {row['old']['rank']} new {row['new']['rank']}", flush=True)
    rng = random.Random(SEED)  # noqa: S311 (a seeded bootstrap, not a secret)
    old_r, new_r = [r["old"]["rank"] for r in rows], [r["new"]["rank"] for r in rows]
    hit = {arm: [float(x is not None and x <= 3) for x in rs] for arm, rs in (("old", old_r), ("new", new_r))}
    summary = {
        "n": len(rows),
        "rerank": rows[0]["new"]["rerank"] if rows else "",
        "old": {**metrics(old_r), "hit@3 ci": boot(hit["old"], rng)},
        "new": {**metrics(new_r), "hit@3 ci": boot(hit["new"], rng)},
    }
    diff = [b - a for a, b in zip(hit["old"], hit["new"], strict=True)]
    summary["diff hit@3"] = sum(diff) / len(diff)
    summary["diff hit@3 ci"] = boot(diff, rng)
    out.with_suffix(".json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str))
    md = [
        f"Set D (#175), {summary['n']} cases, `dev reuse --from` alone, R4, n = {N}; re-rank: {summary['rerank']}; "
        f"run {time.strftime('%Y-%m-%d')}; cases SHA-256 `{digest}`",
        "",
        "| arm | hit@3 [95% CI] | hit@1 | hit@10 | MRR@10 |",
        "|---|---|---|---|---|",
    ]
    for arm in ("old", "new"):
        s = summary[arm]
        lo, hi = s["hit@3 ci"]
        md.append(f"| {arm} | {s['hit@3']:.2f} [{lo:.2f}, {hi:.2f}] | {s['hit@1']:.2f} | {s['hit@10']:.2f} | "
                  f"{s['mrr@10']:.2f} |")  # fmt: skip
    lo, hi = summary["diff hit@3 ci"]
    md += ["", f"Paired hit@3 new - old: {summary['diff hit@3']:+.2f} [{lo:+.2f}, {hi:+.2f}]", ""]
    for r in rows:
        md.append(f"- {r['id']} -> {r['gold']}")
        for arm in ("old", "new"):
            top = [c["name"] for c in r[arm]["top"][:3]]
            md.append(f"  - {arm} rank {r[arm]['rank'] or '>10'}; top 3 {top}")
    out.with_suffix(".md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:8]))


if __name__ == "__main__":
    main()
