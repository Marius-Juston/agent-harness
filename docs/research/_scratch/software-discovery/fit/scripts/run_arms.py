"""Run one fit-check arm (F0, F1, F3, F3r) over a frozen case file (#196, PROTOCOL.md).

Usage: uv run --no-project python run_arms.py <arm> <cases.json> <out.json> --env <envroot> [--pypi <snapshot.json>]
           [--f3 <f3 out.json>] [--model claude-opus-5-5] [--jobs 4] [--confirm-test-run]

Every agent call is a fresh `claude -p --tools '' --strict-mcp-config --setting-sources '' --output-format json` in a
new empty directory, one case per call. F3 and F3r scripts run through sandbox.py `run`. Cases marked "exclude" are
skipped. A run over cases_test.json refuses to start without --confirm-test-run and appends a line to
data/TEST_RUNS.log.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
FIT = HERE.parent
A = sys.argv[1:]
ARM, CASES, OUT = A[0], Path(A[1]), Path(A[2])


def opt(name: str, default: str | None = None) -> str | None:
    return A[A.index(name) + 1] if name in A else default


ENV = Path(opt("--env") or sys.exit("--env is required"))
MODEL = opt("--model", "claude-opus-5-5")
JOBS = int(opt("--jobs", "4") or 4)


def body(name: str) -> str:
    parts = (FIT / "prompts" / name).read_text().split("\n---\n")
    return parts[1].strip("\n")


def fill(text: str, c: dict, **kw: str) -> str:
    text = text.replace("<PACKAGE>", c["package"]).replace("<NEED>", c["need"])
    for k, v in kw.items():
        text = text.replace(f"<{k}>", v)
    return text


def claude(prompt: str) -> dict:
    with tempfile.TemporaryDirectory() as empty:
        t = time.monotonic()
        try:
            r = subprocess.run(["claude", "-p", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                                "--output-format", "json", "--model", MODEL], input=prompt, capture_output=True,
                               text=True, cwd=empty, timeout=900, check=False)  # fmt: skip
            rep = json.loads(r.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            # amendment 3: a call that runs past 900 s or returns no JSON is recorded, not fatal
            rep = {"result": "", "error": type(e).__name__}
    u = rep.get("usage") or {}
    return {
        "text": rep.get("result") or "",
        "models": sorted((rep.get("modelUsage") or {}).keys()),
        "seconds": round(time.monotonic() - t, 1),
        "tokens_in": (u.get("input_tokens") or 0)
        + (u.get("cache_read_input_tokens") or 0)
        + (u.get("cache_creation_input_tokens") or 0),
        "tokens_out": u.get("output_tokens") or 0,
        "cost_usd": rep.get("total_cost_usd"),
        "error": rep.get("error"),
    }


def verdict_json(text: str) -> tuple[str, str]:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    v = d.get("verdict")
    return (v if v in ("FIT", "NOT_FIT") else "UNPARSED"), d.get("reason", text[:300])


def code_block(text: str) -> str | None:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return m.group(1) if m else None


def sandbox(c: dict, script: str, tag: str) -> dict:
    d = OUT.parent / (OUT.stem + "_scripts")
    d.mkdir(exist_ok=True)
    p = d / f"{c['id']}{tag}.txt"
    p.write_text(script)
    r = subprocess.run(["uv", "run", "--no-project", "python", str(HERE / "sandbox.py"), "run", str(ENV / c["package"]),
                        str(p)], capture_output=True, text=True, check=True)  # fmt: skip
    return json.loads(r.stdout)


def one(c: dict, prev: dict | None, pypi: dict | None) -> dict:
    head = fill(body("common.md"), c)
    if ARM in ("f0", "f1"):
        extra = ""
        if ARM == "f1":
            extra = fill(body("f1.md"), c, PYPI=json.dumps(pypi[c["package"]], indent=1))
        else:
            extra = fill(body("f0.md"), c)
        call = claude(head + "\n\n" + extra)
        v, why = verdict_json(call["text"])
        return {"verdict": v, "reason": why, "calls": [call]}
    if ARM in ("f4", "f4r"):  # amendment 4: the F1 PyPI text in front of the F3 / F3r body
        head += "\n\n" + fill(body("f4.md"), c, PYPI=json.dumps(pypi[c["package"]], indent=1))
    if ARM in ("f3", "f4"):
        call = claude(head + "\n\n" + fill(body("f3.md"), c))
        script = code_block(call["text"])
        if script is None:
            return {"verdict": "NOT_FIT", "reason": "no script returned", "calls": [call], "run": None}
        run = sandbox(c, script, "")
        return {"verdict": "FIT" if run["fit_ok"] else "NOT_FIT", "calls": [call], "run": run}
    # f3r / f4r
    if prev is None:
        raise SystemExit(f"f3r needs the F3 result of {c['id']}")
    if prev["verdict"] == "FIT":
        return {**prev, "repaired": False}
    old = Path(OUT.parent / (Path(opt("--f3")).stem + "_scripts") / f"{c['id']}.txt")
    run0 = prev.get("run") or {"exit": None, "stdout": "", "stderr": "no script"}
    result = f"exit status: {run0['exit']}\nstdout (tail):\n{run0['stdout']}\nstderr (tail):\n{run0['stderr']}"
    script0 = old.read_text() if old.exists() else "(none)"
    call = claude(head + "\n\n" + fill(body("f3r.md"), c, SCRIPT=script0, RESULT=result))
    t = call["text"].strip()
    calls = prev["calls"] + [call]
    if t.startswith("NOT_FIT"):
        return {"verdict": "NOT_FIT", "reason": t[:300], "calls": calls, "run": run0, "repaired": False}
    script = code_block(t)
    if script is None:
        return {"verdict": "NOT_FIT", "reason": "unparsed repair reply", "calls": calls, "run": run0, "repaired": False}
    run = sandbox(c, script, "_r")
    return {"verdict": "FIT" if run["fit_ok"] else "NOT_FIT", "calls": calls, "run": run, "run0": run0,
            "repaired": True}  # fmt: skip


def main() -> None:
    cases = [c for c in json.loads(CASES.read_text()) if "exclude" not in c]
    if CASES.name in ("cases_test.json", "cases_n.json"):
        if "--confirm-test-run" not in A:
            sys.exit("TEST cases: pass --confirm-test-run (PROTOCOL.md: TEST runs once per arm)")
        with (FIT / "data" / "TEST_RUNS.log").open("a") as log:
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} run_arms.py {ARM} on {CASES.name} -> {OUT.name}\n")
    pypi = json.loads(Path(opt("--pypi")).read_text()) if ARM in ("f1", "f4", "f4r") else None
    prev = json.loads(Path(opt("--f3")).read_text()) if ARM in ("f3r", "f4r") else {}
    res: dict = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [c for c in cases if c["id"] not in res]

    def work(c: dict) -> tuple[str, dict]:
        return c["id"], one(c, prev.get(c["id"]), pypi)

    with ThreadPoolExecutor(JOBS) as ex:
        for cid, r in ex.map(work, todo):
            res[cid] = r
            print(cid, r["verdict"], flush=True)
            OUT.write_text(json.dumps(res, indent=1))
    res["_meta"] = {"arm": ARM, "model_requested": MODEL, "date": time.strftime("%Y-%m-%d"), "cases": CASES.name}
    OUT.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
