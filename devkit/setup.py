"""`uv run dev setup` (idempotent, once per machine and clone) and `uv run dev doctor` (read-only diagnosis).

Everything shared is in git or in the pinned harness: settings, `.mcp.json`, and the plugins named in `[tool.devkit]
plugins` (agents, skills, hooks). What a clone still needs is a git repository, tools on PATH, auth, git config, the
generated references and the plugin installs; setup does those and doctor reports what is missing.
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

from devkit import config, lit
from devkit.paths import git, root

TOOLS = {
    "git": "https://git-scm.com",
    "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "gh": "https://cli.github.com (then `gh auth login`)",
    "jq": "apt install jq / brew install jq (hooks parse JSON with it)",
    "claude": "npm install -g @anthropic-ai/claude-code",
    "npx": "Node.js 20+ (the Playwright MCP server runs through npx)",
    "flock": "util-linux (gate and hooks use it)",
}


def _ok(cmd: list[str]) -> tuple[bool, str]:
    try:
        out = subprocess.run(cmd, cwd=root(), capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return out.returncode == 0, (out.stdout + out.stderr).strip()


def _trusted() -> bool:
    try:
        data = json.loads((Path.home() / ".claude.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    paths = data.get("projects", {})
    return any(paths.get(str(p), {}).get("hasTrustDialogAccepted") for p in (root(), *root().parents))


SEMVER = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def latest_release(tags: list[str]) -> str | None:
    """The highest plain `vX.Y.Z` tag; pre-releases and other names are not releases."""
    found = [(tuple(map(int, m.groups())), t) for t in tags if (m := SEMVER.fullmatch(t))]
    return max(found)[1] if found else None


def _older(ref: str, latest: str) -> bool:
    a, b = SEMVER.fullmatch(ref if ref.startswith("v") else f"v{ref}"), SEMVER.fullmatch(latest)
    return bool(a and b) and tuple(map(int, a.groups())) < tuple(map(int, b.groups()))


def repo_url(source: str) -> str:
    """A Copier `_src_path` or a plugin marketplace source as something `git ls-remote` takes."""
    source = source.removeprefix("gh:")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):
        return f"https://github.com/{source}"
    return source


def remote_tags(url: str) -> list[str]:
    ok, out = _ok(["git", "ls-remote", "--tags", "--refs", url])
    return [line.rpartition("refs/tags/")[2] for line in out.splitlines()] if ok else []


def staleness(
    answers: dict, plugins: dict[str, str], installed: list[dict], project: Path, tags: Callable[[str], list[str]]
) -> list[str]:
    """Warnings for a project behind its harness: the Copier template (`.copier-answers.yml`), a plugin pinned to an
    older release than the latest, or a plugin installed for this project older than its pin. An unreachable remote
    gives no warning (doctor is not the place to fail on the network)."""
    out = []
    if (ref := answers.get("_commit")) and (src := answers.get("_src_path")):
        latest = latest_release(tags(repo_url(src)))
        if latest and _older(ref, latest):
            out.append(f"template {ref} is behind {latest}  ->  uvx copier update, then uv sync")
    for plugin, source in plugins.items():
        repo, _, pin = source.partition("@")
        if not pin:
            continue
        latest = latest_release(tags(repo_url(repo)))
        if latest and _older(pin, latest):
            out.append(f"plugin {plugin} pinned to {pin}, behind {latest}  ->  uvx copier update")
        here = [p for p in installed if p.get("id") == plugin and p.get("projectPath") == str(project)]
        if here and _older(str(here[0].get("version", "")), pin):
            version = here[0]["version"]
            out.append(f"plugin {plugin} installed {version}, pinned {pin}  ->  claude plugin update {plugin}")
    return out


KEYS_DOC = "docs/SETUP.md, section 'Research API keys'"


def key_lines(where: dict[str, str], problem: str) -> list[tuple[bool | None, str, str]]:
    """Doctor lines for the research API keys: (ok, what, fix); None = a warning. Names and origins, never values."""
    out: list[tuple[bool | None, str, str]] = [
        (True, f"{name} found ({origin})", "")
        if origin != "missing"
        else (None, f"{name} missing (dev lit uses the shared, rate-limited pool)", KEYS_DOC)
        for name, origin in where.items()
    ]
    if problem:
        out.append((False, "research API keys file", f"{problem}; see {KEYS_DOC}"))
    return out


def doctor() -> int:
    problems = 0

    def report(ok: bool, what: str, fix: str = "") -> None:
        nonlocal problems
        problems += not ok
        print(f"{'ok  ' if ok else 'FIX '} {what}{'' if ok or not fix else '  ->  ' + fix}")

    for tool, fix in TOOLS.items():
        report(shutil.which(tool) is not None, f"{tool} on PATH", fix)
    ok, _ = _ok(["gh", "auth", "status"])
    report(ok, "gh authenticated", "gh auth login")
    report(bool(git("remote", "get-url", "origin", check=False)), "origin remote", "see README: clone from GitHub")
    report(git("config", "--get", "rerere.enabled", check=False) == "true", "git rerere enabled", "uv run dev setup")
    ok, out = _ok(["claude", "plugin", "list"])
    for plugin in config.get().plugins:
        report(ok and plugin.partition("@")[0] in out, f"Claude Code plugin {plugin} installed", "uv run dev setup")
    ok, out = _ok(["claude", "mcp", "list"])
    mcp_file = root() / ".mcp.json"
    mcp = json.loads(mcp_file.read_text()).get("mcpServers", {}) if mcp_file.exists() else {}
    for name in mcp:
        report(
            ok and any(line.startswith(f"{name}:") and "Connected" in line for line in out.splitlines()),
            f"MCP server {name} connected",
            "approve project MCP servers in Claude Code (/mcp) or check the command",
        )
    report((root() / ".venv").exists(), "uv environment", "uv sync")
    report(
        _trusted(),
        "workspace trusted by Claude Code (else headless agents ignore the project allowlist)",
        "run `claude` here once interactively and accept the trust dialog",
    )
    for ok, what, fix in key_lines(*lit.key_report(dict(os.environ))):
        if ok is None:
            print(f"WARN {what}  ->  {fix}")
        else:
            report(ok, what, fix)
    from devkit.workflow import POLLER, quota_reading

    report(quota_reading()[0] is not None, "quota reading under 30 min old (spawn guard enforces the cap)", POLLER)
    answers_file = root() / ".copier-answers.yml"
    answers = (yaml.safe_load(answers_file.read_text()) or {}) if answers_file.exists() else {}
    ok, out = _ok(["claude", "plugin", "list", "--json"])
    try:
        installed = json.loads(out) if ok else []
    except json.JSONDecodeError:
        installed = []
    for warning in staleness(answers, config.get().plugins, installed, root(), remote_tags):
        print(f"WARN {warning}")
    print(f"\n{problems} thing(s) to fix" if problems else "\nall good")
    return 1 if problems else 0


def plugin_steps() -> list[tuple[str, list[str]]]:
    steps = []
    for plugin, source in config.get().plugins.items():
        steps.append((f"plugin marketplace {source}", ["claude", "plugin", "marketplace", "add", source]))
        steps.append((f"plugin {plugin}", ["claude", "plugin", "install", plugin, "--scope", "project"]))
    return steps


def setup() -> int:
    inside = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if inside.returncode != 0:
        subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
        print("ok   git init (branch main)")
    steps = [
        ("uv sync", ["uv", "sync"]),
        ("generated references (dev docs)", ["uv", "run", "--quiet", "dev", "docs"]),
        ("git rerere (replays conflict resolutions across worktrees)", ["git", "config", "rerere.enabled", "true"]),
        ("git rerere autoupdate", ["git", "config", "rerere.autoupdate", "true"]),
        ("git pull rebase off (merge commits keep PR history)", ["git", "config", "pull.rebase", "false"]),
        ("pre-commit hooks (prek)", ["uvx", "prek", "install"]),
        *plugin_steps(),
    ]
    for what, cmd in steps:
        ok, out = _ok(cmd)
        already = "already" in out.lower()
        print(f"{'ok  ' if ok or already else 'FAIL'} {what}" + ("" if ok or already else f": {out[-300:]}"))
    from devkit.workflow import POLLER

    print(f"""
Still to do by hand:
- run `claude` here once interactively and accept the trust dialog (headless agents need it)
- once per clone and boot: start the quota poller: `{POLLER}`
- once per repository: create it on GitHub (`gh repo create`), then `uv run dev board init`""")
    return doctor()
