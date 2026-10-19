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
    # PyPI's `agent-harness` is an unrelated project: the requirement itself names the git tag, so `--no-sources`,
    # `uv export` and pip resolve this release, never PyPI's (docs/research/agent-harness.md, section 9).
    assert py["dependency-groups"]["dev"] == [
        f"agent-harness @ git+https://github.com/Marius-Juston/agent-harness@{tag}"
    ]
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
    # a stored answer beats the new default, so the pin moves only when the update passes it (as README says)
    pin = {**data, "harness_ref": f"v{version()}"}
    run_update(dest, data=pin, defaults=True, overwrite=True, vcs_ref="HEAD", unsafe=False, quiet=True)
    for name, text in edited.items():
        assert (dest / name).read_text() == text, name
    files = [p for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts]
    assert not [p for p in files if p.suffix == ".rej"]
    assert not [p for p in files if "<<<<<<<" in p.read_text(errors="replace")]
    assert "## Rules" in (dest / ".devkit/HARNESS.md").read_text()
    py = tomllib.loads((dest / "pyproject.toml").read_text())  # the pinned requirement reaches existing projects
    url = f"git+https://github.com/Marius-Juston/agent-harness@v{version()}"
    assert py["dependency-groups"]["dev"] == [f"agent-harness @ {url}"]
    assert py["tool"]["uv"]["sources"]["agent-harness"]["git"] == "https://github.com/Marius-Juston/agent-harness"


STANDARD = ("CONTRIBUTING.md", "SECURITY.md", ".editorconfig", "CHANGELOG.md")
HARNESS_ONLY = (
    "CODE_OF_CONDUCT.md",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/task.yml",
)


def test_the_standard_repo_files_exist_here_and_in_a_rendered_project(rendered):
    for name in STANDARD:
        assert (rendered / name).is_file(), f"rendered project lacks {name}"
        assert (ROOT / name).is_file(), f"agent-harness lacks {name}"
    for name in HARNESS_ONLY:
        assert (ROOT / name).is_file(), f"agent-harness lacks {name}"
    # one text where nothing is project-specific, so the two never drift apart
    for name in ("SECURITY.md", ".editorconfig", ".github/ISSUE_TEMPLATE/task.yml"):
        assert (ROOT / name).read_text() == (ROOT / "template" / name).read_text(), name
    for path in (ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml"):
        assert isinstance(yaml.safe_load(path.read_text()), dict), path


def test_contributing_links_setup_and_help_and_readme_links_contributing(rendered):
    """CONTRIBUTING is the path from clone to first PR: it links the setup page and `uv run dev --help`, never a copy
    of the command list."""
    for where in (ROOT, rendered):
        text = (where / "CONTRIBUTING.md").read_text()
        assert "docs/SETUP.md" in text and "`uv run dev --help`" in text, where
        assert "uv run dev check" in text, where
        assert "(CONTRIBUTING.md)" in (where / "README.md").read_text(), where
    for name in ("CODE_OF_CONDUCT.md", "SECURITY.md"):
        assert f"({name})" in (ROOT / "CONTRIBUTING.md").read_text(), name


def test_the_changelog_is_project_owned():
    """release-please (#60) writes CHANGELOG.md; a `copier update` must never overwrite a project's release notes."""
    assert "CHANGELOG.md" in yaml.safe_load((ROOT / "copier.yml").read_text())["_skip_if_exists"]


def test_render_check_asserts_the_standard_files():
    script = (ROOT / "scripts/render_check.sh").read_text()
    for name in STANDARD:
        assert name in script, name


@pytest.mark.slow
def test_a_fresh_clone_reaches_a_green_gate_following_readme_and_contributing(tmp_path):
    out = subprocess.run(
        [str(ROOT / "scripts/contributing_check.sh"), str(tmp_path / "clone")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0 and "RESULT: passed" in out.stdout, (out.stdout + out.stderr)[-3000:]


def test_the_project_carries_the_routine_prompts(rendered):
    """docs/PROMPTS.md picks the routine in one line; the /goal prompt itself lives only in docs/SESSION.md."""
    prompts = (rendered / "docs/PROMPTS.md").read_text()
    for must in ("harness-ops:lead", "harness-ops:research", "harness-ops:harness", "uv run dev board brief N"):
        assert must in prompts, must
    assert "(SESSION.md)" in prompts and "/goal You are" not in prompts
    assert "docs/PROMPTS.md" in (rendered / "README.md").read_text()


def test_the_multi_agent_spec_is_rendered_from_the_one_in_this_repository(rendered):
    """The spec is written once (docs/specs/multi-agent.md here); a project gets it with links that resolve there."""
    ours = (ROOT / "docs/specs/multi-agent.md").read_text()
    theirs = (rendered / "docs/specs/multi-agent.md").read_text()
    url = f"https://github.com/Marius-Juston/agent-harness/blob/v{version()}/docs/research/agent-harness.md"
    # template/docs/specs/multi-agent.md.jinja is this transformation of ours: change both together
    assert theirs == ours.replace(
        "[../research/agent-harness.md](../research/agent-harness.md)", f"[agent-harness research]({url})"
    )
    assert "](../" not in theirs  # a relative link out of docs/specs would dangle in a project
    assert "(../docs/specs/multi-agent.md)" in (rendered / ".devkit/HARNESS.md").read_text()


def test_the_memory_script_is_named_for_the_project_and_round_trips(rendered, tmp_path):
    script = rendered / "scripts/claude-memory.sh"
    assert script.stat().st_mode & 0o111, "not executable"
    text = script.read_text()
    assert text.count("-claude-memory.tgz") == text.count("demo-project-claude-memory.tgz") == 2  # no other name
    home = tmp_path / "home"
    key = re.sub(r"[^A-Za-z0-9]", "-", str(rendered.resolve()))
    memory = home / ".claude/projects" / key / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("- a preference\n")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([str(script), "export"], env=env, capture_output=True, text=True, check=True)
    assert (home / "demo-project-claude-memory.tgz").is_file() and "Exported 1 files" in out.stdout
    (memory / "MEMORY.md").unlink()
    subprocess.run([str(script), "import"], env=env, capture_output=True, text=True, check=True)
    assert (memory / "MEMORY.md").read_text() == "- a preference\n"
    usage = subprocess.run([str(script)], env=env, capture_output=True, text=True, check=False)
    assert usage.returncode == 2 and "usage" in usage.stderr


def test_the_project_carries_the_agent_surface_evals(rendered):
    """`uv run dev eval` finds scenarios in a new project: the generic ones, each from a real failure class."""
    scenarios = sorted((rendered / "evals/scenarios").glob("*.yaml"))
    assert [p.stem for p in scenarios] == ["gate-before-pr", "uv-not-pip"]
    for p in scenarios:
        sc = yaml.safe_load(p.read_text())
        assert sc["prompt"] and {"expect_command", "forbid_command", "order"} & set(sc), p
        for regex in [*sc.get("expect_command", []), *sc.get("forbid_command", []), *sc.get("order", [])]:
            re.compile(regex)
    gate_before_pr = yaml.safe_load((rendered / "evals/scenarios/gate-before-pr.yaml").read_text())
    assert "docs/PROMPTS.md" in gate_before_pr["prompt"]  # the file it edits exists in every project
    assert "uv run dev eval" in (rendered / "evals/README.md").read_text()


def test_the_settings_compact_near_400k(rendered):
    # #258: the lead compacted at 501-966k; 150-400k cut its simulated cost 42-47 % (harness-efficiency.md, change 3).
    # 400k, not 300k: subagents inherit the window, and only ~5 of 672 worker runs exceed 400k (32 exceed 300k).
    # A plain int in Claude Code's 100000-1000000 range; `claude --autocompact auto` restores the default per launch.
    window = json.loads((rendered / ".claude/settings.json").read_text())["autoCompactWindow"]
    assert window == 400000
    assert type(window) is int
    lead = (ROOT / "plugins/harness-ops/skills/lead/SKILL.md").read_text()
    assert "`autoCompactWindow`" in lead  # the lead knows when compaction comes and keeps HANDOFF ahead of it


def test_the_settings_allow_the_read_only_board_commands(rendered):
    allow = json.loads((rendered / ".claude/settings.json").read_text())["permissions"]["allow"]
    for rule in (
        "Bash(uv run dev --help)",
        "Bash(uv run dev board lint)",
        "Bash(uv run dev board wave *)",
        "Bash(uv run dev board feedback *)",
    ):
        assert rule in allow, rule
    assert not [r for r in allow if r.startswith("Bash(uv run dev board lint") and r != "Bash(uv run dev board lint)"]


def test_harness_rules_keep_only_what_no_hook_or_skill_already_says():
    """#267: HARNESS.md loads into every agent, so it drops lines a hook enforces or another file already says
    (docs/research/harness-efficiency.md change 12), and states the gate rule the completion gate actually applies."""
    rel = "template/.devkit/HARNESS.md.jinja"
    text = (ROOT / rel).read_text()
    assert frontmatter.words(text) <= tomllib.loads((ROOT / ".devkit/ratchets.toml").read_text())["words"][rel]
    gone = ("never pip", "Navigate by symbol", "Batch independent", "Hand-backs are at most", "Reuse before build")
    gone += ("cite-check", "SessionStart", "check` once", "Never edit generated files")
    for line in gone:
        assert line not in text, line
    limit = re.compile(r"(?i)hand[- ]?backs?\W.*\d+ lines")  # #263: the limit lives in each agent body
    assert limit.search("Hand back in at most 25 lines") and limit.search("- Hand-backs are at most 25 lines.")
    assert not limit.search(text)
    assert "`uv run dev check` gates the committed HEAD" in text and "again after any fix" in text
    assert "`harness-ops:lead`" in text and "`harness-ops:harness`" in text and "Only `uv run dev integrate" in text
    # kept by the lead's decision: skills mostly do not load, so these two do not move into the harness skill
    assert "never copy counts, field lists or commands into prose" in text and "go to agent-harness itself" in text


def test_builder_states_the_read_before_edit_gotcha_and_the_gate_rule():
    """#267: 5.8 % of builder edits failed with 'File has not been read yet' after a sed or cat read; and a builder
    re-runs the gate after every fix, so the text no longer says to run it once."""
    text = (ROOT / "plugins/harness-ops/agents/builder.md").read_text()
    assert "Edit and Write need a prior Read of that file; reading it with sed or cat does not count." in text
    assert "`uv run dev check` gates the committed HEAD" in text and "run it again after any fix" in text
    assert "check` once" not in text
