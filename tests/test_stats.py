"""agent-stats token accounting against a transcript fixture in Claude Code's on-disk shape.

Claude Code writes one JSONL line per content block, each repeating the message's usage. Older builds wrote the
usage seen at stream start (stop_reason null, output_tokens a handful) on every line but the last; newer builds
repeat the final usage on every line. Either way a message counts once, with its final usage.

`--cost`, `--reviews` and `--misses` (#255) run on tests/fixtures/stats: a lead session with cost records, a builder,
a resumed reviewer and a run for another repository, plus #254's review series frozen to role and verdicts.
"""

import json
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import board, config, fixloop, stats, workflow
from tests.test_gate import fake_integrate  # noqa: F401 (a fixture)

FIXTURE = Path(__file__).parent / "fixtures/transcript_usage.jsonl"


def _fixture_run() -> stats.Run:
    return stats.read_run(FIXTURE, "builder", "s")


def test_output_tokens_match_final_usage_once_per_message():
    run = _fixture_run()
    # msg_a final 781 (stream-start 6 ignored), msg_b only stream-start 8, msg_c final 698 logged three times
    assert run.output == 781 + 8 + 698
    assert run.messages == 3
    assert run.partial == 1  # msg_b never logged a final usage: its 8 is a lower bound


def test_input_tokens_counted_once_per_message():
    run = _fixture_run()
    assert run.fresh == (3 + 500) + (2 + 0) + (1 + 200)
    assert run.cached == 10000 + 12000 + 15000
    assert run.calls == 3 and run.tools == {"Read": 2, "Bash": 1}
    assert run.result_chars == 12


def test_report_names_the_usage_field(monkeypatch):
    monkeypatch.setattr(stats, "load_runs", lambda dirs: [_fixture_run()])
    text = stats.report()
    assert "usage.output_tokens" in text
    assert "1,487" in text
    assert "33%" in text  # share of messages with only a stream-start usage


def _assistant(mid: str, model: str, stop: str | None, out: int, tool: str = "") -> dict:
    content = [{"type": "tool_use", "id": f"t{mid}", "name": tool, "input": {}}] if tool else []
    usage = {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 10, "output_tokens": out}
    msg = {"id": mid, "model": model, "stop_reason": stop, "content": content, "usage": usage}
    return {"type": "assistant", "timestamp": "2026-11-01T00:00:00Z", "message": msg}


def test_scorecard_skips_synthetic_turns_and_runs_without_a_real_turn(tmp_path, monkeypatch):
    """Owner decision on #450: the scorecard counts as --cost does (load_runs)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    real = [_assistant("m1", OPUS, "end_turn", 50, "Bash"), _assistant("m2", OPUS, None, 4)]
    real.append(_assistant("syn", "<synthetic>", "stop_sequence", 0))
    (proj / "a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in real))
    (proj / "empty.jsonl").write_text(json.dumps(_assistant("s2", "<synthetic>", "stop_sequence", 0)) + "\n")
    (proj / "blank.jsonl").write_text("")
    monkeypatch.setattr(stats, "project_dirs", lambda: [proj])
    [row] = json.loads(stats.report(as_json=True))
    assert (row["type"], row["runs"], row["calls"], row["output"]) == ("main session", 1, 1, 54)
    assert row["partial_share"] == 0.5  # m2 of m1, m2; the synthetic turn is not a message


def test_report_with_no_transcripts(monkeypatch):
    monkeypatch.setattr(stats, "load_runs", lambda dirs: [])
    assert stats.report() == "no transcripts for this checkout yet"
    assert stats.report(as_json=True) == "[]"


# --- cost, reviews and misses (#255; docs/research/harness-efficiency.md §2, §4, §9) -------------------------------

STATS = Path(__file__).parent / "fixtures/stats"
OPUS = "claude-opus-5-5"
PRICES = {OPUS: {"input": 1, "cache_write_5m": 2, "cache_write_1h": 3, "cache_read": 0.5, "output": 10}}


def _merged():
    return json.loads((STATS / "merged_prs.json").read_text())


def _runs():
    return stats.load_runs([STATS / "project"])


def test_hidden_output_is_the_billed_rest_spread_per_turn():
    # s1's records hold two processes (0.5 -> 0.9, then 0.2): 2,000 + 1,000 Opus output tokens; Haiku never appears
    # in a transcript, so its 999 side-call tokens are not ours. Visible estimate: max(5, 1000 x 0.443) + 557 = 1,000.
    assert stats.hidden_per_turn(_runs()) == pytest.approx((3000 - 1000) / 2)


def test_cost_per_agent_type_and_per_merged_issue_at_configured_prices():
    out = stats.cost(_runs(), PRICES, _merged(), "proj")
    lead, builder = out["types"]["main session"], out["types"]["harness-ops:builder"]
    assert lead["tokens"] == {"input": 10, "write_1h": 1000, "write_5m": 0, "read": 0, "output": pytest.approx(1443)}
    assert lead["usd"]["total"] == pytest.approx((10 * 1 + 1000 * 3 + 1443 * 10) / 1e6)
    # two builders: #12 here (557 logged + 1,000 hidden) and one naming another repository's #12
    assert builder["runs"] == 2
    here = (2 * 1 + 500 * 3 + 1000 * 0.5 + 1557 * 10) / 1e6
    reviewer = out["types"]["harness-ops:reviewer"]["usd"]["total"]
    # issue #12: its builder, and the reviewer of PR 20, which closes #12
    assert out["issues"]["#12"]["usd"]["total"] == pytest.approx(here + reviewer)
    assert out["issues"]["#12"]["runs"] == 2
    assert out["other"]["runs"] == 1 and out["shared"]["runs"] == 1  # the lead session is shared
    assert out["merged_issues"] == 2  # #12 (closed by PR 20) and #13 (PR 21's branch), whether or not a run named it
    total = sum(r["usd"]["total"] for r in out["types"].values())
    assert out["total"] == pytest.approx(total)


def test_cost_report_prints_each_token_kind_and_the_per_issue_rate():
    text = stats.cost_report(stats.cost(_runs(), PRICES, _merged(), "proj"))
    for word in ("input", "write1h", "read", "output", "#12", "per merged issue", "0.443", "records-scaled"):
        assert word in text, word
    assert "exact" not in text


def test_cost_reconciles_with_the_cost_records_and_scales_to_them():
    out = stats.cost(_runs(), PRICES, _merged(), "proj")
    rec = out["reconcile"]
    assert rec["sessions"] == 1 and rec["recorded_usd"] == pytest.approx(0.9 + 0.2)  # per process, not the last
    assert rec["records"]["tokens"]["output"] == 2000 + 999 + 1000  # Haiku side calls are in the records
    assert rec["records"]["usd"]["output"] == pytest.approx(3000 * 10 / 1e6)  # Haiku is not priced here
    s1 = (10 * 1 + 1000 * 3 + 1443 * 10 + 2 * 1 + 500 * 3 + 1000 * 0.5 + 1557 * 10) / 1e6  # lead + builder
    assert sum(rec["transcript"]["usd"].values()) == pytest.approx(s1)
    text = stats.cost_report(out)
    own = out["total"] - out["other"]["usd"]["total"]
    assert f"${own * 1.1 / s1 / 2:,.2f} records-scaled" in text  # 2 merged issues


def test_prices_come_from_config_merge_over_defaults_and_match_exact_ids():
    cfg = config.parse({"prices": {OPUS: {"output": 30}}})
    assert cfg.prices[OPUS] == {**config.PRICES[OPUS], "output": 30}  # one key changed, the rest kept
    assert cfg.prices["claude-haiku-5-5"] == config.PRICES["claude-haiku-5-5"]
    assert config.parse({"prices": {"m-1": PRICES[OPUS]}}).prices["m-1"] == PRICES[OPUS]
    with pytest.raises(config.ConfigError, match="prices"):
        config.parse({"prices": {"new-model": {"input": 1}}})  # a new model needs every price
    with pytest.raises(config.ConfigError, match="prices"):
        config.parse({"prices": {OPUS: {"output": "10"}}})
    with pytest.raises(config.ConfigError, match="prices"):
        config.parse({"prices": {OPUS: {"outptu": 10}}})
    assert stats._price("claude-opus-4-8", config.PRICES) is None  # never another generation's prices
    assert stats._price("claude-haiku-5-5-20261001", config.PRICES) == config.PRICES["claude-haiku-5-5"]


def test_turns_of_unpriced_models_are_listed_not_priced():
    out = stats.cost(_runs(), {"claude-sonnet-5-5": PRICES[OPUS]}, _merged(), "proj")
    assert out["total"] == 0 and out["unpriced"][OPUS] == 5
    assert f"{OPUS} (5 turns)" in stats.cost_report(out)


@pytest.mark.parametrize(
    ("report", "want"),
    [
        ("Verdict: merge.", "pass"),
        ("**Verdict: merge after fixes.** Findings: 1. x", "fix"),
        ("Verdict: send back. ## Findings", "fix"),
        ("PR #20 looks fine; approve", "pass"),
        ("", "none"),
    ],
)
def test_verdict_reads_the_first_verdict_word(report, want):
    assert stats.verdict(report) == want


def test_a_resumed_reviewer_is_one_round_per_hand_back():
    rounds = stats.rounds(STATS / "project/s2/subagents/agent-r.jsonl")
    assert [stats.verdict(r["report"]) for r in rounds] == ["fix", "pass"]  # the mid-round correction is not a round
    assert stats.review_series(_runs(), _merged(), "proj") == {(20, "code"): ["fix", "pass"]}


def test_convergence_reproduces_the_254_baseline():
    data = json.loads((STATS / "review_series_254.json").read_text())
    conv = stats.convergence((s["role"], s["verdicts"]) for s in data["series"])
    assert (conv["all"]["converged"], conv["all"]["series"]) == (135, 169)
    assert {r: (conv[r]["converged"], conv[r]["series"]) for r in ("code", "audit", "security")} == {
        "code": (43, 47),
        "audit": (48, 55),
        "security": (44, 67),
    }
    assert conv["all"]["mean_to_pass"] == pytest.approx(1.81, abs=0.005)
    assert conv["all"]["five_plus"] == 7 and conv["security"]["five_plus"] == 7


def test_reviews_report_lists_long_series_by_pr():
    text = stats.reviews_report({(20, "code"): ["fix", "pass"], (31, "security"): ["fix"] * 5 + ["pass"]})
    assert "1/2" in text  # one of two series converged
    assert "#31 security 6" in text


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_misses_blame_the_pr_whose_lines_a_bug_fix_changes(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    shas = []
    for name, text in (("base", "a = 0\n"), ("pr1", "a = 0\nx = 1\n"), ("pr2", "a = 0\nx = 2\n")):
        (tmp_path / "m.py").write_text(text)
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests/test_m.py").write_text(f"# {name}\n")  # test files are never blamed
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", name)
        shas.append(_git(tmp_path, "rev-parse", "HEAD"))
    pr = {"closingIssuesReferences": [], "commits": []}
    prs = [
        {
            **pr,
            "number": 1,
            "headRefName": "5-feature",
            "mergedAt": "2026-10-11T00:00:00Z",
            "mergeCommit": {"oid": shas[1]},
            "commits": [{"oid": shas[1]}],
        },
        {
            **pr,
            "number": 2,
            "headRefName": "9-fix-x",
            "mergedAt": "2026-10-13T00:00:00Z",
            "mergeCommit": {"oid": shas[2]},
            "commits": [{"oid": shas[2]}],
        },
    ]
    bugs = {9: {"createdAt": "2026-10-12T00:00:00Z", "title": "x is wrong"}}
    pairs = stats.trace(prs, bugs, lambda *a: _git(tmp_path, *a))
    assert pairs == [{"intro": 1, "fix": 2, "bugs": [9], "after_merge": True, "path": "m.py", "lines": 1}]
    text = stats.misses_report(pairs, {1: {"code": 2}, 3: {"security": 1}})
    assert "#1 (code 2) <- fix #2" in text and "bug #9" in text
    assert "1 of 2 reviewed PRs" in text


def test_merged_prs_flattens_the_graphql_pages_and_keeps_the_window(monkeypatch):
    node = {
        "number": 20,
        "headRefName": "12-thing",
        "mergedAt": "2026-10-13T12:00:00Z",
        "mergeCommit": {"oid": "a"},
        "closingIssuesReferences": {"nodes": [{"number": 12}]},
        "commits": {"nodes": [{"commit": {"oid": "b"}}]},
    }
    late = {**node, "number": 21, "mergedAt": "2026-10-20T00:00:00Z"}
    monkeypatch.setattr(stats.board, "repo", lambda: "o/proj")
    monkeypatch.setattr(stats.board, "gh", lambda *a: json.dumps(node) + "\n" + json.dumps(late) + "\n")
    prs = stats.merged_prs("2026-10-11", "2026-10-16")
    assert [p["number"] for p in prs] == [20]
    assert prs[0]["closingIssuesReferences"] == [{"number": 12}] and prs[0]["commits"] == [{"oid": "b"}]


def test_each_mode_runs_end_to_end_on_the_fixture(monkeypatch):
    monkeypatch.setattr(stats, "project_dirs", lambda: [STATS / "project"])
    monkeypatch.setattr(stats, "merged_prs", lambda since, until: _merged())
    monkeypatch.setattr(stats, "bug_issues", dict)
    monkeypatch.setattr(stats, "main_checkout", lambda: Path("/x/proj"))
    monkeypatch.setattr(stats.config, "get", lambda: config.Config(prices=PRICES))
    assert "per merged issue" in stats.run("cost")
    assert "code" in stats.run("reviews") and json.loads(stats.run("reviews", as_json=True))[0]["pr"] == 20
    assert "0 of 1 reviewed PRs" in stats.run("misses")


# --- reviewer finding dispositions (#83) -----------------------------------------------------------------------------

ME = "owner"
VERDICT1 = "Verdict: send back, round 1, abc1234\n- **Blocking** a.py:3: off by one\n- **Nit** a.py:9: name\n"
VERDICT2 = (
    "Verdict: merge, round 2, def5678\n- Fixes checked:\n  - probe re-run, passes\n- **Follow-up** b.py:1: cache it\n"
    "prose that is no finding\n  - **Nit** c.py:2: a nested one counts"
)  # untagged bullets are the review's notes, not findings


def _rv(body, at, rid, who=ME):
    url = f"https://github.com/o/r/pull/7#pullrequestreview-{rid}"
    return {"author": {"login": who}, "body": body, "state": "COMMENTED", "submittedAt": at, "url": url}


def _reviews():
    return [
        _rv(VERDICT2, "2026-11-02T00:00:00Z", 22),  # out of order: findings number by submission time
        _rv(VERDICT1, "2026-11-01T00:00:00Z", 11),
        _rv("plain comment\n- **Blocking** not a verdict review", "2026-11-01T01:00:00Z", 12),
        _rv(VERDICT1, "2026-11-01T02:00:00Z", 13, who="stranger"),  # anyone can review a public PR
    ]


def _cm(body, who=ME):
    return {"author": {"login": who}, "body": body, "createdAt": "2026-11-03T00:00:00Z", "url": "https://x/c"}


def test_findings_are_the_bullets_of_trusted_verdict_reviews_numbered_by_submission():
    found = fixloop.review_findings(_reviews(), {ME})
    assert [(f.ref, f.key, f.tag) for f in found] == [
        ("F1", "11.1", "Blocking"),
        ("F2", "11.2", "Nit"),
        ("F3", "22.1", "Follow-up"),
        ("F4", "22.2", "Nit"),
    ]
    assert fixloop.review_findings(_reviews(), set()) == []


def test_dispose_specs_name_findings_and_rest_covers_the_unrecorded():
    found = fixloop.review_findings(_reviews(), {ME})
    got = fixloop.chosen(["F2=false", "rest=fixed"], found, {"22.1": "filed"})
    assert {f.ref: v for f, v in got.items()} == {"F1": "fixed", "F2": "false", "F4": "fixed"}
    for bad in (["F9=fixed"], ["F1=maybe"], ["F1"], []):
        with pytest.raises(ValueError):
            fixloop.chosen(bad, found, {})


def test_a_disposition_comment_round_trips_and_only_trusted_later_records_count():
    found = fixloop.review_findings(_reviews(), {ME})
    first = fixloop.disposition_comment(fixloop.chosen(["F1=false", "F2=fixed"], found, {}))
    again = fixloop.disposition_comment(fixloop.chosen(["F1=fixed"], found, {}))
    forged = fixloop.disposition_comment(fixloop.chosen(["F3=false"], found, {}))
    comments = [_cm(first), _cm("unrelated"), _cm(again), _cm(forged, who="stranger")]
    assert fixloop.recorded(comments, {ME}) == {"11.1": "fixed", "11.2": "fixed"}
    lines = fixloop.finding_lines(found, fixloop.recorded(comments, {ME}))
    assert lines[0].startswith("- F1 Blocking: fixed") and "no disposition" in lines[2]


def test_feedback_records_dispositions_on_the_pr(monkeypatch):
    posted = []
    monkeypatch.setattr(fixloop.board, "pr_feedback", lambda pr: {"reviews": _reviews(), "comments": []})
    monkeypatch.setattr(fixloop.board, "gh_user", lambda: ME)
    monkeypatch.setattr(fixloop.board, "marked_comments", lambda pr, marker: [_cm(b) for _, b in posted])
    monkeypatch.setattr(fixloop.board, "comment", lambda n, body: posted.append((n, body)) or "https://x/c1")
    assert "F1, F2, F3, F4" in fixloop.undisposed_hint(7) and not posted
    lines = fixloop.dispose(7, ["F1=false", "rest=fixed"])
    assert [n for n, _ in posted] == [7]
    assert fixloop.recorded([_cm(posted[0][1])], {ME}) == {"11.1": "false", "11.2": "fixed", "22.1": "fixed",
                                                           "22.2": "fixed"}  # fmt: skip
    assert any("F1 Blocking: false" in ln for ln in lines)
    assert fixloop.undisposed_hint(7) == ""
    assert fixloop.dispose(7, []) == lines and len(posted) == 1  # no specs: list only, post nothing


def _github(monkeypatch, posted, fail=False):
    """The GitHub edge of the disposition commands: the PR's reviews, the gh user, its marked comments, a post."""

    def marked(pr, marker):
        if fail:
            raise board.BoardError("offline")
        return [_cm(b) for _, b in posted]

    feedback = {"since": "2026-11-09T00:00:00Z", "head": "", "trusted": set(), "comments": [], "threads": [],
                "threads_truncated": False, "reviews": _reviews()}  # fmt: skip
    monkeypatch.setattr(board, "pr_feedback", lambda pr: feedback)
    monkeypatch.setattr(board, "gh_user", lambda: ME)
    monkeypatch.setattr(board, "marked_comments", marked)
    monkeypatch.setattr(board, "comment", lambda n, body: posted.append((n, body)) or "https://x/c1")


def test_board_feedback_dispose_on_the_command_line(monkeypatch, capsys):
    from devkit import cli

    posted = []
    _github(monkeypatch, posted)
    for argv in (["7", "--dispose", "F1=false"], ["7", "--dispose"]):
        monkeypatch.setattr("sys.argv", ["dev", "board", "feedback", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0 and "F1 Blocking: false" in capsys.readouterr().out
    assert len(posted) == 1  # the bare --dispose only lists
    monkeypatch.setattr("sys.argv", ["dev", "board", "feedback", "7", "--dispose", "F9=fixed"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and "no finding F9" in capsys.readouterr().err and len(posted) == 1


@pytest.mark.usefixtures("fake_integrate")
def test_integrate_reminds_the_lead_of_unlabelled_findings_after_a_merge(monkeypatch, capsys):
    _github(monkeypatch, [])
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0
    out = capsys.readouterr().out
    assert out.index("without a disposition: F1, F2, F3, F4") < out.index("RESULT: merged PR #7")
    _github(monkeypatch, [], fail=True)
    assert workflow.integrate(7, harness_ok=True, dry_run=False) == 0  # the merge stands; only the reminder goes
    assert "reviewer findings not read (offline)" in capsys.readouterr().out


def test_feedback_hides_the_leads_disposition_comments_but_not_a_strangers_copy():
    found = fixloop.review_findings(_reviews(), {ME})
    rec = fixloop.disposition_comment(fixloop.chosen(["F1=fixed"], found, {}))
    fb = {"since": "2026-11-01T00:00:00Z", "head": "", "trusted": {ME}, "reviews": [], "threads": [],
          "comments": [_cm(rec), _cm(rec + "\nalso: please look", who="stranger")]}  # fmt: skip
    lines = board.pr_feedback_lines(fb)
    assert len(lines) == 1 and "stranger" in lines[0]


def test_false_finding_rate_counts_labelled_findings_with_n():
    found = fixloop.review_findings(_reviews(), {ME})
    rec = fixloop.disposition_comment(fixloop.chosen(["F1=false", "F2=fixed"], found, {}))
    prs = [
        {"number": 7, "reviews": _reviews(), "comments": [_cm(rec)]},
        {"number": 8, "reviews": [], "comments": []},
    ]
    r = stats.finding_rates(prs, {ME})
    assert (r["findings"], r["labelled"], r["false"]) == (4, 2, 1)
    assert r["by_tag"]["Blocking"] == {"findings": 1, "labelled": 1, "false": 1}
    assert r["unlabelled_prs"] == [7]
    text = stats.findings_report(r)
    assert "false-finding rate: 1/2 = 50%" in text and "n=2" in text
    assert "n=0" in stats.findings_report(stats.finding_rates([], {ME}))


@given(st.integers(0, 200), st.integers(1, 200))
def test_wilson_interval_brackets_the_rate(k, n):
    k = min(k, n)
    lo, hi = stats.wilson(k, n)
    assert 0 <= lo <= k / n <= hi <= 1


def test_findings_mode_reads_merged_prs_from_github(monkeypatch):
    node = {"number": 7, "mergedAt": "2026-11-05T00:00:00Z", "reviews": {"nodes": _reviews()},
            "comments": {"nodes": []}}  # fmt: skip
    old = node | {"number": 6, "mergedAt": "2026-10-01T00:00:00Z"}
    monkeypatch.setattr(stats.board, "repo", lambda: "o/proj")
    monkeypatch.setattr(stats.board, "gh_user", lambda: ME)
    monkeypatch.setattr(stats.board, "gh", lambda *a: json.dumps(node) + "\n" + json.dumps(old) + "\n")
    monkeypatch.setattr(stats, "load_runs", lambda *a: pytest.fail("findings need no transcripts"))
    text = stats.run("findings", since="2026-11-01")
    assert "4 findings on 1 merged PRs" in text and "n=0" in text
    assert json.loads(stats.run("findings", since="2026-11-01", as_json=True))["findings"] == 4
