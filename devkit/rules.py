"""Path-scoped lessons in `.claude/rules/` (#78). `uv run dev gotcha` appends one deduped line with provenance to the
rules file whose `paths:` match; the gate's rules stage holds every rule line to provenance and to paths that exist,
and every `paths:` glob to at least one file (a renamed directory otherwise leaves a rule that never loads, silently:
docs/research/decision-records-and-drift.md §3.3-3.4)."""

import re
from datetime import date
from pathlib import Path, PurePosixPath

from devkit import checks, frontmatter
from devkit.paths import root
from devkit.ran import Ran

RULES_DIR = ".claude/rules"  # where Claude Code loads project rules (code.claude.com/docs/en/memory)
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
PROVENANCE = re.compile(r"\s*\(#(\d+), (\S+)\)\s*$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
ROOT_FILE = re.compile(r"^[\w.-]+\.(?:py|md|toml|json|ya?ml|sh|txt|cfg|ini|lock|ts|tsx|js|jinja)$")
NOT_A_PATH = set("<>*?{}[]$()= ") | {"\t"}


def tracked() -> list[Path]:
    return checks.tracked()


def rule_files() -> list[Path]:
    """`.claude/rules/` plus any surface's rules/ directory, recursively (the word budget counts the same files)."""
    found = set(checks._rules()) | set((root() / RULES_DIR).rglob("*.md"))
    return sorted(p for p in found if p.is_file())


def expand_braces(glob: str) -> list[str]:
    """`src/*.{ts,tsx}` -> [`src/*.ts`, `src/*.tsx`]; the form Claude Code's `paths:` examples use."""
    m = BRACES.search(glob)
    if not m:
        return [glob]
    return [g for alt in m.group(1).split(",") for g in expand_braces(glob[: m.start()] + alt + glob[m.end() :])]


def globs_of(meta: dict | None) -> list[str]:
    paths = (meta or {}).get("paths") or []
    return [str(g) for g in ([paths] if isinstance(paths, str) else paths)]


def matches(rel: str, glob: str) -> bool:
    return any(PurePosixPath(rel).full_match(g) for g in expand_braces(glob))


def normalize(lesson: str) -> str:
    """What two lessons share when one is a duplicate of the other: case, spacing and closing punctuation ignored."""
    return " ".join(lesson.casefold().split()).rstrip(" .;:!")


def lesson_of(line: str) -> str:
    m = BULLET.match(line)
    return PROVENANCE.sub("", m.group(1) if m else line)


def named_paths(line: str) -> list[str]:
    """Backticked repository paths a line names: `dir/file` forms and top-level files; commands, placeholders, globs
    and URLs are not paths."""
    out = []
    for token in checks.BACKTICK.findall(line):
        token = re.sub(r":\d+$", "", token).rstrip("/")
        if not token or token[0] in "/~-" or "://" in token or NOT_A_PATH & set(token):
            continue
        if checks.REPO_PATH.match(token) or ROOT_FILE.match(token):
            out.append(token)
    return out


def line_problems(line: str) -> list[str]:
    m = PROVENANCE.search(line)
    out = []
    if not m or not DATE.fullmatch(m.group(2)):
        out.append("no provenance: end the line with (#<issue>, YYYY-MM-DD)")
    else:
        try:
            date.fromisoformat(m.group(2))
        except ValueError:
            out.append(f"{m.group(2)} is not a date")
    out += [f"names `{p}`, which does not exist" for p in named_paths(line) if not (root() / p).exists()]
    return out


def check_rules() -> Ran:
    """Every rule line ends with (#issue, YYYY-MM-DD) and names only paths that exist; every `paths:` glob matches a
    file."""
    ran = Ran("rules provenance and paths", allow_empty=True)
    files = None
    for path in rule_files():
        rel = path.relative_to(root())
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            meta, body = frontmatter.split(text)
        except ValueError as exc:
            ran.problems.append(f"{rel}: frontmatter does not parse ({exc}); Claude Code loads it unscoped")
            continue
        for glob in globs_of(meta):
            ran.checked += 1
            files = files if files is not None else [str(p.relative_to(root())) for p in tracked()]
            if not any(matches(f, glob) for f in files):
                ran.problems.append(f"{rel}: paths glob `{glob}` matches no file")
        offset = text.count("\n") - body.count("\n")
        for n, line in enumerate(body.splitlines(), offset + 1):
            if BULLET.match(line):
                ran.checked += 1
                ran.problems += [f"{rel}:{n}: {p}" for p in line_problems(line)]
    return ran


def _specificity(glob: str) -> int:
    return len(re.sub(r"[*?{}\[\]]", "", glob))


def target(rel: str) -> tuple[Path, list[str]]:
    """The rules file a lesson about `rel` belongs in, and the globs to create it with (empty when it exists): the
    scoped file whose matching glob is most literal, else a new one scoped to `rel`'s top-level directory."""
    best: tuple[int, str, Path] | None = None
    for path in rule_files():
        try:
            meta, _ = frontmatter.split(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for glob in globs_of(meta):
            if matches(rel, glob) and (best is None or (_specificity(glob), str(path)) > best[:2]):
                best = (_specificity(glob), str(path), path)
    if best:
        return best[2], []
    top = PurePosixPath(rel).parts[0]
    glob = f"{top}/**" if (root() / top).is_dir() else top
    stem = re.sub(r"[^\w-]+", "-", top).strip("-") or "root"
    path, n = root() / RULES_DIR / f"{stem}.md", 2
    while path.exists():
        path, n = root() / RULES_DIR / f"{stem}-{n}.md", n + 1
    return path, [glob]


def add(rel: str, lesson: str, issue: int, today: date) -> tuple[Path, bool]:
    """Append `- <lesson> (#issue, today)` to the matching rules file; (file, False) when the lesson is already
    there. A line the gate's rules stage would fail raises ValueError instead."""
    lesson = lesson.strip()
    if not lesson:
        raise ValueError("the lesson is empty")
    if "\n" in lesson:
        raise ValueError("a lesson is one line")
    if PROVENANCE.search(lesson):
        raise ValueError("leave the provenance out: it is added from --issue and today's date")
    rel = PurePosixPath(rel.strip()).as_posix().rstrip("/")
    if rel.startswith(("/", "../")) or rel == ".." or not (root() / rel).resolve().is_relative_to(root().resolve()):
        raise ValueError(f"{rel} must be inside the repository")
    if not (root() / rel).exists():
        raise ValueError(f"{rel} does not exist")
    line = f"- {lesson} (#{issue}, {today.isoformat()})"
    if problems := line_problems(line):
        raise ValueError("; ".join(problems))
    path, globs = target(rel)
    if globs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\npaths:\n" + "".join(f'  - "{g}"\n' for g in globs) + "---\n" + line + "\n")
        return path, True
    text = path.read_text(encoding="utf-8")
    if any(normalize(lesson_of(x)) == normalize(lesson) for x in text.splitlines() if BULLET.match(x)):
        return path, False
    path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + line + "\n")
    return path, True


def checkable_issue(rel: str, lesson: str, issue: int) -> str:
    """The issue to file instead of a rule when a test or hook can catch the mistake: prose is advice, a check holds."""
    q = lesson.replace("'", "'\\''")
    return (
        f"uv run dev board new --type build --title 'check: {q}' "
        f"--done 'a hook or test fails when this is broken: {q}' --owns {rel} --refs {issue}"
    )


def issue_from_branch(branch: str) -> int | None:
    m = re.match(r"(\d+)-", branch)
    return int(m.group(1)) if m else None
