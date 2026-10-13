"""Which models wrote the transcripts that measure_deferral.py reads (#149)?

Scans exactly the files measure_deferral.py scans (main sessions `~/.claude/projects/*/*.jsonl` and subagents
`~/.claude/projects/*/*/subagents/*.jsonl`) and counts the `model` field of their assistant messages.
Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/models_in_window.py`.
"""

import collections
import glob
import re
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
MODEL = re.compile(r'"model":"(claude-[a-z0-9-]+)"')
KINDS = {
    "main": glob.glob(str(PROJECTS / "*" / "*.jsonl")),
    "subagent": glob.glob(str(PROJECTS / "*" / "*" / "subagents" / "*.jsonl")),
}
for kind, files in KINDS.items():
    counts: collections.Counter[str] = collections.Counter()
    for f in files:
        counts.update(MODEL.findall(Path(f).read_text(errors="replace")))
    total = sum(counts.values()) or 1
    print(f"{kind}: {len(files)} files; model fields: {sum(counts.values())}")
    for model, n in counts.most_common(4):
        print(f"  {model}: {n} ({n / total:.1%})")
