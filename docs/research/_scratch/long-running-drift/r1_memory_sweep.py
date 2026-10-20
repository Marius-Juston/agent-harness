"""R1 memory: model sweep that checks each auto-memory entry against its repository, scored against hand labels.

usage:
  python3 -I r1_memory_sweep.py run CONFIG.json OUT.jsonl [--model sonnet] [--jobs 6] [--today YYYY-MM-DD]
  python3 -I r1_memory_sweep.py score OUT.jsonl GOLD.csv

CONFIG.json (private, machine-local): {"<code>": {"entry": "<path to memory .md>", "repo": "<repo checkout>"}}.
`run` starts one read-only `claude -p` per entry inside the repo (tools: Read, Grep, Glob, git log/grep/show; auto memory,
MCP servers and project settings off) and stores its JSON verdict. `score` compares verdicts with GOLD.csv
(r1_memory_gold.csv) and prints precision and recall for "stale" (PARTLY-STALE or STALE), "promoted" and "conflict".
"""

import concurrent.futures as cf
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROMPT = """Today is {today}. Below is an auto-memory entry an AI coding agent saved about the repository in the current
directory. Audit it against the repository as it is now: read CLAUDE.md / AGENTS.md, .claude/ (agents, skills, settings,
hooks), docs/, board or issue files, and `git log`. Decide:
- status: CURRENT (every claim still holds), PARTLY-STALE (some claim, pointer or instruction is outdated, e.g. names
  work that is finished or a value that changed), STALE (its main claim or instruction is no longer true), or
  UNVERIFIABLE (depends on state outside this repository that you cannot check).
- promoted: FULL (the repository's instruction files, hooks or config already carry the rule or fact), PARTIAL (some of
  it), NONE.
- conflicts_repo: Y if the entry contradicts a current repository rule or instruction, else N.
Answer with one JSON object and nothing else:
{{"status": "...", "promoted": "...", "conflicts_repo": "Y|N", "evidence": "<one sentence naming files>"}}

--- ENTRY ---
{entry}
"""


def run_one(code: str, cfg: dict, model: str, today: str) -> dict:
    env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--no-session-persistence",
        "--strict-mcp-config",
        "--setting-sources",
        "user",
        "--output-format",
        "json",
        "--allowedTools",
        "Read",
        "Grep",
        "Glob",
        "Bash(git log:*)",
        "Bash(git grep:*)",
        "Bash(git show:*)",
        "--",
        PROMPT.format(today=today, entry=Path(cfg["entry"]).read_text(errors="replace")),
    ]
    try:
        p = subprocess.run(cmd, cwd=cfg["repo"], env=env, capture_output=True, text=True, timeout=600)
        out = json.loads(p.stdout)
        text = out.get("result", "")
        m = re.search(r"\{.*\}", text, re.S)
        verdict = json.loads(m.group(0)) if m else {"error": text[:300]}
        verdict.update(cost_usd=out.get("total_cost_usd"), turns=out.get("num_turns"))
    except Exception as exc:  # research script: record and continue
        verdict = {"error": repr(exc)[:300]}
    verdict["code"] = code
    return verdict


def score(out_path: str, gold_path: str) -> None:
    pred = {r["code"]: r for r in map(json.loads, Path(out_path).read_text().splitlines()) if "status" in r}
    with open(gold_path, newline="") as fh:
        gold = {r["code"]: r for r in csv.DictReader(fh)}
    codes = [c for c in gold if c in pred]
    print(f"scored {len(codes)} of {len(gold)} (errors/missing: {len(gold) - len(codes)})")
    tests = {
        "stale": (
            lambda g: g["status"] in ("PARTLY-STALE", "STALE"),
            lambda p: p["status"] in ("PARTLY-STALE", "STALE"),
        ),
        "promoted(any)": (lambda g: g["promoted"] != "NONE", lambda p: p["promoted"] != "NONE"),
        "promoted(FULL)": (lambda g: g["promoted"] == "FULL", lambda p: p["promoted"] == "FULL"),
        "conflict": (lambda g: g["conflicts_repo"] == "Y", lambda p: p["conflicts_repo"] == "Y"),
    }
    for name, (gf, pf) in tests.items():
        tp = sum(1 for c in codes if gf(gold[c]) and pf(pred[c]))
        fp = [c for c in codes if not gf(gold[c]) and pf(pred[c])]
        fn = [c for c in codes if gf(gold[c]) and not pf(pred[c])]
        prec = tp / (tp + len(fp)) if tp + len(fp) else float("nan")
        rec = tp / (tp + len(fn)) if tp + len(fn) else float("nan")
        print(
            f"{name:15} TP={tp:2} FP={len(fp):2} FN={len(fn):2} precision={prec:.2f} recall={rec:.2f} FP={fp} FN={fn}"
        )
    exact = sum(1 for c in codes if pred[c]["status"] == gold[c]["status"])
    print(f"status exact agreement {exact}/{len(codes)}")
    cost = sum(pred[c].get("cost_usd") or 0 for c in codes)
    print(
        f"total API-equivalent cost ${cost:.2f}, mean turns {sum(pred[c].get('turns') or 0 for c in codes) / max(1, len(codes)):.1f}"
    )


def main() -> None:
    if sys.argv[1] == "score":
        score(sys.argv[2], sys.argv[3])
        return
    cfg = json.loads(Path(sys.argv[2]).read_text())
    out = Path(sys.argv[3])
    args = sys.argv[4:]
    model = args[args.index("--model") + 1] if "--model" in args else "sonnet"
    jobs = int(args[args.index("--jobs") + 1]) if "--jobs" in args else 6
    today = args[args.index("--today") + 1] if "--today" in args else "2026-10-19"
    done = {json.loads(line)["code"] for line in out.read_text().splitlines()} if out.exists() else set()
    with cf.ThreadPoolExecutor(jobs) as ex, open(out, "a") as fh:
        futs = [ex.submit(run_one, c, v, model, today) for c, v in cfg.items() if c not in done]
        for f in cf.as_completed(futs):
            r = f.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(r.get("code"), r.get("status"), r.get("promoted"), r.get("conflicts_repo"), r.get("error", "")[:80])


if __name__ == "__main__":
    main()
