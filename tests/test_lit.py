"""devkit.lit: shared rate limit, shared cache, keys from env, 429 backoff, compact capped output. No network."""

import http.server
import json
import multiprocessing
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import citecheck, lit, netsafe


class FakeNet:
    """Scripted HTTP: answers[url_prefix] is a list of (status, headers, body) served in order (last one repeats)."""

    def __init__(self, answers: dict[str, list[tuple[int, dict, str]]]):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, dict, str]:
        self.calls.append((url, headers))
        for prefix, seq in self.answers.items():
            if url.startswith(prefix):
                return seq.pop(0) if len(seq) > 1 else seq[0]
        return 404, {}, ""


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


OA = "https://api.openalex.org/"
S2 = "https://api.semanticscholar.org/"
CR = "https://api.crossref.org/"
AX = "https://export.arxiv.org/"


def oa_body(n: int = 3, doi_prefix: str = "10.1/oa") -> str:
    return json.dumps(
        {
            "results": [
                {
                    "id": f"https://openalex.org/W{i}",
                    "doi": f"https://doi.org/{doi_prefix}{i}",
                    "title": f"OpenAlex paper {i}",
                    "publication_year": 2020 + i,
                    "primary_location": {"source": {"display_name": "Learning and Instruction"}},
                    "cited_by_count": 10 * i,
                    "abstract_inverted_index": {"Retrieval": [0], "practice": [1], "works.": [2]},
                }
                for i in range(n)
            ]
        }
    )


def s2_body(papers: list[dict]) -> str:
    return json.dumps({"total": len(papers), "data": papers})


def make(tmp_path, net: FakeNet, env: dict | None = None, clock: Clock | None = None) -> lit.Client:
    clock = clock or Clock()
    return lit.Client(tmp_path, get=net, clock=clock, sleep=clock.sleep, env=env or {})


# ---- token bucket ----


@settings(max_examples=200, deadline=None)
@given(
    gaps=st.lists(st.floats(min_value=0, max_value=5, allow_nan=False), min_size=1, max_size=60),
    rate=st.sampled_from([v[0] for v in lit.RATES.values()]),
    burst=st.sampled_from([1.0, 5.0]),
)
def test_bucket_never_grants_more_than_rate_times_time_plus_burst(gaps, rate, burst):
    state: dict = {}
    t0 = t = 100.0
    granted = 0
    for g in gaps:
        t += g
        state, wait = lit.take(state, t, rate, burst)
        granted += wait == 0
        assert wait >= 0
        assert 0 <= state["tokens"] <= burst
    assert granted <= burst + rate * (t - t0) + 1e-9


def test_bucket_wait_is_exactly_enough():
    state, wait = lit.take({}, 0.0, 2.0, 1.0)
    assert wait == 0
    state, wait = lit.take(state, 0.0, 2.0, 1.0)
    assert wait == pytest.approx(0.5)
    _, wait = lit.take(state, 0.5, 2.0, 1.0)
    assert wait == 0


def test_cooldown_holds_every_caller_off(tmp_path):
    clock = Clock()
    b = lit.Bucket(tmp_path, clock, clock.sleep)
    b.cooldown("openalex", 30)
    other = lit.Bucket(tmp_path, clock, clock.sleep)  # another process: same files
    other.acquire("openalex")
    assert sum(clock.slept) >= 30


def test_clients_of_two_repositories_share_one_rate_dir(tmp_path, monkeypatch):
    # regression: buckets lived in each repository's git dir, so sessions in two repositories spent one S2 key twice
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    shared = lit.user_state_dir("lit")
    assert shared == tmp_path / "state" / "devkit" / "lit"
    clock = Clock()
    a, b = (lit.Client(tmp_path / r, clock=clock, sleep=clock.sleep, env={}, rate_dir=shared) for r in ("r1", "r2"))
    a.bucket.acquire("s2")
    b.bucket.acquire("s2")  # the burst of 1 is spent by the other repository's client
    assert sum(clock.slept) >= 1 / lit.RATES["s2"][0] - 1e-9
    assert not (tmp_path / "r1" / "buckets.json").exists()


def _worker(args):
    d, n = args
    b = lit.Bucket(__import__("pathlib").Path(d))
    stamps = []
    for _ in range(n):
        b.acquire("test")
        stamps.append(time.time())
    return stamps


def test_bucket_is_shared_across_processes(tmp_path, monkeypatch):
    """4 processes x 3 calls at 20/s, burst 1: the 12 grants span at least 11/20 s, so the machine stays at 20/s."""
    monkeypatch.setitem(lit.RATES, "test", (20.0, 1.0))
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(4) as pool:
        stamps = sorted(s for out in pool.map(_worker, [(str(tmp_path), 3)] * 4) for s in out)
    assert len(stamps) == 12
    assert stamps[-1] - stamps[0] >= 11 / 20 - 0.02


def _fetch_one(args):
    d, url, limits = args
    lit.RATES["test"] = limits  # a spawned process starts from a fresh import
    real_take = getattr(lit, "_untraced_take", lit.take)  # a reused worker must not wrap twice
    lit._untraced_take = real_take
    log = Path(d) / f"grants-{os.getpid()}.txt"

    def logged_take(state, now, rate, burst, *rest):  # runs under the bucket's lock, with the clock value it decided on
        new, wait = real_take(state, now, rate, burst, *rest)
        if wait == 0:
            with log.open("a") as f:
                f.write(f"{now!r}\n")
        return new, wait

    lit.take = logged_take
    lit.Client(Path(d), env={}, clock=time.monotonic).fetch("test", url)


def test_20_processes_against_a_fake_server_never_exceed_the_rate(tmp_path):
    """20 separate processes, one call each, against a local fake server at 20/s burst 2: the tokens they are
    granted never exceed burst + rate x window in any window. The check is on the bucket's own grant times (a
    system-wide monotonic clock read under the lock), not on arrival times, so CPU scheduling cannot break it."""
    rate, burst = 20.0, 2.0
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b'{"results": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/works?search="
        with multiprocessing.get_context("spawn").Pool(
            20, maxtasksperchild=1
        ) as pool:  # spawn: this process runs a server thread
            pool.map(_fetch_one, [(str(tmp_path), base + str(i), (rate, burst)) for i in range(20)])
    finally:
        server.shutdown()
    assert len(hits) == 20
    grants = sorted(float(x) for f in tmp_path.glob("grants-*.txt") for x in f.read_text().split())
    assert len(grants) == 20
    for i in range(len(grants)):
        for j in range(i, len(grants)):
            window = grants[j] - grants[i]
            assert j - i + 1 <= burst + rate * window + 1e-6, (i, j, window)


# ---- cache ----


def test_second_search_is_served_from_the_shared_cache(tmp_path):
    net = FakeNet({OA: [(200, {}, oa_body())]})
    make(tmp_path, net).search("retrieval practice", ("openalex",))
    papers, _ = make(tmp_path, FakeNet({})).search("retrieval practice", ("openalex",))  # another worktree
    assert len(net.calls) == 1
    assert [p.title for p in papers] == ["OpenAlex paper 0", "OpenAlex paper 1", "OpenAlex paper 2"]


def test_cache_expires(tmp_path):
    clock = Clock()
    net = FakeNet({OA: [(200, {}, oa_body())]})
    make(tmp_path, net, clock=clock).search("q", ("openalex",))
    clock.t += lit.CACHE_DAYS * 86400 + 1
    make(tmp_path, net, clock=clock).search("q", ("openalex",))
    assert len(net.calls) == 2


def test_failures_are_not_cached(tmp_path):
    net = FakeNet({OA: [(503, {}, ""), (503, {}, ""), (503, {}, ""), (503, {}, ""), (200, {}, oa_body())]})
    papers, notes = make(tmp_path, net).search("q", ("openalex",))
    assert papers == [] and "after 4 tries" in notes[0]
    papers, notes = make(tmp_path, net).search("q", ("openalex",))
    assert len(papers) == 3 and notes == []


# ---- keys ----


def test_keys_from_env_are_sent_but_never_cached_or_printed(tmp_path):
    env = {"OPENALEX_API_KEY": "oa-secret", "S2_API_KEY": "s2-secret"}
    net = FakeNet({OA: [(200, {}, oa_body(1))], S2: [(200, {}, s2_body([]))]})
    papers, notes = make(tmp_path, net, env).search("q", ("openalex", "s2"))
    (oa_url, _), (s2_url, s2_headers) = net.calls
    assert "api_key=oa-secret" in oa_url
    assert s2_headers == {"x-api-key": "s2-secret"} and "secret" not in s2_url
    on_disk = "".join(p.read_text() for p in (tmp_path / "cache").iterdir())
    assert "secret" not in on_disk
    assert "secret" not in lit.render(papers, notes)
    # a run without keys reuses the keyed answer: the key is not part of the cache key
    make(tmp_path, FakeNet({}), {}).search("q", ("openalex",))


def test_no_key_no_param(tmp_path):
    net = FakeNet({OA: [(200, {}, oa_body(1))]})
    make(tmp_path, net).search("q", ("openalex",))
    assert "api_key" not in net.calls[0][0] and net.calls[0][1] == {}


# ---- backoff ----


def test_429_backs_off_by_retry_after_then_succeeds(tmp_path):
    clock = Clock()
    net = FakeNet({OA: [(429, {"Retry-After": "7"}, ""), (200, {}, oa_body(1))]})
    papers, notes = make(tmp_path, net, clock=clock).search("q", ("openalex",))
    assert len(papers) == 1 and notes == []
    assert len(net.calls) == 2
    assert sum(clock.slept) >= 7


def test_429_without_retry_after_uses_exponential_delay(tmp_path):
    clock = Clock()
    net = FakeNet({S2: [(429, {}, ""), (429, {}, ""), (200, {}, s2_body([]))]})
    make(tmp_path, net, clock=clock).search("q", ("s2",))
    assert len(net.calls) == 3
    assert sum(clock.slept) >= 2 + 4


def test_spent_budget_skips_the_source_and_others_still_answer(tmp_path):
    clock = Clock()
    s2p = {"paperId": "a" * 40, "title": "S2 paper", "year": 2019, "citationCount": 5, "externalIds": {}}
    net = FakeNet({OA: [(429, {"Retry-After": "86400"}, "")], S2: [(200, {}, s2_body([s2p]))]})
    papers, notes = make(tmp_path, net, clock=clock).search("q", ("openalex", "s2"))
    assert [p.title for p in papers] == ["S2 paper"]
    assert any("openalex" in n and "skipped" in n for n in notes)
    assert sum(clock.slept) < lit.MAX_WAIT + 2  # did not sleep a day
    assert len([u for u, _ in net.calls if u.startswith(OA)]) == 1
    # regression: the next process (another query, so no cache hit) does not ask OpenAlex again until the budget resets
    _, notes = make(tmp_path, net, clock=clock).search("other query", ("openalex",))
    assert len([u for u, _ in net.calls if u.startswith(OA)]) == 1
    assert "another" in notes[0]


def test_client_error_is_not_retried(tmp_path):
    net = FakeNet({OA: [(400, {}, "")]})
    _, notes = make(tmp_path, net).search("q", ("openalex",))
    assert len(net.calls) == 1 and "HTTP 400" in notes[0]


# ---- output ----


def test_output_is_compact_and_capped(tmp_path):
    long = {f"w{i}": [i] for i in range(400)}
    body = json.loads(oa_body(25))
    for w in body["results"]:
        w["abstract_inverted_index"] = long
    net = FakeNet({OA: [(200, {}, json.dumps(body))]})
    papers, notes = make(tmp_path, net).search("q", ("openalex",), n=5)
    assert len(papers) == 5
    out = lit.render(papers, notes)
    lines = out.splitlines()
    assert len(lines) == 10
    assert lines[0] == "- OpenAlex paper 0 (2020, Learning and Instruction, 0 cites) [doi:10.1/oa0 openalex:W0]"
    assert all(len(line) <= lit.SNIPPET + 2 for line in lines[1::2])
    assert lines[1].endswith("…")


def test_n_is_capped(tmp_path):
    net = FakeNet({OA: [(200, {}, oa_body(1))]})
    make(tmp_path, net).search("q", ("openalex",), n=500)
    assert f"per_page={lit.MAX_N}" in net.calls[0][0]


def test_same_paper_from_two_sources_is_merged(tmp_path):
    s2p = {
        "paperId": "b" * 40,
        "title": "OpenAlex paper 1",
        "year": 2021,
        "venue": "",
        "citationCount": 99,
        "externalIds": {"DOI": "10.1/OA1", "ArXiv": "2101.00001"},
        "abstract": None,
    }
    net = FakeNet({OA: [(200, {}, oa_body(2))], S2: [(200, {}, s2_body([s2p]))]})
    papers, _ = make(tmp_path, net).search("q", ("openalex", "s2"))
    assert len(papers) == 2
    p = next(p for p in papers if p.title == "OpenAlex paper 1")
    assert p.cites == 99
    assert p.ids["s2"] == "b" * 40 and p.ids["arxiv"] == "2101.00001" and p.ids["openalex"] == "W1"
    assert p.abstract == "Retrieval practice works."


def test_parsers_for_crossref_and_arxiv():
    cr = {
        "message": {
            "items": [
                {
                    "DOI": "10.1177/1529100612453266",
                    "title": ["Improving Students' Learning"],
                    "issued": {"date-parts": [[2013, 1]]},
                    "container-title": ["Psychological Science in the Public Interest"],
                    "is-referenced-by-count": 3000,
                    "abstract": "<jats:p>Ten techniques &amp; more.</jats:p>",
                }
            ]
        }
    }
    (p,) = lit.parse_crossref(json.dumps(cr))
    assert (p.year, p.cites, p.venue) == (2013, 3000, "Psychological Science in the Public Interest")
    assert " ".join(p.abstract.split()) == "Ten techniques & more."
    atom = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/2305.12345v2</id><title>A  Title
      Here</title><published>2023-05-20T00:00:00Z</published><summary>Abs.</summary></entry></feed>"""
    (a,) = lit.parse_arxiv(atom)
    assert (a.title, a.year, a.ids["arxiv"], a.venue) == ("A Title Here", 2023, "2305.12345", "arXiv")


def test_years_filter_reaches_every_source():
    assert "filter=publication_year:2015-2020" in lit.search_url("openalex", "q", 5, "2015-2020")
    assert "filter=publication_year:2019" in lit.search_url("openalex", "q", 5, "2019")
    assert "&year=2015-" in lit.search_url("s2", "q", 5, "2015-")
    assert "filter=until-pub-date:2020" in lit.search_url("crossref", "q", 5, "-2020")
    with pytest.raises(ValueError):
        lit.search_url("s2", "q", 5, "last decade")


def test_lookup_by_id_falls_back(tmp_path):
    s2p = {"paperId": "c" * 40, "title": "Found in S2", "year": 2017, "externalIds": {"ArXiv": "1706.03762"}}
    net = FakeNet({S2: [(200, {}, json.dumps(s2p))]})
    p, _ = make(tmp_path, net).lookup("arXiv:1706.03762v5")
    assert p is not None and p.title == "Found in S2"
    cr = {"message": {"DOI": "10.1234/x", "title": ["Crossref only"], "issued": {"date-parts": [[2001]]}}}
    net = FakeNet({CR: [(200, {}, json.dumps(cr))]})  # OpenAlex and S2 answer 404
    p, notes = make(tmp_path, net).lookup("https://doi.org/10.1234/x")
    assert p is not None and p.title == "Crossref only" and len(notes) == 3  # no abstract: Europe PMC asked too
    with pytest.raises(ValueError):
        lit.get_urls("not an id")


def test_parse_sources():
    assert lit.parse_sources("all") == lit.SOURCES
    assert lit.parse_sources("s2,arxiv") == ("s2", "arxiv")
    with pytest.raises(ValueError):
        lit.parse_sources("google")


def test_unreadable_answer_is_a_note(tmp_path):
    net = FakeNet({OA: [(200, {}, "<html>oops</html>")], AX: [(200, {}, "not xml")]})
    papers, notes = make(tmp_path, net).search("q", ("openalex", "arxiv"))
    assert papers == [] and len(notes) == 2


# ---- fallback chain and Europe PMC (#116) ----

EP = "https://www.ebi.ac.uk/europepmc/"
SPENT = [(429, {"Retry-After": "86400"}, "")]  # a spent daily budget


def ep_body(n: int = 2) -> str:
    results: list[dict] = [
        {
            "id": str(30000000 + i),
            "source": "MED",
            "pmid": str(30000000 + i),
            "doi": f"10.9/ep{i}",
            "title": f"Europe PMC paper {i}.",
            "pubYear": "2019",
            "journalInfo": {"journal": {"title": "Memory & Cognition"}},
            "citedByCount": 4,
            "abstractText": "<b><i>Objective:</i></b> Testing &amp; memory.",
        }
        for i in range(n)
    ]
    results.append({"id": "PPR1", "source": "PPR", "title": "A preprint", "pubYear": "2024"})
    return json.dumps({"hitCount": n + 1, "resultList": {"result": results}})


def cr_body(n: int) -> str:
    items = [
        {"DOI": f"10.8/cr{i}", "title": [f"Crossref paper {i}"], "issued": {"date-parts": [[2018]]}} for i in range(n)
    ]
    return json.dumps({"message": {"items": items}})


def test_parse_europepmc():
    p, pre = lit.parse_europepmc(ep_body(1))
    assert (p.title, p.year, p.venue, p.cites, p.source) == (
        "Europe PMC paper 0.",
        2019,
        "Memory & Cognition",
        4,
        "europepmc",
    )
    assert (p.ids["doi"], p.ids["pmid"]) == ("10.9/ep0", "30000000")
    assert " ".join(p.abstract.split()) == "Objective: Testing & memory."
    assert (pre.title, pre.venue, pre.year, pre.ids.get("doi")) == ("A preprint", "preprint", 2024, "")


def test_europepmc_url_and_years():
    url = lit.search_url("europepmc", "retrieval practice", 5)
    assert url.startswith(EP + "webservices/rest/search?query=retrieval%20practice&")
    assert "format=json" in url and "resultType=core" in url and "pageSize=5" in url
    assert "%20AND%20PUB_YEAR%3A%5B2015%20TO%20%2A%5D&" in lit.search_url("europepmc", "q", 5, "2015-")
    assert "PUB_YEAR%3A%5B%2A%20TO%202020%5D&" in lit.search_url("europepmc", "q", 5, "-2020")
    assert "PUB_YEAR%3A%5B2019%20TO%202019%5D&" in lit.search_url("europepmc", "q", 5, "2019")


def test_source_accepts_europepmc_and_its_rate_is_below_the_documented_limit():
    assert lit.parse_sources("europepmc") == ("europepmc",)
    assert "europepmc" in lit.parse_sources("all")
    rate, burst = lit.RATES["europepmc"]
    assert rate < 10 and rate * 60 < 500  # 10/s or 500/min per IP: see the comment on RATES
    assert burst <= 2
    # regression: Crossref cut searches to 1/s (public pool) on 2026-07-21; this client sent up to 5/s
    assert lit.RATES["crossref"][0] < 1 and lit.RATES["crossref"][1] <= 1
    # a Semantic Scholar key allows 1/s across all endpoints, and its terms ask clients to stay below that
    assert lit.RATES["s2"][0] < 1 and lit.RATES["s2"][1] <= 1


def test_plan_auto_falls_back_but_an_explicit_list_does_not():
    assert lit.plan("auto") == (lit.DEFAULT_SOURCES, lit.FALLBACK_SOURCES)
    assert lit.plan("") == (lit.DEFAULT_SOURCES, lit.FALLBACK_SOURCES)
    assert lit.plan("s2,europepmc") == (("s2", "europepmc"), ())
    assert lit.plan("all") == (lit.SOURCES, ())
    assert lit.FALLBACK_SOURCES == ("crossref", "europepmc", "arxiv")


def test_rate_limited_primaries_fall_back_in_order_until_n(tmp_path):
    clock = Clock()
    net = FakeNet({OA: SPENT, S2: SPENT, CR: [(200, {}, cr_body(2))], EP: [(200, {}, ep_body(3))]})
    papers, notes = make(tmp_path, net, clock=clock).search("q", n=4, fallback=lit.FALLBACK_SOURCES)
    assert [p.source for p in papers] == ["crossref", "crossref", "europepmc", "europepmc"]
    assert not [u for u, _ in net.calls if u.startswith(AX)]  # had n results before arXiv's turn
    assert any(n.startswith("openalex") and "skipped" in n for n in notes)
    assert any(n.startswith("s2") and "skipped" in n for n in notes)
    assert "fallback: answered by crossref (2), europepmc (2)" in notes
    assert sum(clock.slept) < lit.MAX_WAIT + 2


def test_europepmc_503_is_a_throttle_every_process_honours(tmp_path):
    clock = Clock()
    net = FakeNet({EP: [(503, {}, ""), (200, {}, ep_body(1))]})
    papers, notes = make(tmp_path, net, clock=clock).search("q", ("europepmc",))
    assert len(papers) == 2 and notes == []
    assert "until" in json.loads((tmp_path / "buckets.json").read_text())["europepmc"]  # shared cooldown
    assert sum(clock.slept) >= 2  # waited out the cooldown in acquire(), not a blind retry
    # a 503 from a source that does not use it for throttling is retried, not a shared cooldown
    make(tmp_path, FakeNet({CR: [(503, {}, ""), (200, {}, cr_body(1))]}), clock=clock).search("q", ("crossref",))
    assert "until" not in json.loads((tmp_path / "buckets.json").read_text()).get("crossref", {})


def test_fallback_is_not_used_when_primaries_have_n(tmp_path):
    net = FakeNet({OA: [(200, {}, oa_body(3))], S2: [(200, {}, s2_body([]))]})
    papers, notes = make(tmp_path, net).search("q", n=3, fallback=lit.FALLBACK_SOURCES)
    assert len(papers) == 3 and notes == []
    assert {u.split("/")[2] for u, _ in net.calls} == {"api.openalex.org", "api.semanticscholar.org"}


def test_every_source_failing_reports_each(tmp_path):
    empty_feed = "<feed xmlns='http://www.w3.org/2005/Atom'/>"
    net = FakeNet({OA: SPENT, S2: SPENT, CR: SPENT, EP: [(400, {}, "")], AX: [(200, {}, empty_feed)]})
    papers, notes = make(tmp_path, net).search("q", fallback=lit.FALLBACK_SOURCES)
    assert papers == []
    assert [n.split(":")[0] for n in notes] == ["openalex", "s2", "crossref", "europepmc"]


def oa_titled(prefix: str, n: int, doi_prefix: str, cites: int = 0) -> str:
    body = json.loads(oa_body(n, doi_prefix))
    for i, w in enumerate(body["results"]):
        w["id"], w["title"] = f"https://openalex.org/W{doi_prefix[-1]}{i}", f"{prefix} paper {i}"
        w["cited_by_count"] = cites or w["cited_by_count"]
    return json.dumps(body)


def test_also_merges_and_dedupes_across_queries(tmp_path):
    """--also (#194): each paraphrase is searched too, the lists interleave by rank, and a paper two queries find is
    listed once with the union of its fields."""
    beta = json.loads(oa_titled("Beta", 2, "10.1/b"))
    beta["results"].insert(0, {**json.loads(oa_titled("Alpha", 3, "10.1/a"))["results"][2], "cited_by_count": 77})
    net = FakeNet(
        {
            OA + "works?search=alpha": [(200, {}, oa_titled("Alpha", 3, "10.1/a"))],
            OA + "works?search=beta": [(200, {}, json.dumps(beta))],
        }
    )
    papers, notes = make(tmp_path, net).search("alpha", ("openalex",), n=10, also=("beta", "alpha", "beta"))
    assert notes == [] and len(net.calls) == 2  # each distinct query asked once
    assert [p.title for p in papers] == [
        "Alpha paper 0",
        "Alpha paper 2",  # beta's rank 0: Alpha paper 2 again, so it moves up and is listed once
        "Alpha paper 1",
        "Beta paper 0",
        "Beta paper 1",
    ]
    assert papers[1].cites == 77


def test_also_interleaves_so_a_paraphrase_reaches_the_top_n(tmp_path):
    net = FakeNet(
        {
            OA + "works?search=alpha": [(200, {}, oa_titled("Alpha", 5, "10.1/a"))],
            OA + "works?search=beta": [(200, {}, oa_titled("Beta", 5, "10.1/b"))],
        }
    )
    papers, _ = make(tmp_path, net).search("alpha", ("openalex",), n=4, also=("beta",))
    assert [p.title for p in papers] == ["Alpha paper 0", "Beta paper 0", "Alpha paper 1", "Beta paper 1"]


def test_also_falls_back_per_query_until_n(tmp_path):
    net = FakeNet({OA: SPENT, S2: SPENT, CR: [(200, {}, cr_body(1))], EP: [(200, {}, ep_body(3))]})
    papers, notes = make(tmp_path, net).search("q", n=3, fallback=lit.FALLBACK_SOURCES, also=("r",))
    crossref = [u for u, _ in net.calls if u.startswith(CR)]
    assert len(crossref) == 2 and "query=r&" in crossref[1]  # the paraphrase asked Crossref too
    assert len(papers) == 3 and "fallback: answered by crossref (1), europepmc (2)" in notes


def test_cli_search_passes_also(monkeypatch):
    from devkit import cli

    seen = []

    class Spy:
        def search(self, query, sources, n, years, *, fallback=(), also=()):
            seen.append((query, also))
            return [], []

    monkeypatch.setattr(lit, "client", Spy)
    args = ["lit", "search", "spaced repetition", "--also", "distributed practice", "--also", "spacing effect"]
    assert cli.run_lit(cli.build().parse_args(args)) == 0
    assert seen == [("spaced repetition", ("distributed practice", "spacing effect"))]


def test_fallback_against_a_local_fake_server(tmp_path):
    """Over real HTTP: OpenAlex and S2 answer 429 with a day-long Retry-After, Crossref is down, Europe PMC and arXiv
    answer. The search returns Europe PMC's and arXiv's papers and says which sources answered."""
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2305.00001v1</id>'
        "<title>An arXiv paper</title><published>2023-05-01T00:00:00Z</published><summary>S.</summary></entry></feed>"
    )
    routes = {
        "api.openalex.org": (429, b""),
        "api.semanticscholar.org": (429, b""),
        "api.crossref.org": (500, b""),
        "www.ebi.ac.uk": (200, ep_body(2).encode()),
        "export.arxiv.org": (200, atom.encode()),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = routes[self.path.split("/")[1]]
            self.send_response(status)
            if status == 429:
                self.send_header("Retry-After", "86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"

    def local(url: str, headers: dict) -> tuple[int, dict, str]:  # https://host/path -> http://127.0.0.1:port/host/path
        return lit.http_get(base + url.split("://", 1)[1], headers, timeout=5)

    clock = Clock()
    try:
        papers, notes = lit.Client(tmp_path, get=local, clock=clock, sleep=clock.sleep, env={}).search(
            "q", n=5, fallback=lit.FALLBACK_SOURCES
        )
    finally:
        server.shutdown()
    assert [p.source for p in papers] == ["europepmc"] * 3 + ["arxiv"]
    assert "fallback: answered by europepmc (3), arxiv (1)" in notes
    assert any(n.startswith("crossref: HTTP 500") for n in notes)
    assert "note: fallback: answered by europepmc (3), arxiv (1)" in lit.render(papers, notes)


# ---- source health before a research round (#8) ----

ATOM_EMPTY = "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"


def healthy() -> dict[str, list[tuple[int, dict, str]]]:
    return {
        OA: [(200, {}, oa_body(1))],
        S2: [(200, {}, s2_body([]))],
        CR: [(200, {}, json.dumps({"message": {"items": []}}))],
        EP: [(200, {}, ep_body(1))],
        AX: [(200, {}, ATOM_EMPTY)],
    }


def test_status_reports_each_source_and_one_working_major_index_is_partial_not_degraded(tmp_path):
    answers = healthy() | {S2: SPENT, EP: [(500, {}, "")]}
    health = make(tmp_path, FakeNet(answers)).status()
    by = {h.source: h for h in health}
    assert list(by) == list(lit.SOURCES)
    assert by["openalex"].ok and by["crossref"].ok and by["arxiv"].ok
    assert not by["s2"].ok and "rate limited" in by["s2"].detail
    assert not by["europepmc"].ok and "500" in by["europepmc"].detail
    assert lit.down(health) == ["s2"]  # europepmc is a fallback: shown, but discovery is judged on the defaults
    assert not lit.degraded(health)  # one working major index is enough (#23)
    last = lit.render_status(health).splitlines()[-1]
    assert last.startswith("discovery: OK, partial") and "s2 down" in last and "DEGRADED" not in last


def test_status_is_degraded_only_when_both_major_indexes_are_down(tmp_path):
    health = make(tmp_path, FakeNet(healthy() | {OA: SPENT, S2: [(500, {}, "")]})).status()
    assert lit.down(health) == ["openalex", "s2"] and lit.degraded(health)
    last = lit.render_status(health).splitlines()[-1]
    assert "DEGRADED" in last and "openalex, s2 down" in last


def test_status_all_healthy_names_keys_and_is_not_degraded(tmp_path):
    health = make(tmp_path, FakeNet(healthy()), env={"S2_API_KEY": "secret"}).status()
    assert all(h.ok for h in health) and lit.down(health) == [] and not lit.degraded(health)
    out = lit.render_status(health)
    assert "secret" not in out and out.splitlines()[-1].startswith("discovery: OK (")
    by = {h.source: h for h in health}
    assert "key" in by["s2"].detail and "no key" in by["openalex"].detail


def test_status_probes_live_instead_of_trusting_the_cache(tmp_path):
    clock = Clock()
    c = make(tmp_path, FakeNet(healthy()), clock=clock)
    assert all(h.ok for h in c.status())
    clock.t += 3600  # past every bucket refill
    c.get = FakeNet({})  # every source now answers 404
    assert not any(h.ok for h in c.status())


def test_status_honours_a_shared_cooldown_without_calling_the_source(tmp_path):
    net = FakeNet(healthy())
    c = make(tmp_path, net)
    c.bucket.cooldown("openalex", 7200)  # another process got a spent-budget 429
    by = {h.source: h for h in c.status()}
    assert not by["openalex"].ok and "7200" in by["openalex"].detail
    assert not any(u.startswith(OA) for u, _ in net.calls)


def test_dev_lit_status_exits_nonzero_when_discovery_is_degraded(tmp_path, monkeypatch, capsys):
    from devkit import cli

    answers = healthy() | {OA: SPENT, S2: SPENT}
    monkeypatch.setattr(lit, "client", lambda: make(tmp_path, FakeNet(answers)))
    assert cli.run_lit(cli.build().parse_args(["lit", "status"])) == 1
    assert "DEGRADED" in capsys.readouterr().out
    (tmp_path / "b").mkdir()
    monkeypatch.setattr(lit, "client", lambda: make(tmp_path / "b", FakeNet(healthy() | {OA: SPENT})))
    assert cli.run_lit(cli.build().parse_args(["lit", "status"])) == 0  # partial: S2 still answers
    assert "OK, partial (openalex down" in capsys.readouterr().out
    (tmp_path / "c").mkdir()
    monkeypatch.setattr(lit, "client", lambda: make(tmp_path / "c", FakeNet(healthy())))
    assert cli.run_lit(cli.build().parse_args(["lit", "status"])) == 0


# ---- key-aware cooldowns (#23) ----


def test_a_keyless_hold_is_ignored_when_a_key_is_present_and_vice_versa(tmp_path):
    net = FakeNet(healthy())
    make(tmp_path, net).bucket.cooldown("openalex", 9.5 * 3600)  # shared-pool 429 recorded while keyless
    keyed = make(tmp_path, net, env={"OPENALEX_API_KEY": "fake-oa"})
    assert {h.source: h for h in keyed.status()}["openalex"].ok  # the key has its own budget: probed, answered
    assert not {h.source: h for h in make(tmp_path, net).status()}["openalex"].ok  # keyless: still held off

    (tmp_path / "b").mkdir()
    c = make(tmp_path / "b", net, env={"S2_API_KEY": "fake-s2"})
    c.bucket.cooldown("s2", 7200, keyed=True)  # the key's own budget is spent
    assert make(tmp_path / "b", net).bucket.held_off("s2") == 0  # the keyless pool is not held off by it
    assert not {h.source: h for h in c.status()}["s2"].ok


def test_a_429_with_a_key_records_a_keyed_hold(tmp_path):
    net = FakeNet({OA: [(429, {"Retry-After": "3600"}, "")]})
    make(tmp_path, net, env={"OPENALEX_API_KEY": "fake-oa"}).search("q", ("openalex",))
    held = json.loads((tmp_path / "buckets.json").read_text())["openalex"]
    assert "until_key" in held and "until" not in held


@given(keyed=st.booleans(), hold_keyed=st.booleans(), secs=st.floats(min_value=1, max_value=1e5))
def test_take_honours_only_its_own_pools_hold(keyed, hold_keyed, secs):
    _, wait = lit.take({lit.hold_field(hold_keyed): 100.0 + secs}, 100.0, 1.0, 1.0, keyed)
    assert (wait > 0) == (keyed == hold_keyed)


# ---- keys from a private secrets file (#23); fake keys only ----


def secrets(tmp_path, text: str, mode: int = 0o600) -> dict:
    p = tmp_path / "api-keys.env"
    p.write_text(text)
    p.chmod(mode)
    return {"DEVKIT_SECRETS_FILE": str(p)}


def test_parse_secrets_takes_key_value_and_export_lines():
    text = "# keys\nexport OPENALEX_API_KEY=fake-oa\nS2_API_KEY='fake-s2'\nOTHER=x\nnot a line\n"
    assert lit.parse_secrets(text) == {"OPENALEX_API_KEY": "fake-oa", "S2_API_KEY": "fake-s2"}
    assert lit.parse_secrets('export S2_API_KEY="fake-s2"') == {"S2_API_KEY": "fake-s2"}


def test_unset_keys_come_from_the_secrets_file_and_set_ones_win(tmp_path):
    env = secrets(tmp_path, "OPENALEX_API_KEY=file-oa\nexport S2_API_KEY=file-s2\n") | {"S2_API_KEY": "env-s2"}
    out, problem = lit.with_secrets(env)
    assert problem == "" and out["OPENALEX_API_KEY"] == "file-oa" and out["S2_API_KEY"] == "env-s2"


def test_default_secrets_path_is_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert lit.secrets_path({}) == tmp_path / ".config/api-keys.env"
    assert lit.with_secrets({}) == ({}, "")  # no file: no keys, no problem


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_secrets_file_readable_by_group_or_others_is_refused(tmp_path, mode):
    env = secrets(tmp_path, "OPENALEX_API_KEY=fake-oa\n", mode)
    out, problem = lit.with_secrets(env)
    assert "OPENALEX_API_KEY" not in out and "chmod 600" in problem and "fake-oa" not in problem


def test_a_key_from_the_file_is_sent_but_never_printed_logged_or_cached(tmp_path, monkeypatch, capsys):
    from devkit import cli

    for k in lit.KEYS.values():
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "s").mkdir()
    for k, v in secrets(tmp_path / "s", "export OPENALEX_API_KEY=fake-oa-123\nS2_API_KEY=fake-s2-456\n").items():
        monkeypatch.setenv(k, v)
    net, clock, real = FakeNet(healthy()), Clock(), lit.Client
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(lit, "state_dir", lambda *_: tmp_path / "state")
    monkeypatch.setattr(lit, "user_state_dir", lambda *_: tmp_path / "state")
    monkeypatch.setattr(
        lit, "Client", lambda d, env, **kw: real(d, get=net, clock=clock, sleep=clock.sleep, env=env, **kw)
    )
    cli.run_lit(cli.build().parse_args(["lit", "search", "q", "--source", "openalex,s2"]))
    assert cli.run_lit(cli.build().parse_args(["lit", "status"])) == 0
    assert any("api_key=fake-oa-123" in u for u, _ in net.calls)
    assert any(h.get("x-api-key") == "fake-s2-456" for _, h in net.calls)
    out = capsys.readouterr()
    stored = "".join(p.read_text() for p in (tmp_path / "state").rglob("*") if p.is_file())
    for key in ("fake-oa-123", "fake-s2-456"):
        assert key not in out.out and key not in out.err and key not in stored


def test_a_refused_secrets_file_is_reported_without_its_contents(tmp_path, monkeypatch, capsys):
    for k in lit.KEYS.values():
        monkeypatch.delenv(k, raising=False)
    for k, v in secrets(tmp_path, "OPENALEX_API_KEY=fake-oa-789\n", 0o644).items():
        monkeypatch.setenv(k, v)
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(lit, "state_dir", lambda *_: tmp_path / "state")
    monkeypatch.setattr(lit, "user_state_dir", lambda *_: tmp_path / "state")
    c = lit.client()
    err = capsys.readouterr().err
    assert "OPENALEX_API_KEY" not in c.env and "chmod 600" in err and "fake-oa-789" not in err


def test_key_report_names_origins_never_values(tmp_path):
    env = secrets(tmp_path, "S2_API_KEY=fake-s2\n") | {"OPENALEX_API_KEY": "fake-oa"}
    where, problem = lit.key_report(env)
    assert where == {"OPENALEX_API_KEY": "environment", "S2_API_KEY": "secrets file"} and problem == ""
    assert "fake" not in repr(where)
    where, _ = lit.key_report({"DEVKIT_SECRETS_FILE": str(tmp_path / "none")})
    assert set(where.values()) == {"missing"}


def test_parse_secrets_reads_values_the_way_a_shell_does():
    text = (
        "\ufeffOPENALEX_API_KEY=fake-oa  # from openalex.org\n"  # a BOM, then an unquoted value and a comment
        "export\tS2_API_KEY='fake-s2 x' # comment after the quotes\n"
    )
    assert lit.parse_secrets(text.removeprefix("\ufeff")) == {"OPENALEX_API_KEY": "fake-oa", "S2_API_KEY": "fake-s2 x"}
    assert lit.parse_secrets('S2_API_KEY="fake#s2"  # c') == {"S2_API_KEY": "fake#s2"}
    assert lit.parse_secrets("S2_API_KEY=fake#s2") == {"S2_API_KEY": "fake#s2"}  # no space: not a comment
    assert lit.parse_secrets("S2_API_KEY='unterminated") == {}


def test_a_secrets_file_with_a_byte_order_mark_is_read(tmp_path):
    p = tmp_path / "k"
    p.write_bytes("\ufeffOPENALEX_API_KEY=fake-oa\n".encode())
    p.chmod(0o600)
    assert lit.read_secrets(p) == ({"OPENALEX_API_KEY": "fake-oa"}, "")


def test_a_fifo_is_refused_without_blocking(tmp_path):
    p = tmp_path / "fifo"
    os.mkfifo(p, 0o600)
    found, problem = lit.read_secrets(p)
    assert found == {} and "not a regular file" in problem


def test_a_symlink_is_refused(tmp_path):
    real = tmp_path / "real"
    real.write_text("OPENALEX_API_KEY=fake-oa\n")
    real.chmod(0o600)
    (tmp_path / "link").symlink_to(real)
    found, problem = lit.read_secrets(tmp_path / "link")
    assert found == {} and "symlink" in problem and "fake-oa" not in problem


def test_a_file_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "k"
    p.write_text("OPENALEX_API_KEY=fake-oa\n")
    p.chmod(0o600)
    monkeypatch.setattr(lit.os, "getuid", lambda: os.stat(p).st_uid + 1)
    found, problem = lit.read_secrets(p)
    assert found == {} and "another user" in problem


def test_a_group_readable_file_is_refused_by_read_secrets(tmp_path):
    p = tmp_path / "k"
    p.write_text("OPENALEX_API_KEY=fake-oa\n")
    p.chmod(0o640)
    found, problem = lit.read_secrets(p)
    assert found == {} and "mode 640" in problem


@pytest.mark.parametrize("key", ["fake-\u043a\u043b\u044e\u0447-s2", "fake-s2\r\nX-Evil: 1"])
def test_a_key_urllib_cannot_send_is_a_source_error_that_names_no_key(key):
    with pytest.raises(lit.SourceError) as exc:
        lit.http_get("http://127.0.0.1:9/x", {"x-api-key": key})
    assert "non-ASCII" in str(exc.value) and key not in str(exc.value) and exc.value.__cause__ is None


def test_a_bad_key_is_a_note_naming_the_source_not_the_key(tmp_path):
    def get(url, headers):
        return lit.http_get("http://127.0.0.1:9/x", headers)

    papers, notes = make(tmp_path, get, env={"S2_API_KEY": "fake-\u00e9\u4e2d"}).search("q", ("s2",))
    assert papers == [] and notes == [
        "s2: key contains non-ASCII characters (or a control character); fix the secrets file"
    ]


def _redirect(src: str, dst: str):
    req = urllib.request.Request(src, headers={"x-api-key": "fake-s2", "User-Agent": "ua"})  # noqa: S310 - never opened
    return lit.KeepKeysOnHost().redirect_request(req, None, 302, "Found", {}, dst)


def test_the_key_header_does_not_follow_a_redirect_to_another_host():
    other = _redirect("https://api.semanticscholar.org/graph/v1/x", "https://evil.example/y")
    assert not any(h.lower() == "x-api-key" for h in [*other.headers, *other.unredirected_hdrs])
    assert other.get_header("User-agent") == "ua"
    same = _redirect("https://api.semanticscholar.org/graph/v1/x", "https://api.semanticscholar.org/graph/v1/z")
    assert same.get_header("X-api-key") == "fake-s2"


def test_the_key_header_is_dropped_on_a_live_cross_host_redirect():
    """Two local servers: A redirects to B (another host:port); B records the headers it got."""
    seen: list[dict] = []

    class B(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    b = http.server.ThreadingHTTPServer(("127.0.0.1", 0), B)

    class A(B):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{b.server_port}/z")
            self.end_headers()

    a = http.server.ThreadingHTTPServer(("127.0.0.1", 0), A)
    for srv in (a, b):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _, _ = lit.http_get(f"http://127.0.0.1:{a.server_port}/x", {"x-api-key": "fake-s2"})
    finally:
        a.shutdown()
        b.shutdown()
    assert status == 200 and seen and not any(k.lower() == "x-api-key" for k in seen[0])


def _raises_like_http_get(url, headers):
    raise lit.SourceError("key contains non-ASCII characters (or a control character); fix the secrets file")


@pytest.mark.parametrize(
    "answers",
    [
        {OA: [(403, {}, "")], S2: [(401, {}, "")]},  # 4xx
        {OA: [(500, {}, "")], S2: [(503, {}, "")]},  # 5xx
        {OA: [(0, {}, "unreachable")], S2: [(429, {"Retry-After": "99999"}, "")]},  # unreachable, spent budget
        "raise",  # the transport raises
    ],
)
def test_no_key_leaks_on_error_paths(tmp_path, monkeypatch, capsys, answers):
    from devkit import cli

    for k in lit.KEYS.values():
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "s").mkdir()
    for k, v in secrets(tmp_path / "s", "export OPENALEX_API_KEY=fake-oa-321\nS2_API_KEY=fake-s2-654\n").items():
        monkeypatch.setenv(k, v)
    net = _raises_like_http_get if answers == "raise" else FakeNet(answers | {CR: [(500, {}, "")]})
    clock, real = Clock(), lit.Client
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(lit, "state_dir", lambda *_: tmp_path / "state")
    monkeypatch.setattr(lit, "user_state_dir", lambda *_: tmp_path / "state")
    monkeypatch.setattr(
        lit, "Client", lambda d, env, **kw: real(d, get=net, clock=clock, sleep=clock.sleep, env=env, **kw)
    )
    cli.run_lit(cli.build().parse_args(["lit", "search", "q"]))
    cli.run_lit(cli.build().parse_args(["lit", "get", "10.1/x"]))
    assert cli.run_lit(cli.build().parse_args(["lit", "status"])) == 1
    out = capsys.readouterr()
    stored = "".join(p.read_text() for p in (tmp_path / "state").rglob("*") if p.is_file())
    for key in ("fake-oa-321", "fake-s2-654"):
        assert key not in out.out and key not in out.err and key not in stored


# ---- shared helpers in devkit.netsafe, used by lit and citecheck (#139) ----


def test_http_get_caps_the_body_and_sends_ua_and_headers():
    seen: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Length", "50")
            self.end_headers()
            self.wfile.write(b"x" * 50)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/x"
        status, _, body = netsafe.http_get(url, ua="ua/1", headers={"X-T": "v"}, timeout=5, cap=10)
    finally:
        server.shutdown()
    assert (status, body) == (200, "x" * 10)
    assert seen[0]["User-Agent"] == "ua/1" and seen[0]["X-T"] == "v"


def test_citecheck_keeps_its_400k_cap_and_lit_its_2m_cap(monkeypatch):
    calls = []
    monkeypatch.setattr(netsafe, "http_get", lambda url, **kw: calls.append(kw) or (200, {}, ""))
    citecheck._get("https://example.org/x")
    lit.http_get("https://example.org/x", {})
    assert [(c["cap"], c["timeout"]) for c in calls] == [(400_000, 20), (2_000_000, 30)]
    assert calls[0]["opener"] is netsafe.OPENER and calls[1]["opener"] is lit.OPENER


def test_atomic_write_replaces_and_leaves_no_temp_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    netsafe.atomic_write(path, "old")
    netsafe.atomic_write(path, "new")
    assert path.read_text() == "new"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        netsafe.atomic_write(path, "lost")
    assert path.read_text() == "new" and [p.name for p in tmp_path.iterdir()] == ["f.json"]


def test_take_grants_when_the_wait_is_below_the_clocks_resolution():
    """Regression (#29): at an epoch-sized clock, sleeping exactly the wait left 0.99999999 tokens and a wait of
    8e-8 s, which the clock cannot advance by, so acquire() looped forever under a fake clock."""
    state, now = {"tokens": 0.0, "t": 1_791_000_000.0}, 1_791_000_000.0
    for _ in range(6):
        state, wait = lit.take(state, now, 0.15, 2.0)
        assert wait == 0.0 or now + wait > now
        now += wait


# ---- dev lit get: authors, open-access link, full abstract, several ids (#30, #174) ----
LONG = [f"w{i}" for i in range(400)]  # an abstract far longer than any snippet


def oa_work(doi: str, wid: str = "W1", *, abstract: bool = True, authors: bool = True) -> dict:
    return {
        "id": f"https://openalex.org/{wid}",
        "doi": f"https://doi.org/{doi}",
        "title": f"Paper {doi}",
        "publication_year": 2021,
        "cited_by_count": 7,
        "authorships": [{"author": {"display_name": n}} for n in ("Ada Lovelace", "Alan Turing")] if authors else [],
        "open_access": {"is_oa": True, "oa_url": "https://example.org/landing"},
        "best_oa_location": {"pdf_url": f"https://example.org/{wid}.pdf", "landing_page_url": "https://x.org/l"},
        "abstract_inverted_index": {w: [i] for i, w in enumerate(LONG)} if abstract else None,
    }


def test_get_prints_authors_oa_pdf_and_the_full_abstract_then_serves_it_from_cache(tmp_path):
    net = FakeNet({OA: [(200, {}, json.dumps(oa_work("10.1234/a")))]})
    (p,), notes = make(tmp_path, net).lookup_many(["10.1234/a"])
    out = lit.render_full([p], notes)
    assert "authors: Ada Lovelace, Alan Turing" in out
    assert "open access: https://example.org/W1.pdf" in out
    assert " ".join(LONG) in out and "…" not in out  # rebuilt from abstract_inverted_index, never cut
    assert "select=" in net.calls[0][0] and "authorships" in net.calls[0][0]
    cached = FakeNet({})
    (again,), _ = make(tmp_path, cached).lookup_many(["10.1234/a"])
    assert cached.calls == [] and again is not None and again.authors == p.authors and again.oa_url == p.oa_url


def test_get_snippet_caps_the_abstract_and_many_authors_are_capped():
    p = lit.Paper("T", authors=[f"A{i}" for i in range(40)], abstract=" ".join(LONG))
    out = lit.render_full([p], [], snippet=50)
    assert "…" in out and " ".join(LONG) not in out
    assert f"A{lit.MAX_AUTHORS - 1} (+{40 - lit.MAX_AUTHORS} more)" in out and f"A{lit.MAX_AUTHORS}," not in out
    assert "w0" not in lit.render_full([p], [], snippet=0)


def test_get_fills_a_missing_abstract_from_the_next_source(tmp_path):
    s2p = {"paperId": "c" * 40, "title": "Paper 10.1234/a", "abstract": "From S2.", "externalIds": {"DOI": "10.1234/a"},
           "authors": [{"name": "Grace Hopper"}], "openAccessPdf": {"url": "https://s2.example/p.pdf"}}  # fmt: skip
    net = FakeNet({OA: [(200, {}, json.dumps(oa_work("10.1234/a", abstract=False)))], S2: [(200, {}, json.dumps(s2p))]})
    p, _ = make(tmp_path, net).lookup("10.1234/a")
    assert p is not None and p.abstract == "From S2."
    assert p.authors == ["Ada Lovelace", "Alan Turing"] and p.oa_url == "https://example.org/W1.pdf"  # first wins
    assert p.ids["s2"] == "c" * 40 and len(net.calls) == 2  # stops once complete: Crossref never asked


def test_get_stops_at_the_first_complete_record(tmp_path):
    net = FakeNet({OA: [(200, {}, json.dumps(oa_work("10.1234/a")))]})
    make(tmp_path, net).lookup("10.1234/a")
    assert len(net.calls) == 1


def test_get_stops_at_an_abstract_even_without_authors(tmp_path):
    net = FakeNet({OA: [(200, {}, json.dumps(oa_work("10.1234/a", authors=False)))]})
    p, _ = make(tmp_path, net).lookup("10.1234/a")
    assert p is not None and p.authors == [] and len(net.calls) == 1


def test_get_skips_s2_while_its_pool_is_held_off(tmp_path):
    cr = {"message": {"DOI": "10.1234/x", "title": ["Crossref"], "abstract": "<p>Abs.</p>"}}
    net = FakeNet({OA: [(200, {}, json.dumps(oa_work("10.1234/x", abstract=False)))], CR: [(200, {}, json.dumps(cr))]})
    c = make(tmp_path, net)
    c.bucket.cooldown("s2", 600)
    p, notes = c.lookup("10.1234/x")
    assert p is not None and p.abstract.strip() == "Abs." and not any(u.startswith(S2) for u, _ in net.calls)
    assert any(n.startswith("s2: rate limited") for n in notes) and c.clock.slept == []


def test_get_many_batches_openalex_and_falls_back_per_id_without_asking_openalex_again(tmp_path):
    works = [oa_work("10.1234/a", "W1"), oa_work("10.1234/B", "W2")]
    s2p = {"paperId": "d" * 40, "title": "Only in S2", "abstract": "S2.", "authors": [{"name": "X"}],
           "externalIds": {"DOI": "10.1234/c"}}  # fmt: skip
    net = FakeNet(
        {
            OA + "works?filter=doi:": [(200, {}, json.dumps({"results": works}))],
            OA + "works?filter=openalex:": [(200, {}, json.dumps({"results": [oa_work("10.1234/w", "W9")]}))],
            S2: [(200, {}, json.dumps(s2p))],
        }
    )
    papers, _ = make(tmp_path, net).lookup_many(["10.1234/a", "doi:10.1234/b", "10.1234/c", "W9", "w9"])
    assert [p.title if p else None for p in papers] == ["Paper 10.1234/a", "Paper 10.1234/B", "Only in S2", "Paper 10.1234/w",
                                                        "Paper 10.1234/w"]  # fmt: skip
    oa_calls = [u for u, _ in net.calls if u.startswith(OA)]
    assert len(oa_calls) == 2 and "10.1234/a|10.1234/b|10.1234/c" in urllib.parse.unquote(oa_calls[0])
    assert len(net.calls) == 3  # 10.1234/c: S2 once, OpenAlex not asked again


def test_get_many_reports_each_id_not_found(tmp_path):
    net = FakeNet({OA: [(200, {}, json.dumps({"results": []}))]})
    papers, notes = make(tmp_path, net).lookup_many(["10.1234/x", "10.1234/y"])
    assert papers == [None, None] and "not found: 10.1234/x" in notes and "not found: 10.1234/y" in notes


def test_a_failed_or_unreadable_batch_is_a_note_and_openalex_is_not_asked_per_id(tmp_path):
    s2p = {"paperId": "d" * 40, "title": "From S2", "abstract": "S2.", "authors": [{"name": "X"}]}
    for answer in [(400, {}, ""), (200, {}, "not json")]:
        net = FakeNet({OA: [answer], S2: [(200, {}, json.dumps(s2p))]})
        (tmp_path / str(answer[0])).mkdir()
        papers, notes = make(tmp_path / str(answer[0]), net).lookup_many(["10.1234/a", "10.1234/b", "1706.03762"])
        assert [p.title for p in papers if p] == ["From S2"] * 3 and notes[0].startswith("openalex: ")
        assert sum(u.startswith(OA) for u, _ in net.calls) == 1  # the arXiv id is not batched: OpenAlex is no source
    assert lit.openalex_key("1706.03762") is None and lit.openalex_key("10.1234/a|b") is None


def test_get_many_rejects_a_bad_id_before_any_call(tmp_path):
    net = FakeNet({})
    with pytest.raises(ValueError):
        make(tmp_path, net).lookup_many(["10.1234/a", "not an id"])
    assert net.calls == []


def test_parsers_read_authors_and_open_access_links():
    s2p = {"paperId": "e" * 40, "title": "T", "authors": [{"name": "A B"}], "openAccessPdf": {"url": "https://p"}}
    (s,) = lit.parse_s2(json.dumps(s2p))
    assert (s.authors, s.oa_url) == (["A B"], "https://p")
    cr = {
        "message": {"DOI": "10.1234/x", "title": ["T"], "author": [{"given": "Ada", "family": "L"}, {"name": "Team"}]}
    }
    assert lit.parse_crossref(json.dumps(cr))[0].authors == ["Ada L", "Team"]
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2305.12345v2</id>
      <title>T</title><author><name>Ann One</name></author><author><name>Bo Two</name></author></entry></feed>"""
    (a,) = lit.parse_arxiv(atom)
    assert (a.authors, a.oa_url) == (["Ann One", "Bo Two"], "https://arxiv.org/pdf/2305.12345")
    ep = {
        "resultList": {
            "result": [{"title": "T", "authorString": "Doe J, Roe R.", "pmcid": "PMC9", "isOpenAccess": "Y"}]
        }
    }
    (e,) = lit.parse_europepmc(json.dumps(ep))
    assert (e.authors, e.oa_url) == (["Doe J", "Roe R"], "https://europepmc.org/article/PMC/PMC9")
    (closed,) = lit.parse_europepmc(json.dumps({"resultList": {"result": [{"title": "T", "pmcid": "PMC9"}]}}))
    assert closed.oa_url == ""
    w = oa_work("10.1234/a") | {"best_oa_location": None}
    assert lit.parse_openalex(json.dumps(w))[0].oa_url == "https://example.org/landing"


def test_a_doi_get_asks_europepmc_last():
    assert [s for s, _ in lit.get_urls("10.1234/x")] == ["openalex", "s2", "crossref", "europepmc"]
    assert 'DOI:"10.1234/x"' in urllib.parse.unquote(lit.get_urls("10.1234/x")[-1][1])


@given(
    a=st.lists(st.text(max_size=3), max_size=3),
    b=st.lists(st.text(max_size=3), max_size=3),
    ta=st.text(max_size=5),
    tb=st.text(max_size=5),
)
def test_fill_never_drops_a_field_either_record_had(a, b, ta, tb):
    p = lit.Paper("T", authors=list(a), abstract=ta, oa_url=tb)
    q = lit.Paper("T", authors=list(b), abstract=tb, oa_url=ta)
    lit.fill(p, q)
    assert p.authors == (a or b) and p.abstract == (ta or tb) and p.oa_url == (tb or ta)


def test_dev_lit_get_takes_several_ids_and_exits_1_when_one_is_missing(tmp_path, monkeypatch, capsys):
    from devkit import cli

    net = FakeNet({OA: [(200, {}, json.dumps({"results": [oa_work("10.1234/a")]}))]})
    monkeypatch.setattr(lit, "client", lambda: make(tmp_path, net))
    assert cli.run_lit(cli.build().parse_args(["lit", "get", "10.1234/a", "10.1234/zz"])) == 1
    out = capsys.readouterr().out
    assert "authors: Ada Lovelace" in out and "note: not found: 10.1234/zz" in out


def test_lit_bypass_script_counts_direct_api_calls_per_run(tmp_path):
    """scripts/lit_bypass.py, the #174 re-measure: curl or WebFetch to an index API counts; dev lit and greps do not."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lit_bypass", Path(__file__).parents[1] / "scripts/lit_bypass.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.apis_called("Bash", {"command": 'curl -s "https://api.openalex.org/works/doi:10.1/x"'}) == ["openalex"]
    assert mod.apis_called("WebFetch", {"url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?q"}) == [
        "europepmc"
    ]
    assert mod.apis_called("Bash", {"command": "uv run dev lit get 10.1/x"}) == []
    assert mod.apis_called("Bash", {"command": "grep -n api.crossref.org devkit/lit.py"}) == []
    sub = tmp_path / "p" / "s1" / "subagents"
    sub.mkdir(parents=True)

    def run(name: str, atype: str, day: str, tools: list[tuple[str, dict]]) -> None:
        (sub / f"{name}.meta.json").write_text(json.dumps({"agentType": atype}))
        recs = [{"timestamp": f"{day}T10:00:00Z", "message": {"content": "go"}}]
        recs += [{"message": {"content": [{"type": "tool_use", "name": n, "input": i}]}} for n, i in tools]
        (sub / f"{name}.jsonl").write_text("\n".join(map(json.dumps, recs)) + "\nnot json\n")

    curl = ("Bash", {"command": "curl https://api.crossref.org/works/10.1/x"})
    run("a", "harness-ops:researcher", "2026-10-12", [curl, curl])
    run("b", "harness-ops:researcher", "2026-10-15", [("Bash", {"command": "uv run dev lit get 10.1/x"})])
    run("c", "harness-ops:research-auditor", "2026-10-15", [curl])
    res = mod.measure(tmp_path / "p")
    assert (res["harness-ops:researcher"]["runs"], res["harness-ops:researcher"]["bypass_runs"]) == (2, 1)
    assert res["harness-ops:researcher"]["calls"]["crossref"] == 2
    after = mod.measure(tmp_path / "p", since="2026-10-14")
    assert (after["harness-ops:researcher"]["runs"], after["harness-ops:researcher"]["bypass_runs"]) == (1, 0)
    assert after["harness-ops:research-auditor"]["bypass_runs"] == 1


# ---- citation chasing: refs (backward) and cited-by (forward) through OpenAlex (#193) ----


def chase_body(n: int, count: int) -> str:
    return json.dumps({"meta": {"count": count}, **json.loads(oa_body(n, "10.9/c"))})


def test_chase_urls_filter_sort_by_citations_and_take_years():
    refs = lit.chase_url("refs", "W42", 5)
    assert refs.startswith(OA + "works?filter=cited_by:W42&") and "sort=cited_by_count:desc" in refs
    assert "per_page=5" in refs and "abstract_inverted_index" in refs
    assert "filter=cites:W42,publication_year:2015-2020&" in lit.chase_url("cited-by", "W42", 5, "2015-2020")
    with pytest.raises(ValueError):
        lit.chase_url("sideways", "W42", 5)


def test_refs_of_a_w_id_take_one_call_and_say_how_many_there_are(tmp_path):
    net = FakeNet({OA + "works?filter=cited_by:W7&": [(200, {}, chase_body(3, 41))]})
    papers, notes = make(tmp_path, net).chase("w7", "refs", n=3)
    assert [p.title for p in papers] == [f"OpenAlex paper {i}" for i in range(3)]
    assert len(net.calls) == 1
    assert notes == ["3 of 41 references of W7, most cited first"]


def test_cited_by_resolves_a_doi_or_arxiv_id_to_its_w_id_first_and_caches(tmp_path):
    work = json.dumps({"id": "https://openalex.org/W99", "doi": "https://doi.org/10.1234/x", "title": "T"})
    net = FakeNet(
        {
            OA + "works/doi:10.48550/arXiv.1706.03762?": [(200, {}, work)],
            OA + "works/doi:10.1234/x?": [(200, {}, work)],
            OA + "works?filter=cites:W99&": [(200, {}, chase_body(2, 2))],
        }
    )
    c = make(tmp_path, net)
    papers, notes = c.chase("arxiv:1706.03762v2", "cited-by", n=2)
    assert len(papers) == 2 and notes == ["2 of 2 citing works of W99, most cited first"]
    papers, _ = c.chase("doi:10.1234/x", "cited-by", n=2)
    assert len(papers) == 2
    assert sum("filter=cites:W99" in u for u, _ in net.calls) == 1  # the second chase came from the shared cache


def test_chase_rejects_an_id_openalex_cannot_resolve_before_any_call(tmp_path):
    net = FakeNet({})
    for bad in ("a" * 40, "not an id"):
        with pytest.raises(ValueError):
            make(tmp_path, net).chase(bad, "refs")
    assert net.calls == []


def test_chase_failures_are_notes(tmp_path):
    net = FakeNet({OA + "works/doi:": [(404, {}, "")]})
    assert make(tmp_path, net).chase("10.1234/missing", "refs") == ([], ["openalex: not found"])
    net = FakeNet({OA: [(429, {"Retry-After": "86400"}, "")]})
    papers, notes = make(tmp_path / "b", net).chase("W1", "cited-by")
    assert papers == [] and "rate limited" in notes[0]
    net = FakeNet({OA: [(200, {}, "not json")]})
    assert make(tmp_path / "c", net).chase("W1", "refs") == ([], ["openalex: unreadable answer (JSONDecodeError)"])


def test_a_paper_nobody_cites_is_no_results_not_a_failure(tmp_path):
    net = FakeNet({OA: [(200, {}, json.dumps({"meta": {"count": 0}, "results": []}))]})
    assert make(tmp_path, net).chase("W1", "cited-by") == ([], [])


def test_refs_with_no_reference_list_says_why_and_where_else_to_look(tmp_path):
    """Live 2026-10-13: OpenAlex has referenced_works_count 0 for the preprint 10.48550/arXiv.2406.10279."""
    work = json.dumps({"id": "https://openalex.org/W4399795583", "title": "A preprint"})
    net = FakeNet(
        {
            OA + "works/doi:10.48550/arXiv.2406.10279?": [(200, {}, work)],
            OA + "works?filter=cited_by:W4399795583&": [(200, {}, json.dumps({"meta": {"count": 0}, "results": []}))],
        }
    )
    papers, notes = make(tmp_path, net).chase("10.48550/arXiv.2406.10279", "refs")
    assert papers == [] and notes == [
        "OpenAlex lists no references for W4399795583 (common for arXiv preprints); "
        "try the published version's DOI or the arxiv MCP"
    ]
    assert "note: OpenAlex lists no references for W4399795583" in lit.render(papers, notes)


def test_dev_lit_refs_and_cited_by_over_a_local_fake_server(tmp_path, monkeypatch, capsys):
    """Over real HTTP: `dev lit refs` and `dev lit cited-by` print the compact lines `lit search` prints."""
    from devkit import cli

    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            body = chase_body(2, 9).encode() if "filter=" in self.path else b""
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"

    def local(url: str, headers: dict) -> tuple[int, dict, str]:
        return lit.http_get(base + url.split("://", 1)[1], headers, timeout=5)

    clock = Clock()
    monkeypatch.setattr(lit, "client", lambda: lit.Client(tmp_path, get=local, clock=clock, sleep=clock.sleep, env={}))
    try:
        assert cli.run_lit(cli.build().parse_args(["lit", "refs", "W5", "-n", "2", "--snippet", "0"])) == 0
        refs = capsys.readouterr().out
        assert cli.run_lit(cli.build().parse_args(["lit", "cited-by", "W5", "--years", "2020-"])) == 0
        cited = capsys.readouterr().out
        assert cli.run_lit(cli.build().parse_args(["lit", "refs", "10.1234/gone"])) == 1
        gone = capsys.readouterr().out
        assert cli.run_lit(cli.build().parse_args(["lit", "refs", "nonsense"])) == 2
    finally:
        server.shutdown()
    assert refs.splitlines()[0] == lit.parse_openalex(chase_body(2, 9))[0].line(0)
    assert refs.splitlines()[-1] == "note: 2 of 9 references of W5, most cited first" and "\n  " not in refs
    assert "note: 2 of 9 citing works of W5, most cited first" in cited and "Retrieval practice works." in cited
    assert any("filter=cites:W5,publication_year:2020-&" in p for p in seen)
    assert "note: openalex: not found" in gone
