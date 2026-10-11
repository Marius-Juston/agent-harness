"""devkit.backoff and its three users (dev lit, cite-check, the gh wrapper): full jitter, Retry-After, escalating
shared cooldowns, spread wake-ups (#25). No network: fake clocks, fake sleeps, seeded random sources."""

import contextlib
import json
import multiprocessing
import random
import subprocess
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import backoff, board, citecheck, lit
from tests.test_lit import OA, Clock, FakeNet, make, oa_body

# ---- the helper ----


@given(st.integers(0, 200), st.floats(0.01, 100), st.floats(0.01, 1000), st.integers(0, 2**32))
def test_full_jitter_stays_inside_its_capped_window(attempt, base, cap, seed):
    w = backoff.full_jitter(attempt, random.Random(seed), base, cap)
    assert 0 <= w <= min(cap, base * 2 ** min(attempt, 62))


def test_full_jitter_is_deterministic_under_a_seed_and_uses_the_whole_window():
    a = [backoff.full_jitter(3, random.Random(7)) for _ in range(2)]
    assert a[0] == a[1] == random.Random(7).uniform(0, 16)
    draws = [backoff.full_jitter(3, random.Random(i)) for i in range(400)]
    assert min(draws) < 2 and max(draws) > 14  # full jitter: from near 0 to the window's top, not a narrow band


NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    ("headers", "want"),
    [
        ({"Retry-After": "7"}, 7.0),
        ({"retry-after": " 120 "}, 120.0),
        ({"Retry-After": format_datetime(datetime.fromtimestamp(NOW + 90, UTC), usegmt=True)}, 90.0),
        ({"Retry-After": format_datetime(datetime.fromtimestamp(NOW - 90, UTC), usegmt=True)}, 0.0),  # past date
        ({"Retry-After": "soon"}, 0.0),
        ({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 42))}, 42.0),
        ({"X-RateLimit-Remaining": "17", "X-RateLimit-Reset": str(int(NOW + 42))}, 0.0),  # not exhausted
        ({"Retry-After": "5", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(NOW + 42))}, 5.0),
        ({}, 0.0),
    ],
)
def test_hinted_reads_retry_after_seconds_and_dates_and_github_reset(headers, want):
    assert backoff.hinted(headers, NOW) == pytest.approx(want)


@given(st.floats(0.001, 1e6), st.integers(0, 2**32))
def test_a_hint_is_a_lower_bound_stretched_at_most_by_spread(hint, seed):
    w = backoff.wait(0, hint, random.Random(seed))
    assert hint <= w <= hint * (1 + backoff.SPREAD)


def test_escalated_doubles_then_caps():
    assert [backoff.escalated(n) for n in range(1, 6)] == [2, 4, 8, 16, 32]
    assert backoff.escalated(100) == backoff.COOLDOWN_CAP


def test_retry_sleeps_full_jitter_until_final_and_honours_a_hint():
    answers = iter([(503, 0.0), (503, 0.0), (429, 9.0), (200, None)])
    slept: list[float] = []
    res = backoff.retry(lambda: next(answers), lambda r: r[1], tries=5, sleep=slept.append, rng=random.Random(3))
    assert res == (200, None)
    ref = random.Random(3)
    assert slept == [ref.uniform(0, 2), ref.uniform(0, 4), 9.0 * (1 + ref.uniform(0, backoff.SPREAD))]


def test_retry_gives_up_on_a_wait_past_max_wait_and_after_its_tries():
    slept: list[float] = []
    calls = []
    res = backoff.retry(
        lambda: calls.append(1) or 429, lambda r: 3600.0, tries=4, sleep=slept.append, rng=random.Random(), max_wait=60
    )
    assert res == 429 and len(calls) == 1 and slept == []
    res = backoff.retry(lambda: calls.append(1) or 500, lambda r: 0.0, tries=3, sleep=slept.append, rng=random.Random())
    assert len(calls) == 4 and len(slept) == 2  # no sleep after the last try


# ---- dev lit ----


def _holds(tmp_path: Path, source: str = "openalex", field: str = "until") -> float:
    return json.loads((tmp_path / "buckets.json").read_text())[source].get(field, 0.0)


def test_lit_shared_cooldown_escalates_across_calls_and_resets_on_a_200(tmp_path):
    clock = Clock()
    net = FakeNet({OA: [(429, {}, "")]})
    c = make(tmp_path, net, clock=clock)
    holds = []
    for _ in range(4):  # four separate calls, each one try, each answered 429 with no Retry-After
        with pytest.raises(lit.SourceError):
            c.fetch("openalex", OA + "works?x", tries=1)
        holds.append(_holds(tmp_path) - clock())
        clock.t = _holds(tmp_path) + 10  # past the hold, inside the streak window
    assert holds == [2, 4, 8, 16]
    other = make(tmp_path, FakeNet({OA: [(429, {}, "")]}), clock=clock)  # another process shares the streak
    with pytest.raises(lit.SourceError):
        other.fetch("openalex", OA + "works?x", tries=1)
    assert _holds(tmp_path) - clock() == 32
    clock.t = _holds(tmp_path) + 10
    make(tmp_path, FakeNet({OA: [(200, {}, oa_body(1))]}), clock=clock).fetch("openalex", OA + "works?ok", tries=1)
    with pytest.raises(lit.SourceError):
        c.fetch("openalex", OA + "works?x", tries=1)
    assert _holds(tmp_path) - clock() == 2  # the 200 reset the streak


def test_lit_streak_restarts_after_a_quiet_window_and_is_kept_per_pool(tmp_path):
    clock = Clock()
    c = make(tmp_path, FakeNet({OA: [(429, {}, "")]}), clock=clock)
    for _ in range(3):
        with pytest.raises(lit.SourceError):
            c.fetch("openalex", OA + "works?x", tries=1)
        clock.t = _holds(tmp_path) + 1
    clock.t += backoff.WINDOW + 1
    with pytest.raises(lit.SourceError):
        c.fetch("openalex", OA + "works?x", tries=1)
    assert _holds(tmp_path) - clock() == 2
    keyed = make(tmp_path, FakeNet({OA: [(429, {}, "")]}), env={"OPENALEX_API_KEY": "k"}, clock=clock)
    with pytest.raises(lit.SourceError):
        keyed.fetch("openalex", OA + "works?x", tries=1)
    assert _holds(tmp_path, field="until_key") - clock() == 2  # the keyed pool has its own streak


def test_lit_escalation_past_max_wait_skips_the_source(tmp_path):
    clock = Clock()
    c = make(tmp_path, FakeNet({OA: [(429, {}, "")]}), clock=clock)
    msgs = []
    for _ in range(7):  # 2, 4, 8, 16, 32, then 64 > MAX_WAIT
        with pytest.raises(lit.SourceError) as e:
            c.fetch("openalex", OA + "works?x", tries=1)
        msgs.append(str(e.value))
        clock.t = _holds(tmp_path) + 1
    assert "skipped" in msgs[5] and "64s" in msgs[5]


def test_lit_retry_after_as_an_http_date_is_honoured(tmp_path):
    clock = Clock()
    date = format_datetime(datetime.fromtimestamp(clock() + 45, UTC), usegmt=True)
    net = FakeNet({OA: [(429, {"Retry-After": date}, ""), (200, {}, oa_body(1))]})
    make(tmp_path, net, clock=clock).fetch("openalex", OA + "works?d")
    assert 45 <= sum(clock.slept) <= 45 * (1 + backoff.SPREAD) + 1


def test_lit_5xx_retries_sleep_full_jitter_from_the_injected_rng(tmp_path):
    clock = Clock()
    net = FakeNet({OA: [(500, {}, ""), (502, {}, ""), (200, {}, oa_body(1))]})
    c = lit.Client(tmp_path, get=net, clock=clock, sleep=clock.sleep, env={}, rng=random.Random(11))
    c.fetch("openalex", OA + "works?j")
    ref = random.Random(11)
    assert clock.slept == [ref.uniform(0, 2), ref.uniform(0, 4)]  # the bucket's burst covers the 3 calls


def _wake(args):
    d, seed, t0 = args
    woke: list[float] = []

    class Woke(Exception):
        pass

    def sleep(s: float) -> None:
        woke.append(t0 + s)
        raise Woke

    b = lit.Bucket(Path(d), clock=lambda: t0, sleep=sleep, rng=random.Random(seed))
    with contextlib.suppress(Woke):
        b.acquire("openalex")
    return woke[0]


def test_20_processes_on_one_cooldown_wake_spread_over_the_jitter_window(tmp_path):
    """20 processes wait on one shared 30 s cooldown: each wakes in [end, end + 20 %], at 20 different times spread
    over most of that window, so the cooldown's end does not release them all in the same instant."""
    t0, hold = 1_000_000.0, 30.0
    lit.Bucket(tmp_path, clock=lambda: t0).cooldown("openalex", hold)
    with multiprocessing.get_context("fork").Pool(20) as pool:
        wakes = pool.map(_wake, [(str(tmp_path), 1000 + i, t0) for i in range(20)])
    end = t0 + hold
    assert all(end <= w <= end + hold * backoff.SPREAD + 1e-9 for w in wakes)
    assert len(set(wakes)) == 20
    assert max(wakes) - min(wakes) > hold * backoff.SPREAD / 2


# ---- cite-check ----


@pytest.fixture
def cite(monkeypatch, tmp_path):
    answers: dict[str, list] = {}
    calls: list[str] = []
    slept: list[float] = []

    def fake_get(url, timeout=20):
        calls.append(url)
        seq = answers[url]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(citecheck, "_get", fake_get)
    monkeypatch.setattr(citecheck, "SLEEP", slept.append)
    monkeypatch.setattr(citecheck, "RNG", random.Random(5))
    return answers, calls, slept


def test_citecheck_retry_is_full_jitter(cite):
    answers, calls, slept = cite
    answers["https://e.org/a"] = [(503, {}, ""), (200, {}, "")]
    assert citecheck._check_one("https://e.org/a")["ok"] is True
    assert slept == [random.Random(5).uniform(0, 2)] and len(calls) == 2


def test_citecheck_honours_retry_after_and_skips_a_long_one(cite):
    answers, calls, slept = cite
    answers["https://e.org/b"] = [(429, {"Retry-After": "5"}, ""), (200, {}, "")]
    assert citecheck._check_one("https://e.org/b")["ok"] is True
    assert 5 <= slept[0] <= 5 * (1 + backoff.SPREAD)
    answers["https://e.org/c"] = [(429, {"Retry-After": "3600"}, "")]
    res = citecheck._check_one("https://e.org/c")
    assert res["transient"] and calls.count("https://e.org/c") == 1 and len(slept) == 1  # did not stall the run


def test_citecheck_github_403_rate_limit_waits_for_its_reset(cite, monkeypatch):
    answers, _, slept = cite
    monkeypatch.setattr(citecheck.time, "time", lambda: NOW)
    api = "https://api.github.com/repos/o/r"
    answers[api] = [(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW + 12))}, ""), (200, {}, "{}")]
    assert citecheck._check_one("https://github.com/o/r")["ok"] is True
    assert 12 <= slept[0] <= 12 * (1 + backoff.SPREAD)


# ---- the gh wrapper ----


@pytest.fixture
def ghrun(monkeypatch):
    outs: list[subprocess.CompletedProcess] = []
    slept: list[float] = []

    def run(argv, **kw):
        return outs.pop(0) if len(outs) > 1 else outs[0]

    monkeypatch.setattr(board.subprocess, "run", run)
    monkeypatch.setattr(board, "root", lambda: Path("."))
    monkeypatch.setattr(board, "GH_SLEEP", slept.append)
    monkeypatch.setattr(board, "GH_RNG", random.Random(9))
    return outs, slept


def _done(code: int, err: str = "", out: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["gh"], code, out, err)


SECONDARY = "HTTP 403: You have exceeded a secondary rate limit. Please wait a few minutes before you try again."


def test_gh_backs_off_on_a_secondary_rate_limit_then_succeeds(ghrun, capsys):
    outs, slept = ghrun
    outs += [_done(1, SECONDARY), _done(1, "HTTP 429: Too Many Requests"), _done(0, out="ok\n")]
    assert board.gh("issue", "list") == "ok\n"
    ref = random.Random(9)
    assert slept == [60 + ref.uniform(0, 30), 60 + ref.uniform(0, 60)]  # at least a minute, then longer, jittered
    assert "secondary rate limit" in capsys.readouterr().err


def test_gh_gives_up_after_its_tries_and_does_not_retry_other_errors(ghrun):
    outs, slept = ghrun
    outs.append(_done(1, SECONDARY))
    with pytest.raises(board.BoardError, match="secondary rate limit"):
        board.gh("issue", "list")
    assert len(slept) == board.GH_TRIES - 1
    outs[:] = [_done(1, "HTTP 404: Not Found")]
    slept.clear()
    with pytest.raises(board.BoardError, match="404"):
        board.gh("issue", "view", "9")
    assert slept == []
