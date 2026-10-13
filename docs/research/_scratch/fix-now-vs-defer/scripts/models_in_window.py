"""Which models wrote the subagent transcripts that measure_deferral.py reads (#149)?

Counts assistant-message `model` fields in ~/.claude/projects/*/**/subagents/*.jsonl modified since 2026-10-03.
Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/models_in_window.py`.
"""

import collections
import datetime as dt
import re
from pathlib import Path

SINCE = dt.datetime(2026, 10, 3).timestamp()
MODEL = re.compile(r'"model":"(claude-[a-z0-9-]+)"')
counts: collections.Counter[str] = collections.Counter()
files = 0
for f in (Path.home() / ".claude" / "projects").glob("*/**/subagents/*.jsonl"):
    if f.stat().st_mtime < SINCE:
        continue
    files += 1
    counts.update(MODEL.findall(f.read_text(errors="replace")))
total = sum(counts.values())
print(f"subagent files since 2026-10-03: {files}; model fields: {total}")
for model, n in counts.most_common():
    print(f"  {model}: {n} ({n / total:.1%})")
