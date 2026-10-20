"""Claude Code versions and models over time, from transcript `version` and assistant `model` fields (#282 r1).

Usage: python cc_versions.py <transcripts root>   (e.g. ~/.claude/projects)

Prints, per Claude Code version: first and last timestamp seen, records, and distinct sessions; then per model: first
and last timestamp and assistant turns. Timestamps are UTC as recorded. Prints no project names or content.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def vkey(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def main() -> int:
    root = Path(sys.argv[1])
    ver = defaultdict(lambda: ["9", "0", 0, set()])
    mod = defaultdict(lambda: ["9", "0", 0])
    for path in root.rglob("*.jsonl"):
        for line in path.open(errors="replace"):
            if '"version"' not in line and '"model"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = r.get("timestamp")
            if not ts:
                continue
            if v := r.get("version"):
                e = ver[v]
                e[0], e[1], e[2] = min(e[0], ts), max(e[1], ts), e[2] + 1
                e[3].add(r.get("sessionId"))
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            if r.get("type") == "assistant" and (m := msg.get("model")) and not str(m).startswith("<"):
                e = mod[m]
                e[0], e[1], e[2] = min(e[0], ts), max(e[1], ts), e[2] + 1
    print("version\tfirst\tlast\trecords\tsessions")
    for v in sorted(ver, key=vkey):
        first, last, n, s = ver[v]
        print(f"{v}\t{first[:16]}\t{last[:16]}\t{n}\t{len(s)}")
    print("\nmodel\tfirst\tlast\tturns")
    for m in sorted(mod, key=lambda k: mod[k][0]):
        first, last, n = mod[m]
        print(f"{m}\t{first[:16]}\t{last[:16]}\t{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
