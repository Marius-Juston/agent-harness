"""devkit: the agent harness of this repository (gate, board, navigation, research checks, setup).

Every command is `uv run dev <command>`; `uv run dev --help` lists them. Facts live in one place each:
the command list is this package's parser, the agent surface is `.claude/` (or a plugin), budgets are
`.devkit/ratchets.toml`, and what differs between projects is `[tool.devkit]` in pyproject.toml (devkit/config.py).
"""
