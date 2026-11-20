"""`dev lit snowball` (#591) against a fake OpenAlex that answers the filters it uses from a small citation graph."""

import json
import re
import urllib.parse
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devkit import cli, lit, snowball

OA = "https://api.openalex.org/"


class FakeOpenAlex:
    """works?filter=<field>:<a|b|...> over a graph {W-id: {refs, related, cites, title, ...}}: openalex and doi pick
    works, cited_by the works they cite, cites the works citing any of them; sort and per_page as OpenAlex does."""

    def __init__(self, graph: dict[str, dict], fail: str = "", junk: str = ""):
        self.graph = graph
        self.fail = fail
        self.junk = junk
        self.calls: list[str] = []

    def record(self, w: str) -> dict:
        g = self.graph[w]
        return {
            "id": f"https://openalex.org/{w}",
            "doi": f"https://doi.org/10.9999/{w.lower()}",
            "title": g.get("title", f"Paper {w}"),
            "publication_year": g.get("year", 2020),
            "primary_location": {"source": {"display_name": g.get("venue", "Venue")}},
            "cited_by_count": g.get("cites", 0),
            "referenced_works": [f"https://openalex.org/{r}" for r in g.get("refs", [])],
            "related_works": [f"https://openalex.org/{r}" for r in g.get("related", [])],
            "open_access": {"oa_url": g.get("oa", None)},
        }

    def __call__(self, url: str, headers: dict) -> tuple[int, dict, str]:
        self.calls.append(url)
        if self.fail and self.fail in url:
            return 404, {}, ""
        if self.junk and self.junk in url:
            return 200, {}, "<html>maintenance</html>"
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        name, _, values = q["filter"][0].partition(":")
        vals = set(values.split("|"))
        if name == "openalex":
            hits = [w for w in self.graph if w in vals]
        elif name == "doi":
            hits = [w for w in self.graph if f"10.9999/{w.lower()}" in vals]
        elif name == "cited_by":
            hits = sorted({r for v in vals if v in self.graph for r in self.graph[v].get("refs", [])} & set(self.graph))
        elif name == "cites":
            hits = [w for w, g in self.graph.items() if vals & set(g.get("refs", []))]
        else:
            return 400, {}, ""
        if q.get("sort"):
            hits.sort(key=lambda w: -self.graph[w].get("cites", 0))
        hits = hits[: int(q["per_page"][0])]
        return 200, {}, json.dumps({"meta": {"count": len(hits)}, "results": [self.record(w) for w in hits]})


def client(tmp_path, net) -> lit.Client:
    return lit.Client(tmp_path, get=net, sleep=lambda s: None, env={})


# S1 and S2 are the seeds. R1 is cited by both seeds; C1 and C2 cite S1; C1 also cites X (co-cited with S1) and R9;
# W40 cites S1 and the three seed references (bibliographic coupling); L is in S1's related works; Z is unconnected.
GRAPH = {
    "W1": {"title": "Seed one", "refs": ["W10", "W11"], "related": ["W50"], "cites": 90},
    "W2": {"title": "Seed two", "refs": ["W10", "W12"], "cites": 80},
    "W10": {"title": "Shared reference", "cites": 500, "oa": "https://example.org/r1.pdf", "year": 2001},
    "W11": {"title": "Reference of S1", "cites": 40},
    "W12": {"title": "Reference of S2", "cites": 30},
    "W20": {"title": "Citer one", "refs": ["W1", "W30", "W2"], "cites": 20},
    "W21": {"title": "Citer two", "refs": ["W1", "W30"], "cites": 10},
    "W30": {"title": "Co-cited with the seeds", "cites": 5, "venue": "Journal of Co-citation"},
    "W40": {"title": "Coupled to the seeds", "refs": ["W1", "W10", "W11", "W12"], "cites": 3},
    "W50": {"title": "Related to S1", "cites": 1},
    "W99": {"title": "Unconnected", "cites": 9999},
}


def test_signals_count_links_co_citation_coupling_and_related_works():
    rec = FakeOpenAlex(GRAPH).record
    frontier = [rec("W1"), rec("W2")]
    citers = [rec("W20"), rec("W21"), rec("W40")]
    coupled = [rec("W40"), rec("W20")]
    counts, cites = snowball.signal_counts(frontier, [rec("W10")], citers, coupled, set())
    assert counts["W10"]["link"] == 2  # both seeds cite it
    assert counts["W20"]["link"] == 2 and counts["W21"]["link"] == 1  # W20 cites both seeds
    assert counts["W30"]["cocite"] == 2 and counts["W30"]["link"] == 0  # cited by two citers of the seeds
    assert counts["W40"]["couple"] == 3  # cites three of the seeds' references
    assert counts["W50"] == {"related": 1}
    assert "W1" not in counts and "W2" not in counts and "W99" not in counts
    assert cites["W10"] == 500


def test_snowball_prints_at_most_n_one_line_candidates_without_the_seeds(tmp_path, monkeypatch, capsys):
    net = FakeOpenAlex(GRAPH)
    monkeypatch.setattr(lit, "client", lambda: client(tmp_path, net))
    code = cli.run_lit(cli.build().parse_args(["lit", "snowball", "W1", "doi:10.9999/w2", "-n", "3"]))
    out = capsys.readouterr().out
    assert code == 0
    lines = [x for x in out.splitlines() if x.startswith("- ")]
    assert len(lines) == 3 and len(out.splitlines()) == 3 + sum(x.startswith("note: ") for x in out.splitlines())
    assert not any("Seed" in x or "Unconnected" in x for x in lines)
    scores = [float(m.group(1)) for x in lines if (m := re.search(r"score ([0-9.]+),", x))]
    assert len(scores) == len(lines)
    assert scores == sorted(scores, reverse=True)
    assert "note: hop 1: 5 OpenAlex calls" in out and "note: 6 OpenAlex calls in all" in out
    assert len(net.calls) == 6  # the seeds by DOI and by W-id, refs, citers, coupling, the printed records


def test_each_line_names_year_venue_id_co_citations_open_access_and_the_signals(tmp_path):
    cands, _ = snowball.snowball(client(tmp_path, FakeOpenAlex(GRAPH)), ["W1", "W2"], n=20)
    line = {x.wid: snowball.line(x) for x in cands}
    assert set(line) == {"W10", "W11", "W12", "W20", "W21", "W30", "W40", "W50"}
    assert line["W10"].startswith("- Shared reference (2001, Venue) [openalex:W10 doi:10.9999/w10] score ")
    assert "OA yes" in line["W10"] and "via link+cocite" in line["W10"]  # cited by both seeds and by citer W40
    assert "Journal of Co-citation" in line["W30"] and "co-cited 2, OA no, via cocite" in line["W30"]
    assert line["W40"].endswith("via link+couple") and line["W50"].endswith("via related")


def test_without_neighbour_signals_only_direct_links_rank(tmp_path):
    cands, _ = snowball.snowball(client(tmp_path, FakeOpenAlex(GRAPH)), ["W1", "W2"], n=20, signals=("link",))
    assert {x.wid for x in cands} == {"W10", "W11", "W12", "W20", "W21", "W40"}
    assert all(x.via() == "link" for x in cands)


# A chain: the seed S cites A; A cites B; B cites C. Depth d reaches d hops out.
CHAIN = {
    "W1": {"refs": ["W2"], "cites": 1},
    "W2": {"refs": ["W3"], "cites": 1},
    "W3": {"refs": ["W4"], "cites": 1},
    "W4": {"cites": 1},
}


@pytest.mark.parametrize(("depth", "reached"), [(1, {"W2"}), (2, {"W2", "W3"}), (3, {"W2", "W3", "W4"})])
def test_depth_chases_the_top_candidates_of_each_hop(tmp_path, depth, reached):
    cands, notes = snowball.snowball(client(tmp_path, FakeOpenAlex(CHAIN)), ["W1"], depth=depth)
    assert {x.wid for x in cands} == reached
    assert [n.split(":")[0] for n in notes if n.startswith("hop")] == [f"hop {h}" for h in range(1, depth + 1)]
    hop2 = next((x for x in cands if x.wid == "W3"), None)
    assert hop2 is None or (hop2.hop == 2 and hop2.score < next(x for x in cands if x.wid == "W2").score)


def test_a_hop_adding_nothing_to_the_top_n_stops_the_expansion(tmp_path):
    net = FakeOpenAlex(CHAIN)
    cands, notes = snowball.snowball(client(tmp_path, net), ["W1"], n=1, depth=3)
    assert [x.wid for x in cands] == ["W2"]  # W3, a hop further, scores DECAY as much: below the top 1
    assert [n for n in notes if n.startswith("hop")] == [
        "hop 1: 4 OpenAlex calls, 1 new in the top 1",
        "hop 2: 4 OpenAlex calls, 0 new in the top 1",
    ]


def test_keep_bounds_the_next_frontier(tmp_path):
    graph = {"W1": {"refs": [f"W{i}" for i in range(10, 20)]}} | {
        f"W{i}": {"refs": [f"W{i + 100}"], "cites": i} for i in range(10, 20)
    }
    graph |= {f"W{i + 100}": {} for i in range(10, 20)}
    net = FakeOpenAlex(graph)
    cands, _ = snowball.snowball(client(tmp_path, net), ["W1"], n=50, depth=2, keep=3)
    assert {x.wid for x in cands if x.hop == 2} == {"W119", "W118", "W117"}  # the 3 most cited of hop 1, chased


def test_bad_arguments_fail_before_any_call(tmp_path, monkeypatch, capsys):
    net = FakeOpenAlex(GRAPH)
    for depth in (0, 4):
        with pytest.raises(ValueError):
            snowball.snowball(client(tmp_path, net), ["W1"], depth=depth)
    with pytest.raises(ValueError):
        snowball.snowball(client(tmp_path, net), ["W1"], signals=("link", "vibes"))
    with pytest.raises(ValueError):
        snowball.snowball(client(tmp_path, net), ["0123456789abcdef0123456789abcdef01234567"])
    assert net.calls == []
    monkeypatch.setattr(lit, "client", lambda: client(tmp_path, net))
    assert cli.run_lit(cli.build().parse_args(["lit", "snowball", "W1", "--depth", "9"])) == 2


def test_failures_and_unknown_seeds_are_notes(tmp_path):
    cands, notes = snowball.snowball(client(tmp_path, FakeOpenAlex(GRAPH)), ["W1", "W777"])
    assert cands and "seed not in OpenAlex: W777" in notes
    cands, notes = snowball.snowball(client(tmp_path / "b", FakeOpenAlex(GRAPH, fail="filter=cites")), ["W1"])
    assert {x.wid for x in cands} == {"W10", "W11", "W50"} and "openalex: not found" in notes
    junk = FakeOpenAlex(GRAPH, junk="abstract_inverted_index")  # the printed candidates' records call
    cands, notes = snowball.snowball(client(tmp_path / "d", junk), ["W1"], n=2)
    assert len(cands) == 2 and "openalex: unreadable answer (JSONDecodeError)" in notes
    assert all(snowball.line(x).startswith("- (no record) (n.d.) [openalex:W") for x in cands)
    cands, notes = snowball.snowball(client(tmp_path / "c", FakeOpenAlex(GRAPH)), ["W99"])
    assert cands == [] and "no candidates" in snowball.render(cands, notes)


def test_a_second_run_comes_from_the_shared_cache(tmp_path):
    net = FakeOpenAlex(GRAPH)
    first, _ = snowball.snowball(client(tmp_path, net), ["W1", "W2"])
    calls = len(net.calls)
    again, _ = snowball.snowball(client(tmp_path, net), ["W1", "W2"])
    assert len(net.calls) == calls and [x.wid for x in again] == [x.wid for x in first]


ids = st.integers(min_value=1, max_value=30).map(lambda i: f"W{i}")


@settings(max_examples=30, deadline=None)
@given(
    graph=st.dictionaries(ids, st.lists(ids, max_size=6, unique=True), min_size=1, max_size=30),
    seeds=st.lists(ids, min_size=1, max_size=4, unique=True),
    n=st.integers(min_value=1, max_value=60),
    depth=st.integers(min_value=1, max_value=3),
)
def test_never_more_than_n_never_a_seed_ranked_by_score(tmp_path_factory, graph, seeds, n, depth):
    g = {w: {"refs": refs, "related": refs[:1], "cites": len(refs)} for w, refs in graph.items()}
    net = FakeOpenAlex(g)
    cands, _ = snowball.snowball(client(tmp_path_factory.mktemp("c"), net), seeds, n=n, depth=depth)
    assert len(cands) <= min(n, snowball.MAX_N)
    assert not {x.wid for x in cands} & set(seeds)
    assert len({x.wid for x in cands}) == len(cands)
    assert [x.score for x in cands] == sorted((x.score for x in cands), reverse=True)
    assert all(x.score > 0 and x.via() for x in cands)
    assert all(len(snowball.line(x).splitlines()) == 1 for x in cands)


def test_fusion_ranks_signals_alike_so_a_hub_leads_one_signal_not_the_sum():
    counts = {
        "HUB": Counter(cocite=180),  # cited by nearly every citer of the seeds
        "TOPIC": Counter(link=2, related=2, cocite=3),
        "TIE1": Counter(link=1),
        "TIE2": Counter(link=1),
    }
    s = snowball.fuse(counts)
    assert s["TOPIC"] > s["HUB"]
    assert s["TIE1"] == s["TIE2"] == 1 / (snowball.K + 2)  # tied counts share the better rank
    assert snowball.fuse(counts, ("related",)) == {"TOPIC": 1 / (snowball.K + 1)}


def test_the_coupling_call_is_the_same_url_in_every_process_so_the_cache_answers_it():
    """Regression: the most shared references were taken in a set's order, which changes with the process's hash
    seed, so a rerun asked OpenAlex a different coupling URL and missed the cache and the eval's cassette."""
    refs = [f"W{i}" for i in range(100, 100 + snowball.COUPLE_REFS + 30)]
    a = [{"id": "W1", "referenced_works": refs}, {"id": "W2", "referenced_works": refs[:5]}]
    b = [{"id": "W1", "referenced_works": refs[::-1]}, {"id": "W2", "referenced_works": refs[4::-1]}]
    assert snowball.shared_refs(a) == snowball.shared_refs(b)
    assert snowball.shared_refs(a)[:5] == refs[:5] and len(snowball.shared_refs(a)) == snowball.COUPLE_REFS
