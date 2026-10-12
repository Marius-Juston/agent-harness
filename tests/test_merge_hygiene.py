"""Merge hygiene in throwaway git repositories (#11, #38): generated files never block a merge, notes agents rewrite
conflict instead of interleaving, and integrate brings a PR that is behind main up to date by itself."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from devkit import board, paths, setup, workflow

ROOT = Path(__file__).resolve().parents[1]
ATTRIBUTES = ROOT / "template/.gitattributes"
INDEX = "docs/research/INDEX.md"
REAL_RUN = subprocess.run


def sh(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return REAL_RUN(args, cwd=cwd, capture_output=True, text=True, check=check)


def out(cwd: Path, *args: str) -> str:
    return sh(cwd, *args).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", message)
    return out(repo, "git", "rev-parse", "HEAD")


def index(tree: Path) -> str:
    """What `dev docs` writes: one row per research note."""
    notes = sorted(p.stem for p in (tree / "docs/research").glob("*.md") if p.name != "INDEX.md")
    return "# Index\n" + "".join(f"- {n}\n" for n in notes)


def note(tree: Path, name: str, text: str = "body\n") -> dict[str, str]:
    """A new note plus the index row its PR regenerates."""
    (tree / f"docs/research/{name}.md").parent.mkdir(parents=True, exist_ok=True)
    (tree / f"docs/research/{name}.md").write_text(text)
    return {f"docs/research/{name}.md": text, INDEX: index(tree)}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "git", "init", "-q", "-b", "main")
    sh(r, "git", "config", "user.email", "t@t")
    sh(r, "git", "config", "user.name", "t")
    shutil.copy(ATTRIBUTES, r / ".gitattributes")
    commit(r, {"docs/research/a.md": "one\ntwo\nthree\n", INDEX: "# Index\n- a\n"}, "init")
    return r


def branch(repo: Path, name: str, files: dict[str, str], base: str = "main") -> None:
    sh(repo, "git", "checkout", "-q", "-b", name, base)
    commit(repo, files, name)
    sh(repo, "git", "checkout", "-q", "main")


# --- .gitattributes: union only for append-only files ------------------------------------------------------------


def test_two_prs_rewriting_one_note_conflict_instead_of_interleaving(repo):
    """#38: fold-ins rewrite notes; a union merge would keep both rewrites silently."""
    branch(repo, "a", {"docs/research/a.md": "one\nTWO by a\nthree\n"})
    branch(repo, "b", {"docs/research/a.md": "one\nTWO by b\nthree\n"})
    sh(repo, "git", "merge", "-q", "--no-edit", "a")
    merge = sh(repo, "git", "merge", "--no-edit", "b", check=False)
    assert merge.returncode != 0 and "docs/research/a.md" in merge.stdout
    assert "<<<<<<<" in (repo / "docs/research/a.md").read_text()


def test_the_old_union_rule_would_have_interleaved_the_rewrites(repo):
    """The hazard the template no longer has: same merge, notes marked merge=union."""
    commit(repo, {".gitattributes": "docs/research/*.md merge=union\n"}, "union notes")
    branch(repo, "a", {"docs/research/a.md": "one\nTWO by a\nthree\n"})
    branch(repo, "b", {"docs/research/a.md": "one\nTWO by b\nthree\n"})
    sh(repo, "git", "merge", "-q", "--no-edit", "a")
    assert sh(repo, "git", "merge", "--no-edit", "b", check=False).returncode == 0
    assert (repo / "docs/research/a.md").read_text() == "one\nTWO by a\nTWO by b\nthree\n"


@pytest.mark.parametrize(
    "path", ["docs/research/_scratch/r1/log.md", "docs/log/2026-10.md", "docs/questions/OPEN_QUESTIONS.md"]
)
def test_append_only_files_keep_both_sides(repo, path):
    commit(repo, {path: "# Log\n"}, "seed")
    branch(repo, "a", {path: "# Log\n- from a\n"})
    branch(repo, "b", {path: "# Log\n- from b\n"})
    sh(repo, "git", "merge", "-q", "--no-edit", "a")
    sh(repo, "git", "merge", "-q", "--no-edit", "b")
    assert (repo / path).read_text() == "# Log\n- from a\n- from b\n"


def test_the_template_marks_only_append_only_paths_union():
    union = [line.split()[0] for line in ATTRIBUTES.read_text().splitlines() if "merge=union" in line]
    assert union == ["docs/research/_scratch/**/*.md", "docs/log/*.md", "docs/questions/OPEN_QUESTIONS.md"]
    assert (ROOT / ".gitattributes").read_text() == ATTRIBUTES.read_text()


# --- the generated-file merge driver (`dev setup`) --------------------------------------------------------------


def _generated_branches(repo: Path) -> None:
    branch(repo, "a", note(repo, "b"))
    sh(repo, "git", "checkout", "-q", "-f", "main")
    sh(repo, "git", "clean", "-qfd")
    branch(repo, "c", note(repo, "c"))
    sh(repo, "git", "checkout", "-q", "-f", "main")
    sh(repo, "git", "clean", "-qfd")
    sh(repo, "git", "merge", "-q", "--no-edit", "a")


def test_without_the_driver_two_index_rows_conflict(repo):
    _generated_branches(repo)
    assert sh(repo, "git", "merge", "--no-edit", "c", check=False).returncode != 0


def test_setup_installs_a_merge_driver_so_generated_files_never_conflict(repo):
    """#11: after `dev setup`'s git config, a plain `git merge` keeps one side; `dev docs` then regenerates."""
    for _, cmd in setup.git_steps():
        sh(repo, *cmd)
    _generated_branches(repo)
    sh(repo, "git", "merge", "-q", "--no-edit", "c")
    assert (repo / "docs/research/c.md").exists()
    assert (repo / INDEX).read_text() == "# Index\n- a\n- b\n"  # one side kept, stale: the gate catches it
    assert index(repo) == "# Index\n- a\n- b\n- c\n"


# --- integrate end to end: a real origin, fake GitHub and fake `uv run dev docs|check` ---------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A bare origin and the lead's clone (cwd). GitHub is faked over the origin's refs; `dev docs` writes the index,
    `dev check` fails on a stale one. Returns a world dict: events, origin, clone, blocked."""
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh(tmp_path, "git", "clone", "-q", str(origin), str(clone))
    sh(clone, "git", "config", "user.email", "t@t")
    sh(clone, "git", "config", "user.name", "t")
    shutil.copy(ATTRIBUTES, clone / ".gitattributes")
    commit(clone, {"docs/research/a.md": "one\ntwo\nthree\n", INDEX: "# Index\n- a\n"}, "init")
    sh(clone, "git", "push", "-q", "origin", "main")
    world = {"events": [], "origin": origin, "clone": clone, "blocked": [], "branch": "12-c", "body": "Closes #12"}

    def head() -> str:
        return out(origin, "git", "rev-parse", f"refs/heads/{world['branch']}")

    def gh(*args, **_):
        world["events"].append(("gh", *args))
        if args[:2] == ("pr", "view"):
            if args[args.index("--json") + 1] == "headRefOid":
                return json.dumps({"headRefOid": head()})
            return json.dumps(
                {
                    "baseRefName": "main",
                    "isCrossRepository": False,
                    "headRefOid": head(),
                    "headRefName": world["branch"],
                    "state": "OPEN",
                    "isDraft": False,
                    "title": "t",
                    "body": world["body"],
                    "statusCheckRollup": [],
                    "latestReviews": [],
                    "reviewDecision": "",
                }
            )
        return ""

    def run(cmd, **kw):
        if list(cmd[:2]) == ["uv", "run"]:
            tree = Path(kw["cwd"])
            world["events"].append(("uv", *cmd[4:]))
            if "docs" in cmd:
                (tree / INDEX).write_text(index(tree))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            fresh = (tree / INDEX).read_text() == index(tree)
            return subprocess.CompletedProcess(cmd, 0 if fresh else 1, "", "")
        return REAL_RUN(cmd, **kw)

    claim = board.Claim(host="box", agent="builder-12", branch="12-c", at="", token=None, cid="")
    paths.root.cache_clear()  # root() and common_dir() are cached per process: drop what other tests resolved
    paths.common_dir.cache_clear()
    monkeypatch.chdir(clone)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(board, "gh", gh)
    monkeypatch.setattr(board, "repo", lambda: "o/r")
    monkeypatch.setattr(board, "pr_feedback", lambda n: {"threads": []})
    monkeypatch.setattr(board, "get", lambda n: type("I", (), {"claim": lambda self: claim})())
    monkeypatch.setattr(board, "block", lambda n, reason: world["blocked"].append((n, reason)))
    monkeypatch.setattr(board, "merged_pr", lambda *a: world["events"].append(("merged_pr", *a)) or ["#12 -> done"])
    monkeypatch.setattr(workflow, "main_checkout", lambda: clone)
    monkeypatch.setattr(workflow, "state_dir", lambda *p: tmp_path.joinpath("state", *p))
    yield world
    paths.root.cache_clear()
    paths.common_dir.cache_clear()


def open_pr(world: dict, files: dict[str, str], number: int = 7) -> str:
    """Push the PR branch (cut from the current main) and its refs/pull/N/head, as GitHub would."""
    clone, name = world["clone"], world["branch"]
    sh(clone, "git", "checkout", "-q", "-b", name)
    tip = commit(clone, files, name)
    sh(clone, "git", "push", "-q", "origin", name)
    sh(world["origin"], "git", "update-ref", f"refs/pull/{number}/head", tip)
    sh(clone, "git", "checkout", "-q", "-f", "main")
    sh(clone, "git", "clean", "-qfd")
    return tip


def land_on_main(world: dict, files: dict[str, str]) -> None:
    """Another PR merged first: main moves on."""
    clone = world["clone"]
    commit(clone, files, "another PR")
    sh(clone, "git", "push", "-q", "origin", "main")


def last_json(text: str) -> dict:
    return json.loads(text.strip().splitlines()[-1].removeprefix("RESULT-JSON: "))


def test_integrate_updates_a_pr_behind_main_and_two_new_notes_integrate_cleanly(project, capsys):
    """#11 and #38: PR c adds a note after PR b's index row landed. Its index is stale and conflicts textually;
    integrate merges main in, regenerates, gates, pushes the update to the PR branch and merges it, unaided."""
    clone = project["clone"]
    old = open_pr(project, note(clone, "c"))
    land_on_main(project, note(clone, "b"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "merged" and res["updated"] and res["regenerated"]
    origin = project["origin"]
    tip = out(origin, "git", "rev-parse", "refs/heads/12-c")
    assert tip == res["head"] != old
    assert out(origin, "git", "show", f"{tip}:{INDEX}") == "# Index\n- a\n- b\n- c"
    main = out(origin, "git", "rev-parse", "refs/heads/main")
    assert sh(origin, "git", "merge-base", "--is-ancestor", main, tip, check=False).returncode == 0
    assert sh(origin, "git", "merge-base", "--is-ancestor", old, tip, check=False).returncode == 0
    ev = project["events"]
    merge = next(i for i, e in enumerate(ev) if e[:3] == ("gh", "pr", "merge"))
    assert ev[merge][ev[merge].index("--match-head-commit") + 1] == tip
    assert next(i for i, e in enumerate(ev) if e[0] == "merged_pr") > merge
    assert not (project["clone"].parent / "state/integrate/pr-7").exists()


def test_integrate_leaves_a_current_pr_alone(project, capsys):
    clone = project["clone"]
    old = open_pr(project, note(clone, "c"))
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    assert last_json(capsys.readouterr().out)["updated"] is False
    assert out(project["origin"], "git", "rev-parse", "refs/heads/12-c") == old


def test_integrate_sends_a_real_conflict_back(project, capsys):
    """#38: both sides rewrite one note: the PR gets the paths, the issue is blocked, the worker is named."""
    old = open_pr(project, {"docs/research/a.md": "one\nTWO by the PR\nthree\n"})
    land_on_main(project, {"docs/research/a.md": "one\nTWO on main\nthree\n"})
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "conflict" and res["conflicts"] == ["docs/research/a.md"]
    assert res["resume"] == ["builder-12@box"]
    [(n, reason)] = project["blocked"]
    assert n == 12 and reason.startswith("rebase on main") and "docs/research/a.md" in reason
    comment = next(e for e in project["events"] if e[:3] == ("gh", "pr", "comment"))
    assert "`docs/research/a.md`" in comment[comment.index("--body") + 1]
    assert not any(e[:3] == ("gh", "pr", "merge") or e[:2] == ("uv", "check") for e in project["events"])
    assert out(project["origin"], "git", "rev-parse", "refs/heads/12-c") == old  # nothing pushed
    assert not any(e[0] == "merged_pr" for e in project["events"])


def test_an_old_pr_cannot_bring_back_union_merges_for_notes(project, capsys):
    """Reviewer HIGH on #73: merging main into the head reads the head's .gitattributes. A PR cut before #38 keeps
    `docs/research/*.md merge=union`; integrate must still merge by main's rules, so the rewrites conflict."""
    old_rules = "docs/research/*.md merge=union\n" + ATTRIBUTES.read_text()
    old = open_pr(project, {".gitattributes": old_rules, "docs/research/a.md": "one\nTWO by the PR\nthree\n"})
    land_on_main(project, {"docs/research/a.md": "one\nTWO on main\nthree\n"})
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 1
    res = last_json(capsys.readouterr().out)
    assert res["outcome"] == "conflict" and res["conflicts"] == ["docs/research/a.md"]
    assert out(project["origin"], "git", "rev-parse", "refs/heads/12-c") == old
