"""`dev release prepare|publish` (#605). git is real (a bare origin and the lead's clone on main); GitHub is faked at
subprocess level, so the real board.gh, its retries and its `release create` recheck run."""

import random
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, cli, config, release
from tests.test_merge_hygiene import REAL_RUN, commit, out, sh

ROOT = Path(__file__).resolve().parents[1]
FILES = ["pyproject.toml", "plugins/harness-ops/.claude-plugin/plugin.json", "copier.yml"]
TLS = "Post https://api.github.com/repos/o/r/releases: net/http: TLS handshake timeout"
RELEASE = "https://github.com/o/r/releases/tag/v0.1.18"
NOTES = "The summary.\n\n### Added\n- `dev release`. (#605)"


def changelog(version: str, notes: str = NOTES) -> str:
    return f"# Changelog\n\nIntro.\n\n## v{version} (2026-11-19)\n\n{notes}\n\n## v0.1.17 (2026-11-01)\n\nOlder.\n"


def release_files(version: str) -> dict[str, str]:
    """The harness's own three release files, as main has them, set to `version`."""
    return {n: release.write_version(n, (ROOT / n).read_text(), version) for n in FILES}


class FakeGitHub:
    """`gh` for releases over the origin's tags: `drop[i]` = (kind, stderr) fails the i-th gh call as a dropped
    connection, before ("before") or after ("after") GitHub acted; `refuse[i]` fails it plainly."""

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.calls: list[list[str]] = []
        self.releases: dict[str, dict] = {}
        self.drop: dict[int, tuple[str, str]] = {}
        self.refuse: dict[int, str] = {}

    def run(self, argv, input=None, **kw):
        if argv[0] != "gh":
            return REAL_RUN(argv, input=input, **kw)
        args = list(argv[1:])
        i = len(self.calls)
        self.calls.append(args)
        if i in self.refuse:
            return subprocess.CompletedProcess(argv, 1, "", self.refuse[i])
        how, err = self.drop.get(i, ("", ""))
        if how == "before":
            return subprocess.CompletedProcess(argv, 1, "", err)
        code, stdout, stderr = self.answer(args, input)
        if how == "after":
            return subprocess.CompletedProcess(argv, 1, "", err)
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    def answer(self, a: list[str], input: str | None) -> tuple[int, str, str]:
        if a[0] == "api" and "/releases/tags/" in a[1]:
            tag = a[1].rsplit("/", 1)[1]
            if tag not in self.releases:
                return 1, "", "gh: Not Found (HTTP 404)"
            return 0, self.releases[tag]["url"] + "\n", ""
        if a[:2] == ["release", "create"]:
            tag = a[2]
            if "--verify-tag" in a and not out(self.origin, "git", "tag", "--list", tag):
                return 1, "", f"tag {tag} doesn't exist in the repo, aborting due to --verify-tag flag"
            if tag in self.releases:
                return 1, "", "HTTP 422: Validation Failed (already_exists)"
            url = f"https://github.com/o/r/releases/tag/{tag}"
            self.releases[tag] = {"url": url, "notes": input, "args": a}
            return 0, url + "\n", ""
        raise AssertionError(f"unexpected gh call {a}")

    def created(self) -> int:
        return sum(c[:2] == ["release", "create"] for c in self.calls)


@pytest.fixture
def world(tmp_path, monkeypatch):
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh(tmp_path, "git", "clone", "-q", str(origin), str(clone))
    sh(clone, "git", "config", "user.email", "t@t")
    sh(clone, "git", "config", "user.name", "t")
    commit(clone, {**release_files("0.1.18"), "CHANGELOG.md": changelog("0.1.18")}, "chore(release): v0.1.18")
    sh(clone, "git", "push", "-q", "origin", "main")
    gh = FakeGitHub(origin)
    monkeypatch.setattr(board.subprocess, "run", gh.run)
    monkeypatch.setattr(board, "root", lambda: clone)
    monkeypatch.setattr(board, "GH_SLEEP", lambda s: None)
    monkeypatch.setattr(board, "GH_RNG", random.Random(7))
    gh.clone = clone
    return gh


def publish(world: FakeGitHub, version: str = "0.1.18") -> list[str]:
    return release.publish(version, world.clone, FILES, "o/r")


def tags(repo: Path) -> list[str]:
    return out(repo, "git", "tag", "--list").split()


# ---- the CHANGELOG section and the version files ----


def test_section_is_the_body_up_to_the_next_release():
    assert release.section(changelog("0.1.18"), "0.1.18") == NOTES
    assert release.section(changelog("0.1.18"), "0.1.17") == "Older."
    assert release.section(changelog("0.1.18"), "0.1.19") is None
    assert release.section(changelog("0.1.18", notes=""), "0.1.18") is None  # a heading alone is no notes
    assert release.section("## v0.1.18\n\nno date\n", "0.1.18") is None
    assert release.section(changelog("0.1.18"), "0.1.1") is None  # 0.1.1 is not a prefix match of 0.1.17


@given(st.from_regex(release.VERSION, fullmatch=True))
def test_every_release_file_takes_any_version_and_changes_only_its_version(version):
    for name in FILES:
        before = (ROOT / name).read_text()
        after = release.write_version(name, before, version)
        assert release.read_version(name, after) == version
        changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
        assert len(changed) <= 1, name


def test_a_version_nested_before_the_top_level_one_is_refused_not_misplaced():
    text = '{"engines": {"version": "1"}, "version": "0.1.0"}'
    with pytest.raises(release.ReleaseError, match="in place"):
        release.write_version("plugin.json", text, "0.2.0")


def test_a_pyproject_writes_the_project_version_not_another_tables():
    text = '[tool.x]\nversion = "9"\n\n[project]\nname = "p"\nversion = "0.1.0"\n\n[tool.y]\nversion = "8"\n'
    after = release.write_version("pyproject.toml", text, "0.2.0")
    assert after == text.replace('version = "0.1.0"', 'version = "0.2.0"')


# ---- prepare ----


def test_prepare_writes_the_three_versions_and_relocks(tmp_path):
    for name, text in {**release_files("0.1.17"), "CHANGELOG.md": changelog("0.1.18")}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    locked: list[Path] = []
    lines = release.prepare("0.1.18", tmp_path, FILES, lock=locked.append)
    assert locked == [tmp_path]
    assert {release.read_version(n, (tmp_path / n).read_text()) for n in FILES} == {"0.1.18"}
    assert lines[0] == "pyproject.toml: 0.1.17 -> 0.1.18" and lines[-1] == "uv.lock: relocked"


@pytest.mark.parametrize("log", [None, changelog("0.1.17"), changelog("0.1.18", notes="")])
def test_prepare_without_the_changelog_section_refuses_and_writes_nothing(tmp_path, log):
    before = release_files("0.1.17")
    for name, text in before.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    if log is not None:
        (tmp_path / "CHANGELOG.md").write_text(log)
    locked: list[Path] = []
    with pytest.raises(release.ReleaseError, match=r"no `## v0\.1\.18 \(YYYY-MM-DD\)` section"):
        release.prepare("0.1.18", tmp_path, FILES, lock=locked.append)
    assert locked == [] and all((tmp_path / n).read_text() == t for n, t in before.items())


def test_prepare_refuses_a_missing_release_file_before_writing_any(tmp_path):
    (tmp_path / "pyproject.toml").write_text(release_files("0.1.17")["pyproject.toml"])
    (tmp_path / "CHANGELOG.md").write_text(changelog("0.1.18"))
    with pytest.raises(release.ReleaseError, match=r"copier\.yml"):
        release.prepare("0.1.18", tmp_path, ["pyproject.toml", "copier.yml"], lock=lambda _: None)
    assert release.read_version("pyproject.toml", (tmp_path / "pyproject.toml").read_text()) == "0.1.17"


@pytest.mark.parametrize("version", ["v0.1.18", "0.1", "0.1.18-rc1", ""])
def test_a_version_that_is_not_x_y_z_is_refused(tmp_path, version):
    with pytest.raises(release.ReleaseError, match="not a version"):
        release.prepare(version, tmp_path, FILES, lock=lambda _: None)


# ---- publish ----


def test_publish_tags_pushes_and_releases_the_changelog_section(world):
    lines = publish(world)
    head = out(world.clone, "git", "rev-parse", "HEAD")
    assert out(world.clone, "git", "cat-file", "-t", "v0.1.18") == "tag"  # annotated, as the lead's were
    assert out(world.origin, "git", "rev-parse", "v0.1.18^{commit}") == head
    made = world.releases["v0.1.18"]
    assert made["notes"].strip() == NOTES
    assert {"--latest", "--verify-tag"} <= set(made["args"]) and "-R" in made["args"]
    assert lines[-1] == "release v0.1.18: published, https://github.com/o/r/releases/tag/v0.1.18"


def test_a_rerun_after_success_creates_nothing(world):
    publish(world)
    lines = publish(world)
    assert world.created() == 1 and lines[0] == "tag v0.1.18: exists at HEAD, kept"
    assert lines[1:] == ["tag v0.1.18: already on origin", f"release v0.1.18: already published, {RELEASE}"]


def test_a_rerun_after_a_failed_push_pushes_and_releases(world):
    sh(world.clone, "git", "tag", "-a", "v0.1.18", "-m", "v0.1.18")  # the first run tagged, then its push failed
    lines = publish(world)
    assert lines[:2] == ["tag v0.1.18: exists at HEAD, kept", "tag v0.1.18: pushed"]
    assert "v0.1.18" in tags(world.origin) and world.created() == 1


def test_a_rerun_after_a_refused_release_creates_it_once(world):
    world.refuse = {1: "HTTP 401: Bad credentials"}  # gh call 0 looks the release up, 1 creates it
    with pytest.raises(board.BoardError, match="Bad credentials"):
        publish(world)
    assert "v0.1.18" in tags(world.origin) and world.releases == {}
    lines = publish(world)
    assert lines[1] == "tag v0.1.18: already on origin" and list(world.releases) == ["v0.1.18"]


@pytest.mark.parametrize("how", ["before", "after"])
def test_a_dropped_connection_on_create_never_makes_a_second_release(world, how):
    world.drop = {1: (how, TLS)}
    lines = publish(world)
    assert len(world.releases) == 1 and lines[-1] == f"release v0.1.18: published, {RELEASE}"
    assert world.created() == (1 if how == "after" else 2)  # "after": the recheck found it, nothing resent


def test_the_recheck_that_cannot_read_github_leaves_the_outcome_unknown(world):
    world.drop = {1: ("after", TLS)}
    world.refuse = {2: "HTTP 401: Bad credentials"}  # the recheck's lookup
    with pytest.raises(board.BoardError, match="outcome is unknown"):
        publish(world)
    assert world.created() == 1


def test_publish_refuses_when_the_versions_disagree(world):
    files = release_files("0.1.18") | {"copier.yml": release_files("0.1.17")["copier.yml"]}
    commit(world.clone, files, "half bumped")
    sh(world.clone, "git", "push", "-q", "origin", "main")
    with pytest.raises(release.ReleaseError, match=r"copier\.yml 0\.1\.17"):
        publish(world)
    assert tags(world.clone) == [] and world.calls == []


def test_publish_reads_the_committed_files_not_the_working_tree(world):
    (world.clone / "copier.yml").write_text(release_files("0.1.17")["copier.yml"])  # an uncommitted edit
    publish(world)
    assert world.created() == 1


def test_publish_refuses_without_the_changelog_section(world):
    commit(world.clone, {"CHANGELOG.md": changelog("0.1.17")}, "lost the section")
    sh(world.clone, "git", "push", "-q", "origin", "main")
    with pytest.raises(release.ReleaseError, match=r"no `## v0\.1\.18"):
        publish(world)
    assert tags(world.clone) == []


def test_publish_refuses_when_main_is_behind_origin(world, tmp_path):
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(world.origin), str(other))
    sh(other, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "later")
    sh(other, "git", "push", "-q", "origin", "main")
    with pytest.raises(release.ReleaseError, match="behind origin/main"):
        publish(world)
    assert tags(world.clone) == [] and tags(world.origin) == [] and world.calls == []


def test_publish_refuses_a_commit_origin_does_not_have(world):
    sh(world.clone, "git", "commit", "-q", "--allow-empty", "-m", "local only")
    with pytest.raises(release.ReleaseError, match="ahead or diverged"):
        publish(world)
    assert tags(world.clone) == []


def test_publish_refuses_off_main(world):
    sh(world.clone, "git", "switch", "-q", "-c", "605-x")
    with pytest.raises(release.ReleaseError, match=r"on 605-x, not main"):
        publish(world)
    assert tags(world.clone) == []


@pytest.mark.parametrize("kind", ["elsewhere", "lightweight"])
def test_publish_refuses_a_tag_that_exists_but_is_not_this_runs(world, kind):
    if kind == "elsewhere":
        sh(world.clone, "git", "tag", "-a", "v0.1.18", "-m", "v0.1.18", "HEAD~0")
        commit(world.clone, {"README.md": "x\n"}, "after the tag")
        sh(world.clone, "git", "push", "-q", "origin", "main")
    else:
        sh(world.clone, "git", "tag", "v0.1.18")
    with pytest.raises(release.ReleaseError, match=r"tag v0\.1\.18 already exists"):
        publish(world)
    assert tags(world.origin) == [] and world.calls == []


def test_publish_refuses_a_tag_already_on_origin_but_not_here(world, tmp_path):
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(world.origin), str(other))
    sh(other, "git", "-c", "user.email=t@t", "-c", "user.name=t", "tag", "-a", "v0.1.18", "-m", "theirs")
    sh(other, "git", "push", "-q", "origin", "v0.1.18")
    with pytest.raises(release.ReleaseError, match="already exists on origin"):
        publish(world)
    assert tags(world.clone) == [] and world.calls == []


def test_a_local_tag_unlike_origins_is_refused_before_any_release(world, tmp_path):
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(world.origin), str(other))
    sh(other, "git", "-c", "user.email=t@t", "-c", "user.name=t", "tag", "-a", "v0.1.18", "-m", "theirs")
    sh(other, "git", "push", "-q", "origin", "v0.1.18")
    sh(world.clone, "git", "tag", "-a", "v0.1.18", "-m", "v0.1.18")  # same commit, another tag object
    with pytest.raises(release.ReleaseError, match=r"origin's tag v0\.1\.18"):
        publish(world)
    assert world.calls == []


# ---- the command ----


def test_the_command_prints_its_steps_and_a_refusal_as_result(world, monkeypatch, capsys):
    monkeypatch.setattr(cli, "root", lambda: world.clone)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    assert cli.run(cli.build().parse_args(["release", "publish", "0.1.18"])) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "RESULT: published v0.1.18"
    assert cli.run(cli.build().parse_args(["release", "prepare", "0.1.19"])) == 1
    assert "RESULT: refused: CHANGELOG.md has no `## v0.1.19" in capsys.readouterr().out


def test_the_harness_names_its_three_release_files_and_they_agree():
    assert config.get().release_files == FILES
    assert len({release.read_version(n, (ROOT / n).read_text()) for n in FILES}) == 1


def test_the_lead_skill_releases_through_these_commands():
    """#605: the lead's release step is the two commands, not hand-run `git tag` / `gh release create`."""
    lead = (ROOT / "plugins/harness-ops/skills/lead/SKILL.md").read_text()
    assert "`dev release prepare X.Y.Z`" in lead and "`dev release publish X.Y.Z`" in lead
    assert "gh release" not in lead and "git tag" not in lead
