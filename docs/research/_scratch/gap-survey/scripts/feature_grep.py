"""Which Claude Code features does the harness reference? (#498)

Lists, per search term, the files under the repository that mention it (case-insensitive),
skipping the venv, research scratch folders, the lockfile and the VCS directory.
Usage: uv run python docs/research/_scratch/gap-survey/scripts/feature_grep.py <repo-root>
"""

import sys
from pathlib import Path

TERMS = [
    "OTEL_",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "sandbox",
    "prompt injection",
    "spotlight",
    "untrusted",
    "plan mode",
    "permissionMode",
    "statusLine",
    "claude -p",
    "claude-code-action",
    "AGENTS.md",
    "checkpoint",
    "rewind",
    "PreCompact",
    "UserPromptSubmit",
    '"Stop"',
    "TaskCompleted",
    "TeammateIdle",
    "agent team",
    "output-format",
    "max-budget",
    "ConfigChange",
    "disableBypassPermissionsMode",
    "CLAUDE_ENV_FILE",
    "cost per merged",
]
SKIP_DIRS = {".venv", "_scratch", ".git", "__pycache__", "node_modules"}
SKIP_FILES = {"uv.lock"}


def files(root: Path):
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file() and p.name not in SKIP_FILES:
            try:
                yield p, p.read_text(encoding="utf-8").lower()
            except (UnicodeDecodeError, OSError):
                continue


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    corpus = list(files(root))
    for term in TERMS:
        hits = [str(p.relative_to(root)) for p, text in corpus if term.lower() in text]
        print(f"{term:32} {len(hits):3}  {' '.join(sorted(hits)[:6])}")


if __name__ == "__main__":
    main()
