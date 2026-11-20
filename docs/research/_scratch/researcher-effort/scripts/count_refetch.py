"""Arm C (#262): what the Opus thread fetched itself, and how much of it the scouts had already opened.

Run from the repository root:
`uv run python -I docs/research/_scratch/researcher-effort/scripts/count_refetch.py <transcripts dir> [arm ...]`
where `<transcripts dir>/Q06-<arm>/transcript.jsonl` exists (default arms: A B C). Prints JSON. Also counts WebSearch
calls on either thread, which the API bills per search on top of tokens.

A fetch is a source-reading call: an arxiv MCP read (`get_abstract`, `get_paper_outline`, `read_paper_section`,
`download_paper`, `search_paper_text`, keyed by paper id) or WebFetch (keyed by arXiv id when the URL is an arXiv
page, else by the URL without scheme, `www.`, query and trailing slash). Search calls (`dev lit search`, WebSearch,
`search_papers`) are not fetches. Main thread = events without `parent_tool_use_id`; helper = events with one.
"""

import json
import re
import sys
from pathlib import Path

ARXIV_READS = {"get_abstract", "get_paper_outline", "read_paper_section", "download_paper", "search_paper_text"}
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")


def target(name: str, inp: dict) -> tuple[str, str] | None:
    """(kind, key) of a source-reading call, or None."""
    short = name.removeprefix("mcp__arxiv__")
    if short in ARXIV_READS and inp.get("paper_id"):
        return ("abstract" if short == "get_abstract" else "arxiv-read", "arxiv:" + str(inp["paper_id"]))
    if name == "WebFetch" and inp.get("url"):
        m = ARXIV_URL.search(inp["url"])
        key = "arxiv:" + m.group(1) if m else re.sub(r"^https?://(www\.)?", "", inp["url"]).split("?")[0].rstrip("/")
        return ("page", key.lower())
    return None


def count(transcript: Path) -> dict:
    main, helper, helper_calls, agent_calls, web_searches = [], [], 0, 0, 0
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant" or not isinstance(ev.get("message"), dict):
            continue
        sub = ev.get("parent_tool_use_id") is not None
        for c in ev["message"].get("content", []):
            if c.get("type") != "tool_use":
                continue
            helper_calls += sub
            agent_calls += (not sub) and c.get("name") in ("Agent", "Task")
            web_searches += c.get("name") == "WebSearch"
            t = target(c.get("name", ""), c.get("input", {}))
            if t:
                (helper if sub else main).append(t)
    helper_keys = {k for _, k in helper}
    by_kind: dict[str, int] = {}
    for kind, _ in main:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    repeats = [k for _, k in main if k in helper_keys]
    return {
        "agent_calls": agent_calls,
        "web_search_calls": web_searches,
        "helper_tool_calls": helper_calls,
        "helper_fetches": len(helper),
        "helper_targets": sorted(helper_keys),
        "main_fetches": len(main),
        "main_fetches_by_kind": by_kind,
        "main_targets": [k for _, k in main],
        "main_fetches_of_helper_targets": len(repeats),
        "main_distinct_targets": len({k for _, k in main}),
        "main_distinct_of_helper_targets": len({k for _, k in main} & helper_keys),
        "main_new_targets": sorted({k for _, k in main} - helper_keys),
    }


def main() -> None:
    tdir, arms = Path(sys.argv[1]), sys.argv[2:] or ["A", "B", "C"]
    print(json.dumps({arm: count(tdir / f"Q06-{arm}" / "transcript.jsonl") for arm in arms}, indent=1))


if __name__ == "__main__":
    main()
