#!/usr/bin/env python3
"""SubagentStop(builder): a builder cannot finish until its tree is clean, the gate passed on HEAD and an open PR shows
HEAD. A refusal (decision "block") names the one command to run next.

Capped at CAP refusals per agent and HEAD, TOTAL_CAP per agent. Claude Code ends the turn itself after 8 consecutive
blocks and still reports success, but that count resets on every tool call (hooks docs, Stop input), so a worker doing
fix-up work between stops would never reach it; and each block costs a full turn of the worker's context (VERIFIED in a
downstream probe, Claude Code 2.1.296). A new commit earns a fresh CAP, since it is progress; TOTAL_CAP bounds a worker
that commits without finishing. Past the cap the stop goes through and the item is blocked on the board with the
reason, once per HEAD: never a silent pass.

`onFailure` has no effect on SubagentStop, so a crash of this script (exit 1, no decision) lets the stop through:
it fails open only on its own crash. An unreadable gate probe (no devkit in the project) counts as such a crash."""

import json
import re
import subprocess
import sys
from pathlib import Path

CAP = 2
TOTAL_CAP = 6


def run(cmd: list[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def git(cwd: str, *args: str) -> str:
    out = run(["git", *args], cwd)
    if out.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def gate_passed(top: str) -> bool:
    code = "from devkit.workflow import _gate_passed; print('GATE', 'yes' if _gate_passed() else 'no')"
    out = run(["uv", "run", "--quiet", "python", "-c", code], top, timeout=120)
    found = re.findall(r"^GATE (yes|no)$", out.stdout, re.M)
    if not found:
        raise RuntimeError(f"gate probe gave no answer: {(out.stderr or out.stdout).strip()[-300:]}")
    return found[-1] == "yes"


def problem(top: str, branch: str, issue: str) -> str | None:
    """What still stands between this worktree and a finished hand-back, as the command to run; None when done."""
    if dirty := git(top, "status", "--porcelain", "--untracked-files=all"):
        files = ", ".join(line[3:] for line in dirty.splitlines()[:5])
        return f"uncommitted files ({files}): `git add -A && git commit`, or move scratch files to .scratch/"
    if not gate_passed(top):
        return "no gate pass on HEAD: run `uv run dev check` until it ends `RESULT: passed`"
    head = git(top, "rev-parse", "HEAD")
    out = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url,headRefOid"], top)
    if out.returncode:
        return f"cannot see an open PR (gh: {out.stderr.strip()[:200]}): retry `gh pr list --head {branch}`"
    prs = json.loads(out.stdout or "[]")
    if not prs:
        return f"no open PR: `uv run dev pr {issue} --summary - <<'EOF'` ... `EOF`"
    if all(pr.get("headRefOid") != head for pr in prs):
        return f"the PR ({prs[0].get('url')}) is behind HEAD: `git push`"
    return None


def main() -> int:
    try:
        data = json.loads(sys.stdin.read())
        kind = data.get("agent_type")
    except (ValueError, AttributeError):
        return 0
    if not isinstance(kind, str) or kind.rsplit(":", 1)[-1] != "builder":
        return 0
    cwd = str(data.get("cwd") or ".")
    top = git(cwd, "rev-parse", "--show-toplevel")
    branch = git(top, "branch", "--show-current")
    if not (m := re.match(r"(\d+)-", branch)):
        msg = f"completion gate skipped: {top} is on {branch or 'a detached HEAD'!r}, not an issue branch <n>-<slug>"
        print(json.dumps({"systemMessage": msg}))
        return 0
    issue = m.group(1)
    agent = re.sub(r"[^\w-]", "_", str(data.get("agent_id") or "unknown"))
    state = Path(git(top, "rev-parse", "--path-format=absolute", "--git-common-dir"), "devkit", "completion-gate")
    count_file = state / agent

    why = problem(top, branch, issue)
    if why is None:
        count_file.unlink(missing_ok=True)
        return 0
    head = git(top, "rev-parse", "HEAD")
    seen = json.loads(count_file.read_text()) if count_file.exists() else {}
    refused = seen.get("count", 0) if seen.get("head") == head else 0
    total = seen.get("total", 0)
    if refused < CAP and total < TOTAL_CAP:
        state.mkdir(parents=True, exist_ok=True)
        count_file.write_text(json.dumps({"head": head, "count": refused + 1, "total": total + 1}))
        reason = f"Not finished ({refused + 1}/{CAP} on this commit), {why}. Then stop again."
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    reason = f"completion gate gave up after {refused or total} refusals on {branch} at {head[:10]}: {why}"
    if seen.get("blocked") == head:
        print(json.dumps({"systemMessage": f"{reason}. #{issue} is already blocked on the board."}))
        return 0
    count_file.write_text(json.dumps({**seen, "blocked": head}))
    out = run(["uv", "run", "--quiet", "dev", "board", "block", issue, "--reason", reason], top)
    note = "" if out.returncode == 0 else f" (board block failed: {out.stderr.strip()[:200]}; block #{issue} by hand)"
    print(json.dumps({"systemMessage": f"{reason}. #{issue} is blocked on the board{note}."}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
