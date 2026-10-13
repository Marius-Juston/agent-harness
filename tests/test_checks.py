"""Ratchet, doc-link and cite-check robustness. No test touches the network: `citecheck._get` is mocked."""

import json
import threading
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import checks, citecheck, config

OWNER = "Marius-Juston"
AGENT = ".claude/agents/builder.md"


@pytest.fixture(autouse=True)
def default_config(monkeypatch):
    """Tests here build a project tree under tmp_path: they read it with the template's defaults (`.claude`)."""
    monkeypatch.setattr(config, "get", lambda: config.Config())


def ratchets(words=None, defaults=None, raises=None) -> dict:
    return {"words": words or {}, "defaults": defaults or {"agent": 600}, "raise": raises or []}


def raise_(key, to, who=OWNER) -> dict:
    return {"key": key, "to": to, "reason": "r", "approved_by": who}


# --- ratchet ---------------------------------------------------------------------------------------------------------


def test_new_per_file_entry_above_main_default_needs_a_raise():
    problems = checks.ratchet_raises(ratchets(words={AGENT: 5000}), ratchets(), [AGENT], OWNER)
    assert len(problems) == 1 and "600 -> 5000" in problems[0] and AGENT in problems[0]


def test_lowering_or_keeping_budgets_is_free():
    old = ratchets(words={AGENT: 700})
    assert checks.ratchet_raises(ratchets(words={AGENT: 650}), old, [AGENT], OWNER) == []
    assert checks.ratchet_raises(ratchets(words={AGENT: 600}), ratchets(), [AGENT], OWNER) == []
    assert checks.ratchet_raises(old, old, [AGENT], OWNER) == []


def test_default_raise_needs_a_raise_for_the_default_key():
    cur = ratchets(defaults={"agent": 900})
    assert checks.ratchet_raises(cur, ratchets(), [AGENT], OWNER)
    cur["raise"] = [raise_("defaults.agent", 900)]
    assert checks.ratchet_raises(cur, ratchets(), [AGENT], OWNER) == []


def test_owner_approved_raise_is_capped_at_its_to_value():
    key = f"words.{AGENT}"
    ok = ratchets(words={AGENT: 800}, raises=[raise_(key, 800)])
    assert checks.ratchet_raises(ok, ratchets(), [AGENT], OWNER) == []
    over = ratchets(words={AGENT: 5000}, raises=[raise_(key, 800)])
    (problem,) = checks.ratchet_raises(over, ratchets(), [AGENT], OWNER)
    assert "above its [[raise]] to = 800" in problem


@pytest.mark.parametrize("who", ["owner", "", "someone-else", "Marius-Juston ", "marius juston", None])
def test_raise_approved_by_anyone_but_the_owner_login_is_refused(who):
    cur = ratchets(words={AGENT: 800}, raises=[raise_(f"words.{AGENT}", 800, who)])
    problems = checks.ratchet_raises(cur, ratchets(), [AGENT], OWNER)
    assert any("not the repository owner" in p for p in problems)
    assert any("without an owner-approved" in p for p in problems)


def test_owner_login_matches_case_insensitively_and_unknown_owner_approves_nothing():
    cur = ratchets(words={AGENT: 800}, raises=[raise_(f"words.{AGENT}", 800, "marius-juston")])
    assert checks.ratchet_raises(cur, ratchets(), [AGENT], OWNER) == []
    assert checks.ratchet_raises(cur, ratchets(), [AGENT], None)


def test_a_key_absent_on_main_is_a_raise():
    old = {"words": {}, "defaults": {}, "raise": []}
    cur = ratchets(defaults={"agent": 600, "hook": 100})
    problems = checks.ratchet_raises(cur, old, [AGENT], OWNER)
    assert any("defaults.agent raised None -> 600" in p for p in problems)
    assert any("defaults.hook raised None -> 100" in p for p in problems)
    cur["raise"] = [raise_("defaults.agent", 600), raise_("defaults.hook", 100)]
    assert checks.ratchet_raises(cur, old, [AGENT], OWNER) == []
    new_file = ".claude/agents/new.md"
    assert checks.ratchet_raises(ratchets(words={new_file: 900}), old | {"defaults": {}}, [], OWNER)


def test_raise_for_another_key_does_not_cover_a_file():
    cur = ratchets(words={AGENT: 800}, raises=[raise_("defaults.agent", 800)])
    assert checks.ratchet_raises(cur, ratchets(), [AGENT], OWNER)


def test_a_raise_already_on_main_is_not_reused_for_a_higher_value():
    key = f"words.{AGENT}"
    old = ratchets(words={AGENT: 800}, raises=[raise_(key, 800, "owner")])
    cur = ratchets(words={AGENT: 900}, raises=[raise_(key, 800, "owner")])
    assert checks.ratchet_raises(cur, old, [AGENT], OWNER)


@given(st.integers(1, 10_000), st.integers(1, 10_000), st.integers(1, 10_000))
def test_ratchet_accepts_exactly_the_covered_raises(before, value, to):
    cur = ratchets(words={AGENT: value}, raises=[raise_(f"words.{AGENT}", to)])
    problems = checks.ratchet_raises(cur, ratchets(words={AGENT: before}), [AGENT], OWNER)
    assert (problems == []) == (value <= before or value <= to)


def floors(**tests) -> dict:
    return ratchets() | {"tests": tests}


def test_a_test_floor_may_rise_or_appear_without_approval():
    assert checks.ratchet_raises(floors(min_tests=10), floors(min_tests=5), [], OWNER) == []
    assert checks.ratchet_raises(floors(min_coverage=80.0), ratchets(), [], OWNER) == []


def test_lowering_or_removing_a_test_floor_needs_an_owner_approved_raise():
    old = floors(min_tests=100, min_coverage=77)
    (problem,) = checks.ratchet_raises(floors(min_tests=90, min_coverage=77), old, [], OWNER)
    assert "tests.min_tests lowered 100 -> 90" in problem
    (problem,) = checks.ratchet_raises(floors(min_tests=100), old, [], OWNER)
    assert "tests.min_coverage lowered 77 -> None" in problem
    ok = floors(min_tests=90, min_coverage=77) | {"raise": [raise_("tests.min_tests", 90)]}
    assert checks.ratchet_raises(ok, old, [], OWNER) == []
    stranger = floors(min_tests=90, min_coverage=77) | {"raise": [raise_("tests.min_tests", 90, "someone")]}
    assert checks.ratchet_raises(stranger, old, [], OWNER)


@given(st.integers(1, 10_000), st.integers(1, 10_000), st.integers(1, 10_000))
def test_floor_accepts_exactly_the_covered_lowerings(before, value, to):
    cur = floors(min_tests=value) | {"raise": [raise_("tests.min_tests", to)]}
    problems = checks.ratchet_raises(cur, floors(min_tests=before), [], OWNER)
    assert (problems == []) == (value >= before or value >= to)


def test_floors_must_be_non_negative_numbers(tmp_path):
    for bad in ('min_tests = "x"', "min_tests = -1", "min_coverage = 101", "min_coverage = true", "max_tests = 3"):
        with pytest.raises(ValueError):
            checks.test_floors(f"[tests]\n{bad}\n")
    assert checks.test_floors("[tests]\nmin_tests = 3\nmin_coverage = 77.5\n") == {"min_tests": 3, "min_coverage": 77.5}
    assert checks.test_floors("[defaults]\nagent = 1\n") == {}


def test_repo_owner_from_origin(monkeypatch):
    monkeypatch.setattr(checks, "git", lambda *a, **k: "git@github.com:Marius-Juston/agent-harness.git")
    assert checks.repo_owner() == "Marius-Juston"
    monkeypatch.setattr(checks, "git", lambda *a, **k: "https://example.com/x/y")
    assert checks.repo_owner() is None


def test_check_ratchets_against_base(monkeypatch, tmp_path):
    (tmp_path / ".devkit").mkdir()
    (tmp_path / ".devkit/ratchets.toml").write_text(f'[defaults]\nagent = 600\n[words]\n"{AGENT}" = 5000\n')
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(checks, "measured_words", lambda: {AGENT: 1000})
    monkeypatch.setattr(checks, "git", lambda *a, **k: "[defaults]\nagent = 600\n")
    ran = checks.check_ratchets("abc123", owner=OWNER)
    assert ran.checked == 1 and len(ran.problems) == 1 and "600 -> 5000" in ran.problems[0]
    ran = checks.check_ratchets(None, owner=OWNER)
    assert not ran.problems and ran.warnings  # no merge base: said out loud, not silently skipped


def test_check_ratchets_reports_a_bad_floor_and_a_lowered_one(monkeypatch, tmp_path):
    (tmp_path / ".devkit").mkdir()
    (tmp_path / ".devkit/ratchets.toml").write_text(
        "[defaults]\nagent = 600\n[tests]\nmin_tests = 5\nmin_coverage = 120\n"
    )
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(checks, "measured_words", lambda: {AGENT: 10})
    monkeypatch.setattr(checks, "git", lambda *a, **k: "[defaults]\nagent = 600\n[tests]\nmin_tests = 9\n")
    problems = checks.check_ratchets("abc123", owner=OWNER).problems
    assert len(problems) == 2
    assert "min_coverage = 120" in problems[0] and "tests.min_tests lowered 9 -> 5" in problems[1]


def _tree(tmp_path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)


def test_measured_words_counts_initial_prompt(monkeypatch, tmp_path):
    _tree(tmp_path, {AGENT: "---\nname: builder\ninitialPrompt: one two three\n---\nbody words\n"})
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    assert checks.measured_words()[AGENT] == 5


def test_measured_words_follows_imports_but_not_code_emails_or_missing_files(monkeypatch, tmp_path):
    claude = "rules @AGENTS.md and @docs/a.md. mail me@x.io `@docs/b.md`\n```\n@docs/b.md\n```\n@nope.md ~/@x\n"
    _tree(
        tmp_path,
        {
            "CLAUDE.md": claude,
            "AGENTS.md": "four words of text\n",
            "docs/a.md": "a @c.md\n",  # relative to docs/, the importing file's directory
            "docs/c.md": "c @a.md @../CLAUDE.md\n",  # cycles are counted once
            "docs/b.md": "never counted\n",
        },
    )
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    measured = checks.measured_words()
    assert measured["CLAUDE.md"] == len(claude.split()) + 4 + 2 + 3
    assert measured["AGENTS.md"] == 4


def test_imports_stop_after_five_hops(monkeypatch, tmp_path):
    _tree(tmp_path, {"CLAUDE.md": "@d1.md\n"} | {f"d{i}.md": f"w @d{i + 1}.md\n" for i in range(1, 8)})
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    assert checks.measured_words()["CLAUDE.md"] == 1 + 5 * 2  # d1..d5 load; d6 would be a sixth hop


def test_skill_supporting_files_have_a_budget(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            ".claude/skills/s/SKILL.md": "---\nname: s\n---\nbody\n",
            ".claude/skills/s/references/r.md": "one two three\n",
            ".claude/skills/s/scripts/x.py": "print(1)\n",
            ".claude/skills/t/SKILL.md": "---\nname: t\n---\nbody\n",
            ".devkit/ratchets.toml": "[defaults]\nskill = 3\n",
        },
    )
    (tmp_path / ".claude/skills/s/logo.png").write_bytes(b"\x89PNG\xff\xfe")
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    measured = checks.measured_words()
    assert measured[".claude/skills/s/*"] == 4 and ".claude/skills/t/*" not in measured
    ran = checks.check_ratchets(None, owner=OWNER)
    assert ran.problems == [".claude/skills/s/*: 4 words > budget 3. Cut text, or ask the owner for a [[raise]]"]


def test_unscoped_rules_count_toward_the_session_context(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            "CLAUDE.md": "one two\n",
            ".claude/rules/a.md": "three four\n",  # no frontmatter: loads at launch
            ".claude/rules/sub/b.md": "---\nother: x\n---\nfive\n",  # recursive; frontmatter without paths
            ".claude/rules/bad.md": "---\npaths: [\n---\nsix\n",  # unparsable YAML loads as unscoped, whole text
            ".claude/rules/scoped.md": "---\npaths:\n  - src/**/*.py\n---\nseven eight nine\n",
            ".devkit/ratchets.toml": "[defaults]\ncontext = 8\n",
        },
    )
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    measured = checks.measured_words()
    assert measured["CLAUDE.md"] == 2 + 2 + 1 + 5  # bad.md: "---", "paths:", "[", "---", "six"
    assert measured[".claude/rules/scoped.md"] == 3  # its own per-file budget, frontmatter not counted
    assert not {".claude/rules/a.md", ".claude/rules/sub/b.md", ".claude/rules/bad.md"} & set(measured)
    ran = checks.check_ratchets(None, owner=OWNER)
    assert ran.problems == [
        f"CLAUDE.md: {measured['CLAUDE.md']} words > budget 8. Cut text, or ask the owner for a [[raise]]"
    ]


def test_unscoped_rule_pushes_the_session_over_budget(monkeypatch, tmp_path):
    _tree(tmp_path, {"CLAUDE.md": "a b c\n", ".devkit/ratchets.toml": "[defaults]\ncontext = 4\n"})
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    assert checks.check_ratchets(None, owner=OWNER).problems == []
    _tree(tmp_path, {".claude/rules/new.md": "d e\n"})
    (problem,) = checks.check_ratchets(None, owner=OWNER).problems
    assert problem.startswith("CLAUDE.md: 5 words > budget 4")


def test_unscoped_rules_without_claude_md_still_count(monkeypatch, tmp_path):
    _tree(tmp_path, {".claude/rules/a.md": "one two\n"})
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    assert checks.measured_words()["CLAUDE.md"] == 2


def test_commands_and_scoped_rules_get_per_file_budgets(monkeypatch, tmp_path):
    _tree(
        tmp_path,
        {
            ".claude/commands/deploy.md": "---\ndescription: d\n---\none two three\n",
            ".claude/commands/ops/up.md": "four\n",
            ".claude/commands/broken.md": "---\ndescription: [\n---\nx\n",  # counted as written, no crash
            ".claude/rules/py.md": "---\npaths: src/*.py\n---\nw w w w\n",
            ".devkit/ratchets.toml": "[defaults]\nskill = 2\ncontext = 3\n",
        },
    )
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    measured = checks.measured_words()
    assert measured[".claude/commands/deploy.md"] == 3 and measured[".claude/commands/ops/up.md"] == 1
    assert measured[".claude/commands/broken.md"] == 5
    assert checks._kind(".claude/commands/deploy.md") == "skill"
    assert checks._kind(".claude/rules/py.md") == "context"
    assert sorted(checks.check_ratchets(None, owner=OWNER).problems) == [
        ".claude/commands/broken.md: 5 words > budget 2. Cut text, or ask the owner for a [[raise]]",
        ".claude/commands/deploy.md: 3 words > budget 2. Cut text, or ask the owner for a [[raise]]",
        ".claude/rules/py.md: 4 words > budget 3. Cut text, or ask the owner for a [[raise]]",
    ]


# --- check_links -----------------------------------------------------------------------------------------------------


def test_inline_triple_backticks_do_not_hide_later_text():
    text = "say ```x``` inline\n[a](missing.md)\n"
    assert "missing.md" in checks.strip_fences(text)


@pytest.mark.parametrize("fence", ["```", "~~~", "````", "  ```"])
def test_fenced_blocks_at_line_start_are_removed(fence):
    text = f"before\n{fence}python\n[a](gone.md)\n{fence.strip()}\nafter [b](kept.md)\n"
    out = checks.strip_fences(text)
    assert "gone.md" not in out and "kept.md" in out and out.count("\n") == text.count("\n")


def test_a_shorter_or_other_fence_does_not_close():
    out = checks.strip_fences("````\n```\n~~~\n[a](x.md)\n````\n[b](y.md)\n")
    assert "x.md" not in out and "y.md" in out


def test_slugs_follow_github():
    assert checks.slug("Hello, World!") == "hello-world"
    assert checks.slug("`dev check` and [links](x.md)") == "dev-check-and-links"
    assert checks.anchors('# A\n## A\n## A\n<a name="Custom"></a>\n') == {"a", "a-1", "a-2", "custom"}


def _repo(monkeypatch, tmp_path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(body)
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(checks, "tracked", lambda *p: [tmp_path / n for n in sorted(files)])


def test_footnote_definitions_are_not_reference_links():
    assert checks.REF_DEF.findall("[^1]: Smith 2020, p. 4\n[ref]: docs/x.md\n") == ["docs/x.md"]


def test_check_links_checks_anchors_and_reference_links(monkeypatch, tmp_path):
    _repo(
        monkeypatch,
        tmp_path,
        {
            "a.md": "# Top\n## Next step\n[ok](#next-step) [bad](#nowhere) [x](b.md#real) [y](b.md#fake)\n"
            "say ```x``` inline then [z](missing.md)\n[ref]: c.md\n```\n[hidden](gone.md) [h](#gone)\n```\n",
            "b.md": "# Real\n```\n# Fake\n```\n",
        },
    )
    ran = checks.check_links()
    assert sorted(ran.problems) == [
        "a.md: link to missing c.md",
        "a.md: link to missing heading #fake in b.md",
        "a.md: link to missing heading #nowhere in a.md",
        "a.md: link to missing missing.md",
    ]
    assert ran.checked == 6


# --- agent surface frontmatter (#153) -------------------------------------------------------------------------------


def _surface_problems(monkeypatch, tmp_path, rel: str, text: str) -> list[str]:
    """check_surface's findings for one agent or skill file written under a throwaway surface root."""
    (tmp_path / "s" / rel).parent.mkdir(parents=True)
    (tmp_path / "s" / rel).write_text(text)
    monkeypatch.setattr(checks, "root", lambda: tmp_path)
    monkeypatch.setattr(config, "get", lambda: config.Config(surface=["s"]))
    ran = checks.check_surface()
    assert ran.checked == 1
    return ran.problems


def _md(field: str, body: str = "body") -> str:
    return f"---\nname: a\ndescription: d\n{field}\n---\n{body}\n"


@pytest.mark.parametrize(
    ("rel", "field"),
    [
        ("agents/a.md", "maxturns: 5"),  # #153: a typo is silently ignored by Claude Code
        ("agents/a.md", "argument-hint: x"),  # a skill field on an agent
        ("skills/a/SKILL.md", "when-to-use: x"),
        ("skills/a/SKILL.md", "version: 1"),  # in neither documented table: Claude Code ignores it
        ("skills/a/SKILL.md", "maxTurns: 5"),  # an agent field on a skill
    ],
)
def test_the_gate_flags_an_undocumented_frontmatter_key(monkeypatch, tmp_path, rel, field):
    problems = _surface_problems(monkeypatch, tmp_path, rel, _md(field))
    assert any("unknown frontmatter keys" in p for p in problems), problems


@pytest.mark.parametrize(
    ("rel", "field"),
    [("skills/a/SKILL.md", f) for f in ("disallowed-tools: Agent", "background: false", "shell: bash")]
    + [("agents/a.md", f) for f in ("color: cyan", "maxTurns: 40", "effort: xhigh", "memory: project")],
)
def test_the_gate_accepts_every_documented_field(monkeypatch, tmp_path, rel, field):
    """code.claude.com/docs/en/skills#frontmatter-reference and /sub-agents#supported-frontmatter-fields."""
    assert _surface_problems(monkeypatch, tmp_path, rel, _md(field)) == []


@pytest.mark.parametrize(
    ("rel", "field"),
    [
        ("agents/a.md", "effort: extreme"),
        ("agents/a.md", "isolation: true"),
        ("agents/a.md", "color: teal"),
        ("agents/a.md", "memory: global"),
        ("agents/a.md", "maxTurns: 0"),
        ("agents/a.md", "maxTurns: many"),
        ("agents/a.md", "maxTurns: true"),
        ("skills/a/SKILL.md", "effort: hgih"),
        ("skills/a/SKILL.md", "context: subagent"),
        ("skills/a/SKILL.md", "shell: zsh"),
    ],
)
def test_the_gate_flags_a_value_outside_the_documented_options(monkeypatch, tmp_path, rel, field):
    problems = _surface_problems(monkeypatch, tmp_path, rel, _md(field))
    assert any(field.split(":")[0] in p and "must be" in p for p in problems), problems


@pytest.mark.parametrize(
    "body",
    [
        "!`uv run dev status`",
        "!`true || false`",
        "!`uv run dev status | head -20 | sort`",
        "```!\nuv run dev status | head -20\ngit status --short\n```",  # a block: its last command decides
    ],
)
def test_the_gate_flags_an_injection_that_can_abort_the_skill(monkeypatch, tmp_path, body):
    """A failed !`cmd` aborts the whole skill invocation (code.claude.com/docs/en/skills#inject-dynamic-context)."""
    problems = _surface_problems(monkeypatch, tmp_path, "skills/a/SKILL.md", _md("", f"## Now\n{body}"))
    assert any("|| true" in p and "allowed-tools" in p for p in problems), problems


@pytest.mark.parametrize(
    "body",
    [
        "!`uv run dev status | head -20 || true`\nKEY=!`not run` and `!not` either",
        "!`uv run dev status | head -20`",  # without pipefail the pipeline's status is head's
        "!`uv run dev status 2>&1 | tail -n 5`",
        "```!\ngit status --short\nuv run dev status || true\n```",
    ],
)
def test_the_gate_accepts_an_injection_that_cannot_fail(monkeypatch, tmp_path, body):
    assert _surface_problems(monkeypatch, tmp_path, "skills/a/SKILL.md", _md("", f"## Now\n{body}")) == []


def test_the_gate_leaves_powershell_injections_alone(monkeypatch, tmp_path):
    """The docs give `|| true` for the bash shell only."""
    text = _md("shell: powershell", "## Now\n!`Get-Date`")
    assert _surface_problems(monkeypatch, tmp_path, "skills/a/SKILL.md", text) == []


# --- cite-check ------------------------------------------------------------------------------------------------------


@pytest.fixture
def net(monkeypatch, tmp_path):
    """Fake network: url -> (status, body), or a list of them answered in turn; counts calls. Cache in tmp_path."""
    answers: dict[str, tuple | list[tuple]] = {}
    calls: list[str] = []
    slept: list[float] = []

    def fake_get(url, timeout=20):
        calls.append(url)
        answer = answers.get(url, (200, ""))
        status, *rest = (answer.pop(0) if len(answer) > 1 else answer[0]) if isinstance(answer, list) else answer
        return (status, *rest) if len(rest) == 2 else (status, {}, rest[0])  # (status, body) or with headers

    monkeypatch.setattr(citecheck, "_get", fake_get)
    monkeypatch.setattr(citecheck, "SLEEP", slept.append)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(citecheck, "state_dir", lambda *p: tmp_path)
    return answers, calls, tmp_path


def _note(tmp_path: Path, *urls: str) -> Path:
    p = tmp_path / "note.md"
    p.write_text("\n".join(f"- [source {i}]({u})" for i, u in enumerate(urls)) + "\n")
    return p


@pytest.mark.parametrize("status", [0, 429, 500, 503])
def test_transient_errors_are_never_cached(net, status):
    answers, calls, tmp = net
    url = "https://example.org/page"
    answers[url] = (status, "timed out" if status == 0 else "")
    _, problems, _ = citecheck.check_notes([_note(tmp, url)])
    assert len(problems) == 1
    assert not (tmp / "cite-cache.json").exists()
    answers[url] = (200, "")
    _, problems, _ = citecheck.check_notes([_note(tmp, url)])
    assert problems == [] and calls.count(url) == 3  # first run: one try + one retry
    assert json.loads((tmp / "cite-cache.json").read_text())[url]["ok"] is True


def test_transient_errors_are_notes_when_offline_ok(net):
    answers, _, tmp = net
    answers["https://example.org/p"] = (0, "timed out")
    _, problems, notes = citecheck.check_notes([_note(tmp, "https://example.org/p")], offline_ok=True)
    assert problems == [] and len(notes) == 1


def test_a_transient_answer_is_retried_once(net):
    answers, calls, tmp = net
    url = "https://example.org/flaky"
    answers[url] = [(503, ""), (200, "")]
    _, problems, _ = citecheck.check_notes([_note(tmp, url)])
    assert problems == [] and calls.count(url) == 2
    assert json.loads((tmp / "cite-cache.json").read_text())[url]["ok"] is True


def test_still_transient_after_retry_is_a_note_under_ci_and_a_problem_otherwise(net, monkeypatch):
    answers, calls, tmp = net
    url = "https://example.org/limited"
    answers[url] = (429, "")
    _, problems, notes = citecheck.check_notes([_note(tmp, url)])
    assert len(problems) == 1 and notes == [] and calls.count(url) == 2
    monkeypatch.setenv("CI", "true")
    _, problems, notes = citecheck.check_notes([_note(tmp, url)])
    assert problems == [] and len(notes) == 1


def test_stale_ok_entries_from_older_versions_are_dropped_on_read(net):
    answers, calls, tmp = net
    old = {"t": 9e12, "ok": True}
    cache = {
        "https://a.org/x": {**old, "note": "HTTP 403: exists, content unverified"},
        "https://github.com/a/b": {**old, "note": "GitHub rate limit: unchecked"},
        "https://c.org/y": {**old},
    }
    (tmp / "cite-cache.json").write_text(json.dumps(cache))
    answers["https://a.org/x"] = (403, "")
    answers["https://api.github.com/repos/a/b"] = (200, '{"full_name": "a/b"}')
    _, problems, notes = citecheck.check_notes([_note(tmp, *cache)])
    assert problems == []
    assert set(calls) == {"https://a.org/x", "https://api.github.com/repos/a/b"}  # c.org/y stays cached
    assert "https://c.org/y" not in calls and any("unverified" in n for n in notes)


def test_github_rate_limit_is_not_cached_as_ok(net):
    answers, _, tmp = net
    answers["https://api.github.com/repos/a/b"] = (403, "")
    _, problems, _ = citecheck.check_notes([_note(tmp, "https://github.com/a/b")])
    assert problems and "rate limit" in problems[0]
    assert not (tmp / "cite-cache.json").exists()


@pytest.mark.parametrize("status", [401, 403])
def test_paywall_is_unverified_not_ok(net, status):
    answers, _, tmp = net
    url = "https://www.sciencedirect.com/science/article/pii/S0000000000000000"
    answers[url] = (status, "")
    _, problems, notes = citecheck.check_notes([_note(tmp, url)])
    assert problems == [] and len(notes) == 1 and "unverified" in notes[0]
    rec = json.loads((tmp / "cite-cache.json").read_text())[url]
    assert rec["ok"] is False and rec["unverified"] is True


def test_definite_404_is_cached(net):
    answers, calls, tmp = net
    answers["https://example.org/gone"] = (404, "")
    for _ in range(2):
        _, problems, _ = citecheck.check_notes([_note(tmp, "https://example.org/gone")])
        assert problems
    assert calls.count("https://example.org/gone") == 1


def test_save_cache_merges_and_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "cite-cache.json"
    path.write_text("{not json")
    citecheck.save_cache(path, {"a": {"t": 1}})
    citecheck.save_cache(path, {"b": {"t": 2}})
    assert json.loads(path.read_text()) == {"a": {"t": 1}, "b": {"t": 2}}
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def _writer(path: str, i: int) -> None:
    for j in range(20):
        citecheck.save_cache(Path(path), {f"{i}-{j}": {"t": j}})


def test_parallel_writers_and_readers_never_see_a_torn_cache(tmp_path):
    path = tmp_path / "cite-cache.json"
    citecheck.save_cache(path, {})
    bad: list[str] = []
    stop = threading.Event()

    def read():
        while not stop.is_set():
            try:
                json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                bad.append(str(exc))

    # flock is per open file, so threads contend for it exactly as separate processes would
    writers = [threading.Thread(target=_writer, args=(str(path), i)) for i in range(6)]
    for p in writers:
        p.start()
    reader = threading.Thread(target=read)
    reader.start()
    for p in writers:
        p.join(30)
    stop.set()
    reader.join()
    assert bad == []
    assert len(json.loads(path.read_text())) == 6 * 20  # no writer lost another's entries


# --- cite-check: DOIs and odd hosts (#35) ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Pediatrics study https://doi.org/10.1001/jamapediatrics.2018.0232 shows",
        "Br J Educ Psychol, doi 10.1111/j.2044-8279.2011.02041.x",
        "see files/2401.12345.pdf and run-2401.12345",
        "a2401.12345",
    ],
)
def test_a_bare_id_inside_a_doi_or_path_is_not_an_arxiv_id(text):
    assert citecheck.BARE_ARXIV.findall(text) == []


@given(
    st.sampled_from(".-/") | st.characters(categories=["L", "Nd"]),
    st.from_regex(r"\d{4}\.\d{4,5}", fullmatch=True),
    st.text(alphabet=" .,;)/-x", max_size=3),
)
def test_no_id_glued_to_a_doi_or_path_character_is_read_as_arxiv(before, ident, after):
    assert citecheck.BARE_ARXIV.findall(f"10.1000/ab{before}{ident}{after}") == []


@pytest.mark.parametrize("text", ["arXiv:2401.12345", "arXiv 2401.12345v2", "(2401.12345)", "id 2401.12345."])
def test_a_bare_arxiv_id_is_still_found(text):
    assert citecheck.BARE_ARXIV.findall(text) == ["2401.12345"]


def test_a_link_keeps_balanced_parentheses_in_a_doi():
    doi = "https://doi.org/10.1016/0361-476x(83)90019-x"
    assert citecheck.LINK.findall(f"x [T]({doi}).") == [("T", doi)]
    assert citecheck.BARE.findall(f"bare {doi} and (see https://e.org/a)") == [doi, "https://e.org/a"]


def test_a_percent_encoded_doi_is_not_quoted_twice(net):
    answers, _, tmp = net
    answers["https://api.crossref.org/works/10.1016/0361-476x%2883%2990019-x"] = (200, '{"message": {"title": ["T"]}}')
    _, problems, _ = citecheck.check_notes([_note(tmp, "https://doi.org/10.1016/0361-476x%2883%2990019-x")])
    assert problems == []


def test_a_datacite_doi_passes_after_crossref_404_and_its_title_is_compared(net):
    answers, calls, tmp = net
    doi = "10.5281/zenodo.1234567"
    answers[f"https://api.crossref.org/works/{doi}"] = (404, "")
    answers[f"https://api.datacite.org/dois/{doi}"] = (
        200,
        '{"data": {"attributes": {"titles": [{"title": "Agent Benchmark Dataset Release"}]}}}',
    )
    note = tmp / "note.md"
    note.write_text(f"- [Agent Benchmark Dataset Release](https://doi.org/{doi})\n")
    _, problems, _ = citecheck.check_notes([note])
    assert problems == [] and f"https://api.datacite.org/dois/{doi}" in calls
    note.write_text(f"- [A Completely Different Paper About Protein Folding](https://doi.org/{doi})\n")
    _, problems, _ = citecheck.check_notes([note])
    assert len(problems) == 1 and "does not match" in problems[0]


def test_a_doi_only_the_handle_server_knows_passes(net):
    answers, _, tmp = net
    doi = "10.9999/medra.42"
    answers[f"https://api.crossref.org/works/{doi}"] = (404, "")
    answers[f"https://api.datacite.org/dois/{doi}"] = (404, "")
    answers[f"https://doi.org/api/handles/{doi}"] = (200, '{"responseCode": 1, "handle": "10.9999/medra.42"}')
    _, problems, _ = citecheck.check_notes([_note(tmp, f"https://doi.org/{doi}")])
    assert problems == []


def test_a_doi_nobody_knows_is_reported_missing(net):
    answers, _, tmp = net
    doi = "10.9999/nothing"
    answers[f"https://api.crossref.org/works/{doi}"] = (404, "")
    answers[f"https://api.datacite.org/dois/{doi}"] = (404, "")
    answers[f"https://doi.org/api/handles/{doi}"] = (404, '{"responseCode": 100}')
    _, problems, _ = citecheck.check_notes([_note(tmp, f"https://doi.org/{doi}")])
    assert len(problems) == 1 and "DOI not found" in problems[0]


@pytest.mark.parametrize("status", [404, 202])
def test_a_flaky_official_source_is_unverified_and_not_cached(net, status):
    answers, calls, tmp = net
    url = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679"
    answers[url] = (status, "")
    _, problems, notes = citecheck.check_notes([_note(tmp, url)])
    assert problems == [] and len(notes) == 1 and "unverified" in notes[0]
    assert calls.count(url) == citecheck.TRIES and not (tmp / "cite-cache.json").exists()


def test_a_tls_error_is_unverified_not_a_problem(net):
    answers, calls, tmp = net
    url = "https://chapters.example.edu/ch3.pdf"
    answers[url] = (0, "<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>")
    _, problems, notes = citecheck.check_notes([_note(tmp, url)])
    assert problems == [] and len(notes) == 1 and "unverified (TLS)" in notes[0]
    assert calls.count(url) == 1  # a certificate error is not transient: no retry, never an insecure one


def _crossref(title: str, *updates: tuple[str, str]) -> str:
    """A Crossref works answer whose `updated-by` lists (type, notice DOI) pairs, as Retraction Watch data fills it."""
    by = [{"DOI": d, "type": t, "label": t.title(), "source": "retraction-watch"} for t, d in updates]
    return json.dumps({"message": {"title": [title], "updated-by": by}})


def test_a_doi_crossref_marks_retracted_is_a_problem_naming_the_notice(net):
    answers, _, tmp = net
    answers["https://api.crossref.org/works/10.1016/s0140-6736%2897%2911096-0"] = (
        200,
        _crossref("RETRACTED: Ileal-lymphoid-nodular hyperplasia", ("correction", "10.1/c"), ("retraction", "10.1/r")),
    )
    _, problems, _ = citecheck.check_notes([_note(tmp, "https://doi.org/10.1016/s0140-6736(97)11096-0")])
    assert len(problems) == 1 and "RETRACTED" in problems[0] and "https://doi.org/10.1/r" in problems[0]


@pytest.mark.parametrize("kind", ["withdrawal", "removal"])
def test_a_withdrawn_or_removed_doi_is_a_problem_too(net, kind):
    answers, _, tmp = net
    answers["https://api.crossref.org/works/10.1234/x"] = (200, _crossref("T", (kind, "10.1/n")))
    _, problems, _ = citecheck.check_notes([_note(tmp, "https://doi.org/10.1234/x")])
    assert (
        len(problems) == 1
        and "RETRACTED" in problems[0]
        and kind in problems[0]
        and "https://doi.org/10.1/n" in problems[0]
    )


@pytest.mark.parametrize("kind", ["expression_of_concern", "partial_retraction"])
def test_a_concern_or_partial_retraction_is_a_note_not_a_problem(net, kind):
    answers, _, tmp = net
    answers["https://api.crossref.org/works/10.1234/x"] = (200, _crossref("T", (kind, "10.1/n")))
    _, problems, notes = citecheck.check_notes([_note(tmp, "https://doi.org/10.1234/x")])
    assert problems == [] and len(notes) == 1 and "https://doi.org/10.1/n" in notes[0]


def test_a_doi_with_only_corrections_passes(net):
    answers, _, tmp = net
    answers["https://api.crossref.org/works/10.1234/x"] = (200, _crossref("T", ("correction", "10.1/c")))
    _, problems, notes = citecheck.check_notes([_note(tmp, "https://doi.org/10.1234/x")])
    assert problems == [] and notes == []


def test_a_cached_doi_from_before_retraction_checks_is_checked_again(net):
    answers, _, tmp = net
    url = "https://doi.org/10.1234/x"
    (tmp / "cite-cache.json").write_text(json.dumps({url: {"t": 9e99, "ok": True, "title": "T"}}))
    answers["https://api.crossref.org/works/10.1234/x"] = (200, _crossref("T", ("retraction", "10.1/r")))
    _, problems, _ = citecheck.check_notes([_note(tmp, url)])
    assert len(problems) == 1 and "RETRACTED" in problems[0]
