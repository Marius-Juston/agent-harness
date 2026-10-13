"""Run a fixed agent prompt as fresh `claude -p` calls from empty directories, in batches, and write the merged JSON
answers (#176 rerun PROTOCOL.md; the #169 scripts/run_agent.py plus --web and parallel workers).

Usage: uv run --no-project python run_agent.py <prompt.md> <input.json> <out.json> [--batch 16] [--model <id>]
           [--web] [--workers 1] [--extra k=v ...]

The prompt is the text between the two `---` lines of <prompt.md>, with `<NEEDS>` replaced by the JSON array of a
batch and nothing else. Tool-less: `claude -p --tools '' --strict-mcp-config --setting-sources '' --output-format
json`. With --web (arm B0W): `--tools WebSearch,WebFetch --allowedTools WebSearch,WebFetch` instead, nothing else.
Each call runs in a new empty temporary directory; the model ids are read from the calls' own reports, and the
number of turns and of web tool calls per batch is kept under `_calls`.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROMPT, INPUT, OUT = (Path(a) for a in sys.argv[1:4])


def arg(name: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


BATCH = int(arg("--batch", "16"))
WORKERS = int(arg("--workers", "1"))
MODEL = ["--model", arg("--model")] if "--model" in sys.argv else []
WEB = "--web" in sys.argv
TOOLS = ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch"] if WEB else ["--tools", ""]
EXTRA = dict(a.split("=", 1) for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--extra")


def body(md: str) -> str:
    parts = md.split("\n---\n")
    return parts[1].strip("\n") if len(parts) >= 3 else sys.exit("prompt file lacks the two --- lines")


def call(prompt: str) -> dict:
    cmd = ["claude", "-p", *TOOLS, "--strict-mcp-config", "--setting-sources", "", "--output-format", "json", *MODEL]
    for attempt in range(3):
        with tempfile.TemporaryDirectory() as empty:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=empty, timeout=1800, check=False)
        try:
            return json.loads(r.stdout)
        except ValueError:
            print(f"call failed (attempt {attempt + 1}): {r.stderr[:300]}", flush=True)
            time.sleep(30)
    return {}


def main() -> None:
    template = body(PROMPT.read_text())
    needs = json.loads(INPUT.read_text())
    answers: dict = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [needs[s : s + BATCH] for s in range(0, len(needs), BATCH)]
    todo = [b for b in todo if not all(n["id"] in answers for n in b)]

    def run(batch: list[dict]) -> tuple[list[dict], dict]:
        return batch, call(template.replace("<NEEDS>", json.dumps(batch, indent=1)))

    models = set(filter(None, answers.get("_model", "").split(",")))
    calls = answers.get("_calls", {})
    with ThreadPoolExecutor(WORKERS) as pool:
        for batch, rep in pool.map(run, todo):
            models |= set((rep.get("modelUsage") or {}).keys())
            text = rep.get("result") or ""
            m = re.search(r"\{.*\}", text, re.S)
            try:
                got = json.loads(m.group(0)) if m else {}
            except ValueError:
                got = {}
            missing = [n["id"] for n in batch if n["id"] not in got]
            print(f"batch {batch[0]['id']}: {len(got)} answers, missing {missing}", flush=True)
            answers.update({n["id"]: got.get(n["id"], []) for n in batch})
            calls[batch[0]["id"]] = {
                "turns": rep.get("num_turns"),
                "web_search_requests": sum(
                    (u.get("webSearchRequests") or 0) for u in (rep.get("modelUsage") or {}).values()
                ),
                "cost_usd": rep.get("total_cost_usd"),
                "error": bool(rep.get("is_error")) or not rep,
            }
            answers["_model"] = ",".join(sorted(models))
            answers["_calls"] = calls
            answers["_date"] = time.strftime("%Y-%m-%d")
            answers["_how"] = (
                f"claude -p {' '.join(MODEL)} {' '.join(t if t else repr(t) for t in TOOLS)} --strict-mcp-config "
                f"--setting-sources '' --output-format json from an empty directory; prompt = {PROMPT.name} body with "
                f"{INPUT.name}, batches of {BATCH}"
            )
            answers.update(EXTRA)
            OUT.write_text(json.dumps(answers, indent=1))


if __name__ == "__main__":
    main()
