"""`dev orient`'s consolidation line (#113): orient shows it, and the trigger never holds orient past its budget.
Kept apart from test_consolidation.py, whose asserts call the functions these tests replace."""

import time

import pytest

from devkit import board, consolidation, workflow
from tests.test_consolidation import Repo, cfg, code


@pytest.fixture
def repo(tmp_path) -> Repo:
    return Repo(tmp_path)


def test_orient_shows_the_review_line(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "sync", lambda: [])
    monkeypatch.setattr(board, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(board, "closed_numbers", lambda: set())
    monkeypatch.setattr(workflow, "root", lambda: tmp_path)
    monkeypatch.setattr(workflow, "status", lambda: "  wt")
    monkeypatch.setattr(consolidation, "orient_line", lambda path, c=None: "## Consolidation review due: x")
    assert "## Consolidation review due: x" in workflow.orient()
    monkeypatch.setattr(consolidation, "orient_line", lambda path, c=None: None)
    assert "Consolidation" not in workflow.orient()

    def boom(path, c=None):
        raise RuntimeError("git broke")

    monkeypatch.setattr(consolidation, "orient_line", boom)
    assert "consolidation trigger failed: git broke" in workflow.orient()  # reported, never fatal


def test_orient_line_gives_up_after_its_budget(monkeypatch, repo):
    repo.commit({"a.py": code(10)})
    monkeypatch.setattr(consolidation, "due", lambda *a, **k: time.sleep(5))
    t0 = time.perf_counter()
    line = consolidation.orient_line(repo.path, cfg(), budget_s=0.2)
    assert time.perf_counter() - t0 < 2
    assert line is not None and "skipped: over 0.2 s" in line


def test_orient_line_reraises_a_trigger_error(monkeypatch, repo):
    repo.commit({"a.py": code(10)})

    def boom(*a, **k):
        raise consolidation.GitError("bad object")

    monkeypatch.setattr(consolidation, "due", boom)
    with pytest.raises(consolidation.GitError, match="bad object"):
        consolidation.orient_line(repo.path, cfg())
