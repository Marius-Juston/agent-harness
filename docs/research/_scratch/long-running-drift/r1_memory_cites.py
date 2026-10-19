"""R1 memory: find assistant turns that cite auto memory (by entry file stem, index title, or the words "memory says").

usage: python3 -I r1_memory_cites.py PROJECT_DIR [--grep REGEX] [--show N]
Scans every transcript under PROJECT_DIR (main and subagents). Prints per-entry citation counts with first/last date,
and with --grep prints up to N matching assistant text snippets (timestamp, main/sub, 300 chars) for manual labelling.
Machine-local and private: do not copy snippets into public files.
"""

import argparse
import collections
import json
import re
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("project_dir")
ap.add_argument("--grep")
ap.add_argument("--show", type=int, default=20)
a = ap.parse_args()
root = Path(a.project_dir).expanduser()
mem = root / "memory"
idx = (mem / "MEMORY.md").read_text(errors="replace") if (mem / "MEMORY.md").exists() else ""
keys = {}
for title, fname in re.findall(r"- \[([^\]]+)\]\(([^)]+\.md)\)", idx):
    keys[fname] = [re.escape(Path(fname).stem), re.escape(title)]
GENERIC = re.compile(
    r"(auto[- ]memory|my memory|memory (?:says|entry|note|rule)|per (?:the )?memory|from memory)", re.I
)
pat = re.compile(a.grep, re.I) if a.grep else None
hits = collections.defaultdict(list)
shown = 0
for f in root.rglob("*.jsonl"):
    sub = "subagents" in f.parts
    with open(f, errors="replace") as fh:
        for line in fh:
            if '"type":"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp", "")
            for blk in rec.get("message", {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") not in ("text", "thinking"):
                    continue
                text = blk.get("text") or blk.get("thinking") or ""
                for fname, ks in keys.items():
                    if any(re.search(k, text, re.I) for k in ks):
                        hits[fname].append((ts, sub))
                if GENERIC.search(text):
                    hits["<generic>"].append((ts, sub))
                if pat and pat.search(text) and shown < a.show:
                    m = pat.search(text)
                    s = max(0, m.start() - 150)
                    print(f"[{ts[:16]} {'sub' if sub else 'main'} {f.stem[:8]}] ...{text[s : s + 300]!r}")
                    shown += 1
for k, v in sorted(hits.items()):
    v.sort()
    print(f"{k:40} cites={len(v):4} main={sum(1 for _, s in v if not s):3} first={v[0][0][:10]} last={v[-1][0][:10]}")
