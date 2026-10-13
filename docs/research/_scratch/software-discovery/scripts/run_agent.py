"""Run a fixed agent prompt (data/b0_prompt.md or data/a3_prompt.md) as a fresh, tool-less `claude -p` call from an
empty directory, in batches, and write the merged JSON answers (PROTOCOL.md amendment 1; r3-tuning.md).

Usage: uv run --no-project python run_agent.py <prompt.md> <input.json> <out.json> [--batch 16] [--model <id>]
           [--extra k=v ...]

The prompt is the text between the two `---` lines of <prompt.md>, with `<NEEDS>` replaced by the JSON array of a
batch and nothing else. The call is `claude -p --tools '' --strict-mcp-config --setting-sources '' --output-format
json`, run in a new empty temporary directory; the model id is read from the call's own report.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROMPT, INPUT, OUT = (Path(a) for a in sys.argv[1:4])
BATCH = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 16
MODEL = ["--model", sys.argv[sys.argv.index("--model") + 1]] if "--model" in sys.argv else []
EXTRA = dict(a.split("=", 1) for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--extra")


def body(md: str) -> str:
    parts = md.split("\n---\n")
    return parts[1].strip("\n") if len(parts) >= 3 else sys.exit("prompt file lacks the two --- lines")


def main() -> None:
    template = body(PROMPT.read_text())
    needs = json.loads(INPUT.read_text())
    answers: dict = json.loads(OUT.read_text()) if OUT.exists() else {}
    models = set()
    for s in range(0, len(needs), BATCH):
        batch = needs[s : s + BATCH]
        if all(n["id"] in answers for n in batch):
            continue
        prompt = template.replace("<NEEDS>", json.dumps(batch, indent=1))
        with tempfile.TemporaryDirectory() as empty:
            r = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--tools",
                    "",
                    "--strict-mcp-config",
                    "--setting-sources",
                    "",
                    "--output-format",
                    "json",
                    *MODEL,
                ],
                input=prompt,
                capture_output=True,
                text=True,
                cwd=empty,
                timeout=1800,
                check=False,
            )
        rep = json.loads(r.stdout)
        models |= set((rep.get("modelUsage") or {}).keys())
        text = rep.get("result") or ""
        m = re.search(r"\{.*\}", text, re.S)
        got = json.loads(m.group(0)) if m else {}
        missing = [n["id"] for n in batch if n["id"] not in got]
        print(f"batch {s // BATCH + 1}: {len(got)} answers, missing {missing}", flush=True)
        answers.update({n["id"]: got.get(n["id"], []) for n in batch})
        answers["_model"] = sorted(models | set(answers.get("_model", "").split(",")) - {""})
        answers["_model"] = ",".join(answers["_model"])
        answers["_date"] = time.strftime("%Y-%m-%d")
        answers["_how"] = (
            f"claude -p {' '.join(MODEL)} --tools '' --strict-mcp-config --setting-sources '' "
            f"--output-format json from an "
            f"empty directory; prompt = {PROMPT.name} body with {INPUT.name}, batches of {BATCH}"
        )
        answers.update(EXTRA)
        OUT.write_text(json.dumps(answers, indent=1))


if __name__ == "__main__":
    main()
