# Question (#192)

How well does `uv run dev lit search` find the papers that merged research notes ended up citing, compared with the
other discovery tools a researcher agent has (Claude Code's WebSearch and the arxiv MCP `search_papers`)? Gold: the
papers cited by merged notes in this repository and in one private consuming project. Queries: written from each
note's pre-research question by a fresh, tool-less model call, so they do not know the sources.

Decision it serves: whether researcher instructions should name another discovery tool next to `dev lit search`,
whether `dev lit`'s default sources should change, and how much of a note's literature a first search pass misses.

Method: [PROTOCOL.md](PROTOCOL.md), modelled on [the #169 protocol](../software-discovery/PROTOCOL.md).
