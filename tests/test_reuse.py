"""devkit.reuse (`dev reuse`): recall, re-rank, gates, quality and output against a fake network. No network."""

import json
import math
import urllib.parse

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devkit import cli, code, lit, reuse

NOW = 1_791_000_000.0  # 2026-10-03
DAY = 86400


def iso(days_ago: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(NOW - days_ago * DAY, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Net:
    """A fake network: routes(url) -> (status, headers, body); every call recorded."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict):
        self.calls.append((url, headers))
        got = self.routes(url)
        if got is None:
            return 404, {}, ""
        if isinstance(got, tuple):
            return got
        return 200, {}, json.dumps(got)

    def urls(self, prefix: str = "") -> list[str]:
        return [u for u, _ in self.calls if u.startswith(prefix)]


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(tmp_path, net, env=None) -> lit.Client:
    clock = Clock()
    return lit.Client(
        tmp_path, get=net, clock=clock, sleep=clock.sleep, env=env or {}, rates=reuse.RATES, keys=reuse.KEYS,
        auth=reuse.auth,
    )  # fmt: skip


def gh_repo(full: str, desc: str, stars=100, topics=(), lang="Python", lic="MIT", archived=False, pushed=10):
    return {
        "full_name": full,
        "html_url": f"https://github.com/{full}",
        "description": desc,
        "stargazers_count": stars,
        "forks_count": stars // 10,
        "topics": list(topics),
        "language": lang,
        "license": {"spdx_id": lic} if lic else None,
        "archived": archived,
        "pushed_at": iso(pushed),
    }


def record(name, repo, desc="", eco="pypi", lic=("MIT",), first=2000, last=30, versions=40, card=7.0, **kw):
    rec = {
        "name": name,
        "ecosystem": eco,
        "repository_url": f"https://github.com/{repo}" if repo else None,
        "description": desc,
        "normalized_licenses": list(lic),
        "first_release_published_at": iso(first),
        "latest_release_published_at": iso(last),
        "versions_count": versions,
        "downloads": 2_000_000,
        "downloads_period": "last-month",
        "dependent_repos_count": 5000,
        "dependent_packages_count": 300,
        "documentation_url": f"https://{name}.readthedocs.io",
        "advisories": [],
        "status": None,
        "repo_metadata": {
            "stargazers_count": 8000,
            "forks_count": 400,
            "topics": ["retry"],
            "archived": False,
            "pushed_at": iso(5),
            "commit_stats": {"total_committers": 98},
            "scorecard": {"data": {"score": card, "checks": [{"name": "Maintained", "score": 10}]}},
        },
    }
    rec.update(kw)
    return rec


BLUEOAK_LIST = {
    "ratings": [
        {"name": "Silver", "licenses": [{"id": "MIT"}, {"id": "Apache-2.0"}]},
        {"name": "Bronze", "licenses": [{"id": "BSD-3-Clause"}]},
        {"name": "Lead", "licenses": [{"id": "WTFPL"}]},
    ]
}
BLUEOAK_COPYLEFT = {
    "families": {
        "weak": [{"name": "LGPL", "versions": [{"id": "LGPL-3.0-only"}]}],
        "strong": [{"name": "GPL", "versions": [{"id": "GPL-3.0-only"}, {"id": "GPL-3.0-or-later"}]}],
        "network": [{"name": "AGPL", "versions": [{"id": "AGPL-3.0-only"}]}],
    }
}
SPDX_LIST = {
    "licenses": [
        {"licenseId": i, "isOsiApproved": i != "WTFPL"}
        for i in ("MIT", "Apache-2.0", "BSD-3-Clause", "WTFPL", "LGPL-3.0-only", "GPL-3.0-only", "AGPL-3.0-only")
    ]
}

TENACITY = record("tenacity", "jd/tenacity", "Retry code until it succeeds")


def world(
    github=None,
    keywords=None,
    lookups=None,
    registry=frozenset({"tenacity", "backoff", "retrying"}),
    advisories=(),
    down=(),
):
    """Routes for a small, consistent world. github(q) -> items; keywords[term] -> records; lookups[repo] -> records."""
    github = github or (lambda q: [gh_repo("jd/tenacity", "Retry code until it succeeds", 8000, ["retry", "python"])])
    keywords = keywords if keywords is not None else {}
    lookups = lookups if lookups is not None else {"github.com/jd/tenacity": [TENACITY]}

    def routes(url: str):
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        host = u.netloc
        if host in down:
            return 503, {}, ""
        if host == "api.github.com":
            items = github(q["q"][0])
            return {"total_count": len(items), "items": items}
        if host == "packages.ecosyste.ms":
            if u.path.startswith("/api/v1/keywords/"):
                term = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
                return {"name": term, "packages": keywords.get(term, [])}
            if u.path == "/api/v1/packages/lookup":
                return lookups.get(q["repository_url"][0].removeprefix("https://"), [])
        if host == "api.deps.dev":
            path = urllib.parse.unquote(u.path)
            if "/projects/" in path:
                return {"scorecard": {"overallScore": 6.0, "checks": [{"name": "Maintained", "score": 3}]}}
            if "/versions/" in path:
                return {"advisoryKeys": [{"id": a} for a in advisories], "isDeprecated": False, "licenses": ["MIT"]}
            name = path.rsplit("/", 1)[1]
            return {
                "versions": [
                    {"versionKey": {"version": "1.0"}, "publishedAt": iso(900), "isDefault": False},
                    {"versionKey": {"version": "2.0"}, "publishedAt": iso(30), "isDefault": True},
                    {"versionKey": {"version": "2.1"}, "publishedAt": iso(10), "isDefault": False},
                ],
                "name": name,
            }
        if host == "spdx.org":
            return SPDX_LIST
        if url == reuse.BLUEOAK[0]:
            return BLUEOAK_LIST
        if url == reuse.BLUEOAK[1]:
            return BLUEOAK_COPYLEFT
        if host in ("pypi.org", "registry.npmjs.org", "crates.io"):
            name = urllib.parse.unquote(u.path.split("/")[-2 if host == "pypi.org" else -1])
            if host == "registry.npmjs.org":
                name = urllib.parse.unquote(u.path.split("/")[1])
            return {"ok": True} if name in registry else None
        return None

    return routes


# ---- queries ----


def test_relax_drops_stopwords_and_caps_terms():
    assert reuse.relax("a library to Retry the HTTP call with exponential backoff and jitter please now") == [
        "retry",
        "http",
        "call",
        "exponential",
        "backoff",
        "jitter",
    ]
    assert reuse.words("parseHTTPResponse snake_case") == ["parse", "httpresponse", "snake", "case"]


def test_need_from_reads_name_docstring_comments_and_imports():
    text = 'import yaml\nfrom lxml import etree\n\ndef load_config(p):\n    """Parse a YAML file."""\n    # merge env\n'
    need = reuse.need_from(text, "load_config").split()
    for w in ("load", "config", "parse", "yaml", "file", "merge", "env", "lxml"):
        assert w in need
    assert reuse.need_from("def broken(:\n  # still a comment\n").split()[:2] == ["still", "comment"]


def test_from_spec_on_the_backoff_fixture_names_retry_and_the_files_imports():
    need = reuse.from_spec("tests/fixtures/reuse/backoff_helper.py:retry_with_backoff").split()
    assert {"retry", "backoff", "exponential", "jitter", "random", "time"} <= set(need)
    assert "unrelated" not in need  # only the symbol, not its neighbours
    assert "unrelated" in reuse.from_spec("tests/fixtures/reuse/backoff_helper.py").split()
    with pytest.raises(LookupError):
        reuse.from_spec("tests/fixtures/reuse/backoff_helper.py:nope")


def test_code_source_returns_file_symbol_and_method():
    assert "def retry_with_backoff" in code.source("tests/fixtures/reuse/backoff_helper.py")
    body = code.source("tests/fixtures/reuse/backoff_helper.py", "retry_with_backoff")
    assert body.startswith("def retry_with_backoff") and "def unrelated" not in body
    assert code.source("devkit/lit.py", "Bucket.acquire").lstrip().startswith("def acquire")
    with pytest.raises(LookupError):
        code.source("no/such/file.py")


# ---- recall and dedup ----


def test_search_finds_and_deduplicates_by_repository(tmp_path):
    kw = {"retry": [TENACITY, record("backoff", "litl/backoff", "Function decoration for backoff and retry")]}
    net = Net(world(keywords=kw))
    rep = reuse.search(make(tmp_path, net), ["retry with exponential backoff"], "pypi", n=5)
    names = [c.name for c in rep["candidates"]]
    assert names.count("tenacity") == 1 and "backoff" in names
    t = next(c for c in rep["candidates"] if c.name == "tenacity")
    assert t.repo == "github.com/jd/tenacity" and set(t.found_by) == {"github", "ecosyste.ms"}
    assert all("language:python" in urllib.parse.unquote_plus(u) for u in net.urls("https://api.github.com"))


def test_also_paraphrases_are_each_searched(tmp_path):
    net = Net(world())
    rep = reuse.search(make(tmp_path, net), ["retry flaky calls", "resilient decorator"], "pypi")
    qs = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["q"][0] for u in net.urls("https://api.github.com")]
    assert any(q.startswith("retry flaky calls") for q in qs) and any(q.startswith("resilient decorator") for q in qs)
    assert rep["queries"] == ["retry flaky calls", "resilient decorator"]


def test_or_fallback_when_and_query_is_thin_and_topic_expansion(tmp_path):
    many = [gh_repo(f"o/r{i}", "retry helper", 10, ["retry", "resilience"]) for i in range(6)]

    def github(q):
        return many if " OR " in q or q.startswith("topic:") else many[:1]

    net = Net(world(github=github, lookups={}))
    reuse.search(make(tmp_path, net), ["retry flaky"], "pypi")
    qs = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["q"][0] for u in net.urls("https://api.github.com")]
    assert "retry OR flaky language:python" in qs
    assert "topic:resilience language:python" in qs and "topic:retry language:python" in qs


def test_keywords_are_filtered_to_the_ecosystem(tmp_path):
    kw = {"retry": [record("mulligan", "a/mulligan", "retry", eco="npm"), TENACITY]}
    net = Net(world(github=lambda q: [], keywords=kw, registry={"tenacity", "mulligan"}))
    names = [c.name for c in reuse.search(make(tmp_path, net), ["retry"], "pypi")["candidates"]]
    assert names == ["tenacity"]
    names = [c.name for c in reuse.search(make(tmp_path, Net(world(github=lambda q: [], keywords=kw, registry={"tenacity", "mulligan"}))), ["retry"], "any")["candidates"]]  # fmt: skip
    assert set(names) == {"tenacity", "mulligan"}


def test_unknown_ecosystem_is_refused(tmp_path):
    with pytest.raises(ValueError):
        reuse.search(make(tmp_path, Net(world())), ["x"], "maven")


# ---- re-rank ----


def test_bm25_path_says_so_and_ranks_the_matching_doc_first(tmp_path):
    items = [gh_repo("a/colors", "terminal colours", 10), gh_repo("jd/tenacity", "retry with backoff", 10)]
    net = Net(world(github=lambda q: items))
    rep = reuse.search(make(tmp_path, net), ["retry backoff"], "pypi", why="model2vec not installed")
    assert rep["rerank"] == "bm25 (model2vec not installed)"
    assert rep["candidates"][0].name == "tenacity"


def test_semantic_path_uses_the_encoder_and_says_so(tmp_path):
    items = [gh_repo("a/colors", "terminal colours", 10), gh_repo("jd/tenacity", "something else", 10)]

    def enc(texts):  # a fake embedding: 'colour' axis and everything else
        return [[1.0, 0.0] if "colour" in t else [0.0, 1.0] for t in texts]

    rep = reuse.search(make(tmp_path, Net(world(github=lambda q: items))), ["retry"], "pypi", encoder=enc)
    assert rep["rerank"].startswith("semantic (model2vec")
    by = {c.name: c.relevance for c in rep["candidates"]}
    assert by["tenacity"] == 1.0 and by["a/colors"] == 0.0


def test_load_encoder_falls_back_when_model2vec_is_missing_or_fails(monkeypatch):
    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(reuse.importlib, "import_module", missing)
    enc, why = reuse.load_encoder()
    assert enc is None and "not installed" in why

    class Broken:
        class StaticModel:
            @staticmethod
            def from_pretrained(name):
                raise OSError("offline")

    monkeypatch.setattr(reuse.importlib, "import_module", lambda name: Broken)
    enc, why = reuse.load_encoder()
    assert enc is None and "could not load" in why

    class Arr(list):
        def tolist(self):
            return list(self)

    class Works:
        class StaticModel:
            @staticmethod
            def from_pretrained(name):
                return type("M", (), {"encode": lambda self, texts: Arr([[1.0] for _ in texts])})()

    monkeypatch.setattr(reuse.importlib, "import_module", lambda name: Works)
    enc, why = reuse.load_encoder()
    assert enc is not None and enc(["a", "b"]) == [[1.0], [1.0]] and why == ""


@given(st.lists(st.lists(st.sampled_from("abcdef"), max_size=6), min_size=1, max_size=8))
def test_bm25_scores_are_in_unit_range(docs):
    scores = reuse.bm25(docs, [["a", "b"], ["c"]])
    assert len(scores) == len(docs) and all(0.0 <= s <= 1.0 + 1e-9 for s in scores)


# ---- verification (G1) ----


def test_unverified_package_is_never_printed(tmp_path):
    ghost = record("tenacity-pro", None, "retry")  # an ecosyste.ms record the registry no longer knows
    net = Net(world(github=lambda q: [], keywords={"retry": [ghost, TENACITY]}))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi")
    assert [c.name for c in rep["candidates"]] == ["tenacity"]
    assert any("dropped pypi:tenacity-pro" in n and "G1 FAIL" in n for n in rep["notes"])
    assert "tenacity-pro" not in reuse.render(rep).split("note:")[0]


def test_github_repo_whose_package_is_missing_is_shown_as_a_repository(tmp_path):
    net = Net(world(registry=frozenset()))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi")
    c = rep["candidates"][0]
    assert c.ecosystem == "" and c.name == "jd/tenacity" and c.gates["G1"] == "pass (github)"


def test_registry_down_means_unverified(tmp_path):
    net = Net(world(github=lambda q: [], keywords={"retry": [TENACITY]}, down=("pypi.org",)))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi")
    assert rep["candidates"] == [] and any("tenacity" in n for n in rep["notes"])


# ---- metrics, gates, quality ----


def test_each_candidate_shows_maintenance_metrics(tmp_path):
    rep = reuse.search(make(tmp_path, Net(world())), ["retry"], "pypi")
    c = rep["candidates"][0]
    assert (c.contributors, c.dependents, c.scorecard, c.advisories) == (98, 5300, 7.0, 0)
    assert c.releases_last_year == 2 and c.license == "MIT" and c.last_release
    out = reuse.render(rep)
    for s in ("98 contributors", "5300 dependents", "scorecard 7.0", "0 open advisories", "2 releases/yr", "MIT"):
        assert s in out
    assert "gates: G1 pass (pypi), G2 pass, G3 pass, G4 pass, G5 pass (MIT: Blue Oak Silver), G6 pass" in out


def test_open_advisories_are_flagged_and_malicious_ones_fail(tmp_path):
    rep = reuse.search(make(tmp_path, Net(world(advisories=("GHSA-1", "PYSEC-2")))), ["retry"], "pypi")
    c = rep["candidates"][0]
    assert c.advisories == 2 and c.gates["G6"].startswith("flag") and c.status == "pass"
    rep = reuse.search(make(tmp_path / "b", Net(world(advisories=("MAL-2026-1",)))), ["retry"], "pypi")
    assert rep["candidates"] == [] and any("G2 FAIL (malicious: MAL-2026-1)" in n for n in rep["notes"])


def gate(c: reuse.Candidate, allow=False, tiers=None):
    tiers = tiers if tiers is not None else {"MIT": "Silver", "GPL-3.0-only": "strong", "LGPL-3.0-only": "weak"}
    return reuse.gates(c, NOW, tiers, allow)


def test_gates_new_dormant_archived_deprecated():
    base = dict(name="x", verified="pypi", license="MIT")
    assert gate(reuse.Candidate(**base, first_release=iso(30)[:10], last_release=iso(30)[:10]))["G3"].startswith("HOLD")
    assert gate(reuse.Candidate(**base, first_release=iso(2000)[:10], last_release=iso(800)[:10]))["G4"].startswith(
        "HOLD (dormant"
    )
    assert gate(reuse.Candidate(**base, archived=True))["G4"] == "FAIL (archived)"
    assert gate(reuse.Candidate(**base, deprecated=True))["G4"] == "FAIL (deprecated)"
    ok = gate(reuse.Candidate(**base, first_release=iso(2000)[:10], last_release=iso(30)[:10], advisories=0))
    assert reuse.status(ok) == "pass"
    assert reuse.status(gate(reuse.Candidate(**base, archived=True, last_release=iso(800)[:10]))) == "FAIL"


@pytest.mark.parametrize(
    ("lic", "allow", "outcome"),
    [
        ("MIT", False, "pass"),
        ("GPL-3.0-only", False, "FAIL"),
        ("GPL-3.0", False, "FAIL"),  # deprecated SPDX id, mapped to -only
        ("GPL-3.0-only", True, "flag"),
        ("LGPL-3.0-only", False, "flag"),
        ("GPL-3.0-only OR MIT", False, "pass"),
        ("MIT AND GPL-3.0-only", False, "FAIL"),
        ("WTFPL", False, "HOLD"),
        ("non-standard", False, "HOLD"),
        ("", False, "HOLD"),
    ],
)
def test_licence_gate_by_blue_oak_tier(lic, allow, outcome):
    tiers = {"MIT": "Silver", "GPL-3.0-only": "strong", "LGPL-3.0-only": "weak", "WTFPL": "Lead"}
    assert reuse.licence_gate(lic, tiers, allow)[0] == outcome
    assert reuse.licence_gate("MIT", None, allow)[0] == "HOLD"  # lists down: not checked


def test_osi_flag():
    osi = {"MIT": True, "WTFPL": False}
    assert reuse.osi_flag("MIT", osi) == ""
    assert "not OSI" in reuse.osi_flag("WTFPL", osi)
    assert reuse.osi_flag("", osi) == "no licence found"
    assert "not checked" in reuse.osi_flag("MIT", None)


def test_copyleft_candidate_is_dropped_unless_allowed_and_non_osi_is_flagged(tmp_path):
    gpl = record("tenacity", "jd/tenacity", "retry", lic=("GPL-3.0-only",))
    rep = reuse.search(make(tmp_path, Net(world(lookups={"github.com/jd/tenacity": [gpl]}))), ["retry"], "pypi")
    assert rep["candidates"] == [] and any("G5 FAIL" in n for n in rep["notes"])
    rep = reuse.search(
        make(tmp_path / "b", Net(world(lookups={"github.com/jd/tenacity": [gpl]}))),
        ["retry"],
        "pypi",
        allow_copyleft=True,
    )
    assert rep["candidates"][0].gates["G5"].startswith("flag")
    wtf = record("tenacity", "jd/tenacity", "retry", lic=("WTFPL",))
    rep = reuse.search(make(tmp_path / "c", Net(world(lookups={"github.com/jd/tenacity": [wtf]}))), ["retry"], "pypi")
    c = rep["candidates"][0]
    assert c.status == "HOLD" and "licence WTFPL is not OSI-approved" in c.flags
    assert "HOLD" in reuse.render(rep) and "FLAG: licence WTFPL" in reuse.render(rep)


@given(st.one_of(st.none(), st.floats(min_value=0, max_value=1e12)), st.floats(min_value=1, max_value=1e7))
def test_norm_is_bounded_and_saturates(s, cap):
    v = reuse.norm(s, cap)
    assert 0.0 <= v <= 1.0
    if s is not None and s >= cap:
        assert v == pytest.approx(1.0)


def test_quality_weights_and_factors():
    assert sum(reuse.Q_WEIGHTS.values()) == pytest.approx(1.0, abs=0.002)
    strong = reuse.Candidate(
        name="a", first_release=iso(4000), last_release=iso(10), releases=80, docs="d", scorecard=10, maintained=10,
        downloads_month=10**7, stars=10**5, contributors=500,
    )  # fmt: skip
    assert reuse.quality(strong, NOW) == pytest.approx(sum(reuse.Q_WEIGHTS.values()))
    empty = reuse.Candidate(name="b")
    assert reuse.quality(empty, NOW) == pytest.approx(reuse.Q_WEIGHTS["security"] * reuse.SECURITY_UNKNOWN)
    f = reuse.factors(reuse.Candidate(name="c", maintained=0, last_release=iso(30)), NOW)
    assert f["maintenance"] == 1.0  # recency rescues a Scorecard Maintained of 0 (httpx, #169 F3)
    assert reuse.recency(iso(455), NOW) == pytest.approx(0.5, abs=0.01)
    assert reuse.factors(reuse.Candidate(name="d", forks=50), NOW)["community"] == pytest.approx(1.0)


def test_score_is_relevance_plus_lambda_quality(tmp_path):
    rep = reuse.search(make(tmp_path, Net(world())), ["retry"], "pypi")
    c = rep["candidates"][0]
    assert c.score == pytest.approx(c.relevance + reuse.LAMBDA * c.quality, abs=1e-3)
    assert rep["score"].startswith(f"S = R + {reuse.LAMBDA} * Q")


# ---- shared client, failures, cache ----


def test_a_source_down_is_reported_not_fatal(tmp_path):
    net = Net(world(keywords={"retry": [TENACITY]}, down=("api.github.com", "api.deps.dev")))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi")
    assert [c.name for c in rep["candidates"]] == ["tenacity"]
    assert any(n.startswith("github:") for n in rep["notes"]) and any(n.startswith("depsdev:") for n in rep["notes"])
    assert rep["candidates"][0].releases_last_year is None


def test_unreadable_answer_is_a_note(tmp_path):
    net = Net(lambda url: (200, {}, "<html>") if "github" in url else world()(url))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi")
    assert any("github: unreadable answer" in n for n in rep["notes"])


def test_requests_are_cached_and_go_through_the_shared_buckets(tmp_path):
    net = Net(world())
    reuse.search(make(tmp_path, net), ["retry"], "pypi")
    first = len(net.calls)
    reuse.search(make(tmp_path, net), ["retry"], "pypi")
    assert first > 0 and len(net.calls) == first  # the second run is served from dev lit's cache
    state = json.loads((tmp_path / "buckets.json").read_text())
    assert {"github", "ecosystems", "depsdev", "pypi"} <= set(state)


def test_github_403_rate_limit_is_a_throttle_with_shared_cooldown(tmp_path):
    answers = iter([(403, {"X-RateLimit-Remaining": "0", "Retry-After": "5"}, ""), (200, {}, '{"items": []}')])
    net = Net(lambda url: next(answers))
    c = make(tmp_path, net)
    assert json.loads(c.fetch("github", reuse.GITHUB + "q=x")) == {"items": []}
    assert len(net.calls) == 2 and c.clock() >= NOW + 5
    assert not lit.limit_403(403, {"x-ratelimit-remaining": "12"})
    with pytest.raises(lit.SourceError, match="HTTP 403"):
        make(tmp_path / "b", Net(lambda url: (403, {}, ""))).fetch("github", reuse.GITHUB + "q=y")


def test_github_token_is_sent_as_a_key_header_and_never_in_the_cache_key(tmp_path):
    net = Net(world())
    c = make(tmp_path, net, env={"GITHUB_TOKEN": "ghp_secret"})
    c.fetch("github", reuse.GITHUB + "q=z")
    url, headers = net.calls[0]
    assert headers["authorization"] == "Bearer ghp_secret" and "ghp_secret" not in url
    assert "authorization" in lit.KEY_HEADERS and c.keyed("github")
    assert "ghp_secret" not in "".join(p.read_text() for p in (tmp_path / "cache").iterdir())
    assert reuse.auth("depsdev", "u", {"GITHUB_TOKEN": "t"}) == ("u", {})


def test_github_token_sources(monkeypatch):
    assert reuse.github_token({"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}) == "a"
    assert reuse.github_token({"GH_TOKEN": "b"}) == "b"
    monkeypatch.setattr(reuse.shutil, "which", lambda name: None)
    assert reuse.github_token({}) == ""
    monkeypatch.setattr(reuse.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(reuse.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "tok\n"})())
    assert reuse.github_token({}) == "tok"


def test_client_shares_dev_lit_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(reuse, "state_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(reuse, "github_token", lambda env: "t")
    c = reuse.client()
    assert c.dir == tmp_path / "lit" and c.rates is reuse.RATES and c.env["GITHUB_TOKEN"] == "t"


# ---- output and CLI ----


def test_json_output_is_complete_and_pasteable(tmp_path):
    rep = reuse.search(make(tmp_path, Net(world())), ["retry"], "pypi")
    data = json.loads(reuse.to_json(rep))
    assert {"queries", "ecosystem", "rerank", "date", "score", "candidates", "notes", "recalled"} <= set(data)
    c = data["candidates"][0]
    assert "record" not in c and c["name"] == "tenacity" and c["gates"]["G1"] == "pass (pypi)"
    assert data["date"] == "2026-10-03"


def test_render_without_candidates_says_so():
    out = reuse.render({"rerank": "none", "queries": ["x"], "recalled": 0, "ecosystem": "pypi", "score": "S",
                        "candidates": [], "notes": ["github: HTTP 500 after 4 tries"]})  # fmt: skip
    assert "no candidates" in out and "note: github: HTTP 500" in out


def dev(argv: list[str]) -> int:
    return cli.run(cli.build().parse_args(argv))


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    """dev reuse wired to a fake network where only queries about retrying find tenacity."""

    def github(q):
        return [gh_repo("jd/tenacity", "Retry code until it succeeds", 8000)] if "retry" in q else []

    net = Net(world(github=github))
    monkeypatch.setattr(reuse, "client", lambda: make(tmp_path, net))
    monkeypatch.setattr(reuse, "load_encoder", lambda: (None, "model2vec not installed"))
    return net


def test_cli_from_a_hand_written_backoff_helper_surfaces_a_retry_library(fake_cli, capsys):
    rc = dev(["reuse", "--from", "tests/fixtures/reuse/backoff_helper.py:retry_with_backoff"])
    out = capsys.readouterr().out
    assert rc == 0 and "tenacity [pypi] github.com/jd/tenacity" in out
    assert out.startswith("re-rank: bm25 (model2vec not installed)")


def test_cli_json_also_and_errors(fake_cli, capsys):
    assert dev(["reuse", "persist flaky calls", "--also", "retry on failure", "--json", "-n", "3"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["queries"] == ["persist flaky calls", "retry on failure"] and data["candidates"]
    assert dev(["reuse"]) == 2
    assert dev(["reuse", "--from", "tests/fixtures/reuse/backoff_helper.py:nope"]) == 2
    assert dev(["reuse", "unmatched need"]) == 0  # nothing found and no source down: not an error
    assert "no candidates" in capsys.readouterr().out


def test_constants_are_sane():
    assert reuse.FRESH_DAYS < reuse.DORMANT_DAYS and reuse.NEW_DAYS < reuse.DORMANT_DAYS
    assert reuse.STAR_WEIGHT * math.log10(1 + 1e5) == pytest.approx(reuse.LAMBDA, abs=0.001)  # #169 F1
    assert reuse.RATES["github"][0] * 60 <= 10  # below GitHub's keyless search limit
