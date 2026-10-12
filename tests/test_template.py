"""The Copier template renders a project whose files are well formed, pinned to this release, and free of Jinja.
The full proof (uv sync and the rendered project's own gate) is scripts/render_check.sh, which CI runs; the slow
test below runs it too."""

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from copier import run_copy, run_update

from devkit import checks, config, frontmatter
from devkit.paths import root

ROOT = root()


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("rendered") / "proj"
    run_copy(
        str(ROOT),
        dest,
        data={"project_name": "demo-project", "description": "A demo."},
        defaults=True,
        vcs_ref="HEAD",
        unsafe=False,
        quiet=True,
    )
    return dest


def test_one_version_everywhere():
    plugin = json.loads((ROOT / "plugins/harness-ops/.claude-plugin/plugin.json").read_text())
    copier = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert plugin["version"] == version()
    assert copier["harness_ref"]["default"] == f"v{version()}"


def test_the_template_needs_no_trust():
    copier = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert not {"_tasks", "_migrations", "_jinja_extensions"} & set(copier)  # `copier copy` runs without --trust


def test_no_jinja_is_left_in_the_rendered_project(rendered):
    files = [p for p in rendered.rglob("*") if p.is_file()]
    assert len(files) > 15
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"(?<!\$)\{\{|\{%", text), p  # `${{ }}` is a GitHub Actions expression
        assert not p.name.endswith(".jinja"), p


def test_the_project_pins_the_devkit_and_the_plugin_to_this_release(rendered):
    py = tomllib.loads((rendered / "pyproject.toml").read_text())
    tag = f"v{version()}"
    assert py["project"]["name"] == "demo-project"
    assert py["tool"]["uv"]["sources"]["agent-harness"] == {
        "git": "https://github.com/Marius-Juston/agent-harness",
        "tag": tag,
    }
    cfg = config.parse(py["tool"]["devkit"])  # the rendered config is valid for this devkit
    assert cfg.plugins["harness-ops@agent-harness"] == f"Marius-Juston/agent-harness@{tag}"
    settings = json.loads((rendered / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"]["harness-ops@agent-harness"] is True
    source = settings["extraKnownMarketplaces"]["agent-harness"]["source"]
    assert source == {"source": "github", "repo": "Marius-Juston/agent-harness", "ref": tag}


def test_the_answers_file_supports_copier_update(rendered):
    answers = yaml.safe_load((rendered / ".copier-answers.yml").read_text())
    assert answers["project_name"] == "demo-project" and answers["_commit"] and "_src_path" in answers
    assert "harness_path" not in answers  # a test-only override never sticks to a project


def test_the_rendered_files_parse(rendered):
    for p in rendered.rglob("*.json"):
        json.loads(p.read_text())
    for p in [*rendered.rglob("*.yml"), *rendered.rglob("*.yaml")]:
        yaml.safe_load(p.read_text())
    for p in rendered.rglob("*.toml"):
        tomllib.loads(p.read_text())


def test_the_project_carries_no_copy_of_the_plugin_or_the_devkit(rendered):
    """One source of truth: agents, skills and hooks come from the plugin, the gate from the pinned package."""
    assert not (rendered / "devkit").exists()
    assert not list(rendered.glob(".claude/agents/*.md")) and not list(rendered.glob(".claude/skills/*/SKILL.md"))
    assert "hooks" not in json.loads((rendered / ".claude/settings.json").read_text())


@pytest.mark.slow
def test_a_rendered_project_passes_its_own_gate(tmp_path):
    out = subprocess.run(
        [str(ROOT / "scripts/render_check.sh"), str(tmp_path / "proj")], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0 and "RESULT: passed" in out.stdout, (out.stdout + out.stderr)[-3000:]


def test_the_project_carries_the_research_api_keys_setup(rendered):
    setup_md = (rendered / "docs/SETUP.md").read_text()
    assert "## Research API keys" in setup_md
    for must in ("~/.config/api-keys.env", "read -rsp", "chmod 600", "OPENALEX_API_KEY", "S2_API_KEY"):
        assert must in setup_md
    assert "docs/SETUP.md#research-api-keys" in (rendered / "README.md").read_text()
    assert (
        ". ~/.config/api-keys.env" not in setup_md and "Do not export the keys" in setup_md
    )  # exported keys leak into every agent
    deny = json.loads((rendered / ".claude/settings.json").read_text())["permissions"]["deny"]
    assert "Read(~/.config/api-keys.env)" in deny
    # one text: this repository's own setup page is the template's, so the two never drift apart
    assert (ROOT / "docs/SETUP.md").read_text() == (ROOT / "template/docs/SETUP.md").read_text()
    assert "docs/SETUP.md#research-api-keys" in (ROOT / "README.md").read_text()


def test_copier_update_brings_the_research_api_keys_setup_to_an_existing_project(tmp_path):
    """A project made from v0.1.2 (before docs/SETUP.md existed) gets it through `copier update`."""
    dest = tmp_path / "old"
    data = {"project_name": "demo-project", "description": "A demo."}
    run_copy(str(ROOT), dest, data=data, defaults=True, vcs_ref="v0.1.2", unsafe=False, quiet=True)
    assert not (dest / "docs/SETUP.md").exists()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "from v0.1.2"], cwd=dest, check=True)
    readme = (dest / "README.md").read_text()
    run_update(dest, data=data, defaults=True, overwrite=True, vcs_ref="HEAD", unsafe=False, quiet=True)
    assert "## Research API keys" in (dest / "docs/SETUP.md").read_text()
    assert (dest / "README.md").read_text() == readme  # project-owned since #22: updates never touch it


def test_the_project_carries_the_session_goal_prompt(rendered):
    """A fresh lead session starts from one documented /goal prompt with a stop rule a /goal evaluator can check."""
    session = (rendered / "docs/SESSION.md").read_text()
    assert session.count("/goal You are the lead") == 2  # the default and the focused variant
    for must in ("harness-ops:lead", "uv run dev board next", "uv run dev lit status", "Done when", "OPEN_QUESTIONS"):
        assert must in session
    assert "docs/SESSION.md" in (rendered / "README.md").read_text()


def test_claude_md_imports_the_template_owned_harness_rules(rendered):
    """CLAUDE.md holds only the project's facts and an @-import of the generic rules, which the template owns; the
    context word budget counts both, and together they stay inside it."""
    claude, harness = rendered / "CLAUDE.md", rendered / ".devkit/HARNESS.md"
    lines = claude.read_text().splitlines()
    assert "@.devkit/HARNESS.md" in lines and "## Project facts" in lines
    assert "## Rules" not in lines and "## Rules" in harness.read_text().splitlines()
    copier = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert {"CLAUDE.md", "README.md"} <= set(copier["_skip_if_exists"])
    assert checks.imported_words(claude) == frontmatter.words(harness.read_text())
    total = frontmatter.words(claude.read_text()) + checks.imported_words(claude)
    budget = tomllib.loads((rendered / ".devkit/ratchets.toml").read_text())["defaults"]["context"]
    assert total <= budget


def _previous_release() -> str:
    tags = subprocess.run(
        ["git", "tag", "--list", "v*", "--sort=-v:refname", "--no-contains", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tags, "no earlier release tag (CI checks out with fetch-depth 0)"
    return tags[0]


def test_copier_update_never_conflicts_on_project_owned_files(tmp_path):
    """A project from the previous release edits CLAUDE.md and README.md; updating to HEAD leaves both as the project
    wrote them, with no conflict anywhere, and brings the template-owned rules file."""
    dest = tmp_path / "prev"
    data = {"project_name": "demo-project", "description": "A demo."}
    run_copy(str(ROOT), dest, data=data, defaults=True, vcs_ref=_previous_release(), unsafe=False, quiet=True)
    for name in ("CLAUDE.md", "README.md"):
        p = dest / name
        p.write_text(p.read_text().replace("demo-project", "Demo Project, edited by its owner") + "\nOwn line.\n")
    edited = {name: (dest / name).read_text() for name in ("CLAUDE.md", "README.md")}
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "previous release, edited"], cwd=dest, check=True)
    run_update(dest, data=data, defaults=True, overwrite=True, vcs_ref="HEAD", unsafe=False, quiet=True)
    for name, text in edited.items():
        assert (dest / name).read_text() == text, name
    files = [p for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts]
    assert not [p for p in files if p.suffix == ".rej"]
    assert not [p for p in files if "<<<<<<<" in p.read_text(errors="replace")]
    assert "## Rules" in (dest / ".devkit/HARNESS.md").read_text()
