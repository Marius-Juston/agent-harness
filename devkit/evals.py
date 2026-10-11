"""`uv run dev eval`: micro-scenario evals of the agent surface (CLAUDE.md, agents, skills, hooks).

Each scenario in <evals_dir>/*.yaml ([tool.devkit]) is a situation and the decision expected, drawn from a real failure. A trial
runs `claude -p` (optionally `--agent <name>`) in a throwaway clone of the current commit with **no remote**, so nothing
can be pushed or posted. Graders read the transcript (stream-json), not the agent's claims:

    expect_command: [regex, ...]   every regex matches some Bash command the agent ran
    forbid_command: [regex, ...]   no Bash command matches
    expect_tool / forbid_tool: [ToolName, ...]
    forbid_whole_read: [path suffix, ...]  no Read of that file without a limit
    expect_output: [regex, ...]    the final answer matches
    order: [regex_a, regex_b]      the first Bash command matching a comes before the first matching b

pass^k (all trials pass) is reported next to the pass rate: a rule that holds two times out of three does not hold.
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from devkit import config
from devkit.paths import root, state_dir

# A fresh clone is an untrusted workspace, so the project allowlist does not apply there and headless runs cannot ask.
# Trials get this narrow pre-approval instead (a scenario may replace it with `allowed_tools`); the clone has no remote.
DEFAULT_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "Bash(uv run dev *)",
    "Bash(uv run --quiet dev *)",
    "Bash(uv add *)",
    "Bash(uv sync*)",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(git log*)",
    "Bash(git show*)",
    "Bash(git add *)",
    "Bash(git commit *)",
    "Bash(git switch *)",
    "Bash(git checkout *)",
    "Bash(git branch *)",
    "Bash(git worktree *)",
    "Bash(git remote -v)",
    "Bash(grep *)",
    "Bash(ls*)",
    "Bash(sed -n *)",
    "Bash(head *)",
    "Bash(wc *)",
]


def load(names: list[str]) -> list[dict]:
    out = []
    for p in sorted((root() / config.get().evals_dir).glob("*.yaml")):
        data = yaml.safe_load(p.read_text())
        data["name"] = p.stem
        if not names or p.stem in names:
            out.append(data)
    return out


def _sandbox() -> Path:
    tmp = Path(
        tempfile.mkdtemp(prefix="devkit-eval-")
    )  # outside .git/: files there are "sensitive" to the permission system
    subprocess.run(["git", "clone", "-q", "--local", str(root()), str(tmp / "repo")], check=True)
    subprocess.run(["git", "-C", str(tmp / "repo"), "remote", "remove", "origin"], check=True)
    return tmp / "repo"


def _trial(sc: dict) -> dict:
    repo = _sandbox()
    cmd = [
        "claude",
        "-p",
        sc["prompt"],
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--max-turns",
        str(sc.get("max_turns", 15)),
        "--permission-mode",
        "acceptEdits",
    ]
    if sc.get("agent"):
        cmd += ["--agent", sc["agent"]]
    cmd += ["--allowedTools", ",".join(sc.get("allowed_tools", DEFAULT_TOOLS))]
    start = time.time()
    try:
        out = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=sc.get("timeout_s", 600), check=False
        )
        lines = out.stdout.splitlines()
    except subprocess.TimeoutExpired as exc:
        lines = (exc.stdout or b"").decode(errors="replace").splitlines() if isinstance(exc.stdout, bytes) else []
    finally:
        shutil.rmtree(repo.parent, ignore_errors=True)
    commands, tools, reads, final, cost = [], [], [], "", 0.0
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    tools.append(c.get("name"))
                    if c.get("name") == "Bash":
                        commands.append(c.get("input", {}).get("command", ""))
                    elif c.get("name") == "Read":
                        inp = c.get("input", {})
                        reads.append({"path": inp.get("file_path", ""), "whole": not inp.get("limit")})
        elif ev.get("type") == "result":
            final = ev.get("result", "") or ""
            cost = ev.get("total_cost_usd", 0.0) or 0.0
    return {
        "commands": commands,
        "tools": tools,
        "reads": reads,
        "final": final,
        "seconds": round(time.time() - start),
        "cost": cost,
    }


def grade(sc: dict, t: dict) -> list[str]:
    fails = []
    for rx in sc.get("expect_command", []):
        if not any(re.search(rx, c) for c in t["commands"]):
            fails.append(f"no command matched /{rx}/")
    for rx in sc.get("forbid_command", []):
        if hit := next((c for c in t["commands"] if re.search(rx, c)), None):
            fails.append(f"forbidden command /{rx}/: {hit[:120]}")
    fails += [
        f"whole-file Read of {r['path']}"
        for r in t["reads"]
        if r["whole"] and any(r["path"].endswith(w) for w in sc.get("forbid_whole_read", []))
    ]
    fails += [f"tool {n} never used" for n in sc.get("expect_tool", []) if n not in t["tools"]]
    fails += [f"forbidden tool {n} used" for n in sc.get("forbid_tool", []) if n in t["tools"]]
    fails += [f"final answer lacks /{rx}/" for rx in sc.get("expect_output", []) if not re.search(rx, t["final"], re.S)]
    if order := sc.get("order"):
        found = [next((i for i, c in enumerate(t["commands"]) if re.search(rx, c)), None) for rx in order]
        idx = [i for i in found if i is not None]
        if len(idx) < len(found) or idx != sorted(idx):
            fails.append(f"order {order} not followed")
    return fails


def run(names: list[str], trials: int) -> int:
    scenarios = load(names)
    if not scenarios:
        print(f"no scenarios matched ({config.get().evals_dir}/*.yaml)")
        return 1
    record = {"when": time.strftime("%Y-%m-%d %H:%M"), "results": {}}
    all_ok = True
    for sc in scenarios:
        passes, notes, cost = 0, [], 0.0
        for k in range(trials):
            t = _trial(sc)
            fails = grade(sc, t)
            cost += t["cost"]
            passes += not fails
            notes += [f"  trial {k + 1}: {f}" for f in fails]
            if fails:
                notes.append(f"  trial {k + 1} tools: {' '.join(t['tools'][:20])}")
            record["results"].setdefault(sc["name"] + ":trials", []).append({**t, "fails": fails})
        all_ok &= passes == trials
        record["results"][sc["name"]] = {"passes": passes, "trials": trials, "cost_usd": round(cost, 3)}
        print(
            f"{'ok  ' if passes == trials else 'FAIL'} {sc['name']}: {passes}/{trials} (pass^k {'yes' if passes == trials else 'no'}, ${cost:.2f})"
        )
        print("\n".join(notes[:9]))
    (state_dir("evals") / f"run-{int(time.time())}.json").write_text(json.dumps(record, indent=1))
    return 0 if all_ok else 1
