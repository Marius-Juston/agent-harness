"""CI cost and ordering (#57): one billed job per PR in the template, concurrency that cancels superseded PR runs but
never a run on main, and cite-check as a step that only works when research notes changed."""

import subprocess
from pathlib import Path

import pytest
import yaml

from devkit import checks, cli, config
from devkit.paths import root

ROOT = root()
GATE = ROOT / "template/.github/workflows/gate.yml"
WORKFLOWS = [ROOT / ".github/workflows/ci.yml", GATE]


def load(workflow: Path) -> dict:
    return yaml.safe_load(workflow.read_text())


NOT_ON_PR = {"github.event_name == 'schedule'", "github.event_name == 'push'", "github.event_name != 'pull_request'"}


def runs_on_pull_request(job: dict) -> bool:
    return job.get("if") not in NOT_ON_PR


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: str(p.relative_to(ROOT)))
def test_concurrency_groups_by_pr_or_commit_and_cancels_only_prs(workflow):
    c = load(workflow)["concurrency"]
    # github.ref is refs/heads/main for every push to main: a second merge would cancel the first one's run
    assert "github.ref" not in c["group"]
    assert "github.event.pull_request.number || github.sha" in c["group"]
    assert c["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert all("concurrency" not in job for job in load(workflow)["jobs"].values())


def test_template_bills_one_job_per_pr():
    jobs = load(GATE)["jobs"]
    assert [name for name, job in jobs.items() if runs_on_pull_request(job)] == ["check"]
    assert "citations" not in jobs and "security" not in jobs


def test_cite_check_is_a_pr_step_of_check():
    steps = load(GATE)["jobs"]["check"]["steps"]
    step = next(s for s in steps if s.get("run") == "uv run dev cite-check")
    assert "github.event_name == 'pull_request'" in step["if"] and "!cancelled()" in step["if"]
    # the diff base: cite-check diffs against origin/main, which only a full clone brings
    assert steps[0]["with"]["fetch-depth"] == 0
    assert steps.index(step) > next(i for i, s in enumerate(steps) if s.get("run") == "uv sync --locked")


def _sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pr_checkout(tmp_path, monkeypatch):
    """A repository shaped like CI's PR checkout: main with two notes, a PR branch, and HEAD on the merge commit."""
    r = tmp_path / "repo"
    (r / "docs/research").mkdir(parents=True)
    _sh(r, "git", "init", "-q", "-b", "main")
    _sh(r, "git", "config", "user.email", "t@t")
    _sh(r, "git", "config", "user.name", "t")
    for name in ("kept.md", "edited.md", "gone.md", "INDEX.md"):
        (r / "docs/research" / name).write_text(f"# {name}\n")
    (r / "m.py").write_text("X = 1\n")
    _sh(r, "git", "add", ".")
    _sh(r, "git", "commit", "-q", "-m", "init")
    _sh(r, "git", "switch", "-q", "-c", "pr")
    monkeypatch.chdir(r)
    monkeypatch.setattr(cli, "root", lambda: r)
    monkeypatch.setattr(checks, "root", lambda: r)
    monkeypatch.setattr(config, "get", lambda: config.parse({}))
    return r


def _merge_into_main(r: Path) -> None:
    """GitHub checks out refs/pull/N/merge: main merged with the PR, so HEAD is not the PR branch tip."""
    _sh(r, "git", "switch", "-q", "main")
    (r / "other.txt").write_text("main moved on\n")
    _sh(r, "git", "add", ".")
    _sh(r, "git", "commit", "-q", "-m", "main moves")
    _sh(r, "git", "switch", "-q", "--detach", "main")
    _sh(r, "git", "merge", "-q", "--no-ff", "-m", "merge", "pr")


def test_changed_notes_on_a_pr_merge_checkout(pr_checkout):
    r = pr_checkout
    (r / "docs/research/edited.md").write_text("# edited, now with a source\n")
    (r / "docs/research/new.md").write_text("# new\n")
    (r / "docs/research/INDEX.md").write_text("# regenerated\n")
    (r / "docs/research/gone.md").unlink()
    _sh(r, "git", "add", "-A")
    _sh(r, "git", "commit", "-q", "-m", "notes")
    _merge_into_main(r)
    # deleted notes and the generated index are not checked; the note main has untouched is not either
    assert cli._changed_notes() == [r / "docs/research/edited.md", r / "docs/research/new.md"]


def test_cite_check_is_a_no_op_without_changed_notes(pr_checkout, capsys, monkeypatch):
    r = pr_checkout
    (r / "m.py").write_text("X = 2\n")
    _sh(r, "git", "commit", "-q", "-am", "code only")
    _merge_into_main(r)
    assert cli._changed_notes() == []

    def no_network(*a, **k):
        raise AssertionError("cite-check reached the network with no note changed")

    monkeypatch.setattr(cli.citecheck, "check_notes", no_network)
    assert cli.run(cli.build().parse_args(["cite-check"])) == 0
    assert "no research notes changed" in capsys.readouterr().out
