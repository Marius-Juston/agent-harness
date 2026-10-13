"""Arm X (PROTOCOL.md rule 5): the arxiv MCP server's `search_papers`, called over stdio by an MCP client.

Usage: uv run --no-project --with mcp python arxiv_arm.py <in.json> <out.json>
  <in.json>: {query: null, ...}; <out.json> gets {query: {"results": [...], "raw_status": ...}} (resumable).

Server: `uvx arxiv-mcp-server@0.8.2`. Arguments: max_results 20, sort_by relevance, abstract_mode none. A
rate_limited answer is retried after its retry_after_seconds (up to 60 times, amendment 4) (as is an error or timeout) and is never saved as an
answer; the arm's results are what the tool returns once it answers.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = StdioServerParameters(command="uvx", args=["arxiv-mcp-server@0.8.2"])


def parse(text: str) -> tuple[list[dict], str]:
    data = json.loads(text)
    if data.get("status") == "rate_limited":
        return [], "rate_limited"
    out = []
    for p in data.get("papers", []):
        out.append(
            {
                "title": p.get("title", ""),
                "year": int(str(p.get("published", ""))[:4]) if str(p.get("published", ""))[:4].isdigit() else None,
                "ids": {"arxiv": str(p.get("id", "")).split("v")[0] if p.get("id") else ""},
                "url": p.get("url") or p.get("pdf_url") or "",
            }
        )
    return out, data.get("status", "ok")


async def run(queries: list[str], out_path: Path) -> None:
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    async with stdio_client(SERVER) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for q in queries:
            if q in done:
                continue
            for _attempt in range(60):
                res = await s.call_tool(
                    "search_papers",
                    {"query": q, "max_results": 20, "sort_by": "relevance", "abstract_mode": "none"},
                )
                text = "".join(getattr(c, "text", "") for c in res.content)
                try:
                    results, status = parse(text)
                except json.JSONDecodeError:
                    results, status = [], "unparsed: " + text[:200]
                if status not in ("rate_limited", "error") and not status.startswith("unparsed"):
                    break
                try:
                    wait = json.loads(text).get("retry_after_seconds") or 60
                except json.JSONDecodeError:
                    wait = 60
                print(f"rate limited, waiting {wait}s", flush=True)
                await asyncio.sleep(float(wait) + 5)
            if status in ("rate_limited", "error") or status.startswith("unparsed"):
                print(f"still {status} after 60 tries, stopping at: {q}", flush=True)
                return
            done[q] = {"results": results, "status": status, "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
            out_path.write_text(json.dumps(done, indent=1, ensure_ascii=False))
            print(f"{len(results):3d} {status} {q}", flush=True)
            await asyncio.sleep(3)


def main() -> None:
    queries = list(json.loads(Path(sys.argv[1]).read_text()))
    asyncio.run(run(queries, Path(sys.argv[2])))


if __name__ == "__main__":
    main()
