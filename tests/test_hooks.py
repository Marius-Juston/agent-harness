"""Adversarial tests for the hooks: what agents really type, and what nobody planned for."""

import importlib.util
import json
import os
import re
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
# The research API keys file (#23): dev lit reads it itself, no agent ever needs its contents.
SECRETS_READS = [
    "cat ~/.config/api-keys.env",
    "head -2 ~/.config/api-keys.env",
    "grep S2 ~/.config/api-keys.env",
    "sed -n p $HOME/.config/api-keys.env",
    'cat "$DEVKIT_SECRETS_FILE"',
    "tail ${DEVKIT_SECRETS_FILE}",
    "cat < ~/.config/api-keys.env",
    "source ~/.config/api-keys.env",
    ". ~/.config/api-keys.env",
    "cat ~/.config/api-*",
    "cat ~/.config/*",
    "bash -c 'cat ~/.config/api-keys.env'",
    "python3 -c \"print(open('/x/.config/api-keys.env').read())\"",
    "awk 1 /x/api-keys.env | head",
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
    # python fed on stdin or -c: its string literals are still scanned (issue #7)
    "python3 - <<'EOF'\nimport os\nos.system('cat .env*')\nEOF",
    "python3 - <<'EOF'\nprint(open('.env').read())\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'push', 'origin', 'main'])\nEOF",
    "python3 - <<'EOF'\nimport os\nos.system('(.*?); pip install x')\nEOF",
    "python3 -c \"import os; os.system('cat .e*')\"",
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
    # python source is not shell: a regex like (.*?) after a < is not a .env glob (issue #7)
    "python3 - <<'EOF'\nimport re\nprint(re.findall(r'<a>(.*?)</a>', s))\nEOF",
    "python3 - <<'EOF'\nimport re\nm = re.search(\"<td>(.*?)</td>\", s)\nEOF",
    "python3 - <<'EOF'\nif a < len(xs): print('(.*?)')\nEOF",
    "cat page.html | python3 -c \"import re,sys; print(re.findall(r'<b>(.*?)</b>', sys.stdin.read()))\"",
    "python3 -c \"import re; print(re.sub(r'.*', '', 'x'))\"",
]


@pytest.mark.parametrize("cmd", BLOCKED + BYPASSES)
def test_guard_bash_blocks(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 2, cmd
    assert "Blocked by project rule" in err


@pytest.mark.parametrize("cmd", SECRETS_READS)
def test_guard_bash_blocks_reading_the_api_keys_file(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 2, cmd
    assert "research API keys file" in err


@pytest.mark.parametrize(
    "cmd", ["ls -la ~/.config/api-keys.env", "stat -c %a ~/.config/api-keys.env", "uv run dev lit status", "ls ~/x/*"]
)
def test_guard_bash_allows_checking_the_api_keys_file_without_reading_it(hook, cmd):
    code, _, _ = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 0, cmd


def test_guard_bash_blocks_the_file_dev_kit_secrets_names(monkeypatch):
    monkeypatch.setenv("DEVKIT_SECRETS_FILE", "/srv/keys/research.keys")
    assert GUARD.Checker("/").text("cat /srv/keys/research.keys") == GUARD.SECRETS
    assert GUARD.Checker("/").text("grep x research.keys") == GUARD.SECRETS
    assert GUARD.Checker("/").text("cat notes.md") is None


# Read-only agents (#145): an allowed read command must not run another program through config, an environment
# variable or an option, nor write a file through an option.
READ_ONLY_RUNS_CODE = [
    "git -c core.fsmonitor=x status",
    "git -c 'alias.x=!touch pwned' x",
    "git -c core.pager=x log",
    "git --config-env=core.pager=X log",
    "git -ccore.fsmonitor=x status",
    "git grep -Ox foo",
    "git grep --open-files-in-pager=x foo",
    "git grep --open foo",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=x git status",
    "GIT_CONFIG_PARAMETERS=\"'core.fsmonitor'='x'\" git status",
    "GIT_EXTERNAL_DIFF=x git diff",
    "GIT_PAGER=x git log",
    "env PAGER=x git log",
    "PAGER=x; git log",
    "LD_PRELOAD=/tmp/x.so ls",
    "sort --compress-program=x a",
    "sort -o out a",
    "sort --output=out a",
    "uniq a out",
    "tree -o out",
    "rg --pre x foo",
    "rg --pre=x foo",
    "file -C -m magic",
    "date -s 2020-01-01",
]
READ_ONLY_STILL_OK = [
    "git -C dir log",
    "git --no-pager log -1",
    "git grep -n foo",
    "GIT_PAGER=cat git log",
    "PAGER= git log",
    "NO_COLOR=1 git diff",
    "sort -u a | uniq -c",
    "uniq a",
    "rg -n foo src",
    "tree -L 2",
    "date +%F",
]


@pytest.mark.parametrize("agent", ["harness-ops:architect", "harness-ops:suite-auditor"])
@pytest.mark.parametrize("cmd", READ_ONLY_RUNS_CODE)
def test_read_only_agents_may_not_run_code_through_config_env_or_options(hook, cmd, agent):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "agent_type": agent})
    assert code == 2 and "read-only" in err, (cmd, err)


@pytest.mark.parametrize("agent", ["harness-ops:architect", "harness-ops:suite-auditor"])
@pytest.mark.parametrize("cmd", READ_ONLY_STILL_OK)
def test_read_only_agents_keep_plain_reads(hook, cmd, agent):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "agent_type": agent})
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", ALLOWED)
def test_guard_bash_allows(hook, cmd):
    code, _, _ = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 0, cmd


# Destructive local commands (#52): each loses work, maybe another worker's in a sibling worktree. The first
# eight are the probe cases from a downstream harness-gap review; the second item names the rule's message.
DESTRUCTIVE = [
    ("git reset --hard HEAD~1", "RESET"),
    ("git checkout -- .", "DISCARD"),
    ("git clean -fdx", "CLEAN"),
    ("rm -rf ../proj-worktrees", "RM"),
    ("git stash drop", "STASH"),
    ("git worktree remove --force ../x", "WORKTREE"),
    ("git push --force origin x", "FORCE"),
    ("git branch -D 12-foo", "BRANCH_D"),
    ("git reset --hard", "RESET"),
    ("git -C ../other reset --merge", "RESET"),
    ("cd ../x && git reset --hard origin/main", "RESET"),
    ("git checkout .", "DISCARD"),
    ("git checkout HEAD -- :/", "DISCARD"),
    ("git checkout -f main", "DISCARD"),
    ("git checkout --force", "DISCARD"),
    ("git checkout -- tests", "DISCARD"),  # a directory
    ("git restore .", "DISCARD"),
    ("git restore --worktree --staged .", "DISCARD"),
    ("git switch --discard-changes main", "DISCARD"),
    ("git switch -f main", "DISCARD"),
    ("git clean -f", "CLEAN"),
    ("git clean --force -d", "CLEAN"),
    ("git clean -xdf", "CLEAN"),
    ("git stash clear", "STASH"),
    ("git -C .. stash drop stash@{0}", "STASH"),
    ("git worktree remove -f ../x", "WORKTREE"),
    ("git worktree remove ../x --force", "WORKTREE"),
    ("git branch --delete --force 12-foo", "BRANCH_D"),
    ("git branch -d -f 12-foo", "BRANCH_D"),
    ("git branch -df 12-foo", "BRANCH_D"),
    ("rm -rf /", "RM"),
    ("rm -rf ~", "RM"),
    ('rm -rf "$HOME"', "RM"),
    ("rm -r ~/projects", "RM"),
    ("rm -R ../*", "RM"),
    ("rm --recursive --force ..", "RM"),
    ("rm -rf .", "RM"),  # the worktree itself, from its root
    ("rm -rf .git", "RM"),
    ("rm -fr -- ../sibling", "RM"),
    ("cd .. && rm -rf proj-worktrees", "RM"),
    ("bash -c 'git reset --hard'", "RESET"),
    ("sudo rm -rf /etc", "RM"),
    ("find . -name x -exec rm -rf ../y \\;", "RM"),
]
DESTRUCTIVE_OK = [
    "git reset",
    "git reset --soft HEAD~1",
    "git reset HEAD -- file.py",
    "git reset --keep HEAD~1",
    "git checkout main",
    "git checkout -b 12-x origin/main",
    "git checkout -- README.md",  # one file of your own worktree
    "git checkout HEAD~1 -- pyproject.toml",
    "git restore README.md",
    "git restore --staged .",
    "git switch 12-x",
    "git switch -c 12-x",
    "git clean -n",
    "git clean -nfd",  # a dry run never deletes
    "git clean --dry-run -fdx",
    "git stash list",
    "git stash push -u -m tag",
    "git stash apply abc123",
    "git stash show -p",
    "git worktree remove ../x",
    "git worktree list",
    "git worktree prune",
    "git branch -d 12-foo",
    "git branch -f 12-foo origin/main",
    "git branch -vv",
    "rm -rf build",
    "rm -rf .scratch/out dist/*",
    "rm -r ./tests/__pycache__",
    "rm -f ../notes.txt",  # not recursive
    "rm file.py",
    'rm -rf "$tmp"',  # a dynamic target cannot be checked here
    "git commit -m 'never git reset --hard or rm -rf /'",
    "echo 'git stash clear'",
]


@pytest.mark.parametrize(("cmd", "rule"), DESTRUCTIVE)
def test_guard_bash_blocks_destructive_commands_with_a_safe_alternative(hook, tmp_path, cmd, rule):
    repo = _repo(tmp_path)
    (repo / "tests").mkdir()
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "cwd": str(repo)})
    assert code == 2, cmd
    assert f"Blocked by project rule: {getattr(GUARD, rule)}" in err, (cmd, err)


@pytest.mark.parametrize("cmd", DESTRUCTIVE_OK)
def test_guard_bash_allows_benign_variants_of_destructive_commands(hook, tmp_path, cmd):
    repo = _repo(tmp_path)
    (repo / "tests").mkdir()
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}, "cwd": str(repo)})
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("rule", ["RESET", "DISCARD", "CLEAN", "RM", "STASH", "WORKTREE", "BRANCH_D"])
def test_every_destructive_rule_names_a_safe_alternative(rule):
    assert "`" in getattr(GUARD, rule), rule  # the alternative is a command to run instead


def test_guard_bash_rm_outside_a_repository_is_checked_against_the_cwd(hook, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    for cmd, code in [("rm -rf sub", 0), ("rm -rf ../other", 2), (f"rm -rf {tmp_path}", 2)]:
        got, _, _ = hook("guard_bash.py", {"tool_input": {"command": cmd}, "cwd": str(work)})
        assert got == code, cmd


def test_guard_bash_rm_allows_the_temp_dir_unless_the_work_lives_in_it(hook, tmp_path):
    """The temp dir is scratch space, but when the worktree itself sits in it, its siblings are other work."""
    temp, repo = tmp_path / "temp", _repo(tmp_path)
    temp.mkdir()
    env = {**os.environ, "TMPDIR": str(temp)}
    for cwd, code in [(repo, 0), (temp / "w", 2)]:
        got, _, _ = hook("guard_bash.py", {"tool_input": {"command": f"rm -rf {temp}/x"}, "cwd": str(cwd)}, env)
        assert got == code, cwd


GIT_CONFIG_READS = [
    "git config --get core.hooksPath",
    "git config --get-all core.hooksPath",
    "git config --get-regexp 'core\\.hooks.*'",
    "git config --global --get core.hooksPath",
    "git config --show-origin --get core.hooksPath",
    "git config --type=path --get core.hooksPath",
    "git config --file .git/config --get core.hooksPath",
    "git config core.hooksPath",
    "git config get core.hooksPath",
    "git config get --all core.hooksPath",
    "git config --list",
    "git config -l --show-origin | grep -i hookspath",
    "git config list --global",
    "git -C sub config --get core.hooksPath",
]
GIT_CONFIG_WRITES = [
    "git config core.hooksPath /dev/null",
    "git config --global core.hooksPath /dev/null",
    "git config --file .git/config core.hooksPath /dev/null",
    "git config set core.hooksPath /dev/null",
    "git config --add core.hooksPath /dev/null",
    "git config --replace-all core.hooksPath /dev/null",
    "git config --unset core.hooksPath",
    "git config --unset-all core.hooksPath",
    "git config unset core.hooksPath",
    "git config --get core.hooksPath --add core.hooksPath /dev/null",
    "git config --get --unset core.hooksPath",
    "git config -e --get core.hooksPath",
    "git -c core.hooksPath=/dev/null config --get core.hooksPath",
    "git config --rename-section core.hooksPath x",
    "git config --file=x core.hooksPath /dev/null",
]


@pytest.mark.parametrize("cmd", GIT_CONFIG_READS)
def test_guard_bash_allows_reading_hooks_path(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 0, (cmd, err)


@pytest.mark.parametrize("cmd", GIT_CONFIG_WRITES)
def test_guard_bash_blocks_writing_hooks_path(hook, cmd):
    code, _, err = hook("guard_bash.py", {"tool_input": {"command": cmd}})
    assert code == 2, cmd
    assert "core.hooksPath" in err


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
    assert why is None or why in (
        GUARD.PIP,
        GUARD.ENV,
        GUARD.SECRETS,
        GUARD.PKILL,
        GUARD.MAIN,
        GUARD.FORCE,
        GUARD.HOOKS,
    )


@given(st.text(alphabet=st.characters(blacklist_characters="'\x00"), max_size=60))
def test_guard_bash_single_quoted_text_is_data(body):
    """Any text in single quotes, as an echo argument or a commit message, never trips a rule."""
    assert GUARD.Checker("/nonexistent").text(f"git commit -m '{body}'") is None


@given(
    st.lists(st.sampled_from(["--get", "--list", "--global", "--show-origin", "--type=path"]), max_size=4),
    st.sampled_from(["--add", "--replace-all", "--unset", "--unset-all", "-e", "--edit", "--rename-section", "set"]),
    st.lists(st.sampled_from(["--get", "--local", "-z", "/dev/null"]), max_size=3),
    st.sampled_from(["core.hooksPath", "CORE.HOOKSPATH"]),
)
def test_guard_bash_git_config_write_action_is_never_a_read(before, write, after, key):
    """A write action anywhere among read flags still refuses a core.hooksPath change."""
    cmd = shlex.join(["git", "config", *before, write, *after, key, "/dev/null"])
    assert GUARD.Checker("/nonexistent").text(cmd) == GUARD.HOOKS, cmd


def _on_failure_outcome(code: int, on_failure: str) -> str:
    """Claude Code's documented PreToolUse outcome for a command hook's exit code (v2.1.295+): 0 allows, 2 blocks,
    and any other code is a failure that blocks only with `onFailure: "block"`.
    https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails"""
    if code in (0, 2):
        return "allow" if code == 0 else "block"
    return "block" if on_failure == "block" else "allow"


@pytest.mark.parametrize("name", ["guard_bash.py", "edit_guard.py"])
def test_a_crashing_guard_blocks_the_call(hook, tmp_path, name):
    """#46: a guard that cannot run (here: an import that raises) refuses the call instead of allowing it."""
    data = json.loads((HOOKS.parent / "hooks/hooks.json").read_text())
    [entry] = [h for g in data["hooks"]["PreToolUse"] for h in g["hooks"] if h["command"].endswith(f"/{name}")]
    (tmp_path / "json.py").write_text("raise RuntimeError('broken guard')\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    payload = {"tool_input": {"command": "git push origin main", "file_path": "uv.lock"}}
    code, _, err = hook(name, payload, env)
    assert code not in (0, 2) and "broken guard" in err
    assert entry.get("onFailure") == "block"
    assert _on_failure_outcome(code, entry["onFailure"]) == "block"
    assert _on_failure_outcome(code, "continue") == "allow"  # the default this replaces


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


def test_guard_bash_cross_repo_builder_works_in_the_named_worktree_with_the_same_rules(hook, tmp_path):
    """#170: a builder dispatched onto another repository's issue sits in its own isolation worktree and prefixes
    every command with `cd <named worktree> &&` (lead skill, Dispatch). Routine work there is allowed; pushes to
    main and lost work stay refused, and recursive rm stays anchored to the builder's own worktree."""
    home = _repo(tmp_path)
    target = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(home), str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "checkout", "-q", "-b", "170-x"], check=True)

    def run(cmd):
        code, _, err = hook("guard_bash.py", {"cwd": str(home), "tool_input": {"command": f"cd {target} && {cmd}"}})
        return code, err

    for cmd in ("uv run dev test --affected", "git add -A && git commit -m x", "git push -u origin 170-x", "git push"):
        assert run(cmd)[0] == 0, cmd
    for cmd, rule in [("git push origin main", "MAIN"), ("git reset --hard", "RESET"), ("rm -rf .scratch", "RM")]:
        code, err = run(cmd)
        assert code == 2 and getattr(GUARD, rule) in err, (cmd, err)


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


def _hook_entries(event: str, script: str) -> list[dict]:
    data = json.loads((HOOKS.parent / "hooks/hooks.json").read_text())
    return [g for g in data["hooks"].get(event, []) for h in g["hooks"] if h["command"].endswith(f"/{script}")]


def test_spawn_guard_runs_on_workflow_and_agent_spawns_and_at_subagent_start():
    """#85: a workflow fires PreToolUse for `Workflow`, never `Agent`, and SubagentStart for each of its agents."""
    [pre] = _hook_entries("PreToolUse", "spawn_guard.sh")
    assert re.fullmatch(pre["matcher"], "Agent") and re.fullmatch(pre["matcher"], "Workflow")
    [start] = _hook_entries("SubagentStart", "spawn_guard.sh")
    assert "matcher" not in start and start["hooks"][0]["timeout"] > 300


def test_spawn_guard_quota_denies_a_workflow_run(hook, tmp_path):
    repo = _repo(tmp_path)
    (repo / ".git/devkit").mkdir()
    (repo / ".git/devkit/usage.json").write_text(json.dumps({"pct": 96, "t": int(time.time())}))
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Workflow", "tool_input": {"name": "p:wf"}}
    _, out, _ = hook("spawn_guard.sh", payload, env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)})
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def _start(kind: str) -> dict:
    return {"hook_event_name": "SubagentStart", "agent_type": kind, "agent_id": "a1"}


@pytest.mark.parametrize("kind", ["builder", "harness-ops:builder", "harness-ops:tester", "workflow-subagent"])
def test_spawn_guard_memory_wait_times_out_at_subagent_start(hook, tmp_path, kind):
    """SubagentStart cannot refuse: after the wait the agent starts, with a warning naming the free memory."""
    repo = _repo(tmp_path)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "DEVKIT_SPAWN_FLOOR_GB": "100000", "DEVKIT_SPAWN_WAIT_S": "0"}
    code, out, _ = hook("spawn_guard.sh", _start(kind), env=env)
    assert code == 0 and "GB free" in json.loads(out)["systemMessage"]
    # The memory wait moved off PreToolUse: a spawn call is never held or denied for memory.
    code, out, _ = hook("spawn_guard.sh", {"tool_input": {"subagent_type": kind}}, env=env)
    assert code == 0 and out == ""


def test_spawn_guard_holds_a_workflow_agent_until_memory_frees(hook, tmp_path):
    """The hold is a wait: with memory below the floor the hook sleeps (here a 2 s wait, whole seconds by `date`), then lets it start."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(_repo(tmp_path)), "DEVKIT_SPAWN_FLOOR_GB": "100000"}
    start = time.monotonic()
    code, out, _ = hook("spawn_guard.sh", _start("workflow-subagent"), env={**env, "DEVKIT_SPAWN_WAIT_S": "2"})
    assert code == 0 and time.monotonic() - start >= 1 and "held workflow-subagent" in out
    code, out, _ = hook("spawn_guard.sh", _start("workflow-subagent"), env={**env, "DEVKIT_SPAWN_FLOOR_GB": "0"})
    assert code == 0 and out == ""


@pytest.mark.parametrize("kind", ["researcher", "harness-ops:reviewer", "Explore"])
def test_spawn_guard_does_not_hold_light_agents(hook, tmp_path, kind):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(_repo(tmp_path)), "DEVKIT_SPAWN_FLOOR_GB": "100000"}
    code, out, _ = hook("spawn_guard.sh", _start(kind), env=env)
    assert code == 0 and out == ""


@pytest.mark.parametrize("payload", ["", "not json", "[]", "null", json.dumps({"hook_event_name": 5})])
@pytest.mark.parametrize("usage", [None, "{not json", json.dumps({"pct": "x", "t": None})])
def test_spawn_guard_degrades_to_allow_with_warning(hook, tmp_path, payload, usage):
    """#85: malformed input or a broken usage reading never blocks a spawn (exit 0, no deny), it warns instead."""
    repo = _repo(tmp_path)
    if usage is not None:
        (repo / ".git/devkit").mkdir()
        (repo / ".git/devkit/usage.json").write_text(usage)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "DEVKIT_SPAWN_FLOOR_GB": "100000"}
    code, out, _ = hook("spawn_guard.sh", payload, env=env)
    assert code == 0 and "deny" not in out
    if out:
        assert json.loads(out)["systemMessage"].startswith("spawn_guard:")
    if payload in ("", "not json", "[]", "null"):
        assert "unreadable hook input" in out


def test_spawn_guard_allows_with_warning_when_jq_is_missing(hook, tmp_path):
    """A missing jq is a crash of the guard: the ERR trap turns it into an allow with a warning, not exit 127."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "cat", "git", "date", "awk"):
        (bin_dir / tool).symlink_to(subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip())
    env = {"PATH": str(bin_dir), "CLAUDE_PROJECT_DIR": str(_repo(tmp_path))}
    code, out, _ = hook("spawn_guard.sh", {"tool_input": {"subagent_type": "builder"}}, env=env)
    assert code == 0 and json.loads(out)["systemMessage"].startswith("spawn_guard:")


def test_worktree_create_refuses_bad_names_and_places_beside_repo(hook, tmp_path):
    repo = _repo(tmp_path)
    for bad in ("../evil", "a/b", "", "-x", "a..b"):
        code, out, _ = hook("worktree_create.sh", {"name": bad, "cwd": str(repo)})
        assert code != 0 and out == "", bad
    code, out, _ = hook("worktree_create.sh", {"name": "agent-1", "cwd": str(repo)})
    assert code == 0 and out.strip() == str(tmp_path / "proj-worktrees/agent-1")
    code, _, err = hook("worktree_create.sh", {"name": "agent-1", "cwd": str(repo)})
    assert code != 0 and "not empty" in err


FAKE_UV = """#!/usr/bin/env bash
case "$*" in
  *"board block"*) echo "$*" >> "$FAKE_LOG" ;;
  *reuse_problem*) cat > "$FAKE_LOG.body"; echo "REUSE ${FAKE_REUSE:-null}" ;;
  *python*) echo "GATE $FAKE_GATE" ;;
esac
"""
FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "issue view"*) echo "{\\"labels\\": [{\\"name\\": \\"${FAKE_STATUS:-status:in-progress}\\"}]}" ;;
  *) echo "$FAKE_PRS" ;;
esac
"""


def _builder_repo(tmp_path, gate="yes", prs=None):
    """A worker's worktree on branch 12-x, with `uv` (gate probe, board block) and `gh` (open PRs) faked on PATH."""
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "12-x"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("uv", FAKE_UV), ("gh", FAKE_GH)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    prs = [{"url": "https://x/pull/3", "headRefOid": head}] if prs is None else prs
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_GATE": gate,
        "FAKE_PRS": json.dumps(prs),
    }
    return repo, env


def _stop(repo, kind="harness-ops:builder", agent="ag1"):
    return {
        "hook_event_name": "SubagentStop",
        "agent_type": kind,
        "agent_id": agent,
        "cwd": str(repo),
        "stop_hook_active": False,
    }


def _refusal(out: str) -> str:
    data = json.loads(out)
    assert data["decision"] == "block"
    return data["reason"]


def test_completion_gate_is_wired_to_subagent_stop_without_on_failure():
    """onFailure has no effect on SubagentStop (Claude Code docs), so the entry must not pretend it fails closed."""
    [entry] = _hook_entries("SubagentStop", "completion_gate.py")
    assert re.search(entry["matcher"], "harness-ops:builder") and "onFailure" not in entry["hooks"][0]


def test_completion_gate_refuses_a_dirty_tree_with_the_command_to_run(hook, tmp_path):
    repo, env = _builder_repo(tmp_path)
    (repo / "wip.py").write_text("x = 1\n")
    code, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert code == 0 and "git add" in _refusal(out) and "wip.py" in _refusal(out)


def test_completion_gate_refuses_without_a_gate_pass_on_head(hook, tmp_path):
    repo, env = _builder_repo(tmp_path, gate="no")
    _, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert "uv run dev check" in _refusal(out)


def test_completion_gate_refuses_without_an_open_pr(hook, tmp_path):
    repo, env = _builder_repo(tmp_path, prs=[])
    _, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert "uv run dev pr 12 --summary -" in _refusal(out)
    assert "pr list --head 12-x --state open" in (tmp_path / "calls.log").read_text()


def test_completion_gate_refuses_a_pr_behind_head(hook, tmp_path):
    repo, env = _builder_repo(tmp_path, prs=[{"url": "u", "headRefOid": "0" * 40}])
    _, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert "git push" in _refusal(out)


def test_completion_gate_refuses_a_pr_whose_summary_lacks_a_reuse_line(hook, tmp_path):
    """#173: devkit decides (devkit.workflow.reuse_problem, tests in test_workflow.py); the gate passes it the body of
    the PR that shows HEAD and refuses with devkit's answer."""
    repo, env = _builder_repo(tmp_path)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    prs = [{"url": "old", "headRefOid": "0" * 40, "body": "stale"}, {"url": "u", "headRefOid": head, "body": "Closes"}]
    env = {**env, "FAKE_PRS": json.dumps(prs), "FAKE_REUSE": json.dumps("the branch adds module m.py.\nReuse: none")}
    _, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert "adds module m.py" in _refusal(out) and "gh pr edit --body-file -" in _refusal(out)
    assert (tmp_path / "calls.log.body").read_text() == "Closes"
    assert "url,headRefOid,body" in (tmp_path / "calls.log").read_text()


def test_completion_gate_fails_open_on_an_unreadable_reuse_probe(hook, tmp_path):
    repo, env = _builder_repo(tmp_path)
    (tmp_path / "bin/uv").write_text(
        '#!/usr/bin/env bash\ncase "$*" in *reuse_problem*) exit 1 ;; esac\necho GATE yes\n'
    )
    code, out, err = hook("completion_gate.py", _stop(repo), env)
    assert code not in (0, 2) and out == "" and "reuse probe" in err


def test_completion_gate_probes_fit_in_the_hook_timeout_with_room_for_git_and_gh():
    """Review on #186: the reuse probe is bounded at 60 s so both probes plus a 60 s gh call stay within 240 s."""
    spec = importlib.util.spec_from_file_location("completion_gate", HOOKS / "completion_gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    [entry] = _hook_entries("SubagentStop", "completion_gate.py")
    assert gate.REUSE_TIMEOUT == 60
    assert entry["hooks"][0]["timeout"] >= gate.GATE_TIMEOUT + gate.REUSE_TIMEOUT + 60


def test_completion_gate_allows_a_finished_builder_and_forgets_its_refusals(hook, tmp_path):
    repo, env = _builder_repo(tmp_path)
    (repo / "wip.py").write_text("x = 1\n")
    _refusal(hook("completion_gate.py", _stop(repo), env)[1])
    (repo / "wip.py").unlink()
    code, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert code == 0 and out == ""
    assert not list((repo / ".git/devkit/completion-gate").glob("*"))


def test_completion_gate_refuses_at_most_twice_then_records_a_board_block(hook, tmp_path):
    """Claude Code gives up on its own after 8 blocks, each a full turn of context: cap at 2, then block the item."""
    repo, env = _builder_repo(tmp_path, gate="no")
    outs = [hook("completion_gate.py", _stop(repo), env) for _ in range(5)]
    assert [json.loads(o).get("decision") for _, o, _ in outs] == ["block", "block", None, None, None]
    assert all(code == 0 for code, _, _ in outs)
    assert "gave up" in json.loads(outs[2][1])["systemMessage"]
    assert "already blocked" in json.loads(outs[4][1])["systemMessage"]
    blocks = [line for line in (tmp_path / "calls.log").read_text().splitlines() if "board block" in line]
    assert len(blocks) == 1 and blocks[0].startswith("run --quiet dev board block 12 --reason")
    # Another agent's refusals are counted apart.
    assert json.loads(hook("completion_gate.py", _stop(repo, agent="ag2"), env)[1])["decision"] == "block"


def test_completion_gate_counts_refusals_per_commit_up_to_a_total(hook, tmp_path):
    """Claude Code's own cap resets on every tool call, so the gate counts itself: a new commit is progress and earns
    a fresh 2 refusals, but no worker is refused more than 6 times in all."""
    repo, env = _builder_repo(tmp_path, gate="no")
    decisions = []
    for i in range(5):
        for _ in range(3):
            decisions.append(json.loads(hook("completion_gate.py", _stop(repo), env)[1]).get("decision"))
        commit = ["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q"]
        subprocess.run([*commit, "--allow-empty", "-m", f"fix {i}"], check=True)
    assert decisions[:3] == ["block", "block", None] and decisions[3:5] == ["block", "block"]
    assert decisions.count("block") == 6
    blocks = [line for line in (tmp_path / "calls.log").read_text().splitlines() if "board block" in line]
    assert len(blocks) == 5  # one per commit the gate gave up on


@pytest.mark.parametrize("kind", ["harness-ops:researcher", "tester", "workflow-subagent", ""])
def test_completion_gate_lets_other_agents_stop(hook, tmp_path, kind):
    repo, env = _builder_repo(tmp_path, gate="no")
    code, out, _ = hook("completion_gate.py", _stop(repo, kind=kind), env)
    assert code == 0 and out == "" and not (tmp_path / "calls.log").exists()


def test_completion_gate_skips_a_builder_off_an_issue_branch_with_a_warning(hook, tmp_path):
    repo, env = _builder_repo(tmp_path, gate="no")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    code, out, _ = hook("completion_gate.py", _stop(repo), env)
    assert code == 0 and "not an issue branch" in json.loads(out)["systemMessage"]


def test_completion_gate_lets_a_builder_stop_on_a_blocked_item(hook, tmp_path):
    """builder.md: a builder that finds a wrong test runs `uv run dev board block <n>` and stops, with no PR."""
    repo, env = _builder_repo(tmp_path, gate="no", prs=[])
    (repo / "wip.py").write_text("x = 1\n")
    code, out, _ = hook("completion_gate.py", _stop(repo), {**env, "FAKE_STATUS": "status:blocked"})
    assert code == 0 and "status:blocked" in json.loads(out)["systemMessage"]
    assert "board block" not in (tmp_path / "calls.log").read_text()
    # gh failing on the issue lookup is not a block: the gate still refuses.
    (tmp_path / "bin/gh").write_text("#!/usr/bin/env bash\nexit 1\n")
    assert json.loads(hook("completion_gate.py", _stop(repo), env)[1])["decision"] == "block"


@pytest.mark.parametrize("payload", ["", "not json", "[]", json.dumps({"agent_type": 5})])
def test_completion_gate_lets_garbage_through(hook, payload):
    code, out, _ = hook("completion_gate.py", payload)
    assert code == 0 and out == ""


def test_completion_gate_fails_open_only_on_its_own_crash(hook, tmp_path):
    """#46 policy for a gate onFailure cannot reach: a crash exits non-zero without a decision, which Claude Code
    treats as a non-blocking error, so the stop goes through. An unreadable gate probe is such a crash."""
    repo, env = _builder_repo(tmp_path, gate="no")
    (tmp_path / "bin/uv").write_text("#!/usr/bin/env bash\necho 'ModuleNotFoundError: devkit' >&2; exit 1\n")
    code, out, err = hook("completion_gate.py", _stop(repo), env)
    assert code not in (0, 2) and out == "" and "gate probe" in err
