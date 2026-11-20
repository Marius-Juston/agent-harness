"""Every number the note gate-speed.md quotes, from the files the other scripts wrote (#520).

Inputs (paths as arguments, in this order):
  timeline.json      integrate_timeline.py output (lead integrate calls joined to integrate logs)
  worker.json        worker_gates.py output (worker `dev check` calls)
  runs_jobs.json     actions_runs.py output (GitHub Actions ci runs)
  gate dir           the state dir's gate/ (one pass record per tree key: when, seconds)
  test-history.jsonl the state dir's per-test history

Usage: python summarize.py timeline.json worker.json runs_jobs.json <gate dir> <test-history.jsonl>
Prints numbers only (no paths, no transcript text).
"""

import collections
import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path


def q(v: list[float]) -> str:
    v = sorted(x for x in v if x is not None)
    n = len(v)
    return f"n={n} median={st.median(v):g} mean={sum(v) / n:.0f} p10={v[int(0.1 * (n - 1))]:g} p90={v[int(0.9 * (n - 1))]:g} max={v[-1]:g}"


def integrates(path: Path) -> None:
    rows = [r for r in json.loads(path.read_text()) if "gate_s" in r]
    print("## integrate calls that merged (Oct 22, Nov 1), full phase data")
    for k in ("wall_s", "pre_s", "lock_s", "gate_s", "standin_s", "post_s", "tail_s"):
        print(f"{k:10s} {q([r[k] for r in rows])}")
    total = sum(r["wall_s"] for r in rows)
    print(
        "share of summed wall:",
        {
            k: round(100 * sum(r[k] for r in rows) / total, 1)
            for k in ("pre_s", "lock_s", "gate_s", "standin_s", "post_s", "tail_s")
        },
    )
    base_pre = st.median([r["pre_s"] for r in rows if r["pre_s"] < 60])
    base_post = st.median([r["post_s"] for r in rows if not r["updated"]])
    actions = [max(0, r["pre_s"] + r["post_s"] - base_pre - base_post) for r in rows]
    print(f"baseline pre (no check wait) {base_pre:g} s; baseline post (no pushed tip) {base_post:g} s")
    print(f"Actions wait = pre + post - baselines: {q(actions)}")
    print(f"Actions wait share of summed wall: {100 * sum(actions) / total:.1f}%")
    for u in (True, False):
        xs = [r for r in rows if r["updated"] is u]
        print(
            f"updated={u}: n={len(xs)} pre {q([r['pre_s'] for r in xs])}; post {q([r['post_s'] for r in xs])}; "
            f"wall {q([r['wall_s'] for r in xs])}"
        )
    gs = [r["gate_s"] + r["standin_s"] for r in rows]
    print(f"gate + stand-ins: {q(gs)}")
    print(
        f"pipelining saving per integrate = min(gate + stand-ins, Actions wait): "
        f"{q([min(g, a) for g, a in zip(gs, actions, strict=True)])}"
    )
    hit = sum(not r["updated"] for r in rows)
    print(f"merged tree == PR head (updated False): {hit}/{len(rows)} = {100 * hit / len(rows):.0f}%")


def workers(path: Path) -> None:
    rows = json.loads(path.read_text())
    print("\n## worker gates (`dev check` calls with a RESULT line)")
    by = collections.defaultdict(list)
    for r in rows:
        by["2026-11-19" if r["day"] == "2026-11-19" else "2026-10-20..11-01"].append(r)
    for day, xs in sorted(by.items()):
        print(f"{day}: wall {q([x['wall_s'] for x in xs])}")
        print(f"{'':17s} stage {q([x['stage_s'] for x in xs])}")
        print(f"{'':17s} wall-stage (slot wait + fixed costs) {q([x['wall_s'] - x['stage_s'] for x in xs])}")
        print(f"{'':17s} failed {sum(not x['passed'] for x in xs)}/{len(xs)}")


def actions(path: Path) -> None:
    rows = json.loads(path.read_text())
    print(f"\n## GitHub Actions ci runs ({rows[-1]['created'][:10]} .. {rows[0]['created'][:10]})")
    print(f"run wall (created -> last job done) {q([r['wall_s'] for r in rows])}")
    for job in ("check", "template", "plugin"):
        js = [r["jobs"][job] for r in rows if job in r["jobs"]]
        print(f"job {job}: queue {q([j['queue_s'] for j in js])}; run {q([j['run_s'] for j in js])}")
    steps = collections.defaultdict(list)
    for r in rows:
        for k, v in r["jobs"].get("check", {}).get("steps", {}).items():
            steps[k].append(v)
    for k, v in steps.items():
        if st.median(v) >= 2:
            print(f"  check step {k[:50]!r}: {q(v)}")


def gate_records(path: Path) -> None:
    print("\n## gate pass records (stage seconds of passing gates, worker and integrate)")
    by = collections.defaultdict(list)
    for f in path.glob("*.json"):
        d = json.loads(f.read_text())
        by[d["when"][:10]].append(d["seconds"])
    for day in sorted(by):
        if day >= "2026-10-19":
            print(f"{day}: {q(by[day])}")
    light = [s for d, v in by.items() if "2026-10-20" <= d <= "2026-11-01" for s in v]
    print(f"2026-10-20..11-01 pooled: {q(light)}")


def history(path: Path) -> None:
    print("\n## per-test history")
    runs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    groups = collections.defaultdict(list)
    for r in runs:
        groups[datetime.fromtimestamp(r["at"]).strftime("%Y-%m-%d")].append(r)
    for day, rs in sorted(groups.items()):
        per = collections.defaultdict(list)
        for r in rs:
            for k, v in r["tests"].items():
                per[k].append(v[1])
        med = sorted(((st.median(v), k) for k, v in per.items()), reverse=True)
        total = sum(m for m, _ in med)
        counts = [len(r["tests"]) for r in rs]
        sums = [sum(v[1] for v in r["tests"].values()) for r in rs]
        print(f"{day}: runs={len(rs)} tests {min(counts)}-{max(counts)}; summed test seconds per run {q(sums)}")
        if len(rs) > 1:
            print(
                f"   slowest test median {med[0][0]:.2f} s; tests over 1 s: {sum(m > 1 for m, _ in med)}; "
                f"top 10 = {100 * sum(m for m, _ in med[:10]) / total:.0f}% and top 50 = "
                f"{100 * sum(m for m, _ in med[:50]) / total:.0f}% of summed test seconds"
            )
            for m, k in med[:5]:
                print(f"     {m:.2f} s {k}")


def main() -> None:
    integrates(Path(sys.argv[1]))
    workers(Path(sys.argv[2]))
    actions(Path(sys.argv[3]))
    gate_records(Path(sys.argv[4]))
    history(Path(sys.argv[5]))


if __name__ == "__main__":
    main()
