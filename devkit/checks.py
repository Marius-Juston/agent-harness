"""Drift checks: places where two declarations must agree and nothing else forces them to.

Each check returns a `Ran` (devkit/ran.py): how much it examined and what it found. The rule behind all of them is
"declare once, derive the rest": the command list is the CLI parser, the agent surface is `.claude/`, budgets are
`.devkit/ratchets.toml`, the research index and surface reference are generated. Paths and options come from
`[tool.devkit]` (devkit/config.py). Prose may *point* at those facts; these checks fail when a pointer dangles.
"""

import json
import re
import subprocess
import tomllib
from pathlib import Path

from devkit import config, frontmatter
from devkit.paths import git, root
from devkit.ran import Ran

DESCRIPTION_CAP = 1536  # Claude Code truncates a skill/agent description (+ when_to_use) at this many characters
AGENT_KEYS = {
    "name",
    "description",
    "tools",
    "disallowedTools",
    "model",
    "effort",
    "maxTurns",
    "skills",
    "memory",
    "hooks",
    "permissionMode",
    "color",
    "omitClaudeMd",
    "isolation",
    "background",
    "experimental",
    "mcpServers",
    "initialPrompt",
}
SKILL_KEYS = {
    "compatibility",
    "name",
    "description",
    "when_to_use",
    "allowed-tools",
    "paths",
    "hooks",
    "disable-model-invocation",
    "user-invocable",
    "model",
    "effort",
    "context",
    "agent",
    "argument-hint",
    "arguments",
    "license",
    "metadata",
    "version",
}
REPO_PATH = re.compile(r"^\.?[\w-]+/[\w./-]*$")  # a backticked `dir/...`; checked when `dir` is a top-level dir
MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
BACKTICK = re.compile(r"`([^`\n]+)`")
SOURCE_ON_LINE = re.compile(r"\]\(https?://|https?://|\[[^\]]+\]\([^)]+\.md")
DEV_CMD = re.compile(r"uv run dev ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


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
        if path.name != "SKILL.md" and is_plugin(path) and (ignored := sorted(set(meta) & PLUGIN_AGENT_IGNORED)):
            ran.problems.append(f"{where}: {ignored} are ignored in a plugin agent; use plugin hooks or MCP servers")
        if not body.strip():
            ran.problems.append(f"{where}: empty body")
        if path.name == "SKILL.md":
            ran.problems += [f"{where}: {p}" for p in skill_spec_problems(meta, body, path.parent.name)]
    return ran


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


def check_dev_commands(commands: dict[str, set[str]]) -> Ran:
    """Every `uv run dev X [Y]` written anywhere in prose, agents or skills names a real command."""
    ran = Ran("dev commands named in docs")
    for path in tracked("*.md", "*.sh", "*.yml", "*.json", "*.jinja"):
        rel = path.relative_to(root())
        if rel.is_relative_to(config.get().generated_dir):
            continue
        for m in DEV_CMD.finditer(path.read_text(encoding="utf-8", errors="replace")):
            ran.checked += 1
            cmd, sub = m.group(1), m.group(2)
            if cmd not in commands:
                ran.problems.append(f"{rel}: `uv run dev {cmd}` is not a command (see `uv run dev --help`)")
            elif sub and commands[cmd] and sub not in commands[cmd] and not sub.startswith("-"):
                ran.problems.append(f"{rel}: `uv run dev {cmd} {sub}` is not a subcommand of {cmd}")
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


def measured_words() -> dict[str, int]:
    """Words each budgeted text costs when loaded: the body plus an agent's initialPrompt, CLAUDE.md/AGENTS.md plus
    what they @-import, and a skill's supporting files under `<skill dir>/*` (budgeted like the skill's body)."""
    out: dict[str, int] = {}
    for p in [root() / "CLAUDE.md", root() / "AGENTS.md"]:
        if p.exists():
            out[p.name] = frontmatter.words(p.read_text()) + imported_words(p)
    for p in [*_agents(), *_skills()]:
        meta, body = frontmatter.split(p.read_text())
        rel = str(p.relative_to(root()))
        out[rel] = frontmatter.words(body) + frontmatter.words(str((meta or {}).get("initialPrompt") or ""))
        if p.name == "SKILL.md" and (n := supporting_words(p)) is not None:
            out[str(p.parent.relative_to(root())) + "/*"] = n
    return out


def _load_ratchets(text: str) -> dict:
    data = tomllib.loads(text)
    return {"words": data.get("words", {}), "defaults": data.get("defaults", {}), "raise": data.get("raise", [])}


GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def repo_owner() -> str | None:
    """GitHub login that owns the origin repository: the only valid approver of a [[raise]]."""
    url = git("remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+)/", url)
    return m.group(1) if m and GITHUB_LOGIN.match(m.group(1)) else None


def _kind(rel: str) -> str:
    """Budget kind of a measured file: agent, skill (a SKILL.md or a skill's `<dir>/*`), or context."""
    parts = Path(rel).parts
    surface = {Path(s).parts for s in config.get().surface}
    for i in range(len(parts)):
        if parts[:i] in surface and parts[i : i + 1] in (("agents",), ("skills",)):
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
    the key that sets it, approved_by the repository owner's GitHub login, with a `to` that covers the new value."""
    problems: list[str] = []
    known = {(r.get("key"), r.get("to")) for r in old["raise"]}
    allowed: dict[str, int] = {}
    for r in cur["raise"]:
        key, to, who = str(r.get("key")), r.get("to"), str(r.get("approved_by", ""))
        if (r.get("key"), to) in known:
            continue
        if not owner or not GITHUB_LOGIN.match(who) or who.lower() != owner.lower():
            problems.append(
                f"ratchets.toml: [[raise]] {key}: approved_by {who!r} is not the repository owner {owner!r}"
            )
        elif not isinstance(to, int) or isinstance(to, bool):
            problems.append(f"ratchets.toml: [[raise]] {key}: `to` must be an integer")
        else:
            allowed[key] = max(to, allowed.get(key, to))
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
    """Word budgets of the always-loaded and agent files may only go down. A raise needs an owner-signed [[raise]]."""
    ran = Ran("ratchets (budgets only tighten)")
    ratchets = config.get().ratchets
    cur = _load_ratchets((root() / ratchets).read_text())
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


def merge_base() -> str | None:
    for ref in ("origin/main", "main"):
        out = subprocess.run(
            ["git", "merge-base", "HEAD", ref], cwd=root(), capture_output=True, text=True, check=False
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return None
