"""Arm W (PROTOCOL.md rule 5): Claude Code's WebSearch tool, each query verbatim, results read from the tool's own
structured answer (`tool_use_result`) in the call's stream-json output, so the model cannot filter or reorder them.

Usage: uv run --no-project python web_arm.py '<json list of queries>' <out.json>

One `claude -p --model haiku --tools WebSearch` call per unit from a new empty directory. A query the model did not
run (or ran altered) is retried in a second call; a query never run is saved with status "not run".
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROMPT = (
    "Run the WebSearch tool once for each query below, with the query string exactly as written and mode "
    '"standard". Run nothing else and do not summarize; after the last search reply DONE.\n'
)


def call(queries: list[str]) -> tuple[dict[str, list[dict]], set[str]]:
    prompt = PROMPT + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(queries))
    with tempfile.TemporaryDirectory() as empty:
        r = subprocess.run(
            [
                "claude",
                "-p",
                "--model",
                "haiku",
                "--tools",
                "WebSearch",
                "--allowedTools",
                "WebSearch",
                "--strict-mcp-config",
                "--setting-sources",
                "",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=empty,
            timeout=1800,
            check=False,
        )
    got: dict[str, list[dict]] = {}
    models: set[str] = set()
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "result":
            models |= set((e.get("modelUsage") or {}).keys())
        tur = e.get("tool_use_result")
        if e.get("type") == "user" and isinstance(tur, dict) and "query" in tur:
            links = []
            for block in tur.get("results") or []:
                if isinstance(block, dict):
                    links += [x for x in block.get("content") or [] if isinstance(x, dict) and x.get("url")]
            got[tur["query"]] = [{"title": x.get("title", ""), "url": x["url"], "ids": {}} for x in links]
    return got, models


def main() -> None:
    queries = json.loads(sys.argv[1])
    out = Path(sys.argv[2])
    done = json.loads(out.read_text()) if out.exists() else {}
    models: set[str] = set()
    for _attempt in range(2):
        todo = [q for q in queries if q not in done]
        if not todo:
            break
        got, m = call(todo)
        models |= m
        for q in todo:
            if q in got:
                done[q] = {
                    "results": got[q],
                    "status": "ok",
                    "model": ",".join(sorted(m)),
                    "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
        out.write_text(json.dumps(done, indent=1, ensure_ascii=False))
    for q in queries:
        done.setdefault(q, {"results": [], "status": "not run"})
        print(f"{len(done[q]['results']):3d} {done[q]['status']} {q}", flush=True)
    out.write_text(json.dumps(done, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
