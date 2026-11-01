"""`uv run dev setup` (idempotent, once per machine and clone) and `uv run dev doctor` (read-only diagnosis).

Everything shared is in git or in the pinned harness: settings, `.mcp.json`, and the plugins named in `[tool.devkit]
plugins` (agents, skills, hooks). What a clone still needs is a git repository, tools on PATH, auth, git config, the
generated references and the plugin installs; setup does those and doctor reports what is missing.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
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

# The oldest python3 the plugin's hook scripts run on (they run as `#!/usr/bin/env python3`, the host's interpreter);
# guard_bash.MIN_PYTHON in the plugin states the same (a test checks they agree).
HOOK_PYTHON = (3, 10)


def hook_python(version: str) -> tuple[bool, str, str]:
    """(ok, what, fix) for the host's python3 version ("3.10", or "" when there is none) against HOOK_PYTHON."""
    want = ".".join(map(str, HOOK_PYTHON))
    try:
        ok = tuple(int(x) for x in version.split(".")[:2]) >= HOOK_PYTHON
    except ValueError:
        ok = False
    what = f"python3 on PATH is {version or 'missing'} (plugin hooks need {want}+)"
    return ok, what, f"install Python {want}+ as python3 (the hooks run on the host's python3, not the venv's)"


def host_leaks(env: Mapping[str, str]) -> str:
    """A warning when the shell redirects Python (a sourced ROS sets PYTHONPATH), else ""."""
    from devkit.gate import hermetic_env

    if not (dropped := hermetic_env(env)[1]):
        return ""
    clear = json.dumps({"env": dict.fromkeys(dropped, "")})
    unset = " ".join(f"-u {k}" for k in dropped)
    return (
        f"{', '.join(dropped)} set in this shell: the gate and `dev test` ignore it, but `uv run python` and "
        f"a bare pytest still load it  ->  for agents, `.claude/settings.local.json` {clear}; "
        f"in a shell, `env {unset} ...` (docs/SETUP.md, Host Python environment)"
    )


def _ok(cmd: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    try:
        out = subprocess.run(cmd, cwd=cwd or root(), capture_output=True, text=True, check=False, timeout=60)
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


def staleness(answers: dict, plugins: dict[str, str], tags: Callable[[str], list[str]]) -> list[str]:
    """Warnings for a project behind its harness: the Copier template (`.copier-answers.yml`) or a plugin pinned to an
    older release than the latest. An unreachable remote gives no warning (doctor is not the place to fail on the
    network). An installed plugin that is not its pin's build is a failure, not a warning: `build_skew`."""
    out = []
    if (ref := answers.get("_commit")) and (src := answers.get("_src_path")):
        latest = latest_release(tags(repo_url(src)))
        if latest and _older(ref, latest):
            out.append(f"template {ref} is behind {latest}  ->  uvx copier update, then uv sync, then uv run dev docs")
        # A template before v0.1.15 stored harness_ref and reused it on update, so the pins could stay behind the
        # template (#163); only an explicit ref moves them out of that state. Later answers files have no harness_ref.
        if (pin := answers.get("harness_ref")) and _older(pin, ref):
            to = latest if latest and _older(ref, latest) else ref
            out.append(
                f"pins {pin} lag the template {ref}  ->  uvx copier update --data harness_ref={to}, then uv sync,"
                " then uv run dev docs"
            )
    for plugin, source in plugins.items():
        repo, _, pin = source.partition("@")
        if not pin:
            continue
        latest = latest_release(tags(repo_url(repo)))
        if latest and _older(pin, latest):
            out.append(f"plugin {plugin} pinned to {pin}, behind {latest}  ->  uvx copier update")
    return out


# The plugin whose build this devkit release ships (#296). The marketplace installs its branch head under the
# manifest's version string, so neither the string nor the cache directory name identifies the build: compare content.
PLUGIN = "harness-ops"
# Claude Code's own markers in a plugin cache directory, and bytecode: never part of a build.
NOT_BUILD = frozenset({".in_use", ".orphaned_at", "__pycache__"})
SKEW_FIX = "uv run dev setup, then restart Claude Code"


def plugin_digest(plugin_root: Path) -> str:
    """SHA-256 over the plugin tree's relative paths and file contents (not modes or times), skipping `NOT_BUILD`."""
    files = []
    for top, dirs, names in os.walk(plugin_root):
        dirs[:] = [d for d in dirs if d not in NOT_BUILD]
        files += [Path(top, n) for n in names if n not in NOT_BUILD]
    digest = hashlib.sha256()
    for rel, path in sorted((f.relative_to(plugin_root).as_posix(), f) for f in files):
        digest.update(f"{rel}\0".encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def pinned_plugin_root() -> Path | None:
    """The plugin tree of this devkit's own release. The wheel bundles it (pyproject `force-include`), so a project
    that installs devkit from the pinned tag holds that tag's plugin byte for byte; a source checkout has its tree."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "_plugin" / PLUGIN, here.parent / "plugins" / PLUGIN):
        if (candidate / ".claude-plugin/plugin.json").is_file():
            return candidate
    return None


def _manifest_version(plugin_root: Path) -> str:
    try:
        return str(json.loads((plugin_root / ".claude-plugin/plugin.json").read_text())["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "?"


def _pin(plugins: dict[str, str]) -> tuple[str, str] | None:
    """(plugin id, pinned ref) of this harness's plugin, or None when it tracks a branch instead of a release."""
    for plugin, source in plugins.items():
        if plugin.partition("@")[0] == PLUGIN and (ref := source.partition("@")[2]):
            return plugin, ref
    return None


def skew_line(loaded: Path, pinned: Path | None, plugins: dict[str, str]) -> str:
    """One line when the loaded plugin root is not the pinned release's build, else "". The plugin's own SessionStart
    and SubagentStart hooks run it (scripts/plugin_skew.sh) through the project's devkit."""
    pin = _pin(plugins)
    if not pin or pinned is None or not loaded.is_dir():
        return ""
    have, want = plugin_digest(loaded), plugin_digest(pinned)
    if have == want:
        return ""
    return (
        f"{PLUGIN} skew: loaded {_manifest_version(loaded)} (content {have[:12]}) from {loaded}, pinned {pin[1]} "
        f"(content {want[:12]})  ->  {SKEW_FIX}"
    )


def _install_record(installed: list[dict], plugin: str, projects: set[str]) -> dict | None:
    """The install Claude Code loads here: this project's (main checkout or worktree), else a user-scope one."""
    mine = [p for p in installed if isinstance(p, dict) and p.get("id") == plugin]
    here = [p for p in mine if p.get("projectPath") in projects] or [p for p in mine if p.get("scope") == "user"]
    return here[0] if here else None


def build_skew(plugins: dict[str, str], installed: list[dict], projects: set[str], pinned: Path | None) -> list[str]:
    """Doctor failures (#296): the installed plugin's version string or its content differs from the pin's."""
    pin = _pin(plugins)
    if not pin or not (record := _install_record(installed, pin[0], projects)):
        return []
    version, out = str(record.get("version", "")), []
    if version != pin[1].removeprefix("v"):
        out.append(f"{pin[0]} installed {version or '?'}, pinned {pin[1]}")
    path = Path(str(record.get("installPath") or "/nonexistent"))
    if pinned is not None and path.is_dir() and (have := plugin_digest(path)) != (want := plugin_digest(pinned)):
        out.append(f"{pin[0]} installed content {have[:12]} is not {pin[1]}'s {want[:12]}")
    return out


def _main_checkout() -> Path:
    """The main checkout, also from a worktree: Claude Code records project installs by it."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root(), check=False)
    return Path(common).parent if common else root()


def _projects() -> set[str]:
    """This checkout and, from a worktree, the main checkout."""
    return {str(root()), str(_main_checkout())}


def _installed() -> list[dict]:
    ok, out = _ok(["claude", "plugin", "list", "--json"])
    try:
        data = json.loads(out) if ok else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def mcp_prereqs(
    servers: Mapping[str, object], prereqs: Mapping[str, list[str]], which: Callable[[str], str | None]
) -> list[tuple[bool, str, str]]:
    """Doctor lines (ok, what, fix) for the binaries `[tool.devkit] mcp_prereqs` says the configured MCP servers need.
    `which` is shutil.which, which sees real executables only: a shell function (Claude Code defines one for rg) passes
    `command -v` in a session while the server, started without that shell, finds nothing (#390)."""
    out = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        words = [name, str(spec.get("command", "")), *map(str, spec.get("args") or [])]
        for key, tools in prereqs.items():
            if any(key in w for w in words):
                out += [
                    (
                        which(tool) is not None,
                        f"{tool} on PATH as a real binary (MCP server {name} needs it)",
                        f"install {tool} as a real binary on PATH (a shell function or alias does not count), "
                        "then reconnect the server (/mcp): it checks only at start",
                    )
                    for tool in tools
                ]
    return out


def _mcp_servers() -> dict:
    """The MCP servers Claude Code starts here: user scope and this project's local scope (both in ~/.claude.json)
    and the project's `.mcp.json`, a later scope's server replacing an earlier one of the same name."""

    def servers_in(path: Path) -> tuple[dict, dict]:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}, {}
        if not isinstance(data, dict):
            return {}, {}
        local = data.get("projects", {}).get(str(root()), {}) if isinstance(data.get("projects"), dict) else {}
        return data, local if isinstance(local, dict) else {}

    user, local = servers_in(Path.home() / ".claude.json")
    project, _ = servers_in(root() / ".mcp.json")
    out: dict = {}
    for scope in (user, project, local):
        if isinstance(found := scope.get("mcpServers"), dict):
            out |= found
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
    ok, out = _ok(["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    report(*hook_python(out if ok else ""))
    ok, _ = _ok(["gh", "auth", "status"])
    report(ok, "gh authenticated", "gh auth login")
    report(bool(git("remote", "get-url", "origin", check=False)), "origin remote", "see README: clone from GitHub")
    report(git("config", "--get", "rerere.enabled", check=False) == "true", "git rerere enabled", "uv run dev setup")
    installed, projects = _installed(), _projects()  # every install on the machine; only this project's loads (#3)
    for plugin in config.get().plugins:
        here = _install_record(installed, plugin, projects) is not None
        report(here, f"Claude Code plugin {plugin} installed", "uv run dev setup")
    ok, out = _ok(["claude", "mcp", "list"])
    mcp_file = root() / ".mcp.json"
    mcp = json.loads(mcp_file.read_text()).get("mcpServers", {}) if mcp_file.exists() else {}
    for name in mcp:
        report(
            ok and any(line.startswith(f"{name}:") and "Connected" in line for line in out.splitlines()),
            f"MCP server {name} connected",
            "approve project MCP servers in Claude Code (/mcp) or check the command",
        )
    for line in mcp_prereqs(_mcp_servers(), config.get().mcp_prereqs, shutil.which):
        report(*line)
    report((root() / ".venv").exists(), "uv environment", "uv sync")
    if leaks := host_leaks(os.environ):
        print(f"WARN {leaks}")
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
    from devkit.quota import POLLER, quota_reading

    report(quota_reading()[0] is not None, "quota reading under 30 min old (spawn guard enforces the cap)", POLLER)
    skew = build_skew(config.get().plugins, installed, projects, pinned_plugin_root())
    detail = f" ({'; '.join(skew)})" if skew else ""
    report(not skew, f"installed plugin is the pinned release's build{detail}", "uv run dev setup")
    answers_file = root() / ".copier-answers.yml"
    answers = (yaml.safe_load(answers_file.read_text()) or {}) if answers_file.exists() else {}
    for warning in staleness(answers, config.get().plugins, remote_tags):
        print(f"WARN {warning}")
    print(f"\n{problems} thing(s) to fix" if problems else "\nall good")
    return 1 if problems else 0


# What `marketplace add` says when user (or managed) settings register the marketplace's name at another source.
# Claude Code 2.1.x, verified in #327.
SOURCE_MISMATCH = "extraKnownMarketplaces"


def user_marketplace(name: str) -> dict:
    """The source user settings register marketplace `name` at, or {} when they register none."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        source = json.loads((base / "settings.json").read_text())["extraKnownMarketplaces"][name]["source"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return source if isinstance(source, dict) else {}


def _repo(source: dict) -> str:
    where = repo_url(str(source["repo"])) if source.get("repo") else str(source.get("url") or source.get("path") or "")
    return where.rstrip("/").removesuffix(".git")


def clash_warning(source: str, registered: dict) -> str | None:
    """#327: Claude Code keeps one marketplace registry per user, keyed by name, so `marketplace add` fails when
    another project registered the same repository at another ref. That is a warning naming both refs: the install
    uses the registered one, and `repin_skewed` re-points it when this project's pin needs another build. None when
    the registry names another repository, or the same ref: then the failure stands."""
    repo, _, ref = source.partition("@")
    have = str(registered.get("ref") or "")
    if not registered or _repo(registered) != _repo({"url": repo_url(repo)}) or have == ref:
        return None
    return (
        f"user settings register {repo} at {have or 'its default branch'} (another project's pin); "
        f"this project pins {ref or 'no ref'}"
    )


def _clash(source: str) -> str | None:
    names = [plugin.partition("@")[2] for plugin, s in config.get().plugins.items() if s == source]
    return next((warn for name in names if (warn := clash_warning(source, user_marketplace(name)))), None)


def run_steps(steps: list[tuple[str, list[str]]]) -> None:
    """Run each step and print ok, WARN or FAIL. A marketplace another project registered at another ref of the same
    repository is a WARN (`clash_warning`), not a FAIL."""
    for what, cmd in steps:
        ok, out = _ok(cmd)
        if ok or "already" in out.lower():
            print(f"ok   {what}")
        elif cmd[1:4] == ["plugin", "marketplace", "add"] and SOURCE_MISMATCH in out and (warn := _clash(cmd[4])):
            print(f"WARN {what}: {warn}")
        else:
            print(f"FAIL {what}: {out[-300:]}")


def plugin_steps() -> list[tuple[str, list[str]]]:
    steps = []
    for plugin, source in config.get().plugins.items():
        steps.append((f"plugin marketplace {source}", ["claude", "plugin", "marketplace", "add", source]))
        steps.append((f"plugin {plugin}", ["claude", "plugin", "install", plugin, "--scope", "project"]))
    return steps


REPIN_ALL_COST = (
    "removes the marketplace in every scope: uninstalls the plugin from every other project and the user scope on "
    "this machine and deletes its data, options and secrets"
)


# What `marketplace remove --scope user` says when only project settings declare the marketplace (a trust prompt
# registered it from the template's extraKnownMarketplaces): nothing to remove there, and `add` still re-points it.
# Claude Code 2.1.304, verified in the #310 review. A project-scope remove instead would uninstall other projects.
NOT_IN_USER_SETTINGS = "not declared in user settings"
STOPPED = "re-pin stopped at: "


def repin_steps(plugin: str, source: str, everywhere: bool = False) -> list[list[str]]:
    """Commands that put the pinned release of `plugin` in place. Claude Code keeps one marketplace registry per user,
    keyed by name and recording one ref: adding an existing name is a no-op, and `marketplace update` plus `plugin
    update` fetch the registered ref (or the branch head) and keep the cached copy while the version string is
    unchanged. So the registry entry is removed and added at the pin's ref (#296). Removed at user scope only, the
    marketplace stays declared by the projects' settings and their installs survive; `everywhere` removes it in
    every scope (`REPIN_ALL_COST`), for a marketplace no project settings declare."""
    marketplace = plugin.partition("@")[2]
    remove = ["claude", "plugin", "marketplace", "remove", marketplace]
    return [
        remove if everywhere else [*remove, "--scope", "user"],
        ["claude", "plugin", "marketplace", "add", source],
        *([] if everywhere else [["claude", "plugin", "uninstall", plugin, "--scope", "project"]]),
        ["claude", "plugin", "install", plugin, "--scope", "project"],
    ]


def _keep_settings(saved: dict[Path, bytes]) -> None:
    """Undo what the re-pin did to the project's settings files (committed; `marketplace remove` can delete the
    marketplace there) but keep the `enabledPlugins` entries the install added. A file that is not a JSON object
    gets its bytes back. Never raises: it runs in the re-pin's `finally`."""
    for f, data in saved.items():
        try:
            now, before = json.loads(f.read_bytes()), json.loads(data)
        except (OSError, ValueError):
            now = before = None
        try:
            if not (isinstance(now, dict) and isinstance(before, dict)):
                if not f.exists() or f.read_bytes() != data:
                    f.write_bytes(data)
                    print(f"WARN {f} is not a JSON object: restored as it was")
                continue
            if now == before:  # same content, maybe reordered or reformatted: the committed bytes win
                if f.read_bytes() != data:
                    f.write_bytes(data)
                continue
            had, got = before.get("enabledPlugins"), now.get("enabledPlugins")
            had, got = (had if isinstance(had, dict) else {}), (got if isinstance(got, dict) else {})
            if added := {k: v for k, v in got.items() if k not in had}:
                before["enabledPlugins"] = {**had, **added}
            text = json.dumps(before, indent=2 if b"\n" in data.strip() else None, ensure_ascii=False)
            f.write_text(text + ("\n" if data.endswith(b"\n") else ""), encoding="utf-8")
        except OSError as exc:
            print(f"WARN could not restore {f}: {exc}")


def repin_skewed(everywhere: bool = False) -> list[str]:
    """When the installed plugin is not the pinned build, re-point its marketplace at the pin and reinstall
    (`repin_steps`). Checks first that the pinned ref exists on the remote and stops at the first failed step, so an
    unreachable remote leaves the current install in place. Returns what still differs afterwards."""
    plugins, pinned, projects = config.get().plugins, pinned_plugin_root(), _projects()
    if not (pin := _pin(plugins)) or not (skew := build_skew(plugins, before := _installed(), projects, pinned)):
        return []
    repo, _, ref = plugins[pin[0]].partition("@")
    if not _ok(["git", "ls-remote", "--exit-code", repo_url(repo), ref])[0]:
        print(f"FAIL {ref} not reachable at {repo_url(repo)}: nothing changed")
        return skew
    others = sorted({str(p.get("projectPath") or "the user scope") for p in before if p.get("id") == pin[0]} - projects)
    main = _main_checkout()  # a project install is recorded by the main checkout, so its commands run there
    saved = {f: f.read_bytes() for d in {root(), main} for f in (d / ".claude").glob("settings*.json")}
    try:
        for cmd in repin_steps(pin[0], plugins[pin[0]], everywhere):
            ok, out = _ok(cmd, cwd=main)
            step = " ".join(cmd[1:])
            if not ok and "--scope" in cmd and NOT_IN_USER_SETTINGS in out:
                print(f"skip {step}: only project settings declare it (they keep it; add re-points it)")
                continue
            print(f"{'ok  ' if ok else 'FAIL'} {step}" + ("" if ok else f": {out[-300:]}"))
            if not ok:
                return [*skew, f"{STOPPED}{step}"]
    finally:
        _keep_settings(saved)
    if everywhere and others:
        print(f"WARN {pin[0]} was uninstalled from {', '.join(others)}  ->  run `uv run dev setup` in each")
    return build_skew(plugins, _installed(), projects, pinned)


def git_steps() -> list[tuple[str, list[str]]]:
    """Per-clone git config. The merge driver lets a merge keep one side of a generated file (`.gitattributes` names
    it); `dev docs` regenerates it afterwards, and the gate fails if nobody did."""
    from devkit.workflow import GENERATED_MERGE

    return [
        ("git rerere (replays conflict resolutions across worktrees)", ["git", "config", "rerere.enabled", "true"]),
        ("git rerere autoupdate", ["git", "config", "rerere.autoupdate", "true"]),
        ("git pull rebase off (merge commits keep PR history)", ["git", "config", "pull.rebase", "false"]),
        *[(f"merge driver for generated files ({k})", ["git", "config", k, v]) for k, v in GENERATED_MERGE],
    ]


def setup(repin_all: bool = False) -> int:
    inside = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if inside.returncode != 0:
        subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
        print("ok   git init (branch main)")
    from devkit.quota import POLLER

    steps = [
        ("uv sync", ["uv", "sync"]),
        ("generated references (dev docs)", ["uv", "run", "--quiet", "dev", "docs"]),
        *git_steps(),
        ("pre-commit hooks (prek)", ["uvx", "prek", "install"]),
        *plugin_steps(),
    ]
    run_steps(steps)
    if still := repin_skewed(everywhere=repin_all):
        print("FAIL the plugin is still not the pinned build after a re-pin: " + "; ".join(still))
        if not repin_all and (failed := [s.removeprefix(STOPPED) for s in still if s.startswith(STOPPED)]):
            print(f"     `{failed[0]}` failed; last resort: `uv run dev setup --repin-all` ({REPIN_ALL_COST})")
    print(f"""
Still to do by hand:
- run `claude` here once interactively and accept the trust dialog (headless agents need it)
- once per clone and boot: start the quota poller: `{POLLER}`
- once per repository: create it on GitHub (`gh repo create`), then `uv run dev board init`""")
    return doctor()
