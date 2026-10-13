"""Probe H's devkit.weaken.findings with synthetic diffs of the weakening kinds repo-L's detector names."""

from devkit.weaken import findings


def diff(path, removed, added):
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        f"@@ -10,{len(removed)} +10,{len(added)} @@",
    ]
    return "\n".join(lines + [f"-{r}" for r in removed] + [f"+{a}" for a in added]) + "\n"


T = "tests/test_x.py"
cases = {
    "assert deleted": diff(T, ["    assert f(2) == 4"], []),
    "assert loosened (== -> >=)": diff(T, ["    assert f(2) == 4"], ["    assert f(2) >= 0"]),
    "assert made trivial": diff(T, ["    assert f(2) == 4"], ["    assert True"]),
    "assert swallowed in try/except": diff(
        T,
        ["    assert f(2) == 4"],
        ["    try:", "        assert f(2) == 4", "    except AssertionError:", "        pass"],
    ),
    "test renamed so it is not collected": diff(T, ["def test_f():"], ["def check_f():"]),
    # assembled from pieces: weaken.py flags a skip marker added in any .py file, this one included
    "skipif added": diff(T, [], ["@pytest.mark." + "skipif(True, reason='x')"]),
    "conftest edited (autouse patch)": diff(
        "tests/conftest.py",
        [],
        ["@pytest.fixture(autouse=True)", "def _p(monkeypatch): monkeypatch.setattr('pkg.mod.f', lambda x: 4)"],
    ),
    "pytest config: deselect via addopts": diff(
        "pyproject.toml", ['addopts = "-q"'], ["addopts = \"-q -k 'not test_f'\""]
    ),
    "golden/fixture file edited": diff("tests/fixtures/expected.json", ['{"f": 4}'], ['{"f": 5}']),
    "module under test monkeypatched": diff(T, [], ["    monkeypatch.setattr(mod, 'f', lambda x: 4)"]),
    "parametrize cases reduced": diff(
        T, ["@pytest.mark.parametrize('x', [1, 2, 3])"], ["@pytest.mark.parametrize('x', [1])"]
    ),
}
for name, d in cases.items():
    print(f"{'FLAGGED' if findings(d) else 'missed '}  {name}")
