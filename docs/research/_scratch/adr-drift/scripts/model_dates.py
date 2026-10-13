"""#37's proposed lint, run read-only: sentences in research notes that name an LLM and a number but no year.
usage: python3 model_dates.py NOTES_DIR...
"""

import re
import sys
from pathlib import Path

MODEL = re.compile(
    r"\b(gpt-?[0-9][\w.-]*|o[134](?:-mini)?\b|claude[- ][\w.-]+|opus [0-9][\w.]*|sonnet [0-9][\w.]*|haiku [0-9][\w.]*|gemini[- ][\w.-]+|llama[- ]?[0-9][\w.-]*|qwen[0-9][\w.-]*|deepseek[- ][\w.-]+|mistral[- ][\w.-]*)",
    re.I,
)
NUM = re.compile(r"\d+(?:\.\d+)?\s*%|\b0\.\d+\b|\b\d+ of \d+\b")
YEAR = re.compile(r"\b20[12]\d\b")
for d in sys.argv[1:]:
    total = flagged = 0
    for p in sorted(Path(d).glob("*.md")):
        if p.name in ("INDEX.md", "README.md"):
            continue
        text = re.sub(r"```.*?```", "", p.read_text(errors="replace"), flags=re.S)
        for s in re.split(r"(?<=[.!?])\s+|\n", text):
            if MODEL.search(s) and NUM.search(s):
                total += 1
                if not YEAR.search(s):
                    flagged += 1
    print(
        f"{d}: {total} sentences name a model and a number; {flagged} ({100 * flagged / max(total, 1):.0f}%) carry no year"
    )
