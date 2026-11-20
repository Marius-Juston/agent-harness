"""Score one #262 trial without an auditor: cost, tokens by kind and model, turns, delegation, leakage, gold recall.

Run from the repository root: `uv run python -I docs/research/_scratch/researcher-effort/scripts/summarize.py
<transcript.jsonl> <answer.md or -> <gold.md> <hide path>...`. Prints one JSON object (PROTOCOL.md §4).
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from devkit import config
from devkit.evals import parse

DOI = re.compile(r"\b10\.\d{4,9}/[^\s)\]>,;\"']+", re.I)
ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})|\barXiv[: ]+(\d{4}\.\d{4,5})", re.I)
URL = re.compile(r"https?://[^\s)\]>\"'`]+")
OWN_REPO = re.compile(r"agent-harness/(?:blob|tree|raw)/[^\s]*docs/research", re.I)


def works(text: str) -> set[str]:
    """Distinct cited works: DOI, else arXiv id, else URL without scheme, query and trailing slash."""
    out = {"doi:" + d.lower().rstrip(".") for d in DOI.findall(text)}
    out |= {"arxiv:" + (a or b) for a, b in ARXIV.findall(text)}
    for u in URL.findall(text):
        if DOI.search(u) or ARXIV.search(u):
            continue
        out.add("url:" + re.sub(r"^https?://(www\.)?", "", u).split("#")[0].split("?")[0].rstrip("/.").lower())
    return out


def priced(model_usage: dict) -> dict:
    """Per model: tokens by kind and $ at [tool.devkit.prices], cache writes apart, next to Claude Code's costUSD."""
    prices = config.get().prices
    out = {}
    for model, u in model_usage.items():
        p = prices.get(re.sub(r"-\d{8}$", "", model))
        toks = {
            "input": u.get("inputTokens", 0),
            "cache_write": u.get("cacheCreationInputTokens", 0),
            "cache_read": u.get("cacheReadInputTokens", 0),
            "output": u.get("outputTokens", 0),
        }
        out[model] = {**toks, "cost_usd_reported": round(u.get("costUSD", 0.0), 4)}
        if p:
            out[model]["read_usd"] = round(toks["cache_read"] * p["cache_read"] / 1e6, 4)
            out[model]["output_usd"] = round(toks["output"] * p["output"] / 1e6, 4)
    return out


def main() -> None:
    transcript, answer, gold, hide = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4:]
    lines = transcript.read_text(errors="replace").splitlines()
    t = parse(lines)
    result, agent_calls, main_turns, leaks = {}, [], 0, []
    # A main thread that waits on background agents ends once per resume: one result event each. Their usage,
    # turns and duration are per segment, so sum them; total_cost_usd and modelUsage are cumulative, so take the last.
    segments, turns, duration_ms, denied, stamps = 0, 0, 0, [], []
    tokens = dict.fromkeys(t["tokens"], 0)
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("timestamp"):
            stamps.append(ev["timestamp"])
        if ev.get("type") == "result":
            result = ev
            segments += 1
            turns += ev.get("num_turns") or 0
            duration_ms += ev.get("duration_ms") or 0
            denied += [d.get("tool_name", "?") for d in ev.get("permission_denials") or []]
            seg = parse([line])["tokens"]
            tokens = {k: tokens[k] + seg[k] for k in tokens}
        if ev.get("type") == "assistant":
            main_turns += ev.get("parent_tool_use_id") is None
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") != "tool_use":
                    continue
                blob = json.dumps(c.get("input", {}))
                if c.get("name") in ("Agent", "Task"):
                    agent_calls.append(c.get("input", {}).get("subagent_type", "?"))
                if OWN_REPO.search(blob) or any(h.rstrip("/") in blob for h in hide):
                    leaks.append(f"{c.get('name')}: {blob[:160]}")
    text = Path(answer).read_text() if answer != "-" and Path(answer).is_file() else ""
    iso = [datetime.fromisoformat(x.replace("Z", "+00:00")) for x in (stamps[0], stamps[-1])] if stamps else []
    g, a = works(gold.read_text()), works(text)
    print(
        json.dumps(
            {
                "cost_usd": round(t["cost"], 4),
                "tokens": tokens,
                "by_model": priced(result.get("modelUsage") or {}),
                "models": t["models"],
                "num_turns": turns,
                "main_assistant_messages": main_turns,
                "duration_s": round(duration_ms / 1000),
                "wall_s": round((iso[1] - iso[0]).total_seconds()) if iso else None,
                "result_segments": segments,
                "permission_denials": denied,
                "stop": result.get("subtype"),
                "is_error": result.get("is_error"),
                "agent_calls": agent_calls,
                "leaks": leaks,
                "answer_chars": len(text),
                "gold_works": len(g),
                "answer_works": len(a),
                "gold_recall": round(len(g & a) / len(g), 3) if g else None,
                "claude_code_version": t["claude_code_version"],
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
