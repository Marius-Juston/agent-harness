"""Per-integrate timeline (#520): join the lead's `dev integrate N` calls (transcript_gates.py output) with the
integrate logs and outcome records in the main checkout's state dir.

Phases of one integrate call that ended `merged`:
  pre   call start -> log created (feedback reads, wait for checks still running on the PR head, fetch, worktree,
        merge main, regenerate)
  lock  log created -> gate stages start (wait for a gate slot; = log span - stage seconds - stand-in seconds)
  gate  the gate's stage seconds (its RESULT line)
  standin  the --local-ci stand-in commands (render_check gate + plugin validate); from the rendered gate's RESULT
        line plus a fixed allowance measured separately (STANDIN_S)
  post  log last written -> outcome record written (Actions wait on the merged tip, feedback re-read, merge, issue
        closing, send-back of conflicting PRs)
  tail  outcome record -> call end (printing; small)

Usage: python integrate_timeline.py calls.jsonl <integrate dir> > timeline.json
"""

import json
import re
import subprocess
import sys
from pathlib import Path

GATE = re.compile(r"RESULT: (passed|failed)[^\n]*?\((?:\d+ stage\(s\), )?(\d+) s\)")


def birth_mtime(p: Path) -> tuple[int, int]:
    b, m = subprocess.run(["stat", "-c", "%W %Y", str(p)], capture_output=True, text=True, check=True).stdout.split()
    return int(b), int(m)


def main() -> None:
    calls = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
    idir = Path(sys.argv[2])
    standin_s = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    logs = []
    for p in idir.glob("pr-*-*.log"):
        pr = int(p.name.split("-")[1])
        b, m = birth_mtime(p)
        text = p.read_text(errors="replace")
        results = GATE.findall(text)
        logs.append(
            {
                "pr": pr,
                "log": p.name,
                "born": b,
                "mtime": m,
                "gate_s": int(results[0][1]) if results else None,
                "gate_ok": results[0][0] == "passed" if results else None,
                "render_gate_s": int(results[1][1]) if len(results) > 1 else None,
                "local_ci": "## scripts/render_check.sh" in text,
                "waited_slot": "every gate slot on this machine is busy" in text,
            }
        )
    outcomes = {}
    for p in idir.glob("pr-*.outcome.json"):
        outcomes[int(p.name.split("-")[1].split(".")[0])] = (birth_mtime(p)[1], json.loads(p.read_text()))
    rows = []
    for c in calls:
        if (
            c["kind"] != "integrate"
            or c["bg"]
            or not c.get("pr")
            or not c.get("t0")
            or not c.get("t1")
            or c.get("integrate_result") != "merged"
        ):
            continue
        pr, t0, t1 = int(c["pr"]), c["t0"], c["t1"]
        log = next((g for g in logs if g["pr"] == pr and t0 - 5 <= g["born"] <= t1), None)
        out = outcomes.get(pr)
        row = {"pr": pr, "t0": t0, "wall_s": round(t1 - t0), "local_ci": c["local_ci"], "train": c["train"]}
        if log and out and t0 - 5 <= out[0] <= t1 + 5 and log["gate_s"] is not None:
            span = log["mtime"] - log["born"]
            standin = (log["render_gate_s"] or 0) + standin_s if log["local_ci"] else 0
            row.update(
                log=log["log"],
                pre_s=round(log["born"] - t0),
                span_s=span,
                gate_s=log["gate_s"],
                standin_s=round(standin),
                lock_s=max(0, round(span - log["gate_s"] - standin)),
                post_s=out[0] - log["mtime"],
                tail_s=round(t1 - out[0]),
                waited_slot=log["waited_slot"],
                updated=out[1].get("updated"),
            )
        rows.append(row)
    json.dump(rows, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
