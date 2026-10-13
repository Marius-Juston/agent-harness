#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Direct literature-API calls that skip `dev lit` (agent-harness#174, #30), per researcher and auditor run.

Reads Claude Code subagent transcripts (``<projects>/**/subagents/*.jsonl`` with their ``.meta.json``) and counts,
per agent type, the runs that call OpenAlex, Crossref, Semantic Scholar or Europe PMC directly: a WebFetch to the
API host, or a Bash command that fetches from it (curl, wget, or a Python/HTTP one-liner). `dev lit` itself never
counts (it never names an API host). Read-only; prints a table, or JSON with --json.

Usage:
  uv run scripts/lit_bypass.py [--projects ~/.claude/projects] [--since 2026-10-14] [--types researcher,auditor]
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

APIS = {
    "openalex": r"api\.openalex\.org",
    "crossref": r"api\.crossref\.org",
    "s2": r"api\.semanticscholar\.org",
    "europepmc": r"ebi\.ac\.uk/europepmc",
}
FETCHER = re.compile(r"\b(curl|wget|urllib|requests|httpx|urlopen|WebFetch)\b")
TYPES = ("harness-ops:researcher", "harness-ops:research-auditor", "researcher", "research-auditor")


def apis_called(name: str, inp: dict) -> list[str]:
    """The literature APIs one tool call fetches from directly ([] for none, and for `dev lit`)."""
    if name == "WebFetch":
        text = str(inp.get("url", ""))
    elif name == "Bash":
        text = str(inp.get("command", ""))
        if not FETCHER.search(text):  # a grep or a note edit that names the host is no call
            return []
    else:
        return []
    return [api for api, pat in APIS.items() if re.search(pat, text, re.I)]


def scan(path: Path) -> tuple[str, collections.Counter]:
    """(start timestamp, calls per API) of one transcript."""
    start, calls = "", collections.Counter()
    for line in path.open(errors="ignore"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        start = start or str(rec.get("timestamp") or "")
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                calls.update(apis_called(b.get("name", ""), b.get("input") or {}))
    return start, calls


def measure(projects: Path, since: str = "", types: tuple[str, ...] = TYPES) -> dict[str, dict]:
    out: dict[str, dict] = {t: {"runs": 0, "bypass_runs": 0, "calls": collections.Counter()} for t in types}
    for meta in sorted(projects.rglob("subagents/*.meta.json")):
        try:
            atype = json.loads(meta.read_text()).get("agentType", "")
        except (OSError, ValueError):
            continue
        log = meta.with_name(meta.name.removesuffix(".meta.json") + ".jsonl")
        if atype not in out or not log.is_file():
            continue
        start, calls = scan(log)
        if since and start[:10] < since:
            continue
        row = out[atype]
        row["runs"] += 1
        row["bypass_runs"] += bool(calls)
        row["calls"].update(calls)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects", type=Path, default=Path.home() / ".claude/projects")
    ap.add_argument("--since", default="", help="only runs started on or after this date (YYYY-MM-DD)")
    ap.add_argument("--types", default=",".join(TYPES), help="comma list of agent types")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = measure(a.projects, a.since, tuple(t for t in a.types.split(",") if t))
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    print(f"direct literature-API calls (skipping dev lit) since {a.since or 'the first transcript'}")
    print("| agent type | runs | runs with a direct call | calls | by API |")
    print("|---|---|---|---|---|")
    for t, r in res.items():
        by = ", ".join(f"{k} {v}" for k, v in r["calls"].most_common()) or "-"
        print(f"| {t} | {r['runs']} | {r['bypass_runs']} | {sum(r['calls'].values())} | {by} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
