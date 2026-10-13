"""uv_add_guard.py (#172): `uv add` runs gates G1, G2 and G5 through `dev reuse check` before a package is installed.
Every gate result comes from devkit.reuse.check against test_reuse's fake network: nothing here reimplements a gate."""

import contextlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_reuse import TENACITY, Net, make, record, world

from devkit import cli, reuse

HOOKS = Path(__file__).resolve().parents[1] / "plugins/harness-ops/scripts"


def _module():
    spec = importlib.util.spec_from_file_location("uv_add_guard", HOOKS / "uv_add_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _module()

MIT = record("requests", "psf/requests", "HTTP for humans")
GPL = record("pyqt6", "pyqt/pyqt6", "Qt bindings", lic=("GPL-3.0-only",))
WTFPL = record("oddlib", "odd/oddlib", "odd", lic=("WTFPL",))
FRESH = record("newlib", "new/newlib", "brand new", first=10)
NAMED = {"requests": MIT, "pyqt6": GPL, "oddlib": WTFPL, "newlib": FRESH, "tenacity": TENACITY, "evil": MIT}
KNOWN = frozenset(NAMED)


def fake_world(**kw):
    return world(named=NAMED, registry=KNOWN, **kw)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """The hook's real argv run through the real `dev` CLI in-process (devkit.reuse.check), on a fake network."""
    state = {"world": fake_world(), "calls": []}

    def execute(cmd, cwd, timeout):
        state["calls"].append(cmd)
        monkeypatch.setattr(reuse, "client", lambda: make(tmp_path / str(len(state["calls"])), Net(state["world"])))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.run(cli.build().parse_args(cmd[1:]))
        return rc, out.getvalue()

    monkeypatch.setattr(G, "execute", execute)
    monkeypatch.setattr(G, "dev_binary", lambda cwd: "/fake/.venv/bin/dev")
    monkeypatch.delenv(G.ALLOW_ENV, raising=False)
    return state


def run(monkeypatch, capsys, command: str, cwd: str = "/w") -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": cwd, "tool_input": {"command": command}})))
    rc = G.main()
    out, err = capsys.readouterr()
    return rc, out, err


def context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


# ---- DONE WHEN 1: refusals ----


@pytest.mark.parametrize(
    ("cmd", "why"),
    [
        ("uv add ghostlib", r"ghostlib: G1 FAIL \(no pypi package of this name"),
        ("uv add pyqt6", r"pyqt6: G5 FAIL \(GPL-3.0-only: strong copyleft"),
    ],
)
def test_nonexistent_and_strong_copyleft_packages_are_refused(wired, monkeypatch, capsys, cmd, why):
    rc, _, err = run(monkeypatch, capsys, cmd)
    assert rc == 2 and re.search(why, err), err
    assert err.startswith("Blocked by project rule: uv add guard")


def test_a_malicious_package_is_refused(wired, monkeypatch, capsys):
    wired["world"] = fake_world(advisories=("MAL-2026-7",))
    rc, _, err = run(monkeypatch, capsys, "uv add evil")
    assert rc == 2 and "evil: G2 FAIL (malicious: MAL-2026-7)" in err


def test_a_permissive_package_passes_silently(wired, monkeypatch, capsys):
    rc, out, err = run(monkeypatch, capsys, "uv add requests")
    assert (rc, out, err) == (0, "", "")
    assert wired["calls"] == [["/fake/.venv/bin/dev", "reuse", "--json", "check", "requests"]]


def test_one_bad_name_refuses_the_whole_command_and_names_only_it(wired, monkeypatch, capsys):
    rc, _, err = run(monkeypatch, capsys, "uv add requests ghostlib tenacity")
    assert rc == 2 and "ghostlib" in err and "requests:" not in err and "tenacity:" not in err


def test_owner_override_lets_named_copyleft_through_with_a_warning(wired, monkeypatch, capsys):
    monkeypatch.setenv(G.ALLOW_ENV, "PyQt6, other")
    rc, out, _ = run(monkeypatch, capsys, "uv add pyqt6")
    assert rc == 0 and "pyqt6: G5 FAIL" in context(out) and G.ALLOW_ENV in context(out)
    monkeypatch.setenv(G.ALLOW_ENV, "pyqt6")
    wired["world"] = fake_world(advisories=("MAL-2026-7",))
    assert run(monkeypatch, capsys, "uv add pyqt6")[0] == 2  # the override is for G5 only, never G2


def test_an_inline_assignment_is_not_the_owner_override(wired, monkeypatch, capsys):
    assert run(monkeypatch, capsys, f"{G.ALLOW_ENV}=pyqt6 uv add pyqt6")[0] == 2


# ---- DONE WHEN 2: warnings ----


@pytest.mark.parametrize(
    ("name", "kw", "seen"),
    [
        ("oddlib", {}, "G5 HOLD (WTFPL: Blue Oak Lead)"),
        ("newlib", {}, "G3 HOLD (first release 10 days ago)"),
        ("tenacity", {"advisories": ("GHSA-1",)}, "G6 flag (1 open advisories)"),
        ("tenacity", {"down": ("api.deps.dev",)}, "G2 HOLD (malware not checked"),
    ],
)
def test_hold_and_flags_are_a_warning_the_agent_sees(wired, monkeypatch, capsys, name, kw, seen):
    named = NAMED
    if "down" in kw:  # a record without advisories, so only deps.dev could have answered
        named = {**NAMED, name: {k: v for k, v in TENACITY.items() if k != "advisories"}}
    wired["world"] = world(named=named, registry=KNOWN, **kw)
    rc, out, err = run(monkeypatch, capsys, f"uv add {name}")
    data = json.loads(out)
    assert rc == 0 and err == "" and data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert f"{name}: {seen}" in context(out) and "uv add guard" in data["systemMessage"]
    assert "permissionDecision" not in data["hookSpecificOutput"]  # a warning never decides the permission


# ---- DONE WHEN 3: parsing and an unreachable registry ----


@pytest.mark.parametrize(
    ("cmd", "names"),
    [
        ("uv add requests", ["requests"]),
        ("uv add 'requests[socks,security]>=2.31' 'httpx~=0.27; python_version>\"3.10\"'", ["requests", "httpx"]),
        ("uv add --dev pytest ruff==0.6", ["pytest", "ruff"]),
        ("uv add --group docs mkdocs --optional extra rich", ["mkdocs", "rich"]),
        ("uv add --group=docs Mkdocs_Material --no-sync", ["mkdocs-material"]),
        ("uv add -m 'sys_platform == \"linux\"' --rev v1 --bounds major pkg", ["pkg"]),
        ("uv -q --directory sub add a b a", ["a", "b"]),
        ("cd sub && uv add attrs", ["attrs"]),
        ("uv run uv add attrs", ["attrs"]),
        ("bash -c 'uv add attrs'", ["attrs"]),
        ("uv add -- -weird", []),
        ("uv add ./local ../x /abs/p file:///w.whl dist/a-1.0.whl 'b @ git+https://g/b' git+https://g/c", []),
        ("uv add -e ./lib", []),
        ("uv add -r requirements.txt", []),
        ("uv remove attrs", []),
        ("echo uv add attrs", []),
        ("git commit -m 'uv add attrs'", []),
        ("uv lock && uv sync", []),
    ],
)
def test_names_are_parsed_from_every_uv_add(cmd, names):
    found = [n for a in G.adds(cmd, "/w") for n in a.names]
    assert found == names


def test_skipped_sources_are_named_in_the_notes():
    [a] = G.adds("uv add ./local 'b @ git+https://g/b' -r req.txt", "/w")
    assert a.names == [] and len(a.notes) == 3
    assert any("./local" in n for n in a.notes) and any("req.txt" in n for n in a.notes)


def test_a_stdlib_name_is_still_checked_and_noted():
    [a] = G.adds("uv add pathlib", "/w")
    assert a.names == ["pathlib"] and "standard-library" in a.notes[0]


def test_a_custom_index_skips_the_pypi_gates_with_a_note():
    [a] = G.adds("uv add --index https://private/simple corp-lib", "/w")
    assert a.names == [] and "corp-lib" in a.notes[0] and "index" in a.notes[0]


NAME = st.from_regex(r"[A-Za-z][A-Za-z0-9]{0,6}([-_.][A-Za-z0-9]{1,4}){0,2}", fullmatch=True)
EXTRAS = st.lists(st.from_regex(r"[a-z]{1,5}", fullmatch=True), max_size=3)
SPEC = st.sampled_from(["", ">=1.0", "==2.*", "~=0.3", "<3,>=1", "!=1.2", ""])
MARKER = st.sampled_from(["", "; python_version >= '3.10'", ';sys_platform=="linux"'])


@given(st.lists(st.tuples(NAME, EXTRAS, SPEC, MARKER), min_size=1, max_size=4), st.sampled_from(["", "--dev "]))
def test_any_requirement_yields_its_normalised_name(reqs, flag):
    import shlex

    words = [n + (f"[{','.join(e)}]" if e else "") + s + m for n, e, s, m in reqs]
    [a] = G.adds("uv add " + flag + " ".join(shlex.quote(w) for w in words), "/w")
    want = list(dict.fromkeys(re.sub(r"[-_.]+", "-", n).lower() for n, *_ in reqs))
    assert a.names == want


def test_unreachable_registry_fails_open_with_a_visible_warning(wired, monkeypatch, capsys):
    wired["world"] = fake_world(down=("pypi.org",))
    rc, out, err = run(monkeypatch, capsys, "uv add requests")
    assert rc == 0 and err == ""
    assert "requests: not verified" in context(out) and "uv run dev reuse check requests" in context(out)


@pytest.mark.parametrize(
    ("fail", "seen"),
    [
        (lambda *a: (_ for _ in ()).throw(TimeoutError("hook budget")), "hook budget"),
        (lambda *a: (1, "Traceback: boom"), "no JSON report"),
    ],
)
def test_a_check_that_cannot_run_fails_open_with_a_warning(wired, monkeypatch, capsys, fail, seen):
    monkeypatch.setattr(G, "execute", fail)
    rc, out, _ = run(monkeypatch, capsys, "uv add requests")
    assert rc == 0 and seen in context(out) and "not checked" in context(out)


def test_without_devkit_the_hook_fails_open_with_a_warning(hook, tmp_path):
    payload = {"cwd": str(tmp_path), "tool_input": {"command": "uv add requests"}}
    code, out, err = hook("uv_add_guard.py", payload)
    assert code == 0 and err == "" and "no devkit" in context(out)


def test_commands_without_uv_add_are_silent(hook, tmp_path):
    for cmd in ("ls -la", "uv run pytest", "git add .", ""):
        assert hook("uv_add_guard.py", {"cwd": str(tmp_path), "tool_input": {"command": cmd}}) == (0, "", "")
    assert hook("uv_add_guard.py", "not json") == (0, "", "")


# ---- DONE WHEN 4: one implementation ----


def test_the_hook_reaches_devkit_reuse_check_through_the_dev_cli(wired, monkeypatch, capsys):
    """The argv the hook builds runs `dev reuse check`, whose only gate code is devkit.reuse.check."""
    seen = []
    real = reuse.check

    def spy(client, names, ecosystem="pypi", allow_copyleft=False):
        seen.append((names, ecosystem, allow_copyleft))
        return real(client, names, ecosystem, allow_copyleft)

    monkeypatch.setattr(reuse, "check", spy)
    assert run(monkeypatch, capsys, "uv add requests pyqt6")[0] == 2
    assert seen == [(["requests", "pyqt6"], "pypi", False)]
    source = (HOOKS / "uv_add_guard.py").read_text()  # no network and no gate sources of its own
    assert not re.search(r"urllib|http\.client|deps\.dev|ecosyste\.ms|blueoak|spdx\.org|pypi\.org", source)


def test_the_hook_is_wired_on_bash_with_a_budget_above_its_own_timeout():
    data = json.loads((HOOKS.parent / "hooks/hooks.json").read_text())
    [entry] = [
        (g, h)
        for g in data["hooks"]["PreToolUse"]
        for h in g["hooks"]
        if h["command"].endswith("/uv_add_guard.py")
    ]
    group, h = entry
    assert group["matcher"] == "Bash" and h["timeout"] > G.TIMEOUT
    assert "onFailure" not in h  # fail open: a crash of the guard must not stop every Bash call
