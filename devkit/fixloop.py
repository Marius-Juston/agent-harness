"""Integrate's fix loop and outcome records: the comment that sends a PR integrate did not merge back to its worker
(failing lines, scrubbed of secrets and session links), the conflict send-backs after a merge, and the RESULT-JSON
record `dev integrate --why` prints. Integrate is the caller; devkit.workflow re-exports these names (devkit/train.py
reads them through it).
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from devkit import board
from devkit.ci_status import CHECK_PASSED, JOB_URL, _named, latest_checks
from devkit.merging import conflicting
from devkit.paths import git, state_dir

FIX_LINES = 40  # failing lines a fix-loop comment carries: the local gate's, or the failed Actions jobs' log tail
NEXT_STEP = "fix each item above, run `uv run dev check`, push, and hand back"
# Credentials a gate or job log can print, cut from a fix-loop comment before it is posted: GitHub tokens (classic and
# fine-grained), API keys of the `sk-` form, AWS access key ids, vendor-prefixed tokens, PEM private keys, JWTs, the
# `user:password` of a URL, and HTTP bearer/token values of 20+ characters (so "Bearer of bad news" stays). Measured
# in docs/research/devkit-reuse-survey.md §2.3. The vendor, private-key and JWT patterns are ported from gitleaks'
# default rules (slack-bot-token, gcp-api-key, pypi-upload-token, npm-access-token, gitlab-pat,
# huggingface-access-token, stripe-access-token, private-key, jwt) in config/gitleaks.toml of
# https://github.com/gitleaks/gitleaks, Copyright (c) 2019 Zachary Rice, MIT License; gitleaks' trailing
# delimiter group is a lookahead here so only the token is cut.
_GITLEAKS_END = r"""(?=[\x60'"\s;]|\\[nr]|$)"""
SECRET = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{8,}|AKIA[A-Z0-9]{12,}"
    r"|xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*|pypi-AgEIcHlwaS5vcmc[\w-]{50,1000}|glpat-[\w-]{20}"
    r"|(?:AIza[\w-]{35}|hf_(?i:[a-z]{34})|(?i:npm_[a-z0-9]{36})|(?:sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99}"
    r"|ey[a-zA-Z0-9]{17,}\.ey[a-zA-Z0-9/\\_-]{17,}\.(?:[a-zA-Z0-9/\\_-]{10,}={0,2})?)" + _GITLEAKS_END + ")"
    r"|(?i:-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----[\s\S]{64,}?KEY(?: BLOCK)?-----)"
    r"|(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|(?<=[Bb]earer )[A-Za-z0-9._~+/=-]{20,}|(?<=[Tt]oken )[A-Za-z0-9_]{20,}"
)
REDACTED = "[redacted]"
# A session link or trailer anywhere in a log line, looser than SESSION_LEAK (which judges a PR body): any scheme or
# none, a subdomain, URL-encoded slashes, and a trailer behind `gh run view --log-failed`'s job/step/time prefix.
SESSION_SCRUB = re.compile(r"claude\.ai(?:/|%2F)code(?:/|%2F)" + "session" + r"|Claude-Session[ \t]*:", re.I)
DURATION = re.compile(r", \d+(?:\.\d+)? s\)$")  # the gate's `RESULT: failed (N stage(s), 30 s)` timing


def scrubbed(text: str) -> str:
    """`text` with each SECRET cut, and each line carrying a session link or a Claude-Session trailer (SESSION_SCRUB)
    replaced, before integrate posts it: a log line is not the worker's to edit once it is on a PR. Session lines go
    whatever the repository's visibility (`repo_public` fails closed to public anyway)."""
    lines = [REDACTED if SESSION_SCRUB.search(ln) else ln for ln in text.split("\n")]
    return SECRET.sub(REDACTED, "\n".join(lines))


def failed_check_lines(info: dict) -> list[str]:
    """The last FIX_LINES lines of `gh run view --job <id> --log-failed` over the PR's failing Actions checks (a
    status context has no log to fetch). A log GitHub will not give (or no gh at all) is one line naming the check."""
    lines: list[str] = []
    for c in latest_checks(info.get("statusCheckRollup") or []):
        if c.get("__typename") == "StatusContext" or c.get("status") != "COMPLETED":
            continue
        if c.get("conclusion") in CHECK_PASSED or not (m := JOB_URL.search(c.get("detailsUrl") or "")):
            continue
        try:
            log = board.gh("run", "view", "-R", board.repo(), "--job", m[1], "--log-failed")
        except (board.BoardError, OSError) as exc:
            lines.append(f"check {_named(c)}: no failed log from GitHub ({exc})")
            continue
        lines += [ln.rstrip() for ln in log.splitlines() if ln.strip()]
    return lines[-FIX_LINES:]


def _fenced(lines: list[str]) -> str:
    """`lines` as a fenced block no backtick run inside can close."""
    fence = "`" * max([3, *(len(r) + 1 for ln in lines for r in re.findall(r"`+", ln))])
    return "\n".join([f"{fence}text", *lines, fence])


def fix_cycle(last: re.Match | None, head: str, counted: bool) -> int:
    """The fix cycle of a send-back on `head` after the latest marker `last`: a cycle is a head integrate failed on,
    so the same head keeps the number (a rerun, new feedback, a new gate duration). After a capped round (cycle
    FIX_CAP, issues blocked 'needs owner') the next head starts at 1: the owner who unblocks grants a fresh round.
    A send-back that is not `counted` (a conflict another merge caused) adds nothing."""
    if last is None:
        return int(counted)
    cycle = int(last[1])
    if last[2] == head:
        return cycle
    return (0 if cycle >= board.FIX_CAP else cycle) + counted


def fix_loop(
    pr_number: int,
    info: dict,
    outcome: str,
    reasons: list[str],
    failing: list[str],
    head: str,
    dry_run: bool = False,
    counted: bool = True,
    step: str = NEXT_STEP,
) -> dict:
    """Close the fix loop on a PR integrate did not merge (#110), with no lead relay: comment the `reasons`, the last
    FIX_LINES `failing` lines and the PR's unresolved feedback on the PR (`scrubbed`), under a board.FIX_MARK that
    `board brief` shows as 'Fix before merge'; then move each issue the PR links back to in-progress, its claim kept,
    so the worker is resumed or respawned on it. The cycle (`fix_cycle`) comes from the gh user's latest marker among
    all the PR's comments; at board.FIX_CAP the issues are blocked 'needs owner' instead. The same content on the same
    head as that marker posts nothing. Returns the result fields: `cycle`, `fix` (in-progress, needs owner, already
    posted, dry run), `resume` (the claims' agent@host) or, when GitHub refused a read or a write, `fix_error`: the
    outcome stands either way."""
    branch = info.get("headRefName", "")
    closes, own = board.linked(info.get("body") or "", branch)
    issues = closes + own
    try:
        fb = board.pr_feedback(pr_number)
        marked = board.fix_comments(pr_number)
        trusted = {board.gh_user()} - {""}
        workers = list(dict.fromkeys(f"{c.agent}@{c.host}" for n in issues if (c := board.get(n).claim())))
    except (board.BoardError, OSError, ValueError, KeyError) as exc:
        print(f"warning: no fix loop on PR #{pr_number}: cannot read it or its issues ({exc}); comment it by hand")
        return {"fix_error": f"cannot read the PR or its issues: {exc}"}
    feedback = board.pr_feedback_lines(fb | {"trusted": fb.get("trusted", set()) | trusted})
    failing = failing[-FIX_LINES:]
    _, last = board.fix_loop_state(marked, trusted)
    mark = board.FIX_MARK.search(last["body"]) if last else None
    timeless = [DURATION.sub(")", ln) if ln.startswith("RESULT:") else ln for ln in failing]  # a rerun's new timing
    key = hashlib.sha256(json.dumps([outcome, reasons, timeless, feedback]).encode()).hexdigest()[:12]
    cycle = fix_cycle(mark, head, counted)
    if mark and (mark[2], mark[3]) == (head, key):
        print(f"PR #{pr_number} already has this failure on {head[:10]} (fix cycle {cycle}): nothing posted")
        return {"cycle": cycle, "fix": "already posted", "resume": workers}
    capped = counted and cycle >= board.FIX_CAP
    who = ", ".join(workers) or "the worker that opened it"
    refs = ", ".join(f"#{n}" for n in issues) or "no linked issue"
    parts = [
        f"`dev integrate`: PR #{pr_number} was not merged: **{outcome}** on head {head[:10]} "
        f"(fix cycle {cycle} of {board.FIX_CAP}).",
        "\n".join(reasons),
    ]
    if failing:
        parts.append(f"Failing lines (last {len(failing)}):\n{_fenced(failing)}")
    if feedback:
        parts.append("Unresolved feedback:\n" + "\n".join(feedback))
    if capped:
        parts.append(
            f"This is fix cycle {cycle} of {board.FIX_CAP}: {refs} blocked 'needs owner'. The owner decides: fix it by "
            "hand, re-scope the issue, or close the PR; the next head after an unblock starts a fresh round."
        )
    else:
        parts.append(
            f"Resume {who} on branch `{branch}`: {step}. Then the lead reruns integrate. {refs} stay in progress, "
            "claims kept; `uv run dev board brief <n>` shows this comment as 'Fix before merge'."
        )
    marker = f"<!-- devkit:fix-loop cycle={cycle} head={head} key={key} outcome={outcome} -->"
    body = marker + "\n" + scrubbed("\n\n".join(p for p in parts if p))
    if dry_run:
        print(f"would comment on PR #{pr_number} and move {refs} to {'blocked' if capped else 'in-progress'}")
        return {"cycle": cycle, "fix": "dry run", "resume": workers}
    try:
        board.gh("pr", "comment", str(pr_number), "-R", board.repo(), "--body", body)
        for n in issues:
            if capped:
                board.block(n, f"needs owner: PR #{pr_number} failed integrate on {cycle} heads (last: {outcome})")
            else:
                board.set_status(n, "in-progress")
    except (board.BoardError, OSError) as exc:
        print(f"warning: the fix loop on PR #{pr_number} failed ({exc}): comment it and move {refs} by hand")
        return {"cycle": cycle, "fix_error": str(exc), "resume": workers}
    fix = "needs owner" if capped else "in-progress"
    print(f"sent back: commented on PR #{pr_number}, {refs} -> {fix} (fix cycle {cycle} of {board.FIX_CAP})")
    return {"cycle": cycle, "fix": fix, "resume": workers}


def send_back(
    pr_number: int, info: dict, main: str, paths: list[str], dry_run: bool = False, counted: bool = True
) -> dict:
    """A real conflict with main, through `fix_loop`: the paths go on the PR and its issues back to in-progress,
    naming the worker to resume. `counted`: integrate's own run on this PR (a conflict another merge caused, found by
    `send_back_conflicting`, is no failed cycle of this PR). Returns `fix_loop`'s fields."""
    files = [f"- `{p}`" for p in paths]
    step = "merge `origin/main`, resolve these files, run `uv run dev docs` and `uv run dev check`, push, and hand back"
    head = info.get("headRefOid") or ""
    reasons = [f"It conflicts with main ({main[:10]}) in:", *files]
    fields = fix_loop(pr_number, info, "conflict", reasons, [], head, dry_run, counted, step)
    if head and not dry_run and "fix_error" not in fields:
        _sent_back_file().write_text(json.dumps(_sent_back() | {str(pr_number): head}) + "\n")
    return fields


def _sent_back_file() -> Path:
    """PR number -> the head last sent back, so a PR its worker has not pushed since is not sent back again."""
    path = state_dir("integrate") / "sent-back.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sent_back() -> dict[str, str]:
    try:
        told = json.loads(_sent_back_file().read_text())
    except (OSError, ValueError):
        return {}
    return told if isinstance(told, dict) else {}  # a corrupt file must not crash integrate after its merge


OPEN_PR_LIMIT = 200  # `gh pr list` returns 30 by default


def send_back_conflicting(merged: int) -> list[int]:
    """After PR `merged` landed (#349): check the new main against every other open, non-draft PR into main with
    `merge_conflicts` and `send_back` each one that conflicts at once, instead of when its own integrate runs (a median
    30.5 min later, docs/research/issue-pr-automation.md §3.2). A PR already sent back on its current head is skipped.
    Prints the count and each PR skipped (git cannot check it, or its send-back failed); returns the PRs sent back.
    Raises BoardError or RuntimeError when listing the PRs or the fetch fails."""
    listed = board.gh(
        "pr", "list", "-R", board.repo(), "--base", "main", "--state", "open", "--limit", str(OPEN_PR_LIMIT),
        "--json", "number,headRefName,headRefOid,body,isDraft",
    )  # fmt: skip
    prs = {p["number"]: p for p in json.loads(listed or "[]") if p["number"] != merged and not p.get("isDraft")}
    if not prs:
        print("conflict check of the new main: no other open PR into main")
        return []
    refs = {n: f"refs/devkit/conflicts/pr-{n}" for n in prs}
    main_ref = "refs/devkit/conflicts/main"
    try:
        pulls = (f"+refs/pull/{n}/head:{r}" for n, r in refs.items())
        git("fetch", "--no-tags", "origin", f"+refs/heads/main:{main_ref}", *pulls)
        main = git("rev-parse", main_ref)
        heads = {n: git("rev-parse", r) for n, r in refs.items()}
        found, failed = conflicting(main, heads)
    finally:
        for ref in (main_ref, *refs.values()):
            git("update-ref", "-d", ref, check=False)
    told = _sent_back()
    again = [n for n in found if told.get(str(n)) == heads[n]]
    sent: list[int] = []
    for n in (n for n in found if n not in again):
        try:  # one PR GitHub refuses must not hide the PRs already commented
            fields = send_back(n, prs[n] | {"headRefOid": heads[n]}, main, found[n], counted=False)
        except (board.BoardError, RuntimeError, OSError) as exc:
            fields = {"fix_error": str(exc)}
        if "fix_error" in fields:
            failed[n] = f"send-back failed: {fields['fix_error']}"
        else:
            sent.append(n)
    kept = {k: v for k, v in _sent_back().items() if k.isdigit() and int(k) in prs}
    _sent_back_file().write_text(json.dumps(kept) + "\n")
    print(
        f"conflict check of the new main {main[:10]}: sent back {len(sent)} of {len(prs)} open PRs"
        + (f" ({', '.join(f'#{n}' for n in sent)})" if sent else "")
        + (f"; already sent back: {', '.join(f'#{n}' for n in again)}" if again else "")
    )
    for n, why in failed.items():
        print(f"  skipped #{n}: {why}")
    return sent


def result(pr_number: int, code: int, outcome: str, *, judged: str | None = None, **fields: object) -> int:
    """The last line integrate prints, for the lead to branch on: `RESULT-JSON: {"pr", "outcome", "exit", ...}`.
    Outcomes: merged, would-merge, conflict, gate-failed, harness-review, refused, stopped, not-open, waiting.
    The same fields, with the PR head this run `judged` and the time, become the PR's record for `--why` (#348)."""
    payload = {"pr": pr_number, "outcome": outcome, "exit": code, **fields}
    print("RESULT-JSON: " + json.dumps(payload))
    record = payload | {"judged": judged, "when": time.strftime("%Y-%m-%d %H:%M")}
    try:  # a record that cannot be written costs `--why`, never the outcome
        path = _outcome_file(pr_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=1) + "\n")
        tmp.replace(path)
    except OSError as exc:
        print(f"warning: integrate --why will not see this outcome: {exc}")
    return code


def _outcome_file(pr_number: int) -> Path:
    """The last integrate outcome of a PR on this machine (`result`), what `--why` prints."""
    return state_dir("integrate") / f"pr-{pr_number}.outcome.json"


WHY_LINES = 30  # gate lines a record keeps from the failed stages; the log keeps the rest
WHY_TAIL = 10  # lines kept from the end of a run that printed no failed stage (a stand-in command, a killed gate)
WHY_END = 5  # lines kept from the end of each failed stage once the stages run over WHY_LINES
KEY_LINE = re.compile(r"(?:FAILED|ERROR|E) |\d+ (?:failed|passed|errors?)\b")  # pytest's verdict and count lines


def failing_lines(text: str) -> list[str]:
    """What a gate run says failed: each `FAIL` stage line with the indented problem lines under it (blank ones too:
    pytest's output has them) and the `RESULT` line. Over WHY_LINES, each stage keeps its own line, its pytest
    verdict and count lines (KEY_LINE) and, while they fit, its last WHY_END lines, then a count of the rest. Without
    a failed stage, the run's last WHY_TAIL non-blank lines."""
    stages: list[list[str]] = []
    results, inside = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("FAIL "):
            stages.append([line])
            inside = True
        elif inside and (not line or raw[:1].isspace()):
            stages[-1].append(line)
        else:
            inside = False
            if line.startswith("RESULT: failed"):
                results.append(line)
    if not stages and not results:
        return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-WHY_TAIL:]
    full = [ln for s in stages for ln in s]
    if len(full) <= WHY_LINES:
        return full + results
    for end in (WHY_END, 0):  # the stages' ends go first when the verdict lines alone fill the record
        kept = [
            ln for s in stages for i, ln in enumerate(s) if not i or KEY_LINE.match(ln.strip()) or i >= len(s) - end
        ]
        if len(kept) <= WHY_LINES:
            break
    kept = kept[:WHY_LINES]
    return [*kept, f"... {len(full) - len(kept)} more lines in the log", *results]


def _shown(value: object, pad: str) -> str:
    """A recorded value on screen: continuation lines indented by `pad`, so no text in it can pose as a RESULT line."""
    return re.sub(r"\r\n?|\n", "\n" + pad, str(value))


def why(pr_number: int) -> int:
    """`dev integrate <pr> --why`: print the PR's last recorded integrate outcome and whether its head has moved since.
    Reads only: one `gh pr view`, no GitHub write, no gate, no merge. 1 when this machine has no readable record."""
    path = _outcome_file(pr_number)
    if not path.exists():
        print(
            f"no integrate outcome recorded for PR #{pr_number} on this machine ({path}); read the PR's comments "
            f"(`uv run dev board feedback {pr_number}`) instead of rerunning integrate to see a failure"
        )
        return 1
    try:
        rec = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        print(f"no integrate outcome for PR #{pr_number}: its record {path} is unreadable")
        return 1
    judged = str(rec.get("judged") or "")
    print(
        f"PR #{pr_number}: last integrate here at {_shown(rec.get('when', '?'), '  ')}: "
        f"{_shown(rec.get('outcome'), '  ')} (exit {_shown(rec.get('exit'), '  ')}), judged head "
        + _shown(judged[:10] or "unknown", "  ")
    )
    for key, value in rec.items():
        if key in ("pr", "outcome", "exit", "judged", "when"):
            continue
        if isinstance(value, list) and value:
            print(f"  {_shown(key, '    ')}:\n" + "\n".join(f"    {_shown(v, '      ')}" for v in value))
        else:
            print(f"  {_shown(key, '    ')}: {_shown(value, '    ')}")
    try:
        now = json.loads(board.gh("pr", "view", str(pr_number), "-R", board.repo(), "--json", "headRefOid"))
        head = now["headRefOid"]
    except (board.BoardError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read the PR head from GitHub ({_shown(exc, '  ')}): whether it moved since is unknown")
        return 0
    if not judged:
        print(f"PR head now {head[:10]}; the record names no judged head, so whether it moved is unknown")
    elif head == judged:
        print(f"PR head unchanged since ({head[:10]}): a rerun judges the same commit")
    else:
        print(f"PR head moved since: {judged[:10]} -> {head[:10]}: a rerun judges the new head")
    return 0
