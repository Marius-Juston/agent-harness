#!/usr/bin/env python3
"""SubagentStop(builder): a builder cannot finish until its tree is clean, the gate passed on HEAD, an open PR shows
HEAD, and the PR summary has a `Reuse:` line when the branch adds a dependency or a new non-test module (#173; the
forms: REUSE_FORMS in devkit/workflow.py). A refusal (decision "block") names the one command to run next.

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
# Seconds for the two devkit probes; with the git and gh calls the whole stop stays under hooks.json's 240 s.
GATE_TIMEOUT, REUSE_TIMEOUT = 120, 60


def run(cmd: list[str], cwd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def git(cwd: str, *args: str) -> str:
    out = run(["git", *args], cwd)
    if out.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def gate_passed(top: str) -> bool:
    code = "from devkit.workflow import _gate_passed; print('GATE', 'yes' if _gate_passed() else 'no')"
    out = run(["uv", "run", "--quiet", "python", "-c", code], top, timeout=GATE_TIMEOUT)
    found = re.findall(r"^GATE (yes|no)$", out.stdout, re.M)
    if not found:
        raise RuntimeError(f"gate probe gave no answer: {(out.stderr or out.stdout).strip()[-300:]}")
    return found[-1] == "yes"


def reuse_missing(top: str, body: str) -> str | None:
    """What the PR body lacks by devkit's reuse rule (devkit.workflow.reuse_problem), with the accepted forms."""
    code = (
        "import json, sys; from devkit.workflow import REUSE_FORMS, reuse_problem; "
        "p = reuse_problem(sys.stdin.read()); print('REUSE', json.dumps(p and f'{p}.\\n{REUSE_FORMS}'))"
    )
    cmd = ["uv", "run", "--quiet", "python", "-c", code]
    out = subprocess.run(cmd, cwd=top, input=body, capture_output=True, text=True, timeout=REUSE_TIMEOUT, check=False)
    found = re.findall(r"^REUSE (.*)$", out.stdout, re.M)
    if not found:
        raise RuntimeError(f"reuse probe gave no answer: {(out.stderr or out.stdout).strip()[-300:]}")
    why = json.loads(found[-1])
    return why and f"{why}\nAdd the line to the PR body: `gh pr edit --body-file - <<'EOF'` ... `EOF`"


def problem(top: str, branch: str, issue: str) -> str | None:
    """What still stands between this worktree and a finished hand-back, as the command to run; None when done."""
    if dirty := git(top, "status", "--porcelain", "--untracked-files=all"):
        files = ", ".join(line[3:] for line in dirty.splitlines()[:5])
        return f"uncommitted files ({files}): `git add -A && git commit`, or move scratch files to .scratch/"
    if not gate_passed(top):
        return "no gate pass on HEAD: run `uv run dev check` until it ends `RESULT: passed`"
    head = git(top, "rev-parse", "HEAD")
    out = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url,headRefOid,body"], top)
    if out.returncode:
        return f"cannot see an open PR (gh: {out.stderr.strip()[:200]}): retry `gh pr list --head {branch}`"
    prs = json.loads(out.stdout or "[]")
    if not prs:
        return f"no open PR: `uv run dev pr {issue} --summary - <<'EOF'` ... `EOF`"
    current = [pr for pr in prs if pr.get("headRefOid") == head]
    if not current:
        return f"the PR ({prs[0].get('url')}) is behind HEAD: `git push`"
    return reuse_missing(top, str(current[0].get("body") or ""))


def blocked(top: str, issue: str) -> bool:
    """The item is status:blocked: the builder stopped on purpose (`uv run dev board block`, e.g. a wrong test), so
    refusing would push it toward a PR it should not open. Unknown (gh fails) counts as not blocked."""
    out = run(["gh", "issue", "view", issue, "--json", "labels"], top)
    if out.returncode:
        return False
    try:
        return any(label.get("name") == "status:blocked" for label in json.loads(out.stdout).get("labels", []))
    except (ValueError, AttributeError):
        return False


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
    if blocked(top, issue):
        msg = f"completion gate: #{issue} is status:blocked on the board, so the builder may stop unfinished ({why})"
        print(json.dumps({"systemMessage": msg}))
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
