"""Arm C of #262: write the `--agents` JSON (researcher-helper on Opus, research-scout on Sonnet 5.5) to argv[2].

Run: `python3 -I scripts/arm_c_agents.py <path to plugins/harness-ops/agents/researcher.md> <out.json>`.
The researcher-helper prompt is the shipped researcher body plus DELEGATE, so arms A/B and C differ only in delegation.
"""

import json
import sys
from pathlib import Path

DELEGATE = """

## Search helper (this run)
Hand literature search and source reading to the `research-scout` agent (Sonnet 5.5): give it one focused search
task at a time (the question, what to look for, years) and ask for extracts. Its report carries verbatim quotes with
locators, titles, authors, years and links. You decide what to cite: re-open a source yourself (`uv run dev lit get`,
WebFetch, the arxiv MCP) before quoting it for a claim that drives a finding. Write the file yourself.
"""

SCOUT = """You search and read sources for a researcher. You write no files and make no claims of your own.
For the task you are given: run `uv run dev lit search "<q>"` (and `--years`, `-n`), `uv run dev lit get <ids>`,
WebSearch, WebFetch and the arxiv MCP (`get_abstract`, `get_paper_outline`, single sections). Never curl or WebFetch
OpenAlex, Crossref, Semantic Scholar or Europe PMC. Report in at most about 1,500 words: per source its title,
authors, year, venue, link, study type and N, and 1-3 verbatim quotes with a locator (page, section, table, abstract)
that bear on the task, including contrary evidence and failed replications. Report the worst `discovery` line printed.
Never invent a source or a number."""


def body(path: Path) -> str:
    text = path.read_text()
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    agents = {
        "researcher-helper": {
            "description": "Researcher of #262 arm C: researches one question and delegates search to research-scout.",
            "prompt": body(src) + DELEGATE,
            "model": "opus",
            "disallowedTools": ["Artifact", "Skill"],
            "maxTurns": 250,
            "experimental": {"cacheTtl": "1h"},  # as the shipped researcher; checked from write_1h tokens
        },
        "research-scout": {
            "description": "Searches and reads sources for the researcher and returns cited extracts (#262 arm C).",
            "prompt": SCOUT,
            "model": "sonnet",
            "tools": [
                "Bash",
                "Read",
                "Grep",
                "Glob",
                "WebSearch",
                "WebFetch",
                "mcp__arxiv__get_abstract",
                "mcp__arxiv__get_paper_outline",
                "mcp__arxiv__read_paper_section",
                "mcp__arxiv__search_papers",
            ],
        },
    }
    out.write_text(json.dumps(agents, indent=1))
    print(f"wrote {out}: {', '.join(agents)}")


if __name__ == "__main__":
    main()
