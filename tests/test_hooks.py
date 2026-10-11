"""Adversarial tests for the hooks: what agents really type, and what nobody planned for."""

import importlib.util
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

HOOKS = Path(__file__).resolve().parents[1] / "plugins/harness-ops/scripts"


def _nested(wrapper: str, cmd: str, times: int) -> str:
    for _ in range(times):
        cmd = f"{wrapper} {shlex.quote(cmd)}"
    return cmd


BLOCKED = [
    "pip install requests",
    "uv run pip install x",
    "python3 -m pip install -U foo",
    "cd x && pip install y",
    "FOO=1 pip3 uninstall bar",
    "pkill -f pytest",
    "sudo pkill --full worker",
    "cat .env",
    "head -5 ../.env",
    "source .env",
    "git push origin main",
    "git push -u origin master",
    "git push --force origin 12-x",
    "git commit -m wip --no-verify",
    "cat > f <<EOF\nx\nEOF\npip install y",  # a command after a heredoc is still a command
]
# Red-team findings: each of these got past an earlier, regex-based guard.
BYPASSES = [
    # pip
    "uv pip install x",
    ".venv/bin/pip install x",
    "python3 -mpip install x",
    "env pip install x",
    "bash -c 'pip install x'",
    "time pip install x",
    # reading .env
    "grep KEY .env",
    "head -n 5 .env",
    "cat .env.production",
    "cat '.env'",
    "sed -n p .env",
    "awk 1 .env",
    "cat < .env",
    # pushing to main
    "git -C . push origin main",
    "git push origin +main",
    "git push --all",
    "git push --mirror origin",
    "git push origin HEAD:refs/heads/main",
    # force push: --force-with-lease elsewhere no longer exempts --force
    "git push --force-with-lease origin 12-x && git push --force origin 13-y",
    "git push --force-with-lease --force origin 12-x",
    "git push -uf origin 12-x",
    "git push origin +12-x",
    # skipping hooks
    "git commit -n -m wip",
    "git commit -anm wip",
    "git -c core.hooksPath=/dev/null commit -m wip",
    "SKIP=ruff git commit -m wip",
    "git commit --no-veri -m wip",
    # pkill and interpreters fed commands
    "pkill -9 -f pytest",
    "bash <<EOF\npip install x\nEOF",
    "cat <<'EOF' | sh\npip install x\nEOF",
    "python3 - <<'EOF'\nimport os\nos.system('pip install x')\nEOF",
    # wrappers and substitutions
    "echo $(pip install x)",
    'git commit -m "$(pip install x)"',
    "echo 'pip install x' | bash",
    "sudo -u root pip install x",
    "timeout 60 pip install x",
    "xargs pip install < reqs.txt",
    "find . -name x -exec cat .env ';'",
    "eval 'pip install x'",
    "p''ip install x",
    "$'\\x70ip' install x",
    "grep -b KEY .env",
    "cat .env*",
    'echo "$(cat .env)"',
    "cat <<EOF\n$(pip install x)\nEOF",  # an unquoted heredoc still runs its substitutions
    "echo " + "$(" * 400 + ")" * 400,  # too deep to check: refused, not waved through
    _nested("bash -c", "echo hi", 10),  # past MAX_DEPTH: fail closed
    # found in review
    "{pip,x} install y",
    "p{i,}p install y",
    "cat .{env,x}",
    "git push origin HEAD:heads/main",
    "git push origin 'refs/heads/*:refs/heads/*'",
    "export SKIP=ruff; git commit -m x",
    "declare -x PRE_COMMIT_ALLOW_NO_CONFIG=1",
    "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath",
    "SKIP=ruff",
    "flock /tmp/l -c 'pip install x'",
    "su -c 'pip install x' me",
    "script -q -c 'cat .env' /dev/null",
    "trap 'pip install x' EXIT",
    "git rebase -x 'pip install x' main",
    "git rebase --exec='git push origin main' main",
    "grep -e KEY .env",
    "grep -f .env notes.txt",
]
ALLOWED = [
    "uv add requests",
    "git commit -m 'never pip install in this repo'",
    "echo 'pkill -f is banned'",
    "cat .env.example",
    "git push -u origin 12-fix-main-loop",
    "git push --force-with-lease origin 12-x",
    "grep -n main devkit/cli.py",
    "cat > t.py <<'EOF'\npip install requests\ngit push origin main\nEOF",  # heredoc bodies are data
    "",
    # S3 false positives: quoted commit and issue text is data
    "git commit -m 'fix; pip install x'",
    'git commit -m "guard: (pkill -f x) and git push origin main"',
    "gh issue create --title t --body 'never run (pkill -f x); pip install y; cat .env'",
    "git commit -F - <<'EOF'\nfix: do not pip install\n(pkill -f x)\nEOF",
    "git commit -m \"$(cat <<'EOF'\nno pip install; git push origin main :)\nEOF\n)\"",
    "git commit -am wip",
    "git push -n origin 12-x",
    "git push --force-with-lease origin 12-x --force-if-includes",
    "git push origin main:12-x",
    "pgrep -f pytest",
    "ls -la .env",
    "grep -rn 'pip install' docs/  # comment: git push origin main",
    "python3 -c 'print(1)'",
    "git commit -m .env -m 'keep .env.local out'",
    "gh pr create --title .env --body x",
    "bash scripts/x.sh",
    # globs never match a leading dot, case patterns, .gitignore lookups
    "grep -rn TODO *",
    "wc -l tests/*",
    "du -sh *",
    "git add docs/research/*",
    "cp build/* /tmp/out/",
    "uv run ruff check devkit/*",
    "head -3 notes/*",
    'case "$b" in main) x;; *) y;; esac',
    "git check-ignore -q .env",
    "grep .env .gitignore",
    "grep -n '^.env' .gitignore",
    "git commit --message -n",
    "mkdir -p src/{a,b}",
    _nested("bash -c", "echo hi", 3),
]


@pytest.mark.parametrize("cmd", BLOCKED + BYPASSES)
def test_guard_bash_blocks(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 2, cmd
    assert "Blocked by project rule" in err


@pytest.mark.parametrize("cmd", ALLOWED)
def test_guard_bash_allows(hook, cmd):
    code, _, _ = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 0, cmd


def _guard_module():
    spec = importlib.util.spec_from_file_location("guard_bash", HOOKS / "guard_bash.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GUARD = _guard_module()


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=st.sampled_from(list("ab pi.env$()`'\"\\<>|;&#\n{}-=+*")), max_size=80))
def test_guard_bash_never_crashes(cmd):
    why = GUARD.Checker("/nonexistent").text(cmd)
    assert why is None or why in (GUARD.PIP, GUARD.ENV, GUARD.PKILL, GUARD.MAIN, GUARD.FORCE, GUARD.HOOKS)


@given(st.text(alphabet=st.characters(blacklist_characters="'\x00"), max_size=60))
def test_guard_bash_single_quoted_text_is_data(body):
    """Any text in single quotes, as an echo argument or a commit message, never trips a rule."""
    assert GUARD.Checker("/nonexistent").text(f"git commit -m '{body}'") is None


def test_guard_bash_bare_push_follows_current_branch(hook, tmp_path):
    repo = _repo(tmp_path)

    def code(cmd, cwd=repo):
        return hook("guard_bash.py", {"cwd": str(cwd), "tool_input": {"command": cmd}})[0]

    for cmd in ("git push", "git push -u origin", "git push origin HEAD", "git push origin @"):
        assert code(cmd) == 2, cmd
    assert code(f"git -C {repo} push", cwd=tmp_path) == 2
    assert code(f"cd {repo} && git push", cwd=tmp_path) == 2
    assert code(f"pushd {repo} && git push", cwd=tmp_path) == 2
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "12-x"], check=True)
    for cmd in ("git push", "git push -u origin HEAD"):
        assert code(cmd) == 0, cmd


@pytest.mark.parametrize(
    "payload", ["", "not json", "[]", json.dumps({"tool_input": None}), json.dumps({"tool_input": {"command": 5}})]
)
def test_hooks_fail_open_on_garbage(hook, payload):
    for name in ("guard_bash.py", "edit_guard.py", "read_guard.py", "lint_after_edit.py"):
        code, out, _ = hook(name, payload)
        assert code == 0 and out == "", (name, payload)


@pytest.mark.parametrize(
    ("path", "blocked"),
    [
        ("docs/generated/agent-surface.md", True),
        ("/abs/repo/docs/research/INDEX.md", True),
        ("uv.lock", True),
        (".env", True),
        ("config/.env.local", True),
        (".env.example", False),
        ("docs/research/caching.md", False),
        ("devkit/cli.py", False),
    ],
)
def test_edit_guard(hook, path, blocked):
    code, _, _ = hook("edit_guard.py", {"tool_input": {"file_path": path}})
    assert (code == 2) == blocked


def test_edit_guard_blocks_any_file_with_the_generated_marker(hook, tmp_path):
    moved = tmp_path / "reference/surface.md"
    moved.parent.mkdir()
    moved.write_text("<!-- Generated by `uv run dev docs`. Do not edit by hand. -->\n# x\n")
    plain = tmp_path / "reference/notes.md"
    plain.write_text("# notes\n")
    assert hook("edit_guard.py", {"tool_input": {"file_path": str(moved)}})[0] == 2
    assert hook("edit_guard.py", {"tool_input": {"file_path": str(plain)}})[0] == 0


def _long_file(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("".join(f"x{i} = {i}\n" for i in range(lines)))
    return p


def test_read_guard_caps_long_read(hook, tmp_path):
    p = _long_file(tmp_path, "big.py", 900)
    code, out, _ = hook("read_guard.py", {"tool_name": "Read", "tool_input": {"file_path": str(p)}})
    data = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and data["updatedInput"]["limit"] == 250 and "900 lines" in data["additionalContext"]


def test_read_guard_respects_explicit_limit_and_short_files(hook, tmp_path):
    big = _long_file(tmp_path, "big.py", 900)
    small = _long_file(tmp_path, "small.py", 50)
    for payload in (
        {"file_path": str(big), "limit": 2000},
        {"file_path": str(big), "offset": 10},
        {"file_path": str(small)},
    ):
        _, out, _ = hook("read_guard.py", {"tool_name": "Read", "tool_input": payload})
        assert out == ""


def _devkit_project(tmp_path) -> dict:
    """A project with devkit installed (`.venv/bin/dev`): the environment its hooks run in."""
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/dev").write_text("#!/bin/sh\n")
    return {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}


def test_read_guard_rewrites_plain_cat_only(hook, tmp_path):
    _long_file(tmp_path, "big.py", 900)
    env = _devkit_project(tmp_path)
    _, out, _ = hook(
        "read_guard.py", {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": "cat big.py"}}, env=env
    )
    assert json.loads(out)["hookSpecificOutput"]["updatedInput"]["command"] == "uv run --quiet dev code outline big.py"
    for cmd in ("cat big.py | grep x", "cat big.py # whole-file", "cat big.py > out.txt"):
        payload = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": cmd}}
        _, out, _ = hook("read_guard.py", payload, env=env)
        assert out == "", cmd


def test_read_guard_leaves_cat_alone_in_a_project_without_devkit(hook, tmp_path):
    _long_file(tmp_path, "big.py", 900)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": "cat big.py"}}
    assert hook("read_guard.py", payload, env=env)[1] == ""


@pytest.mark.parametrize(
    ("cmd", "rewritten"),
    [
        ("head -n 20 big.py", False),  # S3: a short head is already a window
        ("head -20 big.py", False),
        ("head big.py", False),
        ("head -n 2000 big.py", True),
        ("head -500 big.py", True),
        ("head --lines=1000 big.py", True),
        ("cat 'big.py'", True),
    ],
)
def test_read_guard_head_only_when_long(hook, tmp_path, cmd, rewritten):
    _long_file(tmp_path, "big.py", 900)
    payload = {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": cmd}}
    _, out, _ = hook("read_guard.py", payload, env=_devkit_project(tmp_path))
    assert (out != "") == rewritten, cmd


def _repo(tmp_path):
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=a@b",
            "-c",
            "user.name=a",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
    )
    return repo


def test_spawn_guard_quota_and_light_agents(hook, tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".git/devkit"
    state.mkdir(parents=True)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}

    (state / "usage.json").write_text(json.dumps({"pct": 60, "t": int(time.time()), "cap": 50}))
    _, out, _ = hook("spawn_guard.sh", {"tool_input": {"subagent_type": "harness-ops:researcher"}}, env=env)
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    (state / "usage.json").write_text(json.dumps({"pct": 60, "t": int(time.time())}))  # no cap recorded: 95
    code, out, _ = hook("spawn_guard.sh", {"tool_input": {"subagent_type": "researcher"}}, env=env)
    assert code == 0 and out == ""

    (state / "usage.json").write_text(json.dumps({"pct": 10, "t": int(time.time()), "cap": 50}))
    code, out, _ = hook("spawn_guard.sh", {"tool_input": {"subagent_type": "researcher"}}, env=env)
    assert code == 0 and out == ""

    stale = {"pct": 99, "t": int(time.time()) - 7200, "cap": 50}  # stale reading: fail open
    (state / "usage.json").write_text(json.dumps(stale))
    code, out, _ = hook(
        "spawn_guard.sh", {"tool_input": {"subagent_type": "builder"}}, env={**env, "DEVKIT_SPAWN_FLOOR_GB": "0"}
    )
    assert code == 0 and out == ""


@pytest.mark.parametrize("kind", ["builder", "harness-ops:builder", "harness-ops:tester"])
def test_spawn_guard_memory_wait_times_out(hook, tmp_path, kind):
    repo = _repo(tmp_path)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "DEVKIT_SPAWN_FLOOR_GB": "100000", "DEVKIT_SPAWN_WAIT_S": "0"}
    _, out, _ = hook("spawn_guard.sh", {"tool_input": {"subagent_type": kind}}, env=env)
    assert "GB free" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_worktree_create_refuses_bad_names_and_places_beside_repo(hook, tmp_path):
    repo = _repo(tmp_path)
    for bad in ("../evil", "a/b", "", "-x", "a..b"):
        code, out, _ = hook("worktree_create.sh", {"name": bad, "cwd": str(repo)})
        assert code != 0 and out == "", bad
    code, out, _ = hook("worktree_create.sh", {"name": "agent-1", "cwd": str(repo)})
    assert code == 0 and out.strip() == str(tmp_path / "proj-worktrees/agent-1")
    code, _, err = hook("worktree_create.sh", {"name": "agent-1", "cwd": str(repo)})
    assert code != 0 and "not empty" in err
