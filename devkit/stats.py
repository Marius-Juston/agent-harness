"""`uv run dev agent-stats`: a scorecard of agent runs from Claude Code transcripts (tokens, calls, waste).

Guidance should be kept only when it changes behavior or outcomes; this is the measurement half of that loop. It reads
~/.claude/projects/<this checkout's key>/ (main sessions and their subagents, worktrees included) and reports, per
agent type: runs, median tool calls, median fresh/cached/output tokens, and the share of tool-result characters that
came from whole-file reads and long outputs (the cheapest waste to remove).
"""

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from devkit.paths import main_checkout

BIG_RESULT = 8000  # characters


def project_dirs() -> list[Path]:
    base = Path.home() / ".claude/projects"
    key = re.sub(r"[^A-Za-z0-9]", "-", str(main_checkout()))
    return sorted(p for p in base.glob(f"{key}*") if p.is_dir())


def _result_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(c.get("text", "")) for c in content if isinstance(c, dict))
    return 0


def scan(path: Path) -> dict:
    run: dict[str, Any] = {
        "calls": 0,
        "fresh": 0,
        "cached": 0,
        "output": 0,
        "result_chars": 0,
        "big": 0,
        "tools": defaultdict(int),
    }
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = d.get("message") or {}
        if d.get("type") == "assistant":
            u = msg.get("usage") or {}
            run["fresh"] += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            run["cached"] += u.get("cache_read_input_tokens", 0)
            run["output"] += u.get("output_tokens", 0)
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    run["calls"] += 1
                    run["tools"][c.get("name", "?")] += 1
        elif d.get("type") == "user" and isinstance(msg.get("content"), list):
            for c in msg["content"]:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    n = _result_chars(c.get("content"))
                    run["result_chars"] += n
                    run["big"] += n if n > BIG_RESULT else 0
    return run


def collect() -> dict[str, list[dict]]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for proj in project_dirs():
        for session in proj.glob("*.jsonl"):
            by_type["main session"].append(scan(session))
        for sub in proj.glob("*/subagents/*.jsonl"):
            meta = sub.with_suffix(".meta.json")
            kind = json.loads(meta.read_text()).get("agentType", "?") if meta.exists() else "?"
            by_type[kind].append(scan(sub))
    return by_type


def report(as_json: bool = False) -> str:
    data = collect()
    rows = []
    for kind, runs in sorted(data.items(), key=lambda kv: -len(kv[1])):
        med = lambda k, runs=runs: int(statistics.median(r[k] for r in runs))  # noqa: E731
        chars = sum(r["result_chars"] for r in runs) or 1
        tools: dict[str, int] = defaultdict(int)
        for r in runs:
            for t, n in r["tools"].items():
                tools[t] += n
        top = ", ".join(f"{t} {n}" for t, n in sorted(tools.items(), key=lambda kv: -kv[1])[:4])
        rows.append(
            {
                "type": kind,
                "runs": len(runs),
                "calls": med("calls"),
                "fresh": med("fresh"),
                "cached": med("cached"),
                "output": med("output"),
                "big_share": round(sum(r["big"] for r in runs) / chars, 2),
                "top_tools": top,
            }
        )
    if as_json:
        return json.dumps(rows, indent=1)
    if not rows:
        return "no transcripts for this checkout yet"
    out = [
        f"{'agent type':<24}{'runs':>5}{'calls':>7}{'fresh tok':>11}{'cached tok':>12}{'out tok':>9}{'big%':>6}  top tools (medians per run)"
    ]
    for r in rows:
        out.append(
            f"{r['type'][:23]:<24}{r['runs']:>5}{r['calls']:>7}{r['fresh']:>11,}{r['cached']:>12,}{r['output']:>9,}"
            f"{r['big_share'] * 100:>5.0f}%  {r['top_tools']}"
        )
    out.append(f"big% = share of tool-result characters in results over {BIG_RESULT} chars (whole files, long logs).")
    return "\n".join(out)
