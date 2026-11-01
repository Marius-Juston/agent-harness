"""#431: a workflow's copies follow its action pins. Dependabot bumps `.github/workflows/ci.yml` but never the
template's copies, so `[tool.devkit] action_pins` names them and `dev docs` (which integrate runs on the merged tree)
rewrites their pins to the source's."""

import re
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from devkit import config, docs

ROOT = Path(__file__).resolve().parents[1]
OLD, NEW = "a" * 40, "b" * 40
SOURCE = f"""jobs:
  check:
    steps:
      - uses: actions/checkout@{NEW} # v7.1.0
      - if: ${{{{ !cancelled() }}}}
        uses: actions/upload-artifact@{NEW} # v7.0.2
"""
MIRROR = f"""jobs:
  check:
    steps:
      - uses: actions/checkout@{OLD} # v7.0.1
        with: {{ fetch-depth: 0 }}
      - uses: lycheeverse/lychee-action@{OLD} # v2.9.0 (lychee v0.24.2)
      - if: ${{{{ !cancelled() }}}}
        uses: actions/upload-artifact@{OLD} # v7.0.1
"""


def test_a_mirror_takes_the_source_pins_and_keeps_everything_else():
    synced = docs.synced_pins(SOURCE, MIRROR)
    assert synced == MIRROR.replace(f"checkout@{OLD} # v7.0.1", f"checkout@{NEW} # v7.1.0").replace(
        f"upload-artifact@{OLD} # v7.0.1", f"upload-artifact@{NEW} # v7.0.2"
    )
    assert f"lychee-action@{OLD} # v2.9.0 (lychee v0.24.2)" in synced  # not in the source: left alone


def test_a_comment_keeps_its_words_after_the_version():
    source = f"- uses: o/a@{NEW} # v3\n"
    assert (
        docs.synced_pins(source, f"- uses: o/a@{OLD} # v2 (pinned: see #1)\n")
        == f"- uses: o/a@{NEW} # v3 (pinned: see #1)\n"
    )
    assert docs.synced_pins(source, f"- uses: o/a@{OLD}\n") == f"- uses: o/a@{NEW} # v3\n"


def test_an_action_the_source_pins_two_ways_is_left_alone():
    source = f"- uses: o/a@{NEW} # v3\n- uses: o/a@{'c' * 40} # v4\n"
    assert docs.synced_pins(source, f"- uses: o/a@{OLD} # v2\n") == f"- uses: o/a@{OLD} # v2\n"


def test_a_subpath_action_is_its_own_action():
    source = f"- uses: github/codeql-action/init@{NEW} # v4\n"
    mirror = f"- uses: github/codeql-action/analyze@{OLD} # v3\n- uses: github/codeql-action/init@{OLD} # v3\n"
    assert docs.synced_pins(source, mirror) == f"- uses: github/codeql-action/analyze@{OLD} # v3\n{source}"


ACTION = st.from_regex(r"[a-z]{1,6}/[a-z]{1,6}", fullmatch=True)
SHA = st.from_regex(r"[0-9a-f]{40}", fullmatch=True)
PIN = st.tuples(ACTION, SHA, st.from_regex(r"v[0-9](\.[0-9]){0,2}", fullmatch=True))


def _workflow(pins: list[tuple[str, str, str]]) -> str:
    return "steps:\n" + "".join(f"  - uses: {a}@{s} # {v}\n    with: {{ x: 1 }}\n" for a, s, v in pins)


@given(st.lists(PIN, max_size=6), st.lists(PIN, max_size=6))
def test_syncing_is_idempotent_and_touches_only_pins(source_pins, mirror_pins):
    source, mirror = _workflow(source_pins), _workflow(mirror_pins)
    once = docs.synced_pins(source, mirror)
    assert docs.synced_pins(source, once) == once
    strip = re.compile(r"@[0-9a-f]{40} # \S+")
    assert strip.sub("", once) == strip.sub("", mirror)  # every other byte is the mirror's
    wanted = {}
    for a, s, v in source_pins:
        wanted.setdefault(a, set()).add((s, v))
    synced = re.findall(r"uses: (\S+)@(\S+) # (\S+)", once)
    for (action, sha, version), now in zip(mirror_pins, synced, strict=True):
        if len(pins := wanted.get(action, ())) == 1 and next(iter(pins))[0] != sha:
            assert now == (action, *next(iter(pins)))
        else:
            assert now == (action, sha, version)


def test_a_pin_on_the_same_commit_keeps_its_more_precise_label():
    assert (
        docs.synced_pins(f"- uses: o/a@{OLD} # v7\n", f"- uses: o/a@{OLD} # v7.0.1\n")
        == f"- uses: o/a@{OLD} # v7.0.1\n"
    )


def test_mirrors_come_from_the_config(tmp_path):
    (tmp_path / "ci.yml").write_text(SOURCE)
    (tmp_path / "t/w").mkdir(parents=True)
    (tmp_path / "t/w/gate.yml").write_text(MIRROR)
    (tmp_path / "t/w/notes.txt").write_text(MIRROR)  # the glob picks the files
    out = docs.pinned_mirrors(tmp_path, {"ci.yml": ["t/w/*.yml"]})
    assert out == {"t/w/gate.yml": docs.synced_pins(SOURCE, MIRROR)}
    assert docs.pinned_mirrors(tmp_path, {}) == {}
    with pytest.raises(config.ConfigError, match=r"missing\.yml"):
        docs.pinned_mirrors(tmp_path, {"missing.yml": ["t/w/*.yml"]})


def test_the_config_key_is_checked():
    assert config.parse({}).action_pins == {}
    assert config.parse({"action_pins": {"a.yml": ["b/*.yml"]}}).action_pins == {"a.yml": ["b/*.yml"]}
    for bad in ({"a.yml": "b.yml"}, {"a.yml": [1]}, {"a.yml": []}):
        with pytest.raises(config.ConfigError, match="action_pins"):
            config.parse({"action_pins": bad})


def test_this_repository_keeps_the_template_workflows_on_ci_pins():
    """The gate's freshness check covers them, so a Dependabot bump of ci.yml alone is fixed by `dev docs`."""
    pins = config.get().action_pins
    assert pins == {".github/workflows/ci.yml": ["template/.github/workflows/*.yml"]}
    rendered = docs.render([])
    assert {f"template/.github/workflows/{n}.yml" for n in ("gate", "board", "maintenance")} <= set(rendered)
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    used = {s["uses"] for j in ci["jobs"].values() for s in j["steps"] if "uses" in s}
    for rel in ("template/.github/workflows/gate.yml", "template/.github/workflows/board.yml"):
        steps = [s for j in yaml.safe_load(rendered[rel])["jobs"].values() for s in j["steps"]]
        for s in steps:
            if "uses" in s and any(u.split("@")[0] == s["uses"].split("@")[0] for u in used):
                assert s["uses"] in used
