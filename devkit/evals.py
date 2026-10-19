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
    forbid_edit: [regex, ...]      no Edit/Write/MultiEdit/NotebookEdit targets a matching path (e.g. the test file)
    forbid_written: [regex, ...]   no text those tools write matches (e.g. a special-cased test input)

`agent` names the agent; `plugin_dir` (relative to the clone) loads that commit's plugin, so two commits compare.

pass^k (all trials pass) is reported next to the pass rate: a rule that holds two times out of three does not hold.

An arm (#82) is the run's `--model`, `--effort` and `--agent` (the last replaces each scenario's main-thread agent). The
model is checked: a trial fails when the main thread (init event and every top-level assistant message) shows a model
other than the one named, e.g. after a fallback. Subagent and classifier models are recorded from the result's
`modelUsage`, not checked. Effort is not in the transcript, so it is passed twice, as `--effort` and as
CLAUDE_CODE_EFFORT_LEVEL, which beats agent frontmatter (harness-efficiency.md §3), and is recorded but not verified;
model and effort variables the arm does not set are removed from the trial's environment. `--compare A B` pairs two run
records by scenario and reports B minus A with one-sided 95 % bounds and a non-inferiority verdict at `--margin`;
the method and its minimum size are in harness-efficiency.md §10.
"""

import hashlib
import json
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from devkit import config
from devkit.paths import git, root, state_dir

EFFORTS = ("low", "medium", "high", "xhigh", "max")
TOKEN_KINDS = ("input", "write_1h", "write_5m", "read", "output")  # cache writes priced apart (#255)
FAMILIES = ("opus", "sonnet", "haiku", "fable")  # bare aliases; any other --model must equal the id, up to a date
NOT_A_MODEL = ("opusplan", "default")  # switch or pick models themselves, so no single model can be checked
# Inherited by `claude -p` and able to change the model or effort behind the arm's back (Model configuration docs).
ARM_ENV = ("CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
# --compare gives a verdict only from this size up (harness-efficiency.md §10).
MIN_SCENARIOS, MIN_TRIALS = 10, 3

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

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


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


def command(sc: dict, repo: Path, model: str = "", effort: str = "") -> tuple[list[str], dict[str, str]]:
    """The `claude -p` argv of one trial of `sc` in `repo`, and the environment it adds."""
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
    if sc.get("plugin_dir"):
        cmd += ["--plugin-dir", str(repo / sc["plugin_dir"])]
    if sc.get("agent"):
        cmd += ["--agent", sc["agent"]]
    cmd += ["--allowedTools", ",".join(sc.get("allowed_tools", DEFAULT_TOOLS))]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd, ({"CLAUDE_CODE_EFFORT_LEVEL": effort} if effort else {})


def trial_env(base: dict[str, str], arm_env: dict[str, str]) -> dict[str, str]:
    """`base` without the model and effort variables of ARM_ENV, plus the ones the arm sets."""
    return {**{k: v for k, v in base.items() if k not in ARM_ENV}, **arm_env}


def fingerprint() -> dict:
    """What else shapes a trial on this machine: host, Claude Code's user-level instructions and settings (hashed), and
    which ARM_ENV variables the caller had set (removed for the trial)."""
    home = Path.home() / ".claude"
    files = [home / "settings.json", home / "CLAUDE.md", *sorted((home / "rules").glob("*.md"))]
    h = hashlib.sha256()
    for f in files:
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes())
    return {
        "machine": socket.gethostname(),
        "user_claude": h.hexdigest()[:12],
        "dropped_env": [k for k in ARM_ENV if k in os.environ],
    }


def _trial(sc: dict, model: str = "", effort: str = "", runner=subprocess.run, sandbox=None) -> dict:
    """One trial; `runner` and `sandbox` are injectable so a test can stand in for `claude` and the clone."""
    repo = (sandbox or _sandbox)()
    cmd, env = command(sc, repo, model, effort)
    start = time.time()
    try:
        out = runner(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=sc.get("timeout_s", 600),
            check=False,
            env=trial_env(dict(os.environ), env),
        )
        lines = out.stdout.splitlines()
    except subprocess.TimeoutExpired as exc:
        lines = (exc.stdout or b"").decode(errors="replace").splitlines() if isinstance(exc.stdout, bytes) else []
    finally:
        shutil.rmtree(repo.parent, ignore_errors=True)
    return {**parse(lines), "seconds": round(time.time() - start)}


def parse(lines: list[str]) -> dict:
    """A stream-json transcript as the graders read it: Bash commands, tools, Reads, edits, final answer, cost, the
    main thread's models in order of appearance, and the billed tokens by kind (cache writes apart)."""
    commands, tools, reads, edits, final, cost, version = [], [], [], [], "", 0.0, ""
    models: list[str] = []
    billed: list[str] = []
    tokens = dict.fromkeys(TOKEN_KINDS, 0)
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        main = ev.get("parent_tool_use_id") is None
        if ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("model"):
            models.append(ev["model"])
            version = ev.get("claude_code_version", "")
        if (
            ev.get("type") == "assistant"
            and main
            and ev.get("message", {}).get("model", "<synthetic>") != "<synthetic>"
        ):
            models.append(ev["message"]["model"])
        if ev.get("type") == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    tools.append(c.get("name"))
                    if c.get("name") == "Bash":
                        commands.append(c.get("input", {}).get("command", ""))
                    elif c.get("name") == "Read":
                        inp = c.get("input", {})
                        reads.append({"path": inp.get("file_path", ""), "whole": not inp.get("limit")})
                    elif c.get("name") in EDIT_TOOLS:
                        inp = c.get("input", {})
                        parts = [inp.get(k) for k in ("new_string", "content", "new_source")]
                        parts += [e.get("new_string") for e in inp.get("edits", []) if isinstance(e, dict)]
                        path = inp.get("file_path") or inp.get("notebook_path") or ""
                        edits.append({"path": path, "text": "\n".join(x for x in parts if isinstance(x, str))})
        elif ev.get("type") == "result":
            final = ev.get("result", "") or ""
            cost = ev.get("total_cost_usd", 0.0) or 0.0
            billed = sorted(ev.get("modelUsage") or {})
            u = ev.get("usage") or {}
            w1h = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0)
            tokens = {
                "input": u.get("input_tokens", 0),
                "write_1h": w1h,
                "write_5m": u.get("cache_creation_input_tokens", 0) - w1h,
                "read": u.get("cache_read_input_tokens", 0),
                "output": u.get("output_tokens", 0),
            }
    return {
        "commands": commands,
        "tools": tools,
        "reads": reads,
        "edits": edits,
        "final": final,
        "cost": cost,
        "models": list(dict.fromkeys(models)),
        "billed_models": billed,
        "tokens": tokens,
        "claude_code_version": version,
    }


def _model_matches(asked: str, seen: str) -> bool:
    """A bare family alias (`sonnet`, `sonnet[1m]`) matches any id with that hyphen-separated part; anything else must
    equal the id, allowing only a trailing -YYYYMMDD date (`claude-sonnet-5` is not `claude-sonnet-5-5`)."""
    asked, seen = asked.lower().split("[")[0], seen.lower().split("[")[0]
    if asked in FAMILIES:
        return re.search(rf"(^|-){asked}(-|$)", seen) is not None
    return re.fullmatch(rf"{re.escape(asked)}(-\d{{8}})?", seen) is not None


def model_fails(asked: str, models: list[str]) -> list[str]:
    """The model check: empty when nothing was asked, or every main-thread model in the transcript is the one asked."""
    if not asked:
        return []
    if not models:
        return [f"no model in the transcript, asked for {asked}"]
    other = [m for m in models if not _model_matches(asked, m)]
    return [f"ran {', '.join(other)}, not {asked}"] if other else []


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
    edits = t.get("edits", [])
    fails += [
        f"edited {e['path']} (/{rx}/)" for rx in sc.get("forbid_edit", []) for e in edits if re.search(rx, e["path"])
    ]
    fails += [
        f"wrote /{rx}/ into {e['path']}"
        for rx in sc.get("forbid_written", [])
        for e in edits
        if re.search(rx, e["text"])
    ]
    if order := sc.get("order"):
        found = [next((i for i, c in enumerate(t["commands"]) if re.search(rx, c)), None) for rx in order]
        idx = [i for i in found if i is not None]
        if len(idx) < len(found) or idx != sorted(idx):
            fails.append(f"order {order} not followed")
    return fails


def run(names: list[str], trials: int, model: str = "", effort: str = "", agent: str = "", label: str = "") -> int:
    if model.lower().split("[")[0] in NOT_A_MODEL:
        print(f"--model {model} picks models itself, so the check cannot hold; name a model or an alias")
        return 1
    scenarios = load(names)
    if not scenarios:
        print(f"no scenarios matched ({config.get().evals_dir}/*.yaml)")
        return 1
    arm = {"model": model, "effort": effort, "agent": agent, "label": label, "commit": git("rev-parse", "HEAD")}
    arm |= fingerprint()
    record: dict = {"when": time.strftime("%Y-%m-%d %H:%M"), "arm": arm, "results": {}}
    print(f"arm: {_arm(arm)}")
    all_ok, seen, billed = True, {}, {}
    for sc in scenarios:
        sc = {**sc, "agent": agent} if agent else sc
        passes, notes, cost = 0, [], 0.0
        for k in range(trials):
            t = _trial(sc, model, effort)
            seen.update(dict.fromkeys(t.get("models", [])))
            billed.update(dict.fromkeys(t.get("billed_models", [])))
            fails = grade(sc, t) + model_fails(model, t.get("models", []))
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
    arm["observed_models"], arm["billed_models"] = list(seen), sorted(billed)
    path = state_dir("evals") / f"run-{int(time.time())}.json"
    path.write_text(json.dumps(record, indent=1))
    print(f"record: {path.stem} (compare: uv run dev eval --compare <A> {label or path.stem})")
    return 0 if all_ok else 1


def _arm(arm: dict) -> str:
    keys = ("model", "effort", "agent", "label")
    out = " ".join(f"{k}={arm.get(k) or '-'}" for k in keys) + f" commit={(arm.get('commit') or '-')[:7]}"
    out += f" ran={','.join(arm['observed_models'])}" if arm.get("observed_models") else ""
    out += f" billed={','.join(arm['billed_models'])}" if arm.get("billed_models") else ""
    return out + (f" on={arm['machine']}/{arm.get('user_claude', '-')}" if arm.get("machine") else "")


def load_record(ref: str) -> dict:
    """A run record by path, by stem (run-<epoch>), or the newest run whose label is `ref`."""
    if Path(ref).is_file():
        return json.loads(Path(ref).read_text())
    runs = sorted(state_dir("evals").glob("run-*.json"), key=lambda p: int(p.stem.split("-")[1]), reverse=True)
    for p in runs:
        rec = json.loads(p.read_text())
        if ref in (p.stem, (rec.get("arm") or {}).get("label")):
            return rec
    raise FileNotFoundError(f"no eval run record {ref!r} (a path, a run-<epoch> stem or a --label)")


def _quantile(means: list[float], q: float) -> float:
    means = sorted(means)
    return means[min(len(means) - 1, int(q * len(means)))]


def _cost_bound(values: list[float], q: float, draws: int, rng: random.Random) -> float:
    """The q-quantile of the means of `draws` resamples of the per-scenario values (clusters stay whole)."""
    return _quantile([sum(rng.choices(values, k=len(values))) / len(values) for _ in range(draws)], q)


def _pass_bound(rows: list[dict], q: float, draws: int, rng: random.Random) -> float:
    """Two-stage bootstrap of the mean pass-rate delta: resample scenarios, then draw each arm's rate in a drawn
    scenario from its Jeffreys posterior Beta(passes + 1/2, fails + 1/2), so 3/3 against 3/3 still carries binomial
    spread instead of a zero-width interval (harness-efficiency.md §10)."""

    def delta(r: dict) -> float:
        (pa, na), (pb, nb) = r["counts"]
        return rng.betavariate(pb + 0.5, nb - pb + 0.5) - rng.betavariate(pa + 0.5, na - pa + 0.5)

    return _quantile([sum(delta(r) for r in rng.choices(rows, k=len(rows))) / len(rows) for _ in range(draws)], q)


def compare(a: dict, b: dict, margin: float = 0.05, draws: int = 10_000, seed: int = 82) -> dict:
    """Paired B - A deltas over the scenarios both records ran: pass rate with a one-sided 95 % lower bound, $/trial with
    a one-sided 95 % upper bound, $ per solved trial per arm, and the non-inferiority verdict at `margin`."""
    ra, rb = a["results"], b["results"]
    names = sorted(n for n in ra if ":" not in n and n in rb)
    rows = []
    for n in names:
        x, y = ra[n], rb[n]
        rows.append(
            {
                "name": n,
                "a": x["passes"] / x["trials"],
                "b": y["passes"] / y["trials"],
                "cost_a": x["cost_usd"] / x["trials"],
                "cost_b": y["cost_usd"] / y["trials"],
                "trials": (x["trials"], y["trials"]),
                "counts": ((x["passes"], x["trials"]), (y["passes"], y["trials"])),
            }
        )
    unpaired = sorted(n for n in {*ra, *rb} if ":" not in n and n not in names)

    def solved(r: dict) -> float | None:
        passes = sum(r[n]["passes"] for n in names)
        return sum(r[n]["cost_usd"] for n in names) / passes if passes else None

    d_pass = [r["b"] - r["a"] for r in rows]
    d_cost = [r["cost_b"] - r["cost_a"] for r in rows]
    out = {
        "arms": (a.get("arm") or {}, b.get("arm") or {}),
        "rows": rows,
        "unpaired": unpaired,
        "margin": margin,
        "draws": draws,
        "seed": seed,
        "pass_delta": sum(d_pass) / len(rows) if rows else None,
        "cost_delta": sum(d_cost) / len(rows) if rows else None,
        "pass_lower": None,
        "cost_upper": None,
        "solved_cost": (solved(ra), solved(rb)),
    }
    if len(rows) < 2:
        out["verdict"] = f"too few paired scenarios ({len(rows)}) for an interval"
        return out
    rng = random.Random(seed)  # noqa: S311 - a seeded resample, not a secret
    out["pass_lower"] = _pass_bound(rows, 0.05, draws, rng)
    out["cost_upper"] = _cost_bound(d_cost, 0.95, draws, rng)
    few = min(min(r["trials"]) for r in rows)
    if len(rows) < MIN_SCENARIOS or few < MIN_TRIALS:
        out["verdict"] = (
            f"insufficient trials: {len(rows)} paired scenarios, at least {few} trials each; a verdict needs "
            f"{MIN_SCENARIOS} scenarios with {MIN_TRIALS} trials per arm"
        )
    elif out["pass_lower"] > -margin:
        out["verdict"] = f"non-inferior: B's pass rate is at most {margin:.2f} below A's (one-sided 95 %)"
    else:
        out["verdict"] = (
            f"not shown non-inferior: the bound allows B a drop of {-out['pass_lower']:.2f} (margin {margin:.2f})"
        )
    return out


def render(c: dict, ref_a: str, ref_b: str) -> str:
    def money(x: float | None) -> str:
        return "n/a" if x is None else f"${x:.3f}"

    out = [f"A {ref_a}: {_arm(c['arms'][0])}, {money(c['solved_cost'][0])} per solved trial"]
    out.append(f"B {ref_b}: {_arm(c['arms'][1])}, {money(c['solved_cost'][1])} per solved trial")
    out.append(f"{'scenario':<32}{'A':>6}{'B':>6}{'B-A':>7}{'$A/trial':>10}{'$B/trial':>10}")
    for r in c["rows"]:
        out.append(
            f"{r['name'][:31]:<32}{r['a']:>6.2f}{r['b']:>6.2f}{r['b'] - r['a']:>+7.2f}{r['cost_a']:>10.3f}{r['cost_b']:>10.3f}"
        )
    if c["unpaired"]:
        out.append(f"unpaired (left out): {', '.join(c['unpaired'])}")
    if few := [r["name"] for r in c["rows"] if min(r["trials"]) < 3]:
        out.append(f"under 3 trials per arm: {', '.join(few)}")
    if c["pass_lower"] is not None:
        how = f"scenario bootstrap with Jeffreys draws, {len(c['rows'])} scenarios, {c['draws']:,} draws, seed {c['seed']}"
        out.append(f"pass rate B-A {c['pass_delta']:+.2f}, one-sided 95 % lower bound {c['pass_lower']:+.2f} ({how})")
        out.append(f"$/trial B-A {c['cost_delta']:+.3f}, one-sided 95 % upper bound {c['cost_upper']:+.3f}")
    out.append(f"verdict: {c['verdict']}")
    return "\n".join(out)


def run_compare(ref_a: str, ref_b: str, margin: float) -> int:
    try:
        a, b = load_record(ref_a), load_record(ref_b)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    c = compare(a, b, margin)
    print(render(c, ref_a, ref_b))
    return 0 if c["rows"] else 1
