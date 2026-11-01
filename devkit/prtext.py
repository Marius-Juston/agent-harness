"""PR text rules: the `Reuse:` line (#173, #196), Conventional Commits titles (#100) and session-link leaks.

`dev pr` and integrate apply them to a PR's title and body; `dev pr-title` checks a title in CI. devkit.workflow
re-exports these names: plugins/harness-ops/scripts/completion_gate.py imports REUSE_FORMS and reuse_problem from it.
"""

import re
import tomllib
from pathlib import PurePosixPath

from devkit import board, checks, config
from devkit.paths import git, root

REUSE_FORMS = """\
A branch that adds a dependency to pyproject.toml or a new non-test .py module needs one `Reuse:` line in its PR
summary, in one of three forms (#173; why: docs/research/software-discovery.md §9):
  Reuse: httpx PASS (chosen), requests HOLD dormant   <- candidates named from memory, each with its result as
                                                         `uv run dev reuse check <name>...` prints it (PASS, HOLD,
                                                         FAIL, FLAG, NOT FOUND or UNVERIFIED)
  Reuse: docs/research/<note>.md                      <- the research note that already weighed them (must exist)
  Reuse: none: <reason>                               <- why no library applies (`none` then :, -, – or —),
                                                         e.g. `Reuse: none: harness adoption` on a copier update
Empty or whitespace-only new .py files (package markers) do not count. Each new dependency also needs a fit clause
in a `Reuse:` line, answered from memory after `reuse check` (#196; why: docs/research/software-discovery.md §11):
  fit: <package> does <need> via <API>   e.g. `fit: httpx does HTTP/2 requests via httpx.Client(http2=True)`
  fit: unsure                            <- when you cannot name the API; `fit: <package> unsure` for one of several
  Reuse: httpx PASS (chosen); fit: httpx does HTTP/2 requests via httpx.Client(http2=True)
`Reuse: none: harness adoption` needs no fit clause: the template, not the agent, picked those dependencies."""
REUSE_LINE = re.compile(r"^[\s>*_-]*Reuse[*_]*:[*_]*[ \t]*(.*)$", re.M | re.I)
GATE_RESULT = re.compile(r"\b[A-Za-z0-9][\w.\[\]-]*[\s:(=]{1,3}(PASS|HOLD|FAIL|FLAG|NOT FOUND|UNVERIFIED)\b")
NONE_REASON = re.compile(r"none\s*[:\-–—]\s*\S", re.I)
NOTE_PATH = re.compile(r"docs/research/[\w./-]+\.md")
HARNESS_ADOPTION = re.compile(r"none\s*[:\-–—]\s*harness adoption\b", re.I)
FIT_CLAUSE = re.compile(r"\bfit:\s*(.*?)\s*(?=[;,]?\s*\bfit:|$)", re.I)
FIT_NAMED = re.compile(r"([A-Za-z0-9][\w.-]*)(?:\[[\w,.\s-]*\])?\s+(?:unsure\b|does\s+\S.*?\s+via\s+\S)", re.I)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dependency_names(pyproject: str) -> set[str]:
    """Normalized names in [project] dependencies and optional-dependencies, [dependency-groups] (PEP 735) and
    [tool.uv] dev-dependencies, less the project's own name (a self-reference such as `p[extra]`)."""
    try:
        data = tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project", {})
    specs = [*project.get("dependencies", []), *data.get("tool", {}).get("uv", {}).get("dev-dependencies", [])]
    for group in (*project.get("optional-dependencies", {}).values(), *data.get("dependency-groups", {}).values()):
        specs += group
    names = (re.match(r"\s*([A-Za-z0-9][\w.-]*)", s) for s in specs if isinstance(s, str))
    return {_normalized(m.group(1)) for m in names if m} - {_normalized(str(project.get("name", "")))}


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    in_tests = bool({"tests", "test"} & set(p.parts[:-1]))
    return in_tests or p.name == "conftest.py" or p.name.startswith("test_") or p.name.endswith("_test.py")


def reuse_needs(base: str) -> list[str]:
    """What on this branch since `base` calls for a `Reuse:` line: added dependencies and new non-test modules (a
    renamed module is not new)."""
    head_deps = _dependency_names(git("show", "HEAD:pyproject.toml", check=False))
    deps = head_deps - _dependency_names(git("show", f"{base}:pyproject.toml", check=False))
    added = git("diff", "--name-only", "-M", "--diff-filter=A", base, "HEAD").splitlines()
    modules = [
        p
        for p in added
        if p.endswith(".py") and not _is_test(p) and git("show", f"HEAD:{p}", check=False).strip()  # not a marker
    ]
    return [*(f"dependency {d}" for d in sorted(deps)), *(f"module {m}" for m in modules)]


def reuse_line_problem(body: str) -> str | None:
    """Why `body` has no `Reuse:` line in one of REUSE_FORMS, or None."""
    lines = [m.group(1).strip() for m in REUSE_LINE.finditer(body)]
    for line in lines:
        if GATE_RESULT.search(line) or NONE_REASON.match(line):
            return None
        if (note := NOTE_PATH.search(line)) and (root() / note.group(0)).is_file():
            return None
    return f"its `Reuse:` line ({lines[0][:80]!r}) is in none of the forms" if lines else "has no `Reuse:` line"


def fit_missing(body: str, dependencies: list[str]) -> list[str]:
    """The new `dependencies` (normalized names) that no fit clause in a `Reuse:` line of `body` covers (#196 F0).
    `fit: <package> does <need> via <API>` or `fit: <package> unsure` covers that package; `fit: unsure` covers all."""
    named: set[str] = set()
    for line in REUSE_LINE.finditer(body):
        if HARNESS_ADOPTION.match(line.group(1).strip()):
            return []  # template-added dependencies are not the agent's pick
        for clause in FIT_CLAUSE.finditer(line.group(1)):
            if re.match(r"unsure\b", clause.group(1), re.I):
                return []
            if m := FIT_NAMED.match(clause.group(1)):
                named.add(_normalized(m.group(1)))
    return [d for d in dependencies if d not in named]


def reuse_problem(body: str) -> str | None:
    """One line on why this branch's PR summary needs a `Reuse:` line it lacks (#173), or a fit clause for a new
    dependency (#196), or None."""
    base = checks.merge_base()
    if base is None or not (needs := reuse_needs(base)):
        return None
    if (why := reuse_line_problem(body)) is not None:
        shown = ", ".join(needs[:3]) + (f" and {len(needs) - 3} more" if len(needs) > 3 else "")
        return f"the branch adds {shown}, but the PR summary {why}"
    if missing := fit_missing(body, [n.removeprefix("dependency ") for n in needs if n.startswith("dependency ")]):
        return (
            f"the branch adds dependency {', '.join(missing)}, but no `Reuse:` line has a fit clause for it"
            " (`fit: <package> does <need> via <API>` or `fit: unsure`)"
        )
    return None


# Conventional Commits 1.0.0 PR titles (#100): the squash subject is the PR title, and release-please (#60) reads the
# subjects on main. The type comes from the issue's type label; the scope from its '<component>: ' title prefix.
COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
KIND_TYPE = {"build": "feat", "epic": "feat", "bug": "fix", "test": "test", "research": "docs", "infra": "ci"}
# Lowercase types only (the spec allows any case; commitizen's check does not). The scope keeps to ASCII word chars,
# '.', '/' and '-', which release-please's and conventional-changelog's parsers both read. The ` (#n)` suffix `dev pr`
# writes is optional: Dependabot's and release-please's PR titles carry none. docs/research/agent-harness.md §12.
CONVENTIONAL_HEAD = re.compile(rf"({'|'.join(COMMIT_TYPES)})(\([\w./-]+\))?!?: (?=\S)", re.ASCII)
CONVENTIONAL = re.compile(CONVENTIONAL_HEAD.pattern + r"(?!\(#\d+\)$)[^\n\r]+", re.ASCII)  # an outcome, not only (#n)
TITLE_FORM = "type(scope): outcome (#n)"
# A release item ('Release v0.1.15', 'release: v0.1.15 notes') is a chore, never a feature scoped by its version.
RELEASE_TITLE = re.compile(r"release:\s*|release\s+(?=v?\d+\.\d)", re.I)


def conventional_title(issue: board.Issue) -> str:
    """`type(scope): outcome (#n)` for `issue`: the type from its type label (KIND_TYPE), the scope from its
    '<component>: ' prefix (board.COMPONENT_TITLE) as a slug; a 'Research: ' / 'Epic: ' prefix is not a scope, and a
    component that is itself a commit type becomes the type. A title that is already Conventional is kept as it is,
    and a release item is `chore(release)`."""
    whole = " ".join(issue.title.split())  # one line, so the subject is one line too
    title, kind, scope = whole, KIND_TYPE.get(issue.kind, "feat"), ""
    if CONVENTIONAL_HEAD.match(whole):
        return f"{whole} (#{issue.number})"
    if m := RELEASE_TITLE.match(title):
        title, kind, scope = title[m.end() :].strip(), "chore", "release"
    elif m := board.TITLE_PREFIX.match(title):
        title = title[m.end() :].strip()
    elif board.COMPONENT_TITLE.match(title):
        component, title = (part.strip() for part in title.split(":", 1))
        if component.lower() in COMMIT_TYPES:
            kind = component.lower()
        else:
            scope = re.sub(r"[^\w.-]+", "-", component.lower(), flags=re.ASCII).strip("-.")
    return f"{kind}{f'({scope})' if scope else ''}: {title or whole} (#{issue.number})"


def title_problem(title: str | None) -> str | None:
    """Why `title` is not a Conventional Commits PR title, or None (always None with `conventional_titles = false`)."""
    if not config.get().conventional_titles or CONVENTIONAL.fullmatch((title or "").strip()):
        return None
    return (
        f"PR title {title!r} is not Conventional Commits: write '{TITLE_FORM}', type one of {', '.join(COMMIT_TYPES)}"
    )


def expected_title(info: dict) -> str:
    """The title `dev pr` gives the PR `info`, from the first issue it links (`board.linked`), quoted; TITLE_FORM when
    it links none or the board cannot be read."""
    closes, own = board.linked(info.get("body") or "", info.get("headRefName", ""))
    for n in closes + own:
        try:
            return repr(conventional_title(board.get(n)))
        except board.BoardError:
            break
    return repr(TITLE_FORM)


def check_title(title: str | None) -> int:
    """`dev pr-title`: CI passes the title in PR_TITLE (an env value, never `${{ }}` inside a script)."""
    if why := title_problem(title):
        print(f"FAIL {why}")
        return 1
    print(f"ok: {title.strip()}")
    return 0


# Owner rule (2026-10-05): a public repository never gets a session link or trailer in a commit, PR or comment.
# Assembled from pieces: the scrub test bans the literal link from this public repository.
SESSION_URL = "claude.ai/code/" + "session"
SESSION_LEAK = re.compile(rf"https?://{re.escape(SESSION_URL)}_\w|^[ \t]*Claude-Session[ \t]*:", re.I | re.M)
SESSION_RULE = f"this repository is public: no {SESSION_URL} URL or Claude-Session trailer"


def session_leaks(**texts: str | None) -> list[str]:
    """The names of `texts` that carry a session link (https:// + SESSION_URL + `_<id>`) or a Claude-Session
    trailer; a mention of the path alone, as in a rule's text, is not one."""
    return [name for name, text in texts.items() if SESSION_LEAK.search(text or "")]
