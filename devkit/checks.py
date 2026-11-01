"""Drift checks: places where two declarations must agree and nothing else forces them to.

Each check returns a `Ran` (devkit/ran.py): how much it examined and what it found. The rule behind all of them is
"declare once, derive the rest": the command list is the CLI parser, the agent surface is `.claude/`, budgets are
`.devkit/ratchets.toml`, the research index and surface reference are generated. Paths and options come from
`[tool.devkit]` (devkit/config.py). Prose may *point* at those facts; these checks fail when a pointer dangles.
"""

import argparse
import fnmatch
import json
import re
import shlex
import subprocess
import tomllib
from pathlib import Path

from devkit import config, frontmatter
from devkit.paths import git, root
from devkit.ran import Ran

DESCRIPTION_CAP = 1536  # Claude Code truncates a skill/agent description (+ when_to_use) at this many characters
# The documented frontmatter fields; Claude Code silently ignores any other key, and a value outside the options
# (code.claude.com/docs/en/sub-agents#supported-frontmatter-fields, code.claude.com/docs/en/skills#frontmatter-reference).
AGENT_KEYS = {
    "name",
    "description",
    "tools",
    "disallowedTools",
    "model",
    "permissionMode",
    "maxTurns",
    "skills",
    "mcpServers",
    "hooks",
    "memory",
    "background",
    "omitClaudeMd",
    "effort",
    "isolation",
    "color",
    "initialPrompt",
    "experimental",
}
SKILL_KEYS = {
    "name",
    "description",
    "when_to_use",
    "argument-hint",
    "arguments",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "disallowed-tools",
    "model",
    "effort",
    "context",
    "agent",
    "background",
    "hooks",
    "paths",
    "shell",
    "metadata",
    "license",
    "compatibility",
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
AGENT_VALUES = {
    "effort": EFFORTS,
    "isolation": ("worktree",),
    "memory": ("user", "project", "local"),
    "color": ("red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"),
}
SKILL_VALUES = {"effort": EFFORTS, "context": ("fork",), "shell": ("bash", "powershell")}
# A !`cmd` placeholder at a line start or after whitespace; a failing command aborts the whole skill invocation
# (code.claude.com/docs/en/skills#inject-dynamic-context).
INJECTION = re.compile(r"(?:^|(?<=\s))!`([^`\n]+)`", re.M)
INJECTION_BLOCK = re.compile(r"^```!\s*\n(.*?)^```", re.M | re.S)  # the multi-line form
PIPE_TAIL = re.compile(r"\|\s*(?:head|tail)(?:\s+-n?\s*\d+|\s+-\d+)?\s*$")  # without pipefail, head/tail decide
REPO_PATH = re.compile(r"^\.?[\w-]+/[\w./-]*$")  # a backticked `dir/...`; checked when `dir` is a top-level dir
MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
BACKTICK = re.compile(r"`([^`\n]+)`")
SOURCE_ON_LINE = re.compile(r"\]\(https?://|https?://|\[[^\]]+\]\([^)]+\.md")
DEV_CMD = re.compile(r"uv run dev ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")
# A backticked `uv run dev X ...` span is parsed by the CLI's own parser for unknown options and leftover positionals
# (decision-records-and-drift.md §5 check 1a, §14.1 item 7). Its syntax rules were fitted on the history it was scored
# on, so it is a warning; #236 flips this once a held-out replay shows 0 FP over 30 merges per repository.
DEV_SPAN_GATE = False
DEV_DOCS = (".md", ".sh", ".yml", ".json", ".jinja")
DEV_SPAN = re.compile(r"(?<![\w/.$-])uv run dev ([a-z][a-z-]*)")
SPAN_CUT = re.compile(r"&&|\|\||\s\|\s|;|\s>|2>|<<|\s&|,\s|(?<!\.)\.(?:\s|$)")  # shell operators, sentence ends
NEGATIVE_REF = re.compile(r"\b(?:removed|reintroduc\w*|historical|no longer|formerly)\b", re.I)
TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test|conftest)\.py$")  # they plant bad spans on purpose


def tracked(*patterns: str) -> list[Path]:
    """Tracked plus untracked-not-ignored files: what the next commit would see."""
    out = git("ls-files", "--cached", "--others", "--exclude-standard", "--", *patterns)
    return [root() / p for p in sorted(set(out.splitlines())) if p and (root() / p).is_file()]


SKILL_NAME = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,64}(?<!-)$")  # Agent Skills spec (agentskills.io/specification)
SPEC_DESCRIPTION_CAP = 1024
SPEC_COMPATIBILITY_CAP = 500
SPEC_BODY_LINES = 500


def skill_spec_problems(meta: dict, body: str, dirname: str) -> list[str]:
    """Portable-skill rules of the Agent Skills open standard, stricter than Claude Code's own limits."""
    out = []
    name = str(meta.get("name", ""))
    if not SKILL_NAME.match(name):
        out.append(f"name {name!r} breaks the Agent Skills spec (1-64 of a-z 0-9 -, no leading/trailing/double hyphen)")
    if name != dirname:
        out.append(f"name {name!r} must match its directory {dirname!r}")
    desc = str(meta.get("description") or "")
    if not 1 <= len(desc) <= SPEC_DESCRIPTION_CAP:
        out.append(f"description is {len(desc)} chars; the Agent Skills spec allows 1-{SPEC_DESCRIPTION_CAP}")
    if "compatibility" in meta and not 1 <= len(str(meta["compatibility"])) <= SPEC_COMPATIBILITY_CAP:
        out.append(f"compatibility must be 1-{SPEC_COMPATIBILITY_CAP} chars")
    if (n := body.count("\n") + 1) >= SPEC_BODY_LINES:
        out.append(f"body is {n} lines; keep SKILL.md under {SPEC_BODY_LINES} (move detail to references/)")
    return out


def _agents() -> list[Path]:
    return sorted(p for s in config.get().surface for p in (root() / s / "agents").rglob("*.md"))


def _skills() -> list[Path]:
    return sorted(p for s in config.get().surface for p in (root() / s / "skills").glob("*/SKILL.md"))


def _rules() -> list[Path]:
    """Every `.md` under a surface's rules/ directory: Claude Code discovers them recursively."""
    return sorted(p for s in config.get().surface for p in (root() / s / "rules").rglob("*.md"))


def _commands() -> list[Path]:
    return sorted(p for s in config.get().surface for p in (root() / s / "commands").rglob("*.md"))


def is_plugin(path: Path) -> bool:
    """True for a file inside a Claude Code plugin (a surface root with .claude-plugin/plugin.json)."""
    return any(
        (root() / s / ".claude-plugin/plugin.json").exists() and path.is_relative_to(root() / s)
        for s in config.get().surface
    )


# Plugin agents load without these (code.claude.com/docs/en/plugins/components#frontmatter-fields-in-plugin-agents).
PLUGIN_AGENT_IGNORED = {"permissionMode", "hooks", "mcpServers", "initialPrompt"}


def check_surface() -> Ran:
    """Agents and skills: valid frontmatter, known keys, name matches the file, description within the cap."""
    ran = Ran("agent surface (agents, skills)", allow_empty=config.get().surface_optional)
    for path, keys, expect in [(p, AGENT_KEYS, p.stem) for p in _agents()] + [
        (p, SKILL_KEYS, p.parent.name) for p in _skills()
    ]:
        ran.checked += 1
        where = path.relative_to(root())
        try:
            meta, body = frontmatter.read(path)
        except ValueError as exc:
            ran.problems.append(f"{where}: {exc}")
            continue
        if meta is None:
            ran.problems.append(f"{where}: no frontmatter")
            continue
        if meta.get("name") != expect:
            ran.problems.append(f"{where}: name {meta.get('name')!r} must be {expect!r}")
        desc = str(meta.get("description") or "") + str(meta.get("when_to_use") or "")
        if not desc.strip():
            ran.problems.append(f"{where}: empty description")
        elif len(desc) > DESCRIPTION_CAP:
            ran.problems.append(f"{where}: description is {len(desc)} chars, cap {DESCRIPTION_CAP}")
        if unknown := sorted(set(meta) - keys):
            ran.problems.append(f"{where}: unknown frontmatter keys {unknown} (silently ignored by Claude Code)")
        ran.problems += [f"{where}: {p}" for p in value_problems(meta, skill=path.name == "SKILL.md")]
        if path.name != "SKILL.md" and is_plugin(path) and (ignored := sorted(set(meta) & PLUGIN_AGENT_IGNORED)):
            ran.problems.append(f"{where}: {ignored} are ignored in a plugin agent; use plugin hooks or MCP servers")
        if not body.strip():
            ran.problems.append(f"{where}: empty body")
        if path.name == "SKILL.md":
            ran.problems += [f"{where}: {p}" for p in skill_spec_problems(meta, body, path.parent.name)]
            ran.problems += [f"{where}: {p}" for p in injection_problems(meta, body)]
    return ran


def injection_problems(meta: dict, body: str) -> list[str]:
    """Injected bash commands that can exit non-zero, which aborts the whole skill invocation. A ```! block runs as
    one script, so its last command decides. `shell: powershell` skills are skipped: the docs give `|| true` for bash
    only."""
    if meta.get("shell") == "powershell":
        return []
    cmds = INJECTION.findall(body)
    cmds += [lines[-1] for block in INJECTION_BLOCK.findall(body) if (lines := [s for s in block.splitlines() if s])]
    return [
        f"!`{c}` aborts the skill if it exits non-zero; end it with `|| true` (or `| head`/`| tail`), and allow it "
        "(a permission rule or `allowed-tools`): outside auto mode an unallowed command aborts it too"
        for c in (c.strip() for c in cmds)
        if not (c.endswith("|| true") or PIPE_TAIL.search(c))
    ]


def value_problems(meta: dict, skill: bool) -> list[str]:
    """Values outside a field's documented options, which Claude Code would not apply."""
    out = [
        f"{k} {meta[k]!r} must be one of {', '.join(opts)}"
        for k, opts in (SKILL_VALUES if skill else AGENT_VALUES).items()
        if k in meta and meta[k] not in opts
    ]
    turns = meta.get("maxTurns")
    if not skill and "maxTurns" in meta and (isinstance(turns, bool) or not isinstance(turns, int) or turns < 1):
        out.append(f"maxTurns {turns!r} must be a positive whole number")
    return out


HOOK_SCRIPT = re.compile(r"\$\{?(CLAUDE_PROJECT_DIR|CLAUDE_PLUGIN_ROOT)\}?\"?/([^\s\"]+)")


def _hook_files() -> list[tuple[str, Path]]:
    """(name, base for ${CLAUDE_PLUGIN_ROOT}) of every settings, MCP, plugin manifest and plugin hooks file."""
    out = [(n, root()) for n in (".claude/settings.json", ".mcp.json")]
    for s in config.get().surface:
        out += [(f"{s}/{n}", root() / s) for n in (".claude-plugin/plugin.json", "hooks/hooks.json", ".mcp.json")]
    return [(n, base) for n, base in out if (root() / n).exists()]


def check_settings() -> Ran:
    """Settings, MCP and plugin JSON parse, every hook command points at an executable that exists, and every mod
    module a plugin lists exists."""
    ran = Ran("settings and hooks")
    for name, base in _hook_files():
        ran.checked += 1
        try:
            data = json.loads((root() / name).read_text())
        except json.JSONDecodeError as exc:
            ran.problems.append(f"{name}: invalid JSON: {exc}")
            continue
        for module in data.get("modules", []) if name.endswith("hooks/hooks.json") else []:
            ran.checked += 1
            if not (root() / name).parent.joinpath(module).is_file():
                ran.problems.append(f"{name}: mod module {module} does not exist")
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    cmd = " ".join([hook.get("command", ""), *hook.get("args", [])])
                    for var, rel in HOOK_SCRIPT.findall(cmd):
                        ran.checked += 1
                        target = (root() if var == "CLAUDE_PROJECT_DIR" else base) / rel
                        if not target.is_file():
                            ran.problems.append(f"{name}: {event} hook {rel} does not exist")
                        elif not target.stat().st_mode & 0o111:
                            ran.problems.append(f"{name}: {event} hook {rel} is not executable")
    return ran


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
REF_DEF = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*<?([^\s>]+)>?", re.M)
HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)[ \t#]*$", re.M)
HTML_ANCHOR = re.compile(r"<a\s[^>]*(?:name|id)=[\"']([^\"']+)[\"']", re.I)


def strip_fences(text: str) -> str:
    """Blank out fenced code blocks (CommonMark: a line that starts with ``` or ~~~, closed by a line of at least as
    many of the same character). A ``` mentioned mid-line opens nothing. Line numbers are kept."""
    out, fence = [], None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                line = ""
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
            line = ""
        out.append(line)
    return "\n".join(out)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: rendered text, lowercased, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).replace("`", "")
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every #anchor a markdown file (already fence-stripped) defines; repeated headings get -1, -2, ... as on GitHub."""
    found = {a.lower() for a in HTML_ANCHOR.findall(text)}
    seen: dict[str, int] = {}
    for heading in HEADING.findall(text):
        base = slug(heading)
        n = seen.get(base, 0)
        seen[base] = n + 1
        found.add(base if n == 0 else f"{base}-{n}")
    return found


def check_links() -> Ran:
    """Relative markdown links (inline and reference-style), their #anchors, and backticked repository paths in prose
    must resolve."""
    ran = Ran("doc links and paths")
    cache: dict[Path, set[str]] = {}

    def anchors_of(p: Path) -> set[str]:
        if p not in cache:
            cache[p] = anchors(strip_fences(p.read_text(encoding="utf-8", errors="replace")))
        return cache[p]

    cfg = config.get()
    files = tracked("*.md")
    top = {p.relative_to(root()).parts[0] for p in tracked() if len(p.relative_to(root()).parts) > 1}
    for path in files:
        rel = path.relative_to(root())
        if rel.is_relative_to(cfg.generated_dir):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = strip_fences(text)  # code blocks show commands and examples, not claims
        for target in MD_LINK.findall(text) + REF_DEF.findall(text):
            if re.match(r"[a-z]+:", target):
                continue
            ran.checked += 1
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / file_part).resolve() if file_part else path.resolve()
            if not dest.exists():
                ran.problems.append(f"{rel}: link to missing {target}")
            elif anchor and dest.suffix == ".md" and dest.is_file() and anchor.lower() not in anchors_of(dest):
                ran.problems.append(f"{rel}: link to missing heading #{anchor} in {dest.name}")
        if rel.is_relative_to(cfg.research_dir) or str(rel).startswith(tuple(cfg.path_check_skip)):
            continue  # they discuss other repositories' paths; their markdown links are still checked above
        for token in BACKTICK.findall(text):
            if REPO_PATH.match(token) and token.split("/")[0] in top and not any(c in token for c in "<>*{}"):
                ran.checked += 1
                if not (root() / token).exists():
                    ran.problems.append(f"{rel}: path `{token}` does not exist")
    return ran


class _SpanError(Exception):
    pass


def _span_error(message: str = "", *_: object) -> None:
    raise _SpanError(message)


def loosen(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Make a parser read prose: no required options or subcommands, optional positionals, no type conversion (a
    placeholder `N` is no int), and errors raised as `_SpanError` instead of printing and exiting. In place."""
    parser.error = _span_error  # type: ignore[method-assign]
    parser.exit = _span_error  # type: ignore[method-assign]
    for a in parser._actions:
        a.required = False
        if isinstance(a, argparse._SubParsersAction):
            for sub in set(a.choices.values()):
                loosen(sub)
            continue
        a.type = None
        if not a.option_strings:
            a.nargs = {None: "?", "+": "*"}.get(a.nargs, a.nargs)  # type: ignore[arg-type]
    return parser


def span_tokens(rest: str) -> list[str] | None:
    """The arguments a span gives after `uv run dev X`, or None when it is no complete command. The syntax rules:
    cut at shell operators; `...`, `a|b` and globs are elisions or alternatives; `<x>` and `N` are placeholders."""
    if cut := SPAN_CUT.search(rest):
        rest = rest[: cut.start()]
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return None
    out = []
    for t in tokens:
        if t in ("...", "…") or t.endswith(("..", "…")) or any(ch in t for ch in "|{}()*"):
            return None
        optional = t.startswith("[") and t.endswith("]")
        t = t.strip("[]")
        if re.fullmatch(r"<[^>]*>", t):
            if optional:
                continue
            t = "1"
        if t not in ("-h", "--help"):
            out.append(re.sub(r"<[^>]*>", "x", t))
    return out


def span_problem(parser: argparse.ArgumentParser, commands: dict[str, set[str]], cmd: str, rest: str) -> str | None:
    """What is wrong with one `uv run dev <cmd> <rest>` span, if anything, against a `loosen`ed parser."""
    tokens = span_tokens(rest)
    if tokens is None:
        return None
    if cmd not in commands:
        return f"`uv run dev {cmd}` is not a command"
    if tokens and commands[cmd] and tokens[0] not in commands[cmd] and not tokens[0].startswith("-"):
        return f"`{tokens[0]}` is not a subcommand of {cmd}"
    try:
        _, extra = parser.parse_known_args([cmd, *tokens])
    except _SpanError as e:
        message = str(e)
        return None if not message or "expected one argument" in message else message
    if unknown := [e for e in extra if e.startswith("-") and e != "-"]:
        return f"unknown option {' '.join(unknown)}"
    return f"{' '.join(extra)} left over: no argument takes it" if extra else None


def check_dev_commands(commands: dict[str, set[str]], parser: argparse.ArgumentParser | None = None) -> Ran:
    """Every `uv run dev X [Y]` written anywhere in prose, agents or skills names a real command (a failure). Each
    backticked span is also parsed for unknown options and leftover positionals (a warning until `DEV_SPAN_GATE`),
    and so are spans in `.py` strings and comments outside test files (a warning only). Fenced blocks, research notes
    and lines that mark a span as a historical or negative reference ("removed", "do not reintroduce") are exempt
    from the parse. `parser` is a fresh `devkit.cli.build()` by default; it is loosened in place."""
    if parser is None:
        from devkit import cli  # cli imports this module

        parser = cli.build()
    loosen(parser)
    ran = Ran("dev commands named in docs")
    cfg = config.get()
    for path in tracked(*(f"*{ext}" for ext in (*DEV_DOCS, ".py"))):
        rel = path.relative_to(root())
        doc = path.suffix in DEV_DOCS
        if rel.is_relative_to(cfg.generated_dir) or not (doc or path.suffix == ".py"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in DEV_CMD.finditer(text) if doc else ():
            ran.checked += 1
            cmd, sub = m.group(1), m.group(2)
            if cmd not in commands:
                ran.problems.append(f"{rel}: `uv run dev {cmd}` is not a command (see `uv run dev --help`)")
            elif sub and commands[cmd] and sub not in commands[cmd] and not sub.startswith("-"):
                ran.problems.append(f"{rel}: `uv run dev {cmd} {sub}` is not a subcommand of {cmd}")
        if rel.is_relative_to(cfg.research_dir) or (not doc and TEST_FILE.search(rel.as_posix())):
            continue
        for n, line in enumerate(strip_fences(text).splitlines(), 1):
            if NEGATIVE_REF.search(line):
                continue
            for span in BACKTICK.findall(line):
                for m in DEV_SPAN.finditer(span):
                    ran.checked += not doc
                    problem = span_problem(parser, commands, m.group(1), span[m.end() :])
                    if problem is None or (doc and "is not a" in problem):
                        continue  # in docs the name check above already failed it
                    out = ran.problems if doc and DEV_SPAN_GATE else ran.warnings
                    out.append(f"{rel}:{n}: `{span}`: {problem} (see `uv run dev {m.group(1)} --help`)")
    return ran


def check_research_notes() -> Ran:
    """House style of docs/research notes: title, Status, Labels, and at least one source link."""
    ran = Ran("research notes")
    ran.allow_empty = True
    for path in sorted((root() / config.get().research_dir).glob("*.md")):
        if path.name in {"INDEX.md", "README.md"}:
            continue
        ran.checked += 1
        rel = path.relative_to(root())
        text = path.read_text(encoding="utf-8")
        head = text[:1500]
        if not re.match(r"# Research: \S", text):
            ran.problems.append(f"{rel}: first line must be '# Research: <topic>'")
        if not re.search(r"^Status: \S", head, re.M):
            ran.problems.append(f"{rel}: no 'Status:' line near the top")
        if not re.search(r"^Labels: \S", head, re.M):
            ran.problems.append(f"{rel}: no 'Labels:' line near the top")
        if not re.search(r"^Search coverage: \S", head, re.M):
            ran.problems.append(
                f"{rel}: no 'Search coverage:' line near the top (the worst `discovery` line `uv run dev lit search` printed)"
            )
        if "](http" not in text:
            ran.problems.append(f"{rel}: cites no sources (no http links)")
        for n, line in enumerate(text.splitlines(), 1):
            if (
                "SOURCED" in line
                and not line.startswith(("Labels:", "Status:", "#"))
                and not SOURCE_ON_LINE.search(line)
            ):
                ran.problems.append(f"{rel}:{n}: a SOURCED claim with no link on its line (cite the URL you fetched)")
    return ran


IMPORT = re.compile(r"(?:^|(?<=\s))@([^\s`]+)")
IMPORT_HOPS = 5  # Claude Code follows @imports up to five hops deep (code.claude.com/docs/en/memory)


def imported_words(path: Path) -> int:
    """Words of the files `path` @-imports, recursively, each counted once. Imports resolve relative to the importing
    file; one inside a code span or block is not an import, and `~/` (machine-local) or missing targets are skipped."""
    seen, total, frontier = {path.resolve()}, 0, [path]
    for _ in range(IMPORT_HOPS):
        nxt = []
        for p in frontier:
            text = BACKTICK.sub("", strip_fences(p.read_text(errors="replace")))
            for target in IMPORT.findall(text):
                target = target.rstrip(".,;:!?)")
                q = (p.parent / target).resolve()
                if target.startswith("~") or q in seen or not q.is_file():
                    continue
                seen.add(q)
                total += frontmatter.words(q.read_text(errors="replace"))
                nxt.append(q)
        frontier = nxt
    return total


def supporting_words(skill: Path) -> int | None:
    """Words of every text file in a skill's directory but SKILL.md (references, scripts, assets), None if none."""
    files = [p for p in skill.parent.rglob("*") if p.is_file() and p != skill and "__pycache__" not in p.parts]
    total = None
    for p in files:
        try:
            total = (total or 0) + frontmatter.words(p.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    return total


def _split_lenient(p: Path) -> tuple[dict | None, str]:
    """(frontmatter, body), or (None, whole text) when the frontmatter does not parse: Claude Code then ignores it
    and loads the file as written."""
    text = p.read_text(errors="replace")
    try:
        return frontmatter.split(text)
    except ValueError:
        return None, text


def measured_words() -> dict[str, int]:
    """Words each budgeted text costs when loaded: the body plus an agent's initialPrompt, CLAUDE.md/AGENTS.md plus
    what they @-import, and a skill's supporting files under `<skill dir>/*` (budgeted like the skill's body).
    Rules without `paths:` load at launch, so they count toward CLAUDE.md (the session context); a path-scoped rule
    and a command each get their own budget (code.claude.com/docs/en/memory)."""
    out: dict[str, int] = {}
    for p in [root() / "CLAUDE.md", root() / "AGENTS.md"]:
        if p.exists():
            out[p.name] = frontmatter.words(p.read_text()) + imported_words(p)
    for p in _rules():
        meta, body = _split_lenient(p)
        if (meta or {}).get("paths"):
            out[str(p.relative_to(root()))] = frontmatter.words(body)  # loads with a matching file: own budget
        else:
            out["CLAUDE.md"] = out.get("CLAUDE.md", 0) + frontmatter.words(body)  # loads at launch, every session
    for p in _commands():
        out[str(p.relative_to(root()))] = frontmatter.words(_split_lenient(p)[1])
    for p in [*_agents(), *_skills()]:
        meta, body = frontmatter.split(p.read_text())
        rel = str(p.relative_to(root()))
        out[rel] = frontmatter.words(body) + frontmatter.words(str((meta or {}).get("initialPrompt") or ""))
        if p.name == "SKILL.md" and (n := supporting_words(p)) is not None:
            out[str(p.parent.relative_to(root())) + "/*"] = n
    return out


def _load_ratchets(text: str) -> dict:
    data = tomllib.loads(text)
    return {
        "words": data.get("words", {}),
        "defaults": data.get("defaults", {}),
        "raise": data.get("raise", []),
        "tests": data.get("tests", {}),
    }


FLOORS = {"min_tests": (int,), "min_coverage": (int, float)}  # [tests] keys: floors that only rise


def test_floors(text: str) -> dict:
    """The [tests] floors of a ratchets.toml: min_tests (passed tests) and min_coverage (line %). ValueError if bad."""
    floors = tomllib.loads(text).get("tests", {})
    for key, value in floors.items():
        if key not in FLOORS:
            raise ValueError(f"[tests] {key}: unknown key; known: {sorted(FLOORS)}")
        if (
            isinstance(value, bool)
            or not isinstance(value, FLOORS[key])
            or not 0 <= value <= (100 if "cov" in key else 1e9)
        ):
            raise ValueError(f"[tests] {key} = {value!r}: expected a number from 0 (min_coverage: up to 100)")
    return floors


GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def repo_owner() -> str | None:
    """GitHub login that owns the origin repository: the only valid approver of a [[raise]]."""
    url = git("remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+)/", url)
    return m.group(1) if m and GITHUB_LOGIN.match(m.group(1)) else None


def _kind(rel: str) -> str:
    """Budget kind of a measured file: agent, skill (a SKILL.md, a skill's `<dir>/*`, or a command, which loads on
    invocation like a skill), or context (CLAUDE.md, AGENTS.md, a path-scoped rule)."""
    parts = Path(rel).parts
    surface = {Path(s).parts for s in config.get().surface}
    for i in range(len(parts)):
        if parts[:i] in surface and parts[i : i + 1] in (("agents",), ("skills",), ("commands",)):
            return "agent" if parts[i] == "agents" else "skill"
    return "context"


def effective_budget(r: dict, rel: str) -> tuple[int | None, str]:
    """(budget, the ratchets.toml key that sets it): a per-file [words] entry wins over its kind's default."""
    if rel in r["words"]:
        return r["words"][rel], f"words.{rel}"
    kind = _kind(rel)
    return r["defaults"].get(kind), f"defaults.{kind}"


def ratchet_raises(cur: dict, old: dict, files: list[str], owner: str | None) -> list[str]:
    """Each budget above main's (every file's effective budget, every default) needs a [[raise]] new since main for
    the key that sets it, approved_by the repository owner's GitHub login, with a `to` that covers the new value.
    A [tests] floor is the mirror image: one below main's (or removed) needs a [[raise]] whose `to` is at most it."""
    problems: list[str] = []
    known = {(r.get("key"), r.get("to")) for r in old["raise"]}
    allowed: dict[str, int] = {}
    floor_to: dict[str, int | float] = {}  # a [[raise]] on a [tests] floor loosens it: `to` is the lowest allowed
    for r in cur["raise"]:
        key, to, who = str(r.get("key")), r.get("to"), str(r.get("approved_by", ""))
        if (r.get("key"), to) in known:
            continue
        if not owner or not GITHUB_LOGIN.match(who) or who.lower() != owner.lower():
            problems.append(
                f"ratchets.toml: [[raise]] {key}: approved_by {who!r} is not the repository owner {owner!r}"
            )
        elif isinstance(to, bool) or not isinstance(to, int | float if key.startswith("tests.") else int):
            problems.append(f"ratchets.toml: [[raise]] {key}: `to` must be an integer")
        elif key.startswith("tests."):
            floor_to[key] = min(to, floor_to.get(key, to))
        else:
            allowed[key] = max(to, allowed.get(key, to))
    for name, before in old.get("tests", {}).items():
        value, key = cur.get("tests", {}).get(name), f"tests.{name}"
        if value is not None and value >= before:
            continue
        if key not in floor_to:
            problems.append(f"ratchets.toml: {key} lowered {before} -> {value} without an owner-approved [[raise]]")
        elif value is None or value < floor_to[key]:
            problems.append(f"ratchets.toml: {key} lowered to {value}, below its [[raise]] to = {floor_to[key]}")
    # A key absent on main is a raise from nothing; a new [words] entry at or below main's budget for that file is
    # a tightening and needs no approval.
    raised: dict[str, tuple[int | None, int, str]] = {}
    for kind, value in cur["defaults"].items():
        before = old["defaults"].get(kind)
        if before is None or value > before:
            raised[f"defaults.{kind}"] = (before, value, f"defaults.{kind}")
    for rel in sorted(set(files) | set(cur["words"])):
        value, key = effective_budget(cur, rel)
        before, _ = effective_budget(old, rel)
        if value is not None and (before is None or value > before) and key not in raised:
            raised[key] = (before, value, f"{rel} (via {key})")
    for key, (before, value, what) in raised.items():
        to = allowed.get(key)
        if to is None:
            problems.append(f"ratchets.toml: {what} raised {before} -> {value} without an owner-approved [[raise]]")
        elif value > to:
            problems.append(f"ratchets.toml: {what} raised to {value}, above its [[raise]] to = {to}")
    return problems


def check_ratchets(base: str | None, owner: str | None = None) -> Ran:
    """Word budgets of the always-loaded and agent files may only go down, and [tests] floors only up. Loosening
    either needs an owner-signed [[raise]]."""
    ran = Ran("ratchets (budgets only tighten)")
    ratchets = config.get().ratchets
    text = (root() / ratchets).read_text()
    cur = _load_ratchets(text)
    try:
        test_floors(text)
    except ValueError as e:
        ran.problems.append(f"{ratchets}: {e}")
    measured = measured_words()
    for rel, words in measured.items():
        ran.checked += 1
        budget, _ = effective_budget(cur, rel)
        if budget is None:
            ran.problems.append(f"{rel}: no word budget (add it to {ratchets})")
        elif words > budget:
            ran.problems.append(f"{rel}: {words} words > budget {budget}. Cut text, or ask the owner for a [[raise]]")
    if not base:
        ran.warnings.append("no merge base with main: budgets not compared with main's")
        return ran
    try:
        old = _load_ratchets(git("show", f"{base}:{ratchets}"))
    except (RuntimeError, tomllib.TOMLDecodeError):
        ran.warnings.append(f"no readable {ratchets} at {base[:10]}: budgets not compared with main's")
        return ran
    ran.problems += ratchet_raises(cur, old, list(measured), owner or repo_owner())
    return ran


def check_generated(render: dict[str, str]) -> Ran:
    """Generated references must equal what `uv run dev docs` would write now."""
    ran = Ran("generated docs fresh")
    for rel, content in render.items():
        ran.checked += 1
        path = root() / rel
        if not path.exists() or path.read_text() != content:
            ran.problems.append(f"{rel} is stale: run `uv run dev docs` and commit it")
    return ran


# A package install a worker could wrap in a script or subprocess to get past a denied `Bash(pip install*)` or a
# guard (#392): pip / uv pip / uv tool, conda and its kin, npm/pnpm/yarn, system package managers, and a piped
# installer. Matched after quotes, commas and brackets become spaces, so `["pip", "install"]` reads as `pip install`.
INSTALL = re.compile(
    r"\b(?:pip(?:3(?:\.\d+)?)?|pipx|conda|mamba|micromamba|apt|apt-get|dnf|yum|zypper|brew|gem|cargo)"
    r"(?:\s++-[\w=.-]*+)*+\s+install\b"  # possessive, and one way to split options: no backtracking blowup
    r"|\buv\s+tool\s+install\b|\buv\s+pip\s+sync\b|\b(?:poetry|pdm|bun)\s+add\b|\bpacman\b[^|;&]*\s-S\w*"
    r"|\b(?:npm|pnpm)\s+(?:install|i|add)\b|\byarn\s+(?:global\s+)?add\b"
    r"|\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:bash|dash|zsh|sh)\b"
)
INSTALL_PUNCT = re.compile(r"[\"'`,()\[\]{}]")
PROSE_SUFFIXES = (".md", ".rst", ".txt", ".md.jinja", ".rst.jinja")
# A permission rule string in a Claude Code settings file, such as the template's deny rule for installs: it
# forbids the command rather than running it.
SETTINGS_FILE = re.compile(r"(?:^|/)\.claude/settings[\w.-]*\.json(?:\.jinja)?$")
PERMISSION_RULE = re.compile(r"^\s*\"\w+\(.*\)\"\s*,?\s*$")


def _uncommented(text: str) -> str:
    """The line up to a `#` comment: one at the start or after whitespace, outside quotes (`echo "#1"` is code)."""
    quote = ""
    for i, c in enumerate(text):
        if quote:
            quote = "" if c == quote and text[i - 1] != "\\" else quote
        elif c in "\"'`":
            quote = c
        elif c == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i]
    return text


def install_allowlist(text: str) -> list[str]:
    """Path globs of an install allowlist: one per line, `#` starts a comment (the reason)."""
    return [g for line in text.splitlines() if (g := line.split("#", 1)[0].strip())]


def install_findings(diff: str, allowed: list[str]) -> list[str]:
    """file:line of each added line of code or script in a `git diff -U0` that runs a package install, outside the
    allowlisted path globs. Prose files are skipped; a line-level heuristic, so a command split over lines escapes."""
    from devkit.weaken import parse  # weaken imports this module

    out = []
    for f in parse(diff):
        if not f.new or f.new.endswith(PROSE_SUFFIXES) or any(fnmatch.fnmatch(f.new, g) for g in allowed):
            continue
        rules = bool(SETTINGS_FILE.search(f.new))
        for ln in f.added:
            if rules and PERMISSION_RULE.match(ln.text):
                continue
            if INSTALL.search(INSTALL_PUNCT.sub(" ", _uncommented(ln.text))):
                out.append(f"{ln.path}:{ln.number}: package install: {ln.text.strip()}")
    return out


def check_installs(base: str | None) -> Ran:
    """No added code or script runs a package install: installs go to the owner, and wrapping a denied command in a
    script or subprocess is the shortcut this catches. A reviewed case is a path glob in the install allowlist."""
    ran = Ran("package installs in code and scripts", allow_empty=True)
    if not base:
        ran.warnings.append("no merge base with main: added install commands not checked")
        return ran
    listed = config.get().install_allowlist
    try:
        allowed = install_allowlist((root() / listed).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        allowed = []
    diff = git("diff", "-M", "-U0", "--no-color", "--no-ext-diff", base, cwd=root())
    files = [line for line in diff.splitlines() if line.startswith("diff --git ")]
    ran.checked = len(files)
    if any(line.endswith(f" b/{listed}") for line in files):
        ran.warnings.append(f"{listed} changed on this branch: the reviewer checks each new entry")
    if found := install_findings(diff, allowed):
        ran.problems += found
        ran.problems.append(
            "installs go to the owner: never wrap a blocked command in a script or subprocess. Ask the lead, or, once"
            f" the owner reviewed it, list the file in {listed} as `<path glob>  # reason`"
        )
    return ran


DECISION_KEYS = {"id", "rule", "why", "enforced-by", "status", "by"}
DECISION_STATUS = ("active", "superseded")
LITERAL_WORDS = 5  # decision-records-and-drift.md §3.5: a short marker on the reason, never on the rule wording


def _mark_problems(name: str, enforced: object) -> list[str]:
    ok = isinstance(enforced, list) and all(
        isinstance(m, dict) and set(m) == {"path", "literal"} and all(isinstance(v, str) for v in m.values())
        for m in enforced
    )
    if not ok or not enforced:
        return [f"{name}: `enforced-by` must be a non-empty list of {{path, literal}} strings"]
    problems = []
    for m in enforced:
        words = len(m["literal"].split())
        if not m["path"].strip() or not words:
            problems.append(f"{name}: an enforced-by entry has an empty path or literal")
        elif words > LITERAL_WORDS:
            problems.append(f"{name}: literal {m['literal']!r} has {words} words, over {LITERAL_WORDS} words")
    return problems


def decision_rows(text: str) -> tuple[list[str], list[dict]]:
    """(problems, rows) of a decision table. A table without a `decision` key is unconfigured: no rows, no problem.
    A `decision` key holding zero rows is a problem, so an emptied table cannot pass as a checked one."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return [f"does not parse: {e}"], []
    if "decision" not in data:
        return [], []
    rows = data["decision"]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return ["`decision` must be an array of tables ([[decision]])"], []
    if not rows:
        return ["the [[decision]] array has zero rows: add a row or delete the key"], []
    ids = [r.get("id") for r in rows]
    problems: list[str] = []
    for r in rows:
        name = r["id"] if isinstance(r.get("id"), str) and r["id"].strip() else "<no id>"
        if extra := sorted(set(r) - DECISION_KEYS):
            problems.append(f"{name}: unknown key(s) {', '.join(extra)}")
        problems += [
            f"{name}: `{key}` must be a non-empty string"
            for key in ("id", "rule", "why")
            if not isinstance(r.get(key), str) or not r[key].strip()
        ]
        if r.get("id") is not None and ids.count(r["id"]) > 1:
            problems.append(f"{name}: duplicate id")
        status, by = r.get("status"), r.get("by")
        if status not in DECISION_STATUS:
            problems.append(f"{name}: `status` must be one of {' | '.join(DECISION_STATUS)}")
        elif status == "superseded" and (by == r.get("id") or by not in ids):
            problems.append(f"{name}: a superseded row needs `by` naming another row's id (got {by!r})")
        elif status == "active" and by is not None:
            problems.append(f"{name}: `by` belongs on a superseded row only")
        problems += _mark_problems(name, r.get("enforced-by"))
    return problems, rows


def check_decisions() -> Ran:
    """Each active decision's `enforced-by` literal still stands in its path, compared with whitespace collapsed so a
    rewrap is no drift. A missing one is a warning naming the row: its precision was measured in-sample, so it stays a
    warning until a held-out replay (decision-records-and-drift.md §5, check 7)."""
    ran = Ran("decision table")
    rel = config.get().decisions
    try:
        text = (root() / rel).read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as e:
        ran.problems.append(f"{rel}: unreadable: {e}")
        return ran
    problems, rows = decision_rows(text)
    ran.problems += [f"{rel}: {p}" for p in problems]
    ran.checked = len(rows)
    if not rows and not problems:
        ran.allow_empty = True
        ran.notes.append(f"{rel}: not configured (no [[decision]] rows)")
    if problems:
        return ran
    flat: dict[str, str | None] = {}
    for r in rows:
        for m in r["enforced-by"] if r["status"] == "active" else []:
            if m["path"] not in flat:
                try:
                    flat[m["path"]] = " ".join((root() / m["path"]).read_text(encoding="utf-8").split())
                except (OSError, UnicodeDecodeError):
                    flat[m["path"]] = None
            if flat[m["path"]] is None:
                ran.warnings.append(f"{r['id']}: {m['path']} is missing, and with it the literal {m['literal']!r}")
            elif " ".join(m["literal"].split()) not in flat[m["path"]]:
                ran.warnings.append(
                    f"{r['id']}: literal {m['literal']!r} is missing from {m['path']} (rule: {r['rule']}). Restore the"
                    f" reason, or mark the row superseded in {rel} in the same PR"
                )
    return ran


def merge_base() -> str | None:
    for ref in ("origin/main", "main"):
        out = subprocess.run(
            ["git", "merge-base", "HEAD", ref], cwd=root(), capture_output=True, text=True, check=False
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return None
