"""Price one extra token of SessionStart (orient) output carried through a lead session.

Usage: python3 against_orient_cost.py <transcript_dir> <from_date> <to_date> <cache_read_usd_per_mtok> <orient_words> [<words_per_item> ...]
Counts lead (top-level, non-sidechain) API calls (unique message ids) per session in the window, then prices
orient_words (and each per-item increment) as tokens carried as cache reads on every later lead call.
Tokens per word = 1.33 (ASSUMED, English prose heuristic). Ignores compaction (which drops it, lowering cost)
and re-injection after compaction (raising it).
"""

import json
import pathlib
import sys


def main(d, lo, hi, price, words, *items):
    price, words = float(price), float(words)
    sessions = {}
    for f in pathlib.Path(d).expanduser().glob("*.jsonl"):
        ids = set()
        for line in f.open(errors="replace"):
            try:
                o = json.loads(line)
            except Exception:  # noqa: S112
                continue
            if o.get("type") != "assistant" or o.get("isSidechain"):
                continue
            ts = (o.get("timestamp") or "")[:10]
            if not (lo <= ts <= hi):
                continue
            mid = o.get("message", {}).get("id")
            if mid:
                ids.add(mid)
        if ids:
            sessions[f.name] = len(ids)
    calls = sum(sessions.values())
    tpw = 1.33
    print(f"lead sessions={len(sessions)} lead calls={calls} mean calls/session={calls / max(1, len(sessions)):.0f}")

    def cost(w):
        return w * tpw * calls * price / 1e6

    print(
        f"orient {words:.0f} words -> {words * tpw:.0f} tokens -> ${cost(words):.2f} over the window (${cost(words) / max(1, len(sessions)):.3f}/session)"
    )
    for w in items:
        w = float(w)
        print(f"  +{w:.0f} words per item -> ${cost(w):.3f} over the window")


if __name__ == "__main__":
    main(*sys.argv[1:])
