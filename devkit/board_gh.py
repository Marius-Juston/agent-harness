"""The board's `gh` calls: one entry point, `gh`, that retries rate limits and dropped connections, and re-reads
GitHub before repeating a write whose outcome is unknown (devkit.board has the whole story)."""

import json
import random
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

from devkit import backoff
from devkit.paths import git, root


class BoardError(RuntimeError):
    pass


# GitHub's secondary rate limits answer 403 or 429; its guidance is to wait at least a minute, then exponentially
# longer (docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api). gh prints no headers, so
# the wait is GH_FLOOR plus full jitter (devkit.backoff). A refused request did nothing, so a retry is safe.
SECONDARY_LIMIT = re.compile(r"secondary rate limit|abuse detection|HTTP 429", re.I)
GH_TRIES: int = 3
GH_FLOOR, GH_BASE, GH_CAP = 60.0, 30.0, 240.0  # seconds
GH_SLEEP = time.sleep
GH_RNG = random.Random()  # noqa: S311 - retry jitter, not a secret
GH_CLOCK = time.time

# A network failure between gh and GitHub (#538): the call is retried after a short full-jitter wait, GH_NET_TRIES
# tries in all, inside each rate-limit try. Unlike a refusal, a dropped connection may come after GitHub acted, so
# only reads and idempotent writes retry blind; a write that adds something (gh_kind "write") retries only through
# its recheck, which re-reads GitHub first: landed -> its output, not landed -> the call again. A write without a
# recheck fails as before, saying its outcome is unknown.
TRANSIENT = re.compile(r"TLS handshake timeout|connection reset by peer|i/o timeout|\bEOF\b|HTTP 50[234]\b", re.I)
GH_NET_TRIES = 4
GH_NET_FLOOR, GH_NET_BASE, GH_NET_CAP = 1.0, 2.0, 30.0  # seconds
GH_SKEW = 120.0  # seconds: a write that landed is looked for this far before the call began (clock skew)
GH_READS = {
    ("issue", "list"), ("issue", "view"), ("pr", "list"), ("pr", "view"), ("pr", "diff"), ("pr", "checks"),
    ("label", "list"), ("repo", "view"), ("run", "list"), ("run", "view"), ("run", "download"), ("auth", "status"),
}  # fmt: skip
GH_IDEMPOTENT = {("issue", "edit"), ("pr", "edit"), ("label", "edit"), ("pr", "ready")}  # set state, add nothing
RECHECKS: dict[tuple[str, ...], Callable] = {}  # (gh, sub) -> a _recheck for a module's own write (devkit.release)
GH_STATUS_API = re.compile(r"^repos/[^/]+/[^/]+/statuses/\w+$")  # a commit status: the latest per context counts


def _flag(args: tuple[str, ...], *names: str) -> str | None:
    """The value after the first of `names` in `args` (or of `--name=value`), else None."""
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            return args[i + 1]
        if (k := a.partition("=")[0]) in names and k.startswith("--") and "=" in a:
            return a.partition("=")[2]
    return None


def _api_method(args: tuple[str, ...]) -> str:
    """The HTTP method of `gh api ...`: -X/--method, else POST when it sends fields (gh's own default), else GET."""
    sends = {"-f", "-F", "--field", "--raw-field", "--input"}
    fields = any(a in sends or a.partition("=")[0] in sends for a in args)
    return (_flag(args, "-X", "--method") or ("POST" if fields else "GET")).upper()


def gh_kind(args: tuple[str, ...]) -> str:
    """The call's kind: "read", "idempotent" (repeating it leaves GitHub as one call would) or "write" (a repeat
    may add a second issue, comment or merge attempt). Anything not known as the first two is a write."""
    head = tuple(args[:2])
    if head in GH_READS or args[:1] == ("search",):
        return "read"
    if args[:1] == ("api",):
        method, rest = _api_method(args), [a for a in args[1:] if not a.startswith("-")]
        if "graphql" in rest:  # a GraphQL POST is a read unless it carries a mutation
            queries = [a.partition("=")[2] for a in args if a.startswith("query=")]
            return "write" if any(q.lstrip().startswith("mutation") for q in queries) else "read"
        if method in ("GET", "HEAD"):
            return "read"
        if method in ("PUT", "DELETE") or (method == "POST" and any(GH_STATUS_API.match(a) for a in rest)):
            return "idempotent"
        return "write"
    if head in GH_IDEMPOTENT or (head == ("label", "create") and "--force" in args):
        return "idempotent"
    if head in (("issue", "close"), ("pr", "close"), ("issue", "reopen"), ("pr", "reopen")):
        return "write" if _flag(args, "-c", "--comment") is not None else "idempotent"
    return "write"


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").replace("\r\n", "\n").strip() == (b or "").replace("\r\n", "\n").strip()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _landed_comment(where: str, number: str, body: str, since: float) -> str | None:
    """The URL of a comment on issue or PR `number` with `body`, posted since `since` (less GH_SKEW), else None."""
    url = f"repos/{where}/issues/{number}/comments?since={_iso(since - GH_SKEW)}&per_page=100"
    rows = json.loads(gh("api", url, "--jq", "[.[] | {body, html_url}]") or "[]")
    found = [r["html_url"] for r in rows if _same(r.get("body"), body)]
    return found[-1] + "\n" if found else None


def _recheck(args: tuple[str, ...], input: str | None) -> Callable[[float], str | tuple[str, ...]] | None:
    """For a write, the re-read that settles a call whose connection dropped: `recheck(since)` returns the call's
    output when it landed, else the args to send again. None when no re-read can tell (such a call is not retried)."""
    head, where = tuple(args[:2]), _flag(args, "-R", "--repo")
    body = input if _flag(args, "-F", "--body-file") == "-" else _flag(args, "-b", "--body")
    if where is None:
        return None
    if head in (("issue", "comment"), ("pr", "comment")) and body is not None and len(args) > 2:
        return lambda since: _landed_comment(where, args[2], body, since) or args
    if head == ("issue", "create") and (title := _flag(args, "-t", "--title")) is not None and body is not None:

        def issue_created(since: float) -> str | tuple[str, ...]:
            url = (
                f"repos/{where}/issues?state=all&sort=created&direction=desc&per_page=30&since={_iso(since - GH_SKEW)}"
            )
            jq = "[.[] | select(.pull_request == null) | {title, body, html_url}]"
            rows = json.loads(gh("api", url, "--jq", jq) or "[]")
            found = [r["html_url"] for r in rows if r.get("title") == title and _same(r.get("body"), body)]
            return found[0] + "\n" if found else args

        return issue_created
    if head == ("pr", "create") and (branch := _flag(args, "-H", "--head")) is not None:

        def pr_created(since: float) -> str | tuple[str, ...]:
            rows = json.loads(
                gh("pr", "list", "-R", where, "--head", branch, "--state", "all", "--json", "url,createdAt")
            )
            found = [r["url"] for r in rows if (r.get("createdAt") or "") >= _iso(since - GH_SKEW)]
            return found[0] + "\n" if found else args

        return pr_created
    if head in (("issue", "close"), ("pr", "close"), ("pr", "merge")) and len(args) > 2:
        note = _flag(args, "-c", "--comment")

        def settled(since: float) -> str | tuple[str, ...]:
            state = json.loads(gh(args[0], "view", args[2], "-R", where, "--json", "state"))["state"].upper()
            if state in ("CLOSED", "MERGED") and (head != ("pr", "merge") or state == "MERGED"):
                return ""
            if note is not None and _landed_comment(where, args[2], note, since):  # posted, then the close dropped
                i = next(i for i, a in enumerate(args) if a.partition("=")[0] in ("-c", "--comment"))
                return args[:i] + args[i + (1 if "=" in args[i] else 2) :]
            return args

        return settled
    return None


def gh(*args: str, input: str | None = None) -> str:
    kind, since = gh_kind(args), GH_CLOCK()
    recheck = RECHECKS.get(tuple(args[:2]), _recheck)(args, input) if kind == "write" else None
    sent = args  # what the next try sends: a recheck may drop a part that already landed
    unknown = False  # the last try's connection dropped: GitHub may have acted on it
    dropped = ""  # that try's error line

    def once() -> subprocess.CompletedProcess:
        nonlocal sent, unknown, dropped
        if unknown and recheck is not None:
            try:
                again = recheck(since)
            except (BoardError, ValueError, KeyError, TypeError) as e:
                raise BoardError(
                    f"gh {' '.join(args[:3])}: the connection dropped and re-reading GitHub failed ({e}); "
                    "the outcome is unknown: check GitHub before rerunning"
                ) from e
            if isinstance(again, str):
                return subprocess.CompletedProcess(["gh", *sent], 0, again, "")
            sent = again
        out = subprocess.run(["gh", *sent], cwd=root(), capture_output=True, text=True, input=input, check=False)
        unknown = out.returncode != 0 and bool(TRANSIENT.search(out.stderr))
        dropped = (out.stderr.strip().splitlines() or [""])[-1][:200]
        return out

    def net_hint(out: subprocess.CompletedProcess) -> float | None:
        return 0.0 if unknown and (kind != "write" or recheck is not None) else None

    def net() -> subprocess.CompletedProcess:
        return backoff.retry(
            once,
            net_hint,
            tries=GH_NET_TRIES,
            sleep=GH_SLEEP,
            rng=GH_RNG,
            base=GH_NET_BASE,
            cap=GH_NET_CAP,
            floor=GH_NET_FLOOR,
            on_wait=lambda w: print(
                f"gh {' '.join(args[:2])}: network error ({dropped}); retrying in {w:.1f}s", file=sys.stderr
            ),
        )

    def hint(out: subprocess.CompletedProcess) -> float | None:
        return 0.0 if out.returncode != 0 and SECONDARY_LIMIT.search(out.stderr + out.stdout) else None

    out = backoff.retry(
        net,
        hint,
        tries=GH_TRIES,
        sleep=GH_SLEEP,
        rng=GH_RNG,
        base=GH_BASE,
        cap=GH_CAP,
        floor=GH_FLOOR,
        on_wait=lambda w: print(
            f"gh {' '.join(args[:2])}: GitHub secondary rate limit; retrying in {w:.0f}s", file=sys.stderr
        ),
    )
    if out.returncode != 0:
        why = out.stderr.strip() or out.stdout.strip()
        if unknown and kind == "write":
            why += "; the connection dropped, so the outcome is unknown: check GitHub before rerunning"
        raise BoardError(f"gh {' '.join(args[:3])}: {why}")
    return out.stdout


def repo() -> str:
    url = git("remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    if not m:
        raise BoardError("no GitHub origin remote: run `uv run dev setup` (or `gh repo create`) first")
    return m.group(1)


def graphql(query: str, **variables: str | int | bool) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        typed = isinstance(v, int)  # -F sends ints and booleans typed, -f sends a string
        args += ["-F" if typed else "-f", f"{k}={str(v).lower() if isinstance(v, bool) else v}"]
    return json.loads(gh(*args))["data"]


def gh_json(*args: str, fields: str, links: str) -> object:
    """`gh ... --json fields,links`; without the link fields when this gh does not know them (or `links` is "")."""
    try:
        return json.loads(gh(*args, "--json", f"{fields},{links}" if links else fields))
    except BoardError as e:
        if "Unknown JSON field" not in str(e):
            raise
        return json.loads(gh(*args, "--json", fields))
