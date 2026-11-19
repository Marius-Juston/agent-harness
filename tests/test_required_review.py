"""#501: this repository requires a harness-ops:reviewer verdict on harness code, read from its own pyproject.toml."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from devkit import board, config, workflow
from tests.test_gate import _last_json, _merged, fake_integrate  # noqa: F401 - fake_integrate is a fixture
from tests.test_workflow import _github_keeps_comments, _push, _verdict

ROOT = Path(__file__).resolve().parents[1]
REVIEWER = "harness-ops:reviewer"


def _ours() -> config.Config:
    return config.parse(tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["devkit"])


@pytest.mark.parametrize(
    ("path", "needed"),
    [
        ("devkit/workflow.py", True),
        ("devkit/templates/x.md", True),
        ("plugins/harness-ops/skills/lead/SKILL.md", True),
        ("plugins/harness-ops/hooks/guard_bash.py", True),
        ("tests/test_workflow.py", False),
        ("docs/research/agent-harness.md", False),
        ("README.md", False),
        ("devkitten/x.py", False),  # a prefix match stops at the directory
    ],
)
def test_this_repository_requires_the_reviewer_on_devkit_and_plugins(path, needed):
    why = workflow.review_problem([path], [], "c" * 40, {"lead"}, _ours().required_reviews, lambda sha: False)
    assert (why is not None) == needed, why
    if needed:
        assert REVIEWER in why


def test_integrate_refuses_a_devkit_pr_here_until_the_reviewer_says_merge(fake_integrate, monkeypatch, capsys):  # noqa: F811
    """#501 DONE WHEN: with this repository's config, integrate refuses a PR touching devkit/ and plugins/ until a
    harness-ops:reviewer `Verdict: merge` review is posted on its head; a `send back` keeps it refused."""
    monkeypatch.setattr(workflow.config, "get", _ours)
    fake_integrate["files"] = ["devkit/workflow.py", "plugins/harness-ops/agents/reviewer.md"]
    _push(fake_integrate, "c" * 40)
    _github_keeps_comments(fake_integrate, monkeypatch)
    reviews: list[dict] = []
    real = board.gh

    def gh(*args, **kw):
        out = real(*args, **kw)
        if args[:2] == ("api", "graphql"):
            data = json.loads(out)
            data["data"]["repository"]["pullRequest"]["reviews"]["nodes"] = reviews
            return json.dumps(data)
        return out

    monkeypatch.setattr(board, "gh", gh)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = _last_json(capsys.readouterr().out)
    assert res["outcome"] == "refused" and REVIEWER in res["reason"] and "devkit/workflow.py" in res["reason"]
    assert not _merged(fake_integrate["events"])

    reviews.append(_verdict("c" * 40, verdict="send back"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    assert _last_json(capsys.readouterr().out)["outcome"] == "refused"
    assert not _merged(fake_integrate["events"])

    reviews.append({**_verdict("c" * 40), "submittedAt": "2026-10-11T12:00:00Z"})
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert _merged(fake_integrate["events"])


def test_the_reviewer_names_itself_in_the_review_it_posts():
    """integrate counts only a verdict review whose body names the required reviewer (workflow.review_problem): the
    template reviewer.md posts must carry the name, or every review of harness code is ignored."""
    text = (ROOT / "plugins/harness-ops/agents/reviewer.md").read_text()
    template = re.search(r"<<'VERDICT_END'\n(.*?)VERDICT_END", text, re.S)
    assert template and REVIEWER in template.group(1)
    verdict = template.group(1).splitlines()[0]
    assert verdict.startswith("Verdict:")


def test_the_lead_dispatches_a_tester_after_a_feature_merge():
    text = (ROOT / "plugins/harness-ops/skills/lead/SKILL.md").read_text()
    assert re.search(r"feature.{0,80}merge.{0,120}harness-ops:tester|harness-ops:tester.{0,120}feature.{0,40}merge",
                     text, re.S | re.I)  # fmt: skip
