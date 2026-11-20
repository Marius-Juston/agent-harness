"""`dev release prepare|publish X.Y.Z` (#605): the release steps the lead ran by hand, from Python.

prepare (on a release branch): refuse unless CHANGELOG.md has a `## vX.Y.Z (YYYY-MM-DD)` section, write the version
into every `[tool.devkit] release_files` entry and run `uv lock`; the branch then goes through `dev pr` and
`dev integrate` like any other. publish (on main at the merged release commit): annotated tag, push it, GitHub Release
with the CHANGELOG section as notes, marked latest. Every publish step first looks whether it already happened, so a
rerun after a partial failure (a dropped connection, a refused push) finishes only the missing steps; gh goes
through board.gh with this module's recheck for `release create` (#571), so it never creates a second release.
"""

import json
import re
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

from devkit import board
from devkit.paths import git

VERSION = re.compile(r"\d+\.\d+\.\d+")
CHANGELOG = "CHANGELOG.md"
MAIN = "main"
TEMPLATE_DEFAULT = re.compile(r"^(\s*default:\s*v)(\d+\.\d+\.\d+)(\s*)$", re.M)  # copier.yml's harness_ref
JSON_VERSION = re.compile(r'("version"\s*:\s*")([^"]*)(")')
TOML_VERSION = re.compile(r'^(version\s*=\s*")([^"]*)(")', re.M)
TOML_TABLE = re.compile(r"^\[", re.M)


class ReleaseError(RuntimeError):
    pass


def section(changelog: str, version: str) -> str | None:
    """The body of CHANGELOG's `## vX.Y.Z (YYYY-MM-DD)` section, stripped; None when there is none or it is empty."""
    head = rf"^## v{re.escape(version)} \(\d{{4}}-\d{{2}}-\d{{2}}\)[ \t]*$"
    m = re.search(head + r"\n(.*?)(?=^## |\Z)", changelog + "\n", re.M | re.S)
    return (m.group(1).strip() or None) if m else None


def _project_span(text: str) -> tuple[int, int] | None:
    """Start and end of the `[project]` table's body in a pyproject.toml."""
    start = re.search(r"^\[project\][ \t]*$", text, re.M)
    if not start:
        return None
    end = TOML_TABLE.search(text, start.end())
    return start.end(), end.start() if end else len(text)


def read_version(name: str, text: str) -> str | None:
    """The version a release file states: a `.toml` its `[project] version`, a `.json` its top-level `version`, a
    `.yml`/`.yaml` its first `default: v<version>` line (copier.yml's `harness_ref`, as tests/test_version_sync.py
    reads it). None when the file states none."""
    try:
        if name.endswith(".toml"):
            v = tomllib.loads(text).get("project", {}).get("version")
        elif name.endswith(".json"):
            v = json.loads(text).get("version")
        elif name.endswith((".yml", ".yaml")):
            v = m.group(2) if (m := TEMPLATE_DEFAULT.search(text)) else None
        else:
            return None
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, AttributeError):
        return None
    return v if isinstance(v, str) else None


def write_version(name: str, text: str, version: str) -> str:
    """`text` with the version read_version finds set to `version`, all else byte for byte."""
    if name.endswith(".toml"):
        if (span := _project_span(text)) is None:
            raise ReleaseError(f"{name}: no [project] table")
        body = TOML_VERSION.sub(rf"\g<1>{version}\g<3>", text[span[0] : span[1]], count=1)
        out = text[: span[0]] + body + text[span[1] :]
    elif name.endswith(".json"):
        out = JSON_VERSION.sub(rf"\g<1>{version}\g<3>", text, count=1)
    else:
        out = TEMPLATE_DEFAULT.sub(rf"\g<1>{version}\g<3>", text, count=1)
    if read_version(name, out) != version:  # e.g. a nested "version" key came first in the JSON
        raise ReleaseError(f"{name}: could not set the version to {version} in place; edit it by hand")
    return out


def _check_version(version: str) -> None:
    if not VERSION.fullmatch(version):
        raise ReleaseError(f"{version!r} is not a version X.Y.Z (no leading v)")


def _uv_lock(top: Path) -> None:
    out = subprocess.run(["uv", "lock"], cwd=top, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise ReleaseError(f"uv lock failed: {(out.stderr or out.stdout).strip()}")


def prepare(version: str, top: Path, files: list[str], lock: Callable[[Path], None] = _uv_lock) -> list[str]:
    """Write `version` into `files` and run `lock`; refuse, writing nothing, without its CHANGELOG section."""
    _check_version(version)
    log = top / CHANGELOG
    if section(log.read_text() if log.exists() else "", version) is None:
        raise ReleaseError(f"{CHANGELOG} has no `## v{version} (YYYY-MM-DD)` section with notes: write it first")
    texts: dict[str, str] = {}
    for name in files:
        path = top / name
        if not path.exists() or read_version(name, text := path.read_text()) is None:
            raise ReleaseError(f"{name} (release_files): missing or states no version")
        texts[name] = text
    lines = []
    for name, text in texts.items():
        old = read_version(name, text)
        (top / name).write_text(write_version(name, text, version))
        lines.append(f"{name}: {old} -> {version}")
    lock(top)
    lines.append("uv.lock: relocked")
    return lines


def _at_head(top: Path, name: str) -> str | None:
    out = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=top, capture_output=True, text=True, check=False)
    return out.stdout if out.returncode == 0 else None


def _release_url(repo: str, tag: str) -> str | None:
    """The URL of the published GitHub Release for `tag`, None when there is none (a GET: board.gh retries it)."""
    try:
        return board.gh("api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".html_url").strip() or None
    except board.BoardError as e:
        if "HTTP 404" in str(e):
            return None
        raise


def _recheck(args: tuple[str, ...], input: str | None) -> Callable[[float], str | tuple[str, ...]] | None:
    """board.gh's recheck for `gh release create <tag> -R <repo>` (#571): after a dropped connection, the tag's
    release (one per tag, whenever it was made) means the call landed; none means send it again."""
    repo = board._flag(args, "-R", "--repo")
    if repo is None or len(args) < 3 or args[2].startswith("-"):
        return None
    return lambda since: (url + "\n") if (url := _release_url(repo, args[2])) else args


board.RECHECKS["release", "create"] = _recheck


def _ensure_tag(top: Path, tag: str, head: str) -> str:
    """Create the annotated `tag` at HEAD, or keep the one a previous run made there; refuse any other."""
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}", cwd=top, check=False):
        at = git("rev-parse", f"refs/tags/{tag}^{{commit}}", cwd=top)
        if git("cat-file", "-t", f"refs/tags/{tag}", cwd=top) != "tag" or at != head:
            raise ReleaseError(f"tag {tag} already exists at {at[:12]}: publish tags HEAD ({head[:12]}) only")
        return f"tag {tag}: exists at HEAD, kept"
    branch = git("branch", "--show-current", cwd=top)
    if branch != MAIN:
        raise ReleaseError(f"on {branch or 'a detached HEAD'}, not {MAIN}: publish runs on {MAIN}")
    git("fetch", "-q", "--no-tags", "origin", MAIN, cwd=top)
    upstream = git("rev-parse", "FETCH_HEAD", cwd=top)
    if head != upstream:
        behind = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head, upstream], cwd=top, capture_output=True, check=False
        )
        if behind.returncode == 0:
            raise ReleaseError(f"{MAIN} is behind origin/{MAIN}: `git pull --ff-only`, then rerun")
        raise ReleaseError(f"{MAIN} is not origin/{MAIN} (ahead or diverged): publish runs at the merged commit")
    if git("ls-remote", "--tags", "origin", f"refs/tags/{tag}", cwd=top):
        raise ReleaseError(f"tag {tag} already exists on origin and not here: fetch it and look before rerunning")
    git("tag", "-a", tag, "-m", tag, cwd=top)
    return f"tag {tag}: created at {head[:12]}"


def publish(version: str, top: Path, files: list[str], repo: str) -> list[str]:
    """Tag HEAD `vX.Y.Z`, push the tag, and publish its GitHub Release; each step skipped when already done."""
    _check_version(version)
    tag = f"v{version}"
    found = {name: read_version(name, _at_head(top, name) or "") for name in files}
    if any(v != version for v in found.values()):
        stated = ", ".join(f"{n} {v or 'none'}" for n, v in found.items())
        raise ReleaseError(f"the release files at HEAD do not all state {version}: {stated}")
    notes = section(_at_head(top, CHANGELOG) or "", version)
    if notes is None:
        raise ReleaseError(f"{CHANGELOG} at HEAD has no `## {tag} (YYYY-MM-DD)` section with notes")
    head = git("rev-parse", "HEAD", cwd=top)
    lines = [_ensure_tag(top, tag, head)]
    local = git("rev-parse", f"refs/tags/{tag}", cwd=top)
    remote = git("ls-remote", "--tags", "origin", f"refs/tags/{tag}", cwd=top).split("\t")[0]
    if not remote:
        git("push", "-q", "origin", f"refs/tags/{tag}", cwd=top)
        lines.append(f"tag {tag}: pushed")
    elif remote != local:
        raise ReleaseError(f"origin's tag {tag} is {remote[:12]}, not this one ({local[:12]}): look before rerunning")
    else:
        lines.append(f"tag {tag}: already on origin")
    if url := _release_url(repo, tag):
        lines.append(f"release {tag}: already published, {url}")
        return lines
    args = ("release", "create", tag, "-R", repo, "--verify-tag", "--title", tag, "--notes-file", "-", "--latest")
    url = board.gh(*args, input=notes + "\n").strip()
    lines.append(f"release {tag}: published, {url}")
    return lines
