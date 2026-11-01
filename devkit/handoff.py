"""`dev handoff`: regenerate the handoff file's board-derived sections (#394).

The lead skill (§6) asks the lead to rewrite the handoff after each integrate batch; under load that slipped and the
SessionStart hook showed day-old work. The board already knows claims, PRs and the next startable items, and git log
knows what merged, so `## In flight`, `## Just merged` and `## Next` are rebuilt from them. Every other part of the
file (the preamble and hand-written sections such as owner decisions) is kept byte for byte. A missing handoff file
is never created: the command says so and does nothing.
"""

from devkit import board, config
from devkit.paths import default_branch, git, root

GENERATED = ("In flight", "Just merged", "Next")  # rebuilt, in this order, where the first of them stands
LIMITS = {"In flight": 12, "Just merged": 5, "Next": 5}  # keeps the file under the skill's 40 lines
ACTIVE = ("in-progress", "review", "blocked")


def chunks(text: str) -> list[tuple[str | None, str]]:
    """The text split before each `## ` heading: (heading name, or None for the preamble; the chunk's exact text)."""
    out: list[tuple[str | None, str]] = [(None, "")]
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            out.append((line[3:].strip(), line))
        else:
            out[-1] = (out[-1][0], out[-1][1] + line)
    return out


def _generated(name: str) -> str | None:
    """The canonical name of a generated section heading (case-insensitive), or None for a hand-written one."""
    return next((g for g in GENERATED if g.lower() == name.lower()), None)


def sections(issues: list[board.Issue], done: set[board.Ref], merged: list[str]) -> dict[str, list[str]]:
    """The bullet lines of each generated section, from the open issues, the closed-done set and merge subjects."""
    flight = []
    for i in issues:
        if i.status not in ACTIVE or i.kind == "epic":
            continue
        c = i.claim()
        who = f" {c.agent} on `{c.branch}`" if c else ""
        flight.append(f"- #{i.number} {i.status}{who}: {i.title}")
    rank = board.ranks(issues)
    nxt = []
    for i in board.startable(issues, done=done)[: LIMITS["Next"]]:
        n = len(rank[i.number].downstream)
        why = f", unblocks {n}" if n else ""
        nxt.append(f"- #{i.number} {rank[i.number].priority} {i.kind}{why}: {i.title}")
    return {
        "In flight": board.cap(flight, LIMITS["In flight"], "`uv run dev board list`") or ["- nothing"],
        "Just merged": [f"- {s}" for s in merged[: LIMITS["Just merged"]]] or ["- nothing"],
        "Next": nxt or ["- nothing startable"],
    }


def render(text: str, generated: dict[str, list[str]]) -> str:
    """`text` with its generated sections replaced by `generated`, in GENERATED order where the first stood (after
    the preamble when it has none); every other chunk is kept byte for byte."""
    block = "".join(f"## {name}\n" + "".join(f"{line}\n" for line in generated[name]) + "\n" for name in GENERATED)
    out: list[str] = []
    at = None  # the index of the generated block in `out`
    for name, chunk in chunks(text):
        if name is None or _generated(name) is None:
            out.append(chunk)
        elif at is None:
            at = len(out)
            out.append(block)
    if at is None:
        pre = out[0]
        out[0] = pre if pre.endswith("\n\n") or not pre else pre.rstrip("\n") + "\n\n"
        at = 1
        out.insert(at, block)
    if at == len(out) - 1:
        out[at] = block[:-1]  # the file ends on the generated sections: no trailing blank line
    return "".join(out)


def merged_subjects(n: int = LIMITS["Just merged"]) -> list[str]:
    """The latest `n` first-parent commit subjects on the default branch (origin's when this clone has it)."""
    branch = default_branch()
    origin = git("rev-parse", "--verify", "--quiet", f"origin/{branch}", cwd=root(), check=False)
    ref = f"origin/{branch}" if origin else branch
    log = git("log", "--first-parent", f"-n{n}", "--format=%s", ref, cwd=root(), check=False)
    return [s for s in log.splitlines() if s.strip()]


def refresh() -> str:
    """Rewrite the handoff file's generated sections from the live board and git log; the one-line outcome."""
    name = config.get().handoff
    path = root() / name
    if not path.is_file():
        return f"no handoff file at {name} ([tool.devkit] handoff): nothing written"
    try:
        issues, done = board.fetch(with_comments=True, links=board.ALL_LINK_FIELDS), board.closed_numbers()
    except board.BoardError as exc:
        return f"handoff not refreshed: board unavailable ({exc})"
    old = path.read_text()
    new = render(old, sections(issues, done, merged_subjects()))
    if new == old:
        return f"{name} already current"
    path.write_text(new)
    return f"refreshed {name}: in flight, just merged and next; commit it with your other lead edits"


def after_integrate() -> str:
    """integrate's call after a merge: refresh, except on the default branch, whose checkout integrate keeps clean so
    its fast-forward pull still runs (the lead lands HANDOFF on a branch, lead skill §6)."""
    if git("branch", "--show-current", cwd=root(), check=False) == default_branch():
        return "handoff: on the default branch, not refreshed; run `uv run dev handoff` on your lead branch"
    try:
        return refresh()
    except (OSError, RuntimeError, ValueError) as exc:  # the merge stands whatever happens here
        return f"handoff not refreshed: {exc}"
