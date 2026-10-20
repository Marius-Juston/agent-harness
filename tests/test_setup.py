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
# A stub `claude` CLI: install records in $FAKE_CLAUDE_STATE, every call logged there. A call starting with
# $FAKE_CLAUDE_FAIL fails and changes nothing. Removing a marketplace at user scope leaves the project installs (the
# projects' settings still declare it); removing it in every scope drops them all.
import json, os, sys
path = os.environ["FAKE_CLAUDE_STATE"]
with open(path) as f:
    state = json.load(f)
args = sys.argv[1:]
state["calls"].append(args)
fail = os.environ.get("FAKE_CLAUDE_FAIL", "").split()
if fail and args[: len(fail)] == fail:
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
    return SimpleNamespace(state=state, install=install, pinned=pinned, head=head, plugins=plugins, remote=remote)


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


def _real_cli_project(path: Path, source: dict) -> Path:
    """A project whose committed settings declare the marketplace at the pinned ref, as a rendered template does."""
    path.mkdir()
    _git("init", "-q", cwd=path)
    (path / ".claude").mkdir()
    (path / ".claude/settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"agent-harness": {"source": source}}})
    )
    return path


@pytest.mark.slow  # about 6 s; needs `claude` on PATH, as doctor does
def test_real_cli_repin_installs_the_tag_commit_and_keeps_other_projects_installs(tmp_path, monkeypatch):
    """#296 with the real `claude` CLI, offline, in a temporary HOME (the owner's ~/.claude is never touched): a
    marketplace added at main installs main's head under the tag's version string in projects A and B; setup's re-pin
    in A gives A the tag's commit, and B keeps its install. A git `insteadOf` serves github.com/fake/ (to git and to
    Claude Code) from a local repository."""
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
    (repo / "p/agents/builder.md").write_text("main body")  # main moves on; plugin.json still says 0.1.1
    _git("commit", "-qam", "unreleased", cwd=repo)
    source = {"source": "github", "repo": "fake/src", "ref": "v0.1.1"}
    a, b = _real_cli_project(tmp_path / "a", source), _real_cli_project(tmp_path / "b", source)
    plugins = {"harness-ops@agent-harness": "fake/src@v0.1.1"}
    monkeypatch.setattr(setup.config, "get", lambda: SimpleNamespace(plugins=plugins))
    monkeypatch.setattr(setup, "pinned_plugin_root", lambda: pinned)
    install = ["claude", "plugin", "install", "harness-ops@agent-harness", "--scope", "project"]
    for cwd, cmd in ((a, ["claude", "plugin", "marketplace", "add", "fake/src"]), (a, install), (b, install)):
        assert subprocess.run(cmd, cwd=cwd, capture_output=True, check=False).returncode == 0, cmd
    monkeypatch.setattr(setup, "root", lambda: a)
    assert setup.build_skew(plugins, setup._installed(), setup._projects(), pinned)  # skewed, same version string
    committed = json.loads((a / ".claude/settings.json").read_text())

    assert setup.repin_skewed() == []
    records = json.loads((home / ".claude/plugins/installed_plugins.json").read_text())["plugins"]
    shas = {r["projectPath"]: r["gitCommitSha"] for r in records["harness-ops@agent-harness"]}
    assert shas[str(a)] == tag and str(b) in shas
    after = json.loads((a / ".claude/settings.json").read_text())
    assert after["extraKnownMarketplaces"] == committed["extraKnownMarketplaces"]
    assert after["enabledPlugins"]["harness-ops@agent-harness"] is True
