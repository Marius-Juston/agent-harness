"""devkit.lit: shared rate limit, shared cache, keys from env, 429 backoff, compact capped output. No network."""

import http.server
import json
import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import lit


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

    def logged_take(state, now, rate, burst):  # runs under the bucket's lock, with the clock value it decided on
        new, wait = real_take(state, now, rate, burst)
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
    assert p is not None and p.title == "Crossref only" and len(notes) == 2
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
