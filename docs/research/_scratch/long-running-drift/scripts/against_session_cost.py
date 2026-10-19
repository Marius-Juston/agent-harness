"""API-equivalent cost of one Claude Code session (top-level file plus its subagents directory).

Usage: python3 against_session_cost.py <session.jsonl> [in out cache_read cache_write  USD per MTok]
Defaults are the Opus 5.5 prices of harness-efficiency.md §2 (input 4.09, output 20.05, cache read 0.20, cache write 7.95).
Pass prices explicitly. Output tokens in transcripts are undercounted (harness-efficiency.md §2), so this is a lower bound.
"""

import json
import pathlib
import sys


def main(path, pin=4.09, pout=20.05, pcr=0.20, pcw=7.95):
    pin, pout, pcr, pcw = map(float, (pin, pout, pcr, pcw))
    p = pathlib.Path(path)
    files = [p] + sorted((p.parent / p.stem).rglob("*.jsonl")) if (p.parent / p.stem).exists() else [p]  # noqa: RUF005
    seen = {}
    for f in files:
        for line in f.open(errors="replace"):
            try:
                o = json.loads(line)
            except Exception:  # noqa: S112
                continue
            if o.get("type") != "assistant":
                continue
            m = o.get("message", {})
            u = m.get("usage") or {}
            mid = m.get("id") or o.get("uuid")
            seen[mid] = u
    tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for u in seen.values():
        for k in tot:
            tot[k] += u.get(k) or 0
    usd = (
        tot["input_tokens"] * pin
        + tot["output_tokens"] * pout
        + tot["cache_read_input_tokens"] * pcr
        + tot["cache_creation_input_tokens"] * pcw
    ) / 1e6
    print(f"files={len(files)} calls={len(seen)} " + " ".join(f"{k}={v}" for k, v in tot.items()) + f" usd>={usd:.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
