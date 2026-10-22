"""`dev doctor` warns when the Copier template or the plugin pin is behind the latest release, fails when the installed
plugin is not the pinned release's build (#296), and names each research API key as found or missing, never its value.
`dev setup` re-pins a skewed plugin."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import lit, setup, workflow

SRC = "https://github.com/Marius-Juston/agent-harness"
PIN = {"harness-ops@agent-harness": "Marius-Juston/agent-harness@v0.1.1", "astral@astral-sh": "astral-sh/x"}
HERE = Path("/p")
PLUGIN_SRC = Path(__file__).resolve().parents[1] / "plugins/harness-ops"


def tags(*names):
    return lambda url: list(names) if url == SRC else []


def installed(version, path="/p", install_path=""):
    record = {"id": "harness-ops@agent-harness", "version": version, "scope": "project", "projectPath": path}
    return [record | ({"installPath": str(install_path)} if install_path else {})]


def build(tmp_path, name: str, version: str = "0.1.1", agent: str = "body") -> Path:
    """A plugin tree: a manifest with `version` and one agent file."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "harness-ops", "version": version}))
    (root / "agents").mkdir()
    (root / "agents/builder.md").write_text(agent)
    return root


def test_latest_release_is_the_highest_semver_tag():
    assert setup.latest_release(["v0.1.0", "v0.10.0", "v0.9.3", "nightly", "v1.0.0-rc1"]) == "v0.10.0"
    assert setup.latest_release(["x"]) is None


@given(st.lists(st.tuples(st.integers(0, 99), st.integers(0, 99), st.integers(0, 99)), min_size=1))
def test_latest_release_matches_tuple_order(versions):
    names = [f"v{a}.{b}.{c}" for a, b, c in versions]
    assert setup.latest_release(names) == "v{}.{}.{}".format(*max(versions))


def test_repo_url_forms():
    assert setup.repo_url("gh:Marius-Juston/agent-harness") == SRC
    assert setup.repo_url("Marius-Juston/agent-harness") == SRC
    assert setup.repo_url(SRC + ".git") == SRC + ".git"
    assert setup.repo_url("/abs/checkout") == "/abs/checkout"


def test_up_to_date_has_no_warning():
    answers = {"_commit": "v0.1.1", "_src_path": "gh:Marius-Juston/agent-harness"}
    assert setup.staleness(answers, PIN, tags("v0.1.0", "v0.1.1")) == []


def test_template_behind_warns_with_the_fix():
    answers = {"_commit": "v0.1.0", "_src_path": "gh:Marius-Juston/agent-harness"}
    out = setup.staleness(answers, {}, tags("v0.1.0", "v0.1.1"))
    assert len(out) == 1 and "v0.1.0" in out[0] and "v0.1.1" in out[0] and "copier update" in out[0]


def test_plugin_pin_behind_warns():
    out = setup.staleness({}, PIN, tags("v0.1.1", "v0.2.0"))
    assert len(out) == 1 and "harness-ops@agent-harness" in out[0] and "v0.2.0" in out[0]


def test_installed_plugin_older_than_pin_is_a_failure(tmp_path):
    pinned = build(tmp_path, "pinned")
    out = setup.build_skew(PIN, installed("0.1.0"), {str(HERE)}, pinned)
    assert len(out) == 1 and "installed 0.1.0" in out[0] and "v0.1.1" in out[0]


def test_same_version_string_with_other_content_is_a_failure(tmp_path):
    """#296: the marketplace installed main's head as "0.1.9"; only the content tells the builds apart."""
    pinned, head = build(tmp_path, "pinned"), build(tmp_path, "head", agent="newer body")
    out = setup.build_skew(PIN, installed("0.1.1", install_path=head), {str(HERE)}, pinned)
    assert len(out) == 1 and "content" in out[0] and "v0.1.1" in out[0]
    assert setup.build_skew(PIN, installed("0.1.1", install_path=build(tmp_path, "same")), {str(HERE)}, pinned) == []


def test_another_projects_install_is_ignored_and_an_unpinned_plugin_never_fails(tmp_path):
    pinned = build(tmp_path, "pinned")
    assert setup.build_skew(PIN, installed("0.1.0", "/other"), {str(HERE)}, pinned) == []
    unpinned = {"harness-ops@agent-harness": "Marius-Juston/agent-harness"}
    assert setup.build_skew(unpinned, installed("0.1.0"), {str(HERE)}, pinned) == []


def test_digest_skips_claude_code_markers_and_bytecode_but_sees_paths_and_content(tmp_path):
    root = build(tmp_path, "p")
    before = setup.plugin_digest(root)
    (root / ".in_use").mkdir()
    (root / ".in_use/123").write_text("pid")
    (root / ".orphaned_at").write_text("1760000000")
    (root / "scripts/__pycache__").mkdir(parents=True)
    (root / "scripts/__pycache__/x.pyc").write_bytes(b"\0")
    assert setup.plugin_digest(root) == before
    (root / "agents/builder.md").rename(root / "agents/tester.md")
    assert setup.plugin_digest(root) != before


def test_the_pinned_tree_of_a_source_checkout_is_its_plugin():
    root = setup.pinned_plugin_root()
    assert root is not None and setup.plugin_digest(root) == setup.plugin_digest(PLUGIN_SRC)


def test_unreachable_remote_or_no_answers_never_warns():
    answers = {"_commit": "v0.1.0", "_src_path": "gh:Marius-Juston/agent-harness"}
    assert setup.staleness(answers, PIN, lambda url: []) == []
    assert setup.staleness({}, {}, tags("v9.9.9")) == []


def test_doctor_names_each_key_found_or_missing_never_its_value_and_points_to_the_docs():
    lines = setup.key_lines({"OPENALEX_API_KEY": "secrets file", "S2_API_KEY": "missing"}, "")
    assert lines[0] == (True, "OPENALEX_API_KEY found (secrets file)", "")
    assert lines[1][0] is None and "S2_API_KEY missing" in lines[1][1] and "Research API keys" in lines[1][2]
    bad = setup.key_lines({"OPENALEX_API_KEY": "missing", "S2_API_KEY": "missing"}, "f is readable by group")
    assert bad[-1][0] is False and "Research API keys" in bad[-1][2]


def test_doctor_prints_key_origins_but_not_values(tmp_path, monkeypatch, capsys):
    p = tmp_path / "keys"
    p.write_text("export S2_API_KEY=fake-s2-000\n")
    p.chmod(0o600)
    monkeypatch.setenv("DEVKIT_SECRETS_FILE", str(p))
    monkeypatch.setenv("OPENALEX_API_KEY", "fake-oa-000")
    monkeypatch.delenv("S2_API_KEY", raising=False)
    for line in setup.key_lines(*lit.key_report(dict(os.environ))):
        print(line)
    out = capsys.readouterr().out
    assert "OPENALEX_API_KEY found (environment)" in out and "S2_API_KEY found (secrets file)" in out
    assert "fake-" not in out


FAKE_CLAUDE = """#!{python}
# A stub `claude` CLI: install records in $FAKE_CLAUDE_STATE, every call logged there with its working directory. A
# call starting with $FAKE_CLAUDE_FAIL fails, prints $FAKE_CLAUDE_FAIL_MSG and changes nothing. Removing a marketplace
# at user scope leaves the project installs (the projects' settings still declare it); removing it in every scope
# drops them all. A project-scope uninstall fails, as the real one does, where no install is recorded for the cwd.
import json, os, sys
path = os.environ["FAKE_CLAUDE_STATE"]
with open(path) as f:
    state = json.load(f)
args = sys.argv[1:]
state["calls"].append(args)
state.setdefault("cwds", []).append(os.getcwd())
fail = os.environ.get("FAKE_CLAUDE_FAIL", "").split()
here = [r for r in state["installed"] if r.get("projectPath") == os.getcwd()]
if (fail and args[: len(fail)] == fail) or (args[:2] == ["plugin", "uninstall"] and not here):
    print(os.environ.get("FAKE_CLAUDE_FAIL_MSG", "is not installed in project scope"), file=sys.stderr)
    with open(path, "w") as f:
        json.dump(state, f)
    sys.exit(1)
if args[:3] == ["plugin", "list", "--json"]:
    print(json.dumps(state["installed"]))
elif args[:2] == ["plugin", "list"]:
    print("\\n".join(r["id"] for r in state["installed"]))
elif args[:3] == ["plugin", "marketplace", "remove"] and "--scope" not in args:
    state["installed"] = [r for r in state["installed"] if not r["id"].endswith("@" + args[3])]
elif args[:2] == ["plugin", "uninstall"]:
    state["installed"] = [r for r in state["installed"] if r.get("projectPath") != os.getcwd()]
elif args[:2] == ["plugin", "install"] and state["on_install"] not in state["installed"]:
    state["installed"].append(state["on_install"])
with open(path, "w") as f:
    json.dump(state, f)
"""
RECORD = {"id": "harness-ops@agent-harness", "version": "0.1.1", "scope": "project"}


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=cwd, check=True, capture_output=True
    )


@pytest.fixture
def stub_project(tmp_path, monkeypatch):
    """A project pinned to v0.1.1 of a local remote, whose doctor checks all pass but the plugin build's, with a stub
    `claude` on PATH (and `gh`, `uv`, `uvx` stubs that succeed, for setup's other steps). `install(tree)` sets the
    recorded install; a re-pin's `plugin install` records `pinned`."""
    project, pinned, remote = tmp_path / "proj", build(tmp_path, "pinned"), tmp_path / "remote"
    (project / ".venv").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    remote.mkdir()
    _git("init", "-q", cwd=remote)
    _git("commit", "-q", "--allow-empty", "-m", "release", cwd=remote)
    _git("tag", "v0.1.1", cwd=remote)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ok = "#!/bin/sh\nexit 0\n"
    for name, body in (("claude", FAKE_CLAUDE.format(python=sys.executable)), ("gh", ok), ("uv", ok), ("uvx", ok)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "claude.json"
    mine = RECORD | {"projectPath": str(project)}

    def install(tree: Path, others: tuple[dict, ...] = ()) -> None:
        version = json.loads((tree / ".claude-plugin/plugin.json").read_text())["version"]
        records = [mine | {"installPath": str(tree), "version": version}, *others]
        state.write_text(
            json.dumps({"calls": [], "installed": records, "on_install": mine | {"installPath": str(pinned)}})
        )

    install(build(tmp_path, "head", agent="newer body"))  # main's head under the pinned version string
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(state))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "user"))  # user settings: never the owner's
    monkeypatch.setattr(setup, "root", lambda: project)
    monkeypatch.setattr(setup, "git", lambda *a, **k: str(project / ".git") if "--git-common-dir" in a else "true")
    monkeypatch.setattr(setup.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(setup, "_trusted", lambda: True)
    monkeypatch.setattr(setup.lit, "key_report", lambda env: ({}, ""))
    monkeypatch.setattr(workflow, "quota_reading", lambda: (10, 95))
    monkeypatch.setattr(setup, "remote_tags", lambda url: [])
    plugins = {"harness-ops@agent-harness": f"{remote}@v0.1.1"}
    monkeypatch.setattr(setup.config, "get", lambda: SimpleNamespace(plugins=plugins))
    monkeypatch.setattr(setup, "pinned_plugin_root", lambda: pinned)
    head = tmp_path / "head"
    return SimpleNamespace(
        state=state, install=install, pinned=pinned, head=head, plugins=plugins, remote=remote, project=project
    )


def _changes(stub) -> list[list[str]]:
    """The claude calls that change something, in order."""
    reads = (["plugin", "list"], ["mcp", "list"])
    return [c for c in json.loads(stub.state.read_text())["calls"] if c[:2] not in reads]


def _installs(stub) -> list[dict]:
    return json.loads(stub.state.read_text())["installed"]


OTHER = RECORD | {"projectPath": "/elsewhere"}
USER = {"id": "harness-ops@agent-harness", "version": "0.1.1", "scope": "user"}


def test_doctor_fails_on_a_skewed_build_and_passes_on_the_pinned_one(stub_project, capsys):
    assert setup.doctor() == 1
    out = capsys.readouterr().out
    assert "FIX  installed plugin is the pinned release's build" in out and "content" in out and "dev setup" in out
    stub_project.install(stub_project.pinned)
    assert setup.doctor() == 0
    assert "all good" in capsys.readouterr().out


def test_doctor_fails_on_another_version_string(stub_project, tmp_path, capsys):
    stub_project.install(build(tmp_path, "old", version="0.1.0"))
    assert setup.doctor() == 1 and "installed 0.1.0, pinned v0.1.1" in capsys.readouterr().out


def test_doctor_plugin_installed_only_counts_this_projects_or_a_user_scope_install(stub_project, capsys):
    """#3: `claude plugin list` names every install on the machine; only this project's or a user-scope one loads."""
    elsewhere = OTHER | {"installPath": str(stub_project.pinned)}
    stub_project.state.write_text(json.dumps({"calls": [], "installed": [elsewhere], "on_install": elsewhere}))
    assert setup.doctor() == 1
    assert "FIX  Claude Code plugin harness-ops@agent-harness installed" in capsys.readouterr().out
    user = USER | {"installPath": str(stub_project.pinned)}
    stub_project.state.write_text(json.dumps({"calls": [], "installed": [elsewhere, user], "on_install": user}))
    assert setup.doctor() == 0
    assert "ok   Claude Code plugin harness-ops@agent-harness installed" in capsys.readouterr().out


def test_setup_repins_at_user_scope_keeping_other_projects_installs_then_doctor_passes(stub_project, capsys):
    stub_project.install(stub_project.head, others=(OTHER,))
    assert setup.setup() == 0
    assert _changes(stub_project)[-4:] == [
        ["plugin", "marketplace", "remove", "agent-harness", "--scope", "user"],
        ["plugin", "marketplace", "add", f"{stub_project.remote}@v0.1.1"],
        ["plugin", "uninstall", "harness-ops@agent-harness", "--scope", "project"],
        ["plugin", "install", "harness-ops@agent-harness", "--scope", "project"],
    ]
    assert OTHER in _installs(stub_project)
    out = capsys.readouterr().out
    assert "uninstalled from" not in out and "still not the pinned build" not in out and "all good" in out


def test_repin_all_removes_everywhere_and_names_every_install_it_removed(stub_project, capsys):
    stub_project.install(stub_project.head, others=(OTHER, USER))
    assert setup.setup(repin_all=True) == 0
    assert ["plugin", "marketplace", "remove", "agent-harness"] in _changes(stub_project)
    out = capsys.readouterr().out
    assert "uninstalled from /elsewhere, the user scope" in out and "all good" in out


def test_repin_changes_nothing_when_the_pinned_tag_is_unreachable(stub_project, monkeypatch, capsys):
    plugins = {"harness-ops@agent-harness": f"{stub_project.remote}@v9.9.9"}
    monkeypatch.setattr(setup.config, "get", lambda: SimpleNamespace(plugins=plugins))
    assert setup.repin_skewed()
    assert _changes(stub_project) == [] and _installs(stub_project)[0]["installPath"] == str(stub_project.head)
    assert "v9.9.9 not reachable" in capsys.readouterr().out


def test_repin_stops_at_a_failed_add_and_leaves_the_install(stub_project, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "plugin marketplace add")
    still = setup.repin_skewed()
    assert still and "stopped" in still[-1]
    assert [c[:3] for c in _changes(stub_project)] == [
        ["plugin", "marketplace", "remove"],
        ["plugin", "marketplace", "add"],
    ]
    assert _installs(stub_project)[0]["installPath"] == str(stub_project.head)
    assert "FAIL plugin marketplace add" in capsys.readouterr().out


def test_repin_skips_a_user_scope_remove_when_only_project_settings_declare_the_marketplace(
    stub_project, monkeypatch, capsys
):
    """#310 review: after a trust prompt registered the marketplace from the project's settings, the user-scope remove
    exits 1 "not declared in user settings"; `add` still re-points it, and other projects keep their installs."""
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "plugin marketplace remove agent-harness --scope user")
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MSG", 'Marketplace "agent-harness" is not declared in user settings')
    stub_project.install(stub_project.head, others=(OTHER,))
    assert setup.setup() == 0
    assert [c[:3] for c in _changes(stub_project)[-3:]] == [
        ["plugin", "marketplace", "add"],
        ["plugin", "uninstall", "harness-ops@agent-harness"],
        ["plugin", "install", "harness-ops@agent-harness"],
    ]
    assert OTHER in _installs(stub_project)
    out = capsys.readouterr().out
    assert "skip plugin marketplace remove" in out and "--repin-all" not in out and "all good" in out


def test_repin_from_a_worktree_runs_in_the_main_checkout_and_keeps_both_settings(stub_project, tmp_path, monkeypatch):
    """#310 review: Claude Code records the project install by the main checkout, so a project-scope uninstall from a
    worktree finds nothing; the re-pin runs its commands in the main checkout and guards both checkouts' settings."""
    worktree = tmp_path / "wt"
    (worktree / ".claude").mkdir(parents=True)
    for d in (worktree, stub_project.project):
        (d / ".claude").mkdir(exist_ok=True)
        (d / ".claude/settings.json").write_text('{"extraKnownMarketplaces": {"agent-harness": {}}}\n')
    monkeypatch.setattr(setup, "root", lambda: worktree)
    assert setup.repin_skewed() == []
    state = json.loads(stub_project.state.read_text())
    repin = [cwd for call, cwd in zip(state["calls"], state["cwds"], strict=True) if call[:2] != ["plugin", "list"]]
    assert repin and set(repin) == {str(stub_project.project)}
    for d in (worktree, stub_project.project):
        assert "extraKnownMarketplaces" in json.loads((d / ".claude/settings.json").read_text())


def test_setup_names_the_failed_step_when_it_suggests_repin_all(stub_project, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "plugin install")
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MSG", "network error")
    assert setup.setup() == 1
    assert "`plugin install harness-ops@agent-harness --scope project` failed; last resort" in capsys.readouterr().out


CLASH = (
    'Cannot add marketplace "agent-harness": its source doesn\'t match its extraKnownMarketplaces entry in user or '
    "managed settings"
)


def _register(tmp_path: Path, source: dict) -> None:
    """User settings that register the agent-harness marketplace at `source`, as another project's setup leaves them."""
    (tmp_path / "user").mkdir(exist_ok=True)
    settings = {"extraKnownMarketplaces": {"agent-harness": {"source": source}}}
    (tmp_path / "user/settings.json").write_text(json.dumps(settings))


@pytest.mark.parametrize("pin", ["", "@v0.1.1"])
def test_setup_warns_naming_both_refs_when_another_project_registered_the_same_repo(
    stub_project, tmp_path, monkeypatch, capsys, pin
):
    """#327: another project's setup registered this repository at v0.1.10; `marketplace add` refuses this project's
    source (unpinned, as in agent-harness itself, or pinned elsewhere). That is a WARN naming both refs, never a FAIL."""
    plugins = {"harness-ops@agent-harness": f"{stub_project.remote}{pin}"}
    monkeypatch.setattr(setup.config, "get", lambda: SimpleNamespace(plugins=plugins))
    _register(tmp_path, {"source": "git", "url": f"{stub_project.remote}.git", "ref": "v0.1.10"})
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "plugin marketplace add")
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MSG", CLASH)
    stub_project.install(stub_project.pinned)
    setup.run_steps(setup.plugin_steps())
    out = capsys.readouterr().out
    assert "FAIL" not in out
    warn = next(line for line in out.splitlines() if line.startswith("WARN plugin marketplace"))
    assert "v0.1.10" in warn and f"pins {pin.removeprefix('@') or 'no ref'}" in warn
    assert f"ok   plugin {next(iter(plugins))}" in out


@pytest.mark.parametrize(
    "registered",
    [
        {"source": "github", "repo": "someone/else", "ref": "v0.1.10"},
        {"source": "directory", "path": "/somewhere/else"},
    ],
)
def test_setup_still_fails_when_another_repository_holds_the_marketplace_name(
    stub_project, tmp_path, monkeypatch, capsys, registered
):
    _register(tmp_path, registered)
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "plugin marketplace add")
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MSG", CLASH)
    setup.run_steps(setup.plugin_steps())
    out = capsys.readouterr().out
    assert f"FAIL plugin marketplace {stub_project.remote}@v0.1.1" in out and "WARN" not in out


def test_a_failed_add_with_no_registry_entry_or_another_error_still_fails(stub_project, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "plugin marketplace add")
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MSG", CLASH)
    setup.run_steps(setup.plugin_steps())  # no user settings at all
    _register(tmp_path, {"source": "git", "url": str(stub_project.remote), "ref": "v0.1.10"})
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MSG", "network error")
    setup.run_steps(setup.plugin_steps())
    out = capsys.readouterr().out
    assert out.count("FAIL plugin marketplace") == 2 and "WARN" not in out


def test_clash_warning_matches_github_shorthand_and_url_forms():
    registered = {"source": "github", "repo": "Marius-Juston/agent-harness", "ref": "v0.1.10"}
    assert "at v0.1.10" in str(setup.clash_warning("Marius-Juston/agent-harness", registered))
    assert setup.clash_warning(f"{SRC}.git@v0.1.9", registered)
    assert setup.clash_warning("Marius-Juston/agent-harness@v0.1.10", registered) is None  # same ref: not this clash
    assert setup.clash_warning("someone/agent-harness", registered) is None
    assert setup.clash_warning("Marius-Juston/agent-harness", {}) is None


def test_settings_that_are_not_an_object_are_restored_without_raising(tmp_path, capsys):
    f = tmp_path / "settings.json"
    f.write_text("[]\n")
    saved = {f: f.read_bytes()}
    f.write_text('{"enabledPlugins": {"x@y": true}}')
    setup._keep_settings(saved)
    assert f.read_text() == "[]\n" and "not a JSON object" in capsys.readouterr().out


def test_settings_restore_keeps_non_ascii_text(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text('{\n  "note": "réglages ✓",\n  "extraKnownMarketplaces": {"m": {}}\n}\n', encoding="utf-8")
    saved = {f: f.read_bytes()}
    f.write_text('{"note": "réglages ✓"}', encoding="utf-8")
    setup._keep_settings(saved)
    text = f.read_text(encoding="utf-8")
    assert "réglages ✓" in text and text.endswith("}\n") and json.loads(text)["extraKnownMarketplaces"] == {"m": {}}


def test_setup_leaves_the_pinned_build_alone(stub_project):
    stub_project.install(stub_project.pinned)
    assert setup.setup() == 0
    assert not [c for c in _changes(stub_project) if "remove" in c]


def test_settings_are_restored_but_keep_the_enabled_plugins_an_install_added(tmp_path):
    f = tmp_path / "settings.json"
    declared = {"extraKnownMarketplaces": {"agent-harness": {"source": {"repo": "o/r", "ref": "v1"}}}, "x": 1}
    f.write_text(json.dumps(declared))
    saved = {f: f.read_bytes()}
    f.write_text(json.dumps({"x": 1, "enabledPlugins": {"harness-ops@agent-harness": True}}))
    setup._keep_settings(saved)
    assert json.loads(f.read_text()) == declared | {"enabledPlugins": {"harness-ops@agent-harness": True}}
    unchanged = f.read_bytes()
    setup._keep_settings({f: unchanged})
    assert f.read_bytes() == unchanged


def test_settings_with_reordered_keys_get_their_committed_bytes_back(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text('{\n  "b": 1,\n  "a": 2\n}\n')
    saved = {f: f.read_bytes()}
    f.write_text('{"a": 2, "b": 1}')
    setup._keep_settings(saved)
    assert f.read_bytes() == saved[f]


GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def _isolation_problem(tmp_path: Path) -> str | None:
    """Why running the real `claude` here could touch something outside the test's directory, or None."""
    if set_ := [k for k in GIT_REDIRECTS if k in os.environ]:
        return f"{', '.join(set_)} set"
    places = {
        "HOME": os.environ.get("HOME", "/"),
        "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR", "/"),
        "root()": setup.root(),
        "main checkout": setup._main_checkout(),
    }
    outside = [f"{k}={v}" for k, v in places.items() if not Path(v).resolve().is_relative_to(tmp_path.resolve())]
    return f"outside {tmp_path}: {', '.join(outside)}" if outside else None


def _project(path: Path, settings: dict | None = None) -> Path:
    """A git project, its settings (when given) committed."""
    path.mkdir()
    _git("init", "-q", "-b", "main", cwd=path)
    if settings is not None:
        (path / ".claude").mkdir()
        (path / ".claude/settings.json").write_text(json.dumps(settings))
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=path)
    return path


@pytest.fixture
def real_cli(tmp_path, monkeypatch):
    """The real `claude` CLI, offline, in a temporary HOME: the owner's ~/.claude and repositories are never touched.
    A git `insteadOf` serves github.com/fake/ (to git and to Claude Code) from a local marketplace repository whose
    tag v0.1.1 is the pinned release and whose main has moved on under the same version string. `claude(cwd, *args)`
    refuses to run unless `_isolation_problem` finds nothing."""
    for key in GIT_REDIRECTS:
        monkeypatch.delenv(key, raising=False)
    home, repo = tmp_path / "home", tmp_path / "src.git"
    for d in (home, repo / ".claude-plugin"):
        d.mkdir(parents=True)
    (home / ".gitconfig").write_text(f'[url "file://{tmp_path}/"]\n\tinsteadOf = https://github.com/fake/\n')
    for key, value in (("HOME", home), ("CLAUDE_CONFIG_DIR", home / ".claude"), ("CLAUDE_CODE_PLUGIN_PREFER_HTTPS", 1)):
        monkeypatch.setenv(key, str(value))
    catalog = {"name": "agent-harness", "owner": {"name": "x"}, "plugins": [{"name": "harness-ops", "source": "./p"}]}
    (repo / ".claude-plugin/marketplace.json").write_text(json.dumps(catalog))
    build(repo, "p", version="0.1.1", agent="tag body")
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "release", cwd=repo)
    _git("tag", "v0.1.1", cwd=repo)
    tag = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    pinned = tmp_path / "pinned"
    shutil.copytree(repo / "p", pinned)
    (repo / "p/agents/builder.md").write_text("main body")
    _git("commit", "-qam", "unreleased", cwd=repo)
    plugins = {"harness-ops@agent-harness": "fake/src@v0.1.1"}
    monkeypatch.setattr(setup.config, "get", lambda: SimpleNamespace(plugins=plugins))
    monkeypatch.setattr(setup, "pinned_plugin_root", lambda: pinned)

    def claude(cwd: Path, *args: str) -> None:
        if problem := _isolation_problem(tmp_path):
            pytest.fail(f"refusing to run the real claude CLI: {problem}")
        assert subprocess.run(["claude", *args], cwd=cwd, capture_output=True, check=False).returncode == 0, args

    def shas() -> dict[str, str]:
        records = json.loads((home / ".claude/plugins/installed_plugins.json").read_text())["plugins"]
        return {r["projectPath"]: r["gitCommitSha"] for r in records["harness-ops@agent-harness"]}

    return SimpleNamespace(tag=tag, pinned=pinned, plugins=plugins, claude=claude, shas=shas)


def test_the_real_cli_guard_refuses_a_decoy_git_dir(real_cli, tmp_path, tmp_path_factory, monkeypatch):
    """A GIT_DIR left in the environment would point git, and so the re-pin's main checkout, at another repository."""
    a = _project(tmp_path / "a")
    monkeypatch.setattr(setup, "root", lambda: a)
    assert _isolation_problem(tmp_path) is None
    decoy = _project(tmp_path_factory.mktemp("decoy") / "repo")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    assert "GIT_DIR" in str(_isolation_problem(tmp_path))
    with pytest.raises(pytest.fail.Exception, match="refusing"):
        real_cli.claude(tmp_path, "--version")
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.setattr(setup, "root", lambda: decoy)
    assert "outside" in str(_isolation_problem(tmp_path))


INSTALL = ("plugin", "install", "harness-ops@agent-harness", "--scope", "project")


@pytest.mark.slow  # about 6 s; needs `claude` on PATH, as doctor does
def test_real_cli_repin_installs_the_tag_commit_and_keeps_other_projects_installs(real_cli, tmp_path, monkeypatch):
    """#296: projects A and B, whose committed settings declare the marketplace at the pinned ref as a rendered
    template does, install main's head under the tag's version string; setup's re-pin in A gives A the tag's commit,
    B keeps its install, and A's committed settings are left as they were."""
    source = {"source": "github", "repo": "fake/src", "ref": "v0.1.1"}
    settings = {"extraKnownMarketplaces": {"agent-harness": {"source": source}}}
    a, b = _project(tmp_path / "a", settings), _project(tmp_path / "b", settings)
    monkeypatch.setattr(setup, "root", lambda: a)
    real_cli.claude(a, "plugin", "marketplace", "add", "fake/src")
    real_cli.claude(a, *INSTALL)
    real_cli.claude(b, *INSTALL)
    _git("commit", "-qam", "enable the plugin", cwd=a)  # the install's enabledPlugins entry, committed
    assert setup.build_skew(real_cli.plugins, setup._installed(), setup._projects(), real_cli.pinned)

    assert setup.repin_skewed() == []
    shas = real_cli.shas()
    assert shas[str(a)] == real_cli.tag and str(b) in shas
    status = subprocess.run(["git", "status", "--porcelain", ".claude"], cwd=a, capture_output=True, text=True)
    assert status.stdout == ""


@pytest.mark.slow  # about 5 s
def test_real_cli_repin_from_a_worktree_with_the_marketplace_in_project_settings_only(
    real_cli, tmp_path, monkeypatch, capsys
):
    """#310 review: only the project's settings declare the marketplace, and setup runs in a worktree. The user-scope
    remove is skipped, the commands run in the main checkout, and the main checkout gets the tag's commit."""
    a = _project(tmp_path / "a")
    monkeypatch.setattr(setup, "root", lambda: a)
    real_cli.claude(a, "plugin", "marketplace", "add", "fake/src", "--scope", "project")
    real_cli.claude(a, *INSTALL)
    _git("add", "-A", cwd=a)
    _git("commit", "-qm", "declare and enable the plugin", cwd=a)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=a)
    monkeypatch.setattr(setup, "root", lambda: worktree)
    assert _isolation_problem(tmp_path) is None and setup._main_checkout().resolve() == a.resolve()

    assert setup.repin_skewed() == []
    assert real_cli.shas()[str(a)] == real_cli.tag
    assert "skip plugin marketplace remove" in capsys.readouterr().out
    for d in (a, worktree):
        status = subprocess.run(["git", "status", "--porcelain", ".claude"], cwd=d, capture_output=True, text=True)
        assert status.stdout == "", d


@pytest.mark.slow  # about 5 s
def test_real_cli_setup_in_an_unpinned_project_after_another_pinned_the_marketplace_prints_no_fail(
    real_cli, tmp_path, monkeypatch, capsys
):
    """#327: project B's setup registered the marketplace at v0.1.1 in user settings; the plugin steps of project A,
    which tracks the same repository unpinned (as agent-harness itself does), print a WARN naming both, never FAIL."""
    a, b = _project(tmp_path / "a"), _project(tmp_path / "b")
    monkeypatch.setattr(setup, "root", lambda: b)
    real_cli.claude(b, "plugin", "marketplace", "add", "fake/src@v0.1.1")
    monkeypatch.setattr(setup, "root", lambda: a)
    plugins = {"harness-ops@agent-harness": "fake/src"}
    monkeypatch.setattr(setup.config, "get", lambda: SimpleNamespace(plugins=plugins))
    assert _isolation_problem(tmp_path) is None
    setup.run_steps(setup.plugin_steps())
    out = capsys.readouterr().out
    assert "FAIL" not in out, out
    assert "v0.1.1" in out and "pins no ref" in out


def test_doctor_warns_when_the_shell_redirects_python():
    assert setup.host_leaks({"PATH": "/bin"}) == ""
    warning = setup.host_leaks({"PYTHONPATH": "/opt/ros/humble/lib/python3.10/site-packages"})
    assert warning.startswith("PYTHONPATH set") and "settings.local.json" in warning and "env -u PYTHONPATH" in warning
