"""Skew windows and the share of harness-ops subagents that ran an older plugin than the pin (#282 r1).

Usage: python skew_summary.py --pins <ISO>=<tag> ... --now <ISO> <skew_scan csv>...

--pins lists when the project's pin moved (from `git log -L` on the marketplace ref in .claude/settings.json).
For every harness-ops subagent row (skew_scan.py output) it compares the release range its agent body matched with the
pin at its start time: "behind" when the range's last tag is older than the pin, else "current".
"""

import argparse
import csv
from collections import Counter
from datetime import datetime


def tag_key(t: str) -> tuple:
    return tuple(int(x) for x in t.lstrip("v").split("."))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pins", nargs="+", required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("csv", nargs="+")
    a = ap.parse_args()
    pins = sorted((ts, tag) for ts, _, tag in (p.partition("=") for p in a.pins))
    rows = []
    for f in a.csv:
        with open(f) as fh:
            rows += [r for r in csv.DictReader(fh) if r["agent"].startswith("harness-ops:")]
    c: Counter = Counter()
    behind_by: Counter = Counter()
    for r in rows:
        pin = max((p for p in pins if p[0] <= r["start"][:19]), default=(None, None))[1]
        body = r["body_tag"]
        if not pin or body in ("", "nomatch"):
            c["unknown"] += 1
            continue
        last = body.split("..")[-1] if not body.startswith("main@") else None
        if last is None:
            c["main-only body"] += 1
        elif tag_key(last) < tag_key(pin):
            c["behind"] += 1
            behind_by[f"pin {pin}, ran <= {last}"] += 1
        else:
            c["current"] += 1
    print(f"harness-ops subagents: {len(rows)}  {dict(c)}")
    for k, v in sorted(behind_by.items()):
        print(f"  {k}: {v}")
    fmt = "%Y-%m-%dT%H:%M"
    for (t0, tag), nxt in zip(pins, [*pins[1:], (a.now, "now")], strict=True):
        h = (datetime.strptime(nxt[0][:16], fmt) - datetime.strptime(t0[:16], fmt)).total_seconds() / 3600
        print(f"pin {tag} from {t0[:16]} for {h:.1f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
