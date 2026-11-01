"""Path-scoped lessons in `.claude/rules/` (#78). `uv run dev gotcha` appends one deduped line with provenance to the
rules file whose `paths:` match; the gate's rules stage holds every rule line to provenance and to paths that exist,
and every `paths:` glob to at least one file (a renamed directory otherwise leaves a rule that never loads, silently:
docs/research/decision-records-and-drift.md §3.3-3.4)."""

import re
import shlex
from datetime import date
from pathlib import Path, PurePosixPath

from devkit import checks, config, frontmatter
from devkit.paths import root
from devkit.ran import Ran

RULES_DIR = ".claude/rules"  # where Claude Code loads project rules (code.claude.com/docs/en/memory)
BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")  # a top-level item; an indented sub-bullet is not a rule
FENCE = re.compile(r"^\s*(```|~~~)")
PROVENANCE = re.compile(r"\s*\(#(\d+), (\S+)\)\s*$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
# Glob syntax beyond stdlib full_match plus comma braces (extglob, negation, [^x], {1..3}): a glob using it that
# matches nothing may be our matcher's blind spot, so it warns instead of failing.
EXOTIC = re.compile(r"[!@+?*]\(|^!|\[\^|\[!|\{[^{},]*\.\.[^{},]*\}")
MAX_WORDS = 25


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


def top_dirs() -> set[str]:
    return {p.relative_to(root()).parts[0] for p in tracked() if len(p.relative_to(root()).parts) > 1}


def named_paths(line: str, top: set[str]) -> list[str]:
    """Backticked repository paths a line names, by check_links' rule: a `dir/...` token whose first segment is a
    top-level directory, without placeholders or globs (`origin/main`, `text/html` and `conftest.py` are not)."""
    return [
        t
        for t in checks.BACKTICK.findall(line)
        if checks.REPO_PATH.match(t) and t.split("/")[0] in top and not any(c in t for c in "<>*{}")
    ]


def path_problems(line: str, top: set[str]) -> list[str]:
    return [f"names `{p}`, which does not exist" for p in named_paths(line, top) if not (root() / p).exists()]


def provenance_problems(line: str) -> list[str]:
    m = PROVENANCE.search(line)
    out = []
    if not m or not DATE.fullmatch(m.group(2)):
        out.append("no provenance: end the line with (#<issue>, YYYY-MM-DD)")
    else:
        try:
            date.fromisoformat(m.group(2))
        except ValueError:
            out.append(f"{m.group(2)} is not a date")
    return out


def rule_lines(body: str, first: int) -> list[tuple[int, str]]:
    """(line number, text) of each top-level bullet outside fenced code blocks."""
    out, fenced = [], False
    for n, line in enumerate(body.splitlines(), first):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and BULLET.match(line):
            out.append((n, line))
    return out


def added_lines(base: str) -> dict[str, set[int]]:
    """Line numbers each rules file adds against `base` (untracked files: every line), so rules a project wrote by
    hand before this check never fail on update."""
    rels = [str(p.relative_to(root())) for p in rule_files()]
    if not rels:
        return {}
    out: dict[str, set[int]] = {}
    current = None
    for line in checks.git("diff", "-U0", "--no-color", "--no-ext-diff", base, "--", *rels, cwd=root()).splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif current and (m := re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)):
            start, count = int(m.group(1)), int(m.group(2) or 1)
            out.setdefault(current, set()).update(range(start, start + count))
    for rel in checks.git("ls-files", "--others", "--exclude-standard", "--", *rels, cwd=root()).splitlines():
        out[rel] = set(range(1, (root() / rel).read_text(errors="replace").count("\n") + 2))
    return out


def glob_hits(glob: str, files: list[str]) -> bool:
    """A tracked file matches, else any file on disk does (gitignored output such as `build/**` still loads rules)."""
    if any(matches(f, glob) for f in files):
        return True
    for g in expand_braces(glob):
        try:
            if next(iter(root().glob(g)), None) is not None:
                return True
        except (ValueError, NotImplementedError):
            continue
    return False


def check_rules(base: str | None) -> Ran:
    """Rule lines added against `base` end with (#issue, YYYY-MM-DD); every rule line names only paths that exist;
    every `paths:` glob matches a file."""
    ran = Ran("rules provenance and paths", allow_empty=True)
    files: list[str] | None = None
    top: set[str] = set()
    added = None
    skip = tuple(config.get().path_check_skip)
    for path in rule_files():
        rel = path.relative_to(root())
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            meta, body = frontmatter.split(text)
        except ValueError as exc:
            ran.problems.append(f"{rel}: frontmatter does not parse ({exc}); Claude Code loads it unscoped")
            continue
        if files is None:
            files = [str(p.relative_to(root())) for p in tracked()]
            top = top_dirs()
            added = added_lines(base) if base else None
            if added is None:
                ran.warnings.append("no merge base with main: provenance of new rule lines not checked")
        for glob in globs_of(meta):
            ran.checked += 1
            if not glob_hits(glob, files):
                where = ran.warnings if EXOTIC.search(glob) else ran.problems
                where.append(f"{rel}: paths glob `{glob}` matches no file")
        new = (added or {}).get(str(rel), set())
        for n, line in rule_lines(body, text.count("\n") - body.count("\n") + 1):
            ran.checked += 1
            if n in new:
                ran.problems += [f"{rel}:{n}: {p}" for p in provenance_problems(line)]
            if not str(rel).startswith(skip):
                ran.problems += [f"{rel}:{n}: {p}" for p in path_problems(line, top)]
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
    if len(lesson.split()) > MAX_WORDS:
        raise ValueError(f"a lesson is at most {MAX_WORDS} words; this one has {len(lesson.split())}")
    if PROVENANCE.search(lesson):
        raise ValueError("leave the provenance out: it is added from --issue and today's date")
    rel = PurePosixPath(rel.strip()).as_posix().rstrip("/")
    if rel.startswith(("/", "../")) or rel == ".." or not (root() / rel).resolve().is_relative_to(root().resolve()):
        raise ValueError(f"{rel} must be inside the repository")
    if not (root() / rel).exists():
        raise ValueError(f"{rel} does not exist")
    line = f"- {lesson} (#{issue}, {today.isoformat()})"
    if problems := provenance_problems(line) + path_problems(line, top_dirs()):
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
    return shlex.join(
        [
            *["uv", "run", "dev", "board", "new", "--type", "build", "--title"],
            f"check: {lesson}",
            "--done",
            f"a hook or test fails when this is broken: {lesson}",
            "--owns",
            rel,
            "--refs",
            str(issue),
        ]
    )


def issue_from_branch(branch: str) -> int | None:
    m = re.match(r"(\d+)-", branch)
    return int(m.group(1)) if m else None
