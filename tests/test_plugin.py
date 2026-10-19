"""The harness-ops plugin and its marketplace: what Claude Code loads must exist, and the texts must use the names
Claude Code gives plugin components (code.claude.com/docs/en/plugins/components)."""

import json
import re
import shutil
import subprocess

import pytest

from devkit import checks, frontmatter
from devkit.paths import root

PLUGIN = root() / "plugins/harness-ops"
AGENTS = sorted(p.stem for p in (PLUGIN / "agents").glob("*.md"))


def test_the_marketplace_lists_the_plugin_under_its_manifest_name():
    market = json.loads((root() / ".claude-plugin/marketplace.json").read_text())
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == manifest["name"])
    assert (root() / entry["source"]).resolve() == PLUGIN.resolve()
    assert market["name"] == "agent-harness" and manifest["name"] == "harness-ops"


def test_the_plugin_has_the_seven_agents_and_three_skills():
    assert AGENTS == ["architect", "builder", "research-auditor", "researcher", "reviewer", "suite-auditor", "tester"]
    assert sorted(p.parent.name for p in (PLUGIN / "skills").glob("*/SKILL.md")) == ["harness", "lead", "research"]


@pytest.mark.parametrize("agent", ["architect", "suite-auditor"])
def test_the_read_only_agents_get_read_tools_only(agent):
    """#113, #114: the consolidation review and the suite audit propose and never edit; Bash is held read-only by
    guard_bash.py (READ_ONLY_AGENTS)."""
    meta, _ = frontmatter.read(PLUGIN / f"agents/{agent}.md")
    assert meta is not None
    assert [t.strip() for t in str(meta["tools"]).split(",")] == ["Read", "Grep", "Glob", "Bash"]
    assert not {"disallowedTools", "permissionMode", "hooks"} & set(meta)


def test_every_hook_script_exists_and_is_executable():
    data = json.loads((PLUGIN / "hooks/hooks.json").read_text())
    commands = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
    assert len(commands) >= 7
    for cmd in commands:
        rel = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}\"?/(\S+)", cmd).group(1)
        target = PLUGIN / rel
        assert target.is_file() and target.stat().st_mode & 0o111, rel
    assert all((PLUGIN / "hooks" / m).is_file() for m in data["modules"])


@pytest.mark.parametrize(
    "path", sorted((PLUGIN / "skills").glob("*/SKILL.md")) + sorted((PLUGIN / "agents").glob("*.md"))
)
def test_texts_name_plugin_agents_with_their_namespace(path):
    """A plugin agent is `harness-ops:<name>`; a bare `builder` in a spawn instruction would not resolve."""
    _, body = frontmatter.read(path)
    bare = [m for m in re.findall(r"`([a-z-]+)`", body) if m in AGENTS]
    bare += re.findall(r"subagent_type: (?!harness-ops:)([a-z-]+)", body)
    assert not bare, f"{path.name}: write harness-ops:{bare[0]}"


def test_plugin_agents_use_no_field_claude_code_ignores_in_a_plugin(monkeypatch):
    for p in (PLUGIN / "agents").glob("*.md"):
        meta, _ = frontmatter.read(p)
        assert not set(meta) & checks.PLUGIN_AGENT_IGNORED, p.name


def test_the_gate_flags_an_ignored_plugin_agent_field(monkeypatch, tmp_path):
    from devkit import config

    (tmp_path / "plug/.claude-plugin").mkdir(parents=True)
    (tmp_path / "plug/.claude-plugin/plugin.json").write_text('{"name": "plug"}')
    (tmp_path / "plug/agents").mkdir()
    (tmp_path / "plug/agents/a.md").write_text("---\nname: a\ndescription: d\npermissionMode: plan\n---\nbody\n")
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(surface=["plug"]))
    ran = checks.check_surface()
    assert ran.checked == 1 and any("ignored in a plugin agent" in p for p in ran.problems)


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not installed")
@pytest.mark.parametrize("target", [".", "plugins/harness-ops"])
def test_claude_plugin_validate_passes(target):
    out = subprocess.run(
        ["claude", "plugin", "validate", "--strict", target], cwd=root(), capture_output=True, text=True, check=False
    )
    assert out.returncode == 0 and "Validation passed" in out.stdout, out.stdout + out.stderr


def test_every_plugin_agent_bounds_its_turns():
    for p in (PLUGIN / "agents").glob("*.md"):
        meta, _ = frontmatter.read(p)
        assert isinstance(meta.get("maxTurns"), int) and meta["maxTurns"] > 0, p.name


# #259: every agent pins its effort, so one /effort in the lead no longer moves every worker. Values from
# harness-efficiency.md §3, judged for one model on one date: a model change means re-measuring them (#82).
EFFORT = {
    "architect": "high",
    "builder": "medium",
    "research-auditor": "high",
    "researcher": "high",
    "reviewer": "medium",
    "suite-auditor": "medium",
    "tester": "high",
}
EFFORT_JUDGED = re.compile(r"^effort: (\S+) +# judged for Opus 5\.5 on 2026-10-19 \(harness-efficiency\.md §3\)$", re.M)


def test_every_plugin_agent_sets_its_effort_and_the_model_it_was_judged_for():
    texts = {p.stem: p.read_text(encoding="utf-8") for p in (PLUGIN / "agents").glob("*.md")}
    assert {n: frontmatter.split(t)[0].get("effort") for n, t in texts.items()} == EFFORT
    assert {n: EFFORT_JUDGED.findall(t) for n, t in texts.items()} == {n: [e] for n, e in EFFORT.items()}
    assert set(EFFORT.values()) <= set(checks.EFFORTS)


@pytest.mark.parametrize("agent", ["architect", "builder", "suite-auditor", "tester"])
def test_agents_that_read_or_change_the_tree_run_in_a_worktree(agent):
    """#153: the read-only reviews read the default branch, not the lead's checkout mid-integration."""
    meta, _ = frontmatter.read(PLUGIN / f"agents/{agent}.md")
    assert meta.get("isolation") == "worktree"


def test_the_research_skill_takes_its_question_as_arguments():
    meta, body = frontmatter.read(PLUGIN / "skills/research/SKILL.md")
    assert meta.get("argument-hint") and "$ARGUMENTS" in body


def test_the_lead_skill_documents_cross_repo_dispatch():
    """#170: the pattern tested in test_hooks.py (cross_repo) is the one the lead is told to use."""
    _, body = frontmatter.read(PLUGIN / "skills/lead/SKILL.md")
    assert "Another repository's issue" in body and "prefix every command with cd <abs path> &&" in body


def test_the_lead_skill_dispatch_escalates_effort_in_order():
    """#259: the escalation ladder lives in section 4 (Dispatch), in the order high, xhigh, fable."""
    _, body = frontmatter.read(PLUGIN / "skills/lead/SKILL.md")
    dispatch = body.split("## 4. Dispatch", 1)[1].split("\n## ", 1)[0]
    rungs = [dispatch.find(s) for s in ("`high`", "`xhigh`", "`model: fable`")]
    assert -1 not in rungs and rungs == sorted(rungs), rungs
    # a resume keeps the old effort, so escalation is a respawn; the log covers the first dispatch too
    assert "gate failure" in dispatch and "respawn one level up" in dispatch
    assert "Log every dispatch: `board beat" in dispatch


def test_the_lead_skill_preloads_dev_status_without_aborting():
    _, body = frontmatter.read(PLUGIN / "skills/lead/SKILL.md")
    cmds = checks.INJECTION.findall(body)
    assert any("dev status" in c for c in cmds)
    assert all(c.rstrip().endswith("|| true") for c in cmds)


@pytest.mark.parametrize("agent", AGENTS)
def test_every_agent_body_states_the_one_hand_back_contract(agent):
    """#263: one limit, stated in each agent body because the plugin travels without HARNESS.md, and one order with
    what needs the lead first (docs/research/harness-efficiency.md change 8)."""
    _, body = frontmatter.read(PLUGIN / "agents" / f"{agent}.md")
    assert re.findall(r"at most (\d+) lines", body) == ["25"]
    heads = [body.find(f"**{h}**") for h in ("Blocked on me", "Changed", "Found")]
    assert -1 not in heads and heads == sorted(heads), heads


def test_the_harness_skill_watches_each_agents_own_hand_back_limit():
    text = (PLUGIN / "skills/harness/SKILL.md").read_text()
    assert "hand-backs over the agent's own line limit" in text
    assert not re.search(r"hand-backs over \d+", text)


def test_the_builder_sends_the_pr_body_as_a_heredoc_with_nothing_piped_after_it():
    """#261: a pipe after `dev pr` (`2>&1 | tail`) is refused by worktree isolation 105 of 106 times."""
    _, body = frontmatter.read(PLUGIN / "agents/builder.md")
    finish = body.split("## Finish", 1)[1]
    assert "--summary - <<'EOF'" in finish and "nothing piped after" in finish
    assert "`| tail` makes worktree isolation refuse the call" in finish


TAGS = ("Blocking", "Nit", "Pre-existing", "Follow-up")


@pytest.mark.parametrize("agent", ["reviewer", "research-auditor"])
def test_review_agents_tag_every_finding_and_only_blocking_sends_back(agent):
    """#260: report every finding, tagged, because a "report only high severity" prompt makes the model report less;
    only Blocking returns the PR, so nits stop costing fix resumes and confirmation rounds (harness-efficiency.md §4,
    change 5)."""
    _, body = frontmatter.read(PLUGIN / "agents" / f"{agent}.md")
    assert all(f"**{t}**" in body for t in TAGS)
    assert "Report every finding" in body and "only report" not in body.lower()
    assert "Only **Blocking** findings send" in body and "At most 3 **Nit**" in body
    assert "no confirmation round" in body
    # a re-review reads what changed, not the whole PR again; the builder's map says which push fixed what
    assert "since the last reviewed SHA" in body and "fix-to-finding map" in body
    # round 3 still blocked: question the approach instead of another fix list
    assert "From round 3" in body and "**reconsider the approach**" in body


def test_the_reviewer_re_review_follows_the_changed_symbols():
    _, body = frontmatter.read(PLUGIN / "agents/reviewer.md")
    line = body.split("since the last reviewed SHA", 1)[1].split("\n", 1)[0]
    assert "`uv run dev code refs`" in line


def test_the_lead_skill_carries_the_review_verdict_contract():
    """#260: the lead acts on the tags, skips confirmation rounds after nit-only fixes (security re-reviews any code
    change), and escalates after round 3 instead of merging an open blocker."""
    _, body = frontmatter.read(PLUGIN / "skills/lead/SKILL.md")
    loop = body.split("## 2. Close the loop first", 1)[1].split("\n## ", 1)[0]
    assert all(t in loop for t in TAGS)
    assert "Only Blocking findings return the PR" in loop and "at most 3 Nits" in loop
    assert "fix-to-finding map" in loop and "last reviewed SHA" in loop
    assert "no confirmation round" in loop and "security re-reviews any code change" in loop
    assert "file the rest as issues" in loop
    assert "reconsider the approach" in loop and "never merge with an open Blocking finding" in loop
