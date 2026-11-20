"""Stage-0 scores the auditor would not give (#262): gold-work overlap across arms and a quote-grounding proxy.

Run from the repository root:
`uv run python -I docs/research/_scratch/researcher-effort/scripts/score_pilot.py <transcripts dir> <pilot dir> <gold.md>`
where `<transcripts dir>/Q06-<arm>/transcript.jsonl` and `<pilot dir>/<arm>/r1-answer.md` exist. Prints JSON.

Quote grounding is NOT the pre-registered accuracy (PROTOCOL.md §4): it only asks whether each verbatim quote of
25+ characters in the answer appears in some tool result the trial saw (its own or a helper's). A quote can be
grounded and still not support the claim, and a WebFetch result is a small model's digest, so a true quote of the
page can be missing from it. It flags paraphrase passed off as quotation; it does not score support.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from summarize import works

# Pair quote marks in order within a paragraph (hard-wrapped notes split quotes over lines); keep 25+ characters.
QUOTE = re.compile(r"\"([^\"]*)\"|“([^”]*)”")
EDITS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
OUTPUT = "_scratch/eval-q/"
ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


def norm(s: str) -> str:
    s = s.replace("\\", "").replace("’", "'").replace("‘", "'")
    s = re.sub(r"[−–—]", "-", s)
    s = re.sub(r"[*_`]", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,;:").lower()


def seen_text(transcript: Path) -> str:
    """Every tool result and helper message in the transcript, normalised and joined, except the results of tools
    that write or read the trial's own output (an Edit result echoes the answer, which would ground itself)."""
    out, own = [], set()
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message")
        content = msg.get("content", []) if isinstance(msg, dict) else []
        if ev.get("type") == "assistant":
            for c in content if isinstance(content, list) else []:
                blob = json.dumps(c.get("input", {}))
                if c.get("type") == "tool_use" and (c.get("name") in EDITS or OUTPUT in blob):
                    own.add(c.get("id"))
        if ev.get("type") != "user":
            continue
        for c in content if isinstance(content, list) else []:
            if c.get("type") != "tool_result" or c.get("tool_use_id") in own:
                continue
            body = c.get("content")
            if isinstance(body, list):
                body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
            out.append(str(body or ""))
    return norm("\n".join(out))


def grounding(answer: str, seen: str) -> dict:
    paragraphs = re.split(r"\n\s*\n", answer)
    quotes = [q for para in paragraphs for a, b in QUOTE.findall(para) if len(q := a or b) >= 25]
    missing = []
    for q in quotes:
        parts = [norm(p) for p in ELLIPSIS.split(q) if len(norm(p)) >= 12]
        if parts and not all(p in seen for p in parts):
            missing.append(q[:120])
    n = len(quotes)
    return {
        "quotes": n,
        "grounded": n - len(missing),
        "share": round((n - len(missing)) / n, 3) if n else None,
        "not_found": missing,
    }


def main() -> None:
    tdir, pdir, gold = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    g = works(gold.read_text())
    found, out = {}, {}
    for arm in "ABC":
        answer = (pdir / arm / "r1-answer.md").read_text()
        found[arm] = works(answer) & g
        out[arm] = {
            "gold_hits": sorted(found[arm]),
            **grounding(answer, seen_text(tdir / f"Q06-{arm}" / "transcript.jsonl")),
        }
    union = set().union(*found.values())
    out["overlap"] = {
        "gold_works": len(g),
        "any_arm": len(union),
        "all_arms": len(set.intersection(*found.values())),
        "only": {arm: sorted(found[arm] - set().union(*(found[o] for o in "ABC" if o != arm))) for arm in "ABC"},
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
