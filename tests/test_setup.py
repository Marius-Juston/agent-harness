"""`dev doctor` warns when the Copier template, the plugin pin or the installed plugin is behind the latest release."""

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from devkit import setup

SRC = "https://github.com/Marius-Juston/agent-harness"
PIN = {"harness-ops@agent-harness": "Marius-Juston/agent-harness@v0.1.1", "astral@astral-sh": "astral-sh/x"}
HERE = Path("/p")


def tags(*names):
    return lambda url: list(names) if url == SRC else []


def installed(version, path="/p"):
    return [{"id": "harness-ops@agent-harness", "version": version, "scope": "project", "projectPath": path}]


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
    assert setup.staleness(answers, PIN, installed("0.1.1"), HERE, tags("v0.1.0", "v0.1.1")) == []


def test_template_behind_warns_with_the_fix():
    answers = {"_commit": "v0.1.0", "_src_path": "gh:Marius-Juston/agent-harness"}
    out = setup.staleness(answers, {}, [], HERE, tags("v0.1.0", "v0.1.1"))
    assert len(out) == 1 and "v0.1.0" in out[0] and "v0.1.1" in out[0] and "copier update" in out[0]


def test_plugin_pin_behind_warns():
    out = setup.staleness({}, PIN, installed("0.1.1"), HERE, tags("v0.1.1", "v0.2.0"))
    assert len(out) == 1 and "harness-ops@agent-harness" in out[0] and "v0.2.0" in out[0]


def test_installed_plugin_older_than_pin_warns():
    out = setup.staleness({}, PIN, installed("0.1.0"), HERE, tags("v0.1.1"))
    assert len(out) == 1 and "installed 0.1.0" in out[0] and "claude plugin update" in out[0]


def test_another_projects_install_is_ignored():
    assert setup.staleness({}, PIN, installed("0.1.0", "/other"), HERE, tags("v0.1.1")) == []


def test_unreachable_remote_or_no_answers_never_warns():
    answers = {"_commit": "v0.1.0", "_src_path": "gh:Marius-Juston/agent-harness"}
    assert setup.staleness(answers, PIN, [], HERE, lambda url: []) == []
    assert setup.staleness({}, {}, [], HERE, tags("v9.9.9")) == []
