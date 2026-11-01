"""Path-scoped lessons in `.claude/rules/` (#78). `uv run dev gotcha` appends one deduped line with provenance to the
rules file whose `paths:` match; the gate's rules stage holds every rule line to provenance and to paths that exist,
and every `paths:` glob to at least one file (a renamed directory otherwise leaves a rule that never loads, silently:
docs/research/decision-records-and-drift.md §3.3-3.4)."""

import os
import re
import shlex
from datetime import date
from pathlib import Path, PurePosixPath

import pathspec

from devkit import checks, config, frontmatter
from devkit.paths import root
from devkit.ran import Ran

RULES_DIR = ".claude/rules"  # where Claude Code loads project rules (code.claude.com/docs/en/memory)
BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")  # a top-level item; an indented sub-bullet is not a rule
FENCE = re.compile(r"^\s*(```|~~~)")
PROVENANCE = re.compile(r"\s*\(#(\d+), (\S+)\)\s*$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
BRACE = re.compile(r"^([^{]*)\{([^}]+)\}(.*)\Z")  # Claude Code's: the first `{` up to the next `}`
BRACE_BUDGET = (1000, 4 * 1024 * 1024)  # patterns, bytes; past either a glob stays unexpanded (memory docs)
# Syntax that is easy to write expecting another glob dialect (extglob, negation, [^x], {1..3}): a glob using it that
# matches nothing warns instead of failing.
EXOTIC = re.compile(r"[!@+?*]\(|^!|\[\^|\[!|\{[^{},]*\.\.[^{},]*\}")
MAX_WORDS = 25
# Directories the disk fallback of glob_hits never enters (VCS, environments, caches; also any dir with pyvenv.cfg):
# walking them makes every gate slow in a large project. A glob anchored inside one still walks from there.
PRUNE = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)


def tracked() -> list[Path]:
    return checks.tracked()


def rule_files() -> list[Path]:
    """`.claude/rules/` plus any surface's rules/ directory, recursively (the word budget counts the same files)."""
    found = set(checks._rules()) | set((root() / RULES_DIR).rglob("*.md"))
    return sorted(p for p in found if p.is_file())


def expand_braces(glob: str, budget: list[int] | None = None) -> list[str]:
    """`src/*.{ts,tsx}` -> [`src/*.ts`, `src/*.tsx`], as Claude Code 2.1.316 expands a `paths:` glob: alternatives
    trimmed, and the glob left whole once `budget` ([patterns, bytes], shared by one `paths:` value) runs out."""
    budget = budget if budget is not None else list(BRACE_BUDGET)
    if "{" not in glob:
        return [glob]
    out, stack = [], [glob]
    while stack:
        g = stack.pop()
        m = BRACE.match(g)
        if not m:
            out.append(g)
            continue
        alts = [a.strip() for a in m.group(2).split(",")]
        budget[1] -= len(g)
        n = len(out) + len(stack) + len(alts)
        if budget[1] < 0 or n > budget[0] or n * len(glob) > budget[1]:
            return [glob]
        stack += [m.group(1) + a + m.group(3) for a in reversed(alts)]
    budget[0] -= len(out)
    budget[1] -= len(out) * len(glob)
    return out


def split_globs(value: object) -> list[str]:
    """A `paths:` value as Claude Code reads it: a list or a string, each string split at commas outside braces;
    anything not a string is dropped."""
    if isinstance(value, list):
        return [g for v in value for g in split_globs(v)]
    if not isinstance(value, str):
        return []
    parts, cur, depth = [], "", 0
    for ch in value:
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        depth += (ch == "{") - (ch == "}")
        cur += ch
    return [p for p in [*parts, cur.strip()] if p]


def patterns(value: object) -> list[str]:
    """The gitignore patterns Claude Code matches a `paths:` value with: split, braces expanded, a trailing `/**`
    cut (`src/**` is `src`, which carries its contents)."""
    budget = list(BRACE_BUDGET)
    globs = [g for raw in split_globs(value) for g in expand_braces(raw, budget)]
    return [g for g in (g.removesuffix("/**") for g in globs) if g]


def invalid(globs: list[str]) -> list[str]:
    """Patterns that do not compile (`foo\\`): node-ignore drops such a pattern, so it matches nothing."""
    out = []
    for g in globs:
        try:
            pathspec.GitIgnoreSpec.from_lines([g.casefold()])
        except ValueError:
            out.append(g)
    return out


def spec(globs: list[str]) -> pathspec.GitIgnoreSpec:
    """node-ignore's gitignore semantics, case-insensitive like its default, via pathspec's GitIgnoreSpec
    (docs/research/devkit-reuse-survey.md §2.2); a pattern that does not compile is dropped, as node-ignore does."""
    bad = set(invalid(globs))
    return pathspec.GitIgnoreSpec.from_lines([g.casefold() for g in globs if g not in bad])


def hit(match: pathspec.GitIgnoreSpec, rel: str) -> bool:
    """node-ignore's verdict: an excluded parent directory excludes the file too (no `!x` re-includes under it), so
    each ancestor is tried as a directory before the file itself."""
    parts = rel.casefold().split("/")
    return any(match.match_file("/".join(parts[:i]) + "/") for i in range(1, len(parts))) or match.match_file(
        "/".join(parts)
    )


def globs_of(meta: dict | None) -> list[str]:
    return split_globs((meta or {}).get("paths"))


def matches(rel: str, glob: str) -> bool:
    return hit(spec(patterns(glob)), rel)


def loads(rel: str, meta: dict | None) -> bool:
    """Claude Code loads the rule when `rel` is read: all of its `paths:` patterns together decide (a later `!x`
    excludes again), and a list of only `**` is unconditional."""
    pats = patterns((meta or {}).get("paths"))
    return all(p == "**" for p in pats) or hit(spec(pats), rel)


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
    pats = patterns(glob)
    match = spec(pats)
    if any(hit(match, f) for f in files):
        return True
    top = root()
    for base in sorted({_walk_base(p) for p in pats if not p.startswith("!")}):
        for here, dirs, names in os.walk(top / base):
            dirs[:] = [d for d in dirs if d not in PRUNE and not (Path(here) / d / "pyvenv.cfg").is_file()]
            rel = Path(here).relative_to(top).as_posix()
            prefix = "" if rel == "." else rel + "/"
            if any(hit(match, prefix + n) for n in names):
                return True
    return False


def _walk_base(pattern: str) -> str:
    """The literal leading directories of an anchored pattern (one with a slash before its end), where a disk walk for
    it can start; "" (the root) for a pattern that matches at any depth."""
    body = pattern.rstrip("/")
    if "/" not in body:
        return ""
    lead = []
    for part in body.lstrip("/").split("/")[:-1]:
        if any(c in part for c in "*?[\\"):
            break
        lead.append(part)
    return "/".join(lead)


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
            bad = invalid(patterns(glob))
            ran.warnings += [f"{rel}: paths pattern `{b}` does not compile; Claude Code drops it" for b in bad]
            if bad and len(bad) == len(patterns(glob)):
                continue
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
        if not loads(rel, meta):
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
