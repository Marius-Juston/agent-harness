"""Free stand-ins for GitHub secret scanning and dependency alerts (not free on private user-owned repositories):
gitleaks as a pre-commit hook and over the whole history in CI, `uv audit` (OSV) over uv.lock, a CycloneDX SBOM per
CI run, and ruff's S (bandit) rules. The fast tests pin the configuration; the live ones (network: prek fetches
gitleaks and a Go toolchain, uv audit asks OSV) prove each check fails on a fixture. The fake secret is generated at
run time from a fixed seed, so this repository never holds a literal one for gitleaks to flag."""

import random
import shutil
import string
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from devkit import citecheck, lit, netsafe
from devkit.paths import root

ROOT = root()
PRECOMMIT = [ROOT / ".pre-commit-config.yaml", ROOT / "template/.pre-commit-config.yaml"]
WORKFLOWS = [ROOT / ".github/workflows/ci.yml", ROOT / "template/.github/workflows/gate.yml"]
GITLEAKS = "https://github.com/gitleaks/gitleaks"


def gitleaks_repo(config: Path) -> dict:
    repos = yaml.safe_load(config.read_text())["repos"]
    return next(r for r in repos if r["repo"] == GITLEAKS)


def security_steps(workflow: Path) -> list[dict]:
    return yaml.safe_load(workflow.read_text())["jobs"]["security"]["steps"]


def step_run(workflow: Path, needle: str) -> str:
    return next(s["run"] for s in security_steps(workflow) if needle in s.get("run", ""))


def fake_token() -> str:
    """A GitHub personal access token shape gitleaks' github-pat rule matches; random, so its entropy is high."""
    rng = random.Random(15)
    return "ghp_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))


@pytest.mark.parametrize("config", PRECOMMIT, ids=lambda p: str(p.relative_to(ROOT)))
def test_gitleaks_hooks_are_pinned_in_both_configs(config):
    repo = gitleaks_repo(config)
    assert repo["rev"].startswith("v8.")
    hooks = {h.get("alias", h["id"]): h for h in repo["hooks"]}
    assert set(hooks) == {"gitleaks", "gitleaks-history"}
    assert "stages" not in hooks["gitleaks"]  # runs on every commit
    history = hooks["gitleaks-history"]
    assert history["stages"] == ["manual"]
    assert history["entry"].split()[:2] == ["gitleaks", "git"] and "--staged" not in history["entry"]
    assert "--redact" in history["entry"]  # CI logs are readable by everyone with repo access


def test_both_configs_pin_the_same_gitleaks():
    assert gitleaks_repo(PRECOMMIT[0]) == gitleaks_repo(PRECOMMIT[1])


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: str(p.relative_to(ROOT)))
def test_ci_security_job(workflow):
    job = yaml.safe_load(workflow.read_text())["jobs"]["security"]
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0  # the history scan needs every commit
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert "--hook-stage manual gitleaks-history" in runs
    assert "uv audit" in runs and "--frozen" in runs
    assert "--format cyclonedx1.5" in runs
    assert any(s.get("uses", "").startswith("actions/upload-artifact@") for s in job["steps"])
    # every check reports even when an earlier one fails
    assert all(s.get("if") == "${{ !cancelled() }}" for s in job["steps"][3:])


def test_the_two_security_jobs_match():
    assert security_steps(WORKFLOWS[0]) == security_steps(WORKFLOWS[1])


@pytest.mark.parametrize("pyproject", [ROOT / "pyproject.toml", ROOT / "template/pyproject.toml.jinja"])
def test_ruff_bandit_rules_on(pyproject):
    # the template is Jinja; its [tool.ruff.lint] tables are plain TOML
    chunk = pyproject.read_text().split("[tool.ruff.lint]", 1)[1].split("\n[tool.pytest", 1)[0]
    lint = tomllib.loads("[tool.ruff.lint]" + chunk)["tool"]["ruff"]["lint"]
    assert "S" in lint["select"]
    ignores = lint["per-file-ignores"]
    assert "S101" in ignores.get("tests/**", ignores.get("**/tests/**", []))
    assert all("S" not in rules for rules in [lint.get("ignore", []), *ignores.values()])  # never the whole family


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo, check=True)


@pytest.fixture
def leak_repo(tmp_path) -> Path:
    """A git repo with only this repository's gitleaks hooks, and a clean first commit."""
    repo = tmp_path / "fixture"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    cfg = {"repos": [gitleaks_repo(PRECOMMIT[0])]}
    (repo / ".pre-commit-config.yaml").write_text(yaml.safe_dump(cfg))
    (repo / "ok.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "clean")
    return repo


def _bash(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=600, check=False)


needs_uv = pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")


@pytest.mark.live
@needs_uv
def test_precommit_hook_fails_on_a_staged_fake_secret(leak_repo):
    prek = step_run(WORKFLOWS[0], "gitleaks-history").split(" run ")[0]  # the same pinned prek CI uses
    assert _bash(f"{prek} run gitleaks", leak_repo).returncode == 0  # nothing staged: clean
    (leak_repo / "leak.py").write_text(f'token = "{fake_token()}"\n')
    _git(leak_repo, "add", "leak.py")
    out = _bash(f"{prek} run gitleaks", leak_repo)
    assert out.returncode != 0 and "github-pat" in out.stdout + out.stderr
    assert fake_token() not in out.stdout + out.stderr  # redacted


@pytest.mark.live
@needs_uv
def test_ci_step_fails_on_a_committed_fake_secret(leak_repo):
    cmd = step_run(WORKFLOWS[0], "gitleaks-history")
    assert _bash(cmd, leak_repo).returncode == 0
    (leak_repo / "leak.py").write_text(f'token = "{fake_token()}"\n')
    _git(leak_repo, "add", "leak.py")
    _git(leak_repo, "commit", "-q", "-m", "leak")  # no hooks installed in the fixture: the commit lands
    (leak_repo / "leak.py").unlink()
    _git(leak_repo, "commit", "-qam", "remove it")  # gone from the tree, still in history
    out = _bash(cmd, leak_repo)
    assert out.returncode != 0 and "github-pat" in out.stdout + out.stderr


@pytest.mark.live
@needs_uv
def test_ci_audit_fails_on_a_pinned_vulnerable_dependency(tmp_path):
    cmd = step_run(WORKFLOWS[0], "uv audit")
    project = tmp_path / "vulnerable"
    project.mkdir()
    pin = 'dependencies = ["requests==2.19.0"]'  # CVE-2018-18074 and later advisories
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "fixture"\nversion = "0"\nrequires-python = ">=3.13"\n{pin}\n[tool.uv]\npackage = false\n'
    )
    assert _bash("uv lock --quiet", project).returncode == 0
    out = _bash(cmd, project)
    assert out.returncode != 0 and "requests" in out.stdout + out.stderr


# ruff S findings fixed in devkit: network fetchers take http(s) only, and XML from the network may carry no DTD
# (entity expansion attacks need one; Atom feeds never do).
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "data:text/plain,hi"])
def test_fetchers_refuse_non_http_schemes(url):
    status, _, body = citecheck._get(url)
    assert status == 0 and "scheme" in body
    status, _, body = lit.http_get(url, {})
    assert status == 0 and "scheme" in body


BOMB = '<?xml version="1.0"?><!DOCTYPE f [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><feed>&b;</feed>'


@pytest.mark.parametrize("body", [BOMB, BOMB.lower(), "<!doctype feed SYSTEM 'http://example.org/x.dtd'><feed/>"])
def test_xml_with_a_dtd_is_refused(body):
    with pytest.raises(ET.ParseError):
        netsafe.parse_xml(body)
    with pytest.raises(ET.ParseError):
        lit.parse_arxiv(body)


def test_plain_xml_still_parses():
    assert netsafe.parse_xml('<feed xmlns="http://www.w3.org/2005/Atom"/>').tag.endswith("feed")
