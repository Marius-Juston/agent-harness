"""devkit.reuse (`dev reuse`): recall, re-rank, gates, quality and output against a fake network. No network."""

import json
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
    readme=None,
    registry_body=None,
    named=None,
):
    """Routes for a small, consistent world. github(q) -> items; keywords[term] -> records; lookups[repo] -> records."""
    github = github or (lambda q: [gh_repo("jd/tenacity", "Retry code until it succeeds", 8000, ["retry", "python"])])
    keywords = keywords if keywords is not None else {}
    lookups = lookups if lookups is not None else {"github.com/jd/tenacity": [TENACITY]}
    named = named if named is not None else {"tenacity": TENACITY}

    def routes(url: str):
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        host = u.netloc
        if host in down:
            return 503, {}, ""
        if host == "api.github.com" and u.path.startswith("/repos/"):
            return {"size": readme} if readme is not None else None
        if host == "api.github.com":
            items = github(q["q"][0])
            return {"total_count": len(items), "items": items}
        if host == "packages.ecosyste.ms":
            if u.path.startswith("/api/v1/keywords/"):
                term = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
                return {"name": term, "packages": keywords.get(term, [])}
            if u.path.startswith("/api/v1/registries/"):
                return named.get(urllib.parse.unquote(u.path.rsplit("/", 1)[1]))
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
            if name not in registry:
                return None
            return registry_body(name) if registry_body else {"ok": True}
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
    searches = net.urls("https://api.github.com/search")
    assert searches and all("is:public language:python" in urllib.parse.unquote_plus(u) for u in searches)


def test_also_paraphrases_are_each_searched(tmp_path):
    net = Net(world())
    rep = reuse.search(make(tmp_path, net), ["retry flaky calls", "resilient decorator"], "pypi")
    qs = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["q"][0] for u in net.urls("https://api.github.com/search")
    ]
    assert any(q.startswith("retry flaky calls") for q in qs) and any(q.startswith("resilient decorator") for q in qs)
    assert rep["queries"] == ["retry flaky calls", "resilient decorator"]


def test_or_fallback_when_and_query_is_thin_and_topic_expansion(tmp_path):
    many = [gh_repo(f"o/r{i}", "retry helper", 10, ["retry", "resilience"]) for i in range(6)]

    def github(q):
        return many if " OR " in q or q.startswith("topic:") else many[:1]

    net = Net(world(github=github, lookups={}))
    reuse.search(make(tmp_path, net), ["retry flaky"], "pypi")
    qs = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["q"][0] for u in net.urls("https://api.github.com/search")
    ]
    assert "retry OR flaky is:public language:python" in qs
    assert "topic:resilience is:public language:python" in qs and "topic:retry is:public language:python" in qs


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


# Blue Oak's own families for the ids below (copyleft.json v11): LGPL and MPL weak, GPL strong.
DUAL_TIERS = {
    **dict.fromkeys(("MPL-1.1", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later"), "weak"),
    **dict.fromkeys(("GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only"), "strong"),
}


@pytest.mark.parametrize(
    ("rec", "lic"),
    [  # ecosyste.ms records as fetched 2026-10-13 (licenses, normalized_licenses)
        (
            {"licenses": "MPL-1.1 OR GPL-2.0-or-later OR LGPL-2.1-or-later",
             "normalized_licenses": ["MPL-1.1", "GPL-2.0-or-later", "LGPL-2.1-or-later"]},
            "MPL-1.1 OR GPL-2.0-or-later OR LGPL-2.1-or-later",
        ),  # cchardet
        (
            {"licenses": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
             "normalized_licenses": ["LGPL-3.0-only", "GPL-2.0-only", "GPL-3.0-only"]},
            "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
        ),  # pyside6
        (
            {"licenses": "GNU Lesser General Public License v3 or later (LGPLv3+)",
             "normalized_licenses": ["LGPL-2.1", "GPL-2.0+"]},
            "LGPL-2.1 OR GPL-2.0+",
        ),  # dramatiq: a classifier, not an expression, so the normalized list joined with OR
        ({"licenses": None, "normalized_licenses": ["LGPL-3.0-or-later"]}, "LGPL-3.0-or-later"),  # LGPL only
    ],
)  # fmt: skip
def test_dual_licensed_and_lgpl_packages_pass_g5_as_weak_copyleft(rec, lic):
    c = reuse.from_record({"name": "x", "ecosystem": "pypi", **rec})
    assert c.license == lic
    outcome, why = reuse.licence_gate(c.license, DUAL_TIERS, False)
    assert outcome == "flag" and "weak copyleft" in why  # an OR passes when any option does; LGPL is weak
    assert reuse.licence_gate("GPL-2.0-or-later AND LGPL-3.0-only", DUAL_TIERS, False)[0] == "FAIL"


@pytest.mark.parametrize(
    ("text", "expr"),
    [
        ("MIT", "MIT"),
        ("(MIT OR Apache-2.0)  AND BSD-3-Clause", "(MIT OR Apache-2.0) AND BSD-3-Clause"),
        ("GPL-2.0-or-later WITH Classpath-exception-2.0", "GPL-2.0-or-later WITH Classpath-exception-2.0"),
        ("MIT License", ""),
        ("MIT OR", ""),
        ("MIT/Apache-2.0", ""),
        ("BSD", "BSD"),
        ("", ""),
        (None, ""),
    ],
)
def test_spdx_expression_accepts_only_expressions(text, expr):
    assert reuse.spdx_expression(text) == expr


@given(st.text(max_size=40))
def test_spdx_expression_returns_its_input_or_nothing(text):
    got = reuse.spdx_expression(text)
    assert got in ("", " ".join(text.split()))


def test_check_prefers_pypis_license_expression(tmp_path):
    expr = "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"
    w = world(named={"pyside6": record("pyside6", "pyside/pyside6", lic=("GPL-3.0-only",))},
              registry_body=lambda name: {"info": {"name": name, "license_expression": expr}},
              registry=frozenset({"pyside6", "shiboken-gui"}))  # fmt: skip
    rep = reuse.check(make(tmp_path, Net(w)), ["pyside6", "shiboken-gui"], "pypi")
    for c in rep["candidates"]:  # the ecosyste.ms record and the registry fallback both read the expression
        assert c.license == expr and c.gates["G5"].startswith("flag (") and "weak copyleft" in c.gates["G5"]


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
    assert rep["score"].startswith(f"PASS before HOLD, then S = R + {reuse.LAMBDA} * Q")


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
    monkeypatch.setenv("GITHUB_TOKEN", "t")
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
    # R4 of #169 (chosen.json): PASS before HOLD, union shortlist with an R floor of 0.20, lambda 1.0, no tier
    assert (reuse.PASS_BEFORE_HOLD, reuse.R_FLOOR, reuse.LAMBDA, reuse.DEFAULT_N) == (True, 0.20, 1.0, 10)
    assert reuse.RATES["github"][0] * 60 <= 10  # below GitHub's keyless search limit


# ---- review fixes (PR #171) ----


def test_g2_holds_when_no_malware_source_answered(tmp_path):
    silent = {k: v for k, v in TENACITY.items() if k != "advisories"}  # a record without its advisories
    w = world(lookups={"github.com/jd/tenacity": [silent]}, advisories=("MAL-2026-1",), down=("api.deps.dev",))
    rep = reuse.search(make(tmp_path, Net(w)), ["retry"], "pypi")
    c = rep["candidates"][0]
    assert c.gates["G2"].startswith("HOLD (malware not checked") and c.status == "HOLD"
    assert gate(reuse.Candidate(name="o/r", verified="github"))["G2"] == "n/a"  # repo-only
    assert gate(reuse.Candidate(name="p", ecosystem="pypi", verified="pypi", malware_checked=True))["G2"] == "pass"
    rep = reuse.search(
        make(tmp_path / "b", Net(world(lookups={"github.com/jd/tenacity": [silent]}))), ["retry"], "pypi"
    )
    assert rep["candidates"][0].gates["G2"] == "pass"  # deps.dev answered for the default version


def test_every_github_search_is_public_only(tmp_path):
    net = Net(world())
    reuse.search(make(tmp_path, net), ["retry flaky", "resilient calls"], "any")
    qs = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["q"][0] for u in net.urls(reuse.GITHUB)]
    assert qs and all(" is:public" in q for q in qs)


def test_the_whole_shortlist_is_scored_before_the_cut(tmp_path):
    items = [gh_repo("big/thing", "retry", 100_000, lang=""), gh_repo("jd/tenacity", "retry", 10)]
    rep = reuse.search(
        make(tmp_path, Net(world(github=lambda q: items))), ["retry"], "pypi", n=1, encoder=lambda t: [[1.0]] * len(t)
    )
    assert [c.name for c in rep["candidates"]] == ["tenacity"]  # fewer stars, higher S
    assert rep["scored"] == 2 and "2 scored, 1 shown" in reuse.render(rep)


def test_researcher_checks_every_recommended_tool_and_names_dev_reuse():
    text = (code.root() / "plugins/harness-ops/agents/researcher.md").read_text()
    assert "every other recommended tool through the API" in text and "uv run dev reuse" in text
    # #169: the agent's own names, verified, beat search alone; so verify first and search only for more options
    for rel in ("agents/researcher.md", "agents/builder.md", "skills/research/SKILL.md"):
        assert "uv run dev reuse check" in (code.root() / "plugins/harness-ops" / rel).read_text()
    harness = (code.root() / "template/.devkit/HARNESS.md.jinja").read_text()
    assert "uv run dev reuse check" in harness and "only when you know no good candidate" in harness


def test_documentation_falls_back_to_readme_size(tmp_path):
    f = reuse.factors(reuse.Candidate(name="r", readme_bytes=reuse.T_README), NOW)
    assert f["documentation"] == pytest.approx(1.0)
    assert 0 < reuse.factors(reuse.Candidate(name="r", readme_bytes=500), NOW)["documentation"] < 1
    items = [gh_repo("o/tool", "retry", 10, lang="")]
    rep = reuse.search(make(tmp_path, Net(world(github=lambda q: items, readme=4000))), ["retry"], "pypi")
    assert rep["candidates"][0].readme_bytes == 4000


@pytest.mark.parametrize(
    ("eco", "body", "why"),
    [
        ("pypi", {"releases": {"1.0": [{"yanked": True}], "1.1": [{"yanked": True}, {"yanked": True}]}}, "yanked"),
        ("cargo", {"versions": [{"yanked": True}]}, "yanked"),
        ("npm", {"deprecated": "use x instead"}, "deprecated"),
    ],
)
def test_g4_fails_a_wholly_yanked_or_deprecated_package(tmp_path, eco, body, why):
    rec = record("tenacity", None, "retry", eco=eco)
    w = world(github=lambda q: [], keywords={"retry": [rec]}, registry_body=lambda name: body)
    rep = reuse.search(make(tmp_path, Net(w)), ["retry"], "any")
    assert rep["candidates"] == [] and any("G4 FAIL" in n and why in n for n in rep["notes"])


def test_partly_yanked_or_unreadable_registry_answer_passes(tmp_path):
    c = reuse.Candidate(name="p", ecosystem="pypi")
    reuse.registry_state(c, json.dumps({"releases": {"1.0": [{"yanked": True}], "1.1": [{"yanked": False}]}}))
    assert not c.yanked
    reuse.registry_state(c, '{"releases": {"1.0": [{"yan')  # cut at the read cap
    reuse.registry_state(c, "[]")
    assert not c.yanked and not c.deprecated


def test_from_refuses_paths_outside_the_repository(fake_cli, tmp_path):
    outside = tmp_path / "secret.py"
    outside.write_text("def leak():\n    '''private words'''\n")
    with pytest.raises(LookupError, match="outside the repository"):
        code.source(str(outside))
    with pytest.raises(LookupError, match="outside the repository"):
        code.source("../" + outside.name)
    assert dev(["reuse", "--from", f"{outside}:leak"]) == 2


def test_g1_existence_is_asked_again_after_a_day(tmp_path):
    net = Net(world())
    c = make(tmp_path, net)
    reuse.search(c, ["retry"], "pypi")
    c.clock.t += reuse.REGISTRY_MAX_AGE + 1
    before = len(net.calls)
    reuse.search(c, ["retry"], "pypi")
    again = [u for u, _ in net.calls[before:]]
    assert again and all(u.startswith("https://pypi.org/") for u in again)  # deps.dev etc. still cached


# ---- search order R4 (#169) and check mode ----

VEC = {"alpha": [1.0, 0.0], "beta": [0.9, 0.4359], "gamma": [0.5, 0.866]}  # cosine to the query: 1.0, 0.9, 0.5


def encoder(texts):
    return [next((v for k, v in VEC.items() if k in t), [1.0, 0.0]) for t in texts]


def three(**dependents):
    """GitHub finds alpha, beta and gamma (relevance 1.0, 0.9, 0.5); each has a package with the given dependents."""
    items = [gh_repo(f"o/{k}", f"{k} retry", 10) for k in VEC]
    looks = {f"github.com/o/{k}": [record(k, f"o/{k}", f"{k} retry", dependent_repos_count=d, dependent_packages_count=0)]
             for k, d in dependents.items()}  # fmt: skip
    return world(github=lambda q: items, lookups=looks, registry=frozenset(VEC))


def test_union_shortlist_adds_the_most_adopted_within_the_r_floor(tmp_path):
    net = Net(three(alpha=1, beta=50_000, gamma=10**7))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi", n=1, encoder=encoder)
    assert rep["scored"] == 2  # alpha by R, beta by adoption; gamma is outside best R - R_FLOOR
    assert not [u for u in net.urls(reuse.ECOSYSTEMS) if "o%2Fgamma" in u or "o/gamma" in u]  # never enriched


def test_every_pass_is_ordered_before_every_hold(tmp_path):
    w = three(alpha=1, beta=1)
    dormant = record("alpha", "o/alpha", "alpha retry", last=reuse.DORMANT_DAYS + 30)
    dormant["repo_metadata"]["pushed_at"] = iso(reuse.DORMANT_DAYS + 30)
    net = Net(lambda u: [dormant] if "o%2Falpha" in u and "lookup" in u else w(u))
    rep = reuse.search(make(tmp_path, net), ["retry"], "pypi", n=3, encoder=encoder)
    names = [(c.name, c.status) for c in rep["candidates"]]
    assert names[0][1] == "pass" and names[-1] == ("alpha", "HOLD")
    assert next(c for c in rep["candidates"] if c.name == "alpha").score > rep["candidates"][0].score


def test_check_verifies_named_packages_and_reports_the_missing_one(tmp_path):
    rep = reuse.check(make(tmp_path, Net(world())), ["tenacity", "tenacity-turbo", "tenacity"], "pypi")
    ok, ghost = rep["candidates"]  # duplicates once, in the order given
    assert ok.status == "pass" and ok.repo == "github.com/jd/tenacity" and ok.dependents == 5300
    assert ok.gates["G1"] == "pass (pypi)" and set(ok.gates) == {"G1", "G2", "G3", "G4", "G5", "G6"}
    assert ghost.status == "NOT FOUND" and ghost.gates["G1"].startswith("FAIL (no pypi package")
    out = reuse.render(rep)
    assert "1. tenacity [pypi] github.com/jd/tenacity  PASS Q " in out and "98 contributors" in out
    assert "gates: G1 pass (pypi), G2 pass, G3 pass, G4 pass, G5 pass (MIT: Blue Oak Silver), G6 pass" in out
    assert "2. tenacity-turbo [pypi]  NOT FOUND" in out


def test_check_falls_back_to_the_registry_and_holds_or_fails_by_gate(tmp_path):
    pypi = {
        "info": {
            "name": "Retrying",
            "summary": "retry",
            "project_urls": {"Source": "https://github.com/rholder/retrying"},
        }
    }
    w = world(named={"backoff": record("backoff", "litl/backoff", archived=True) | {"status": "deprecated"}},
              registry_body=lambda name: pypi, lookups={}, registry=frozenset({"retrying", "Retrying", "backoff"}))  # fmt: skip
    rep = reuse.check(make(tmp_path, Net(w)), ["retrying", "backoff"], "pypi")
    retrying, backoff = rep["candidates"]
    assert (
        retrying.name == "Retrying"
        and retrying.repo == "github.com/rholder/retrying"
        and retrying.gates["G1"] == "pass (pypi)"
    )
    assert backoff.status == "FAIL" and backoff.gates["G4"].startswith("FAIL (deprecated")


def test_check_trusts_the_registrys_404_over_a_stale_ecosystems_record(tmp_path):
    w = world(named={"renpy": record("renpy", None)})  # ecosyste.ms knows it; PyPI answers 404
    rep = reuse.check(make(tmp_path, Net(w)), ["renpy"], "pypi")
    assert rep["candidates"][0].status == "NOT FOUND"
    assert not any("dropped" in n for n in rep["notes"])


def test_check_says_unverified_not_missing_when_the_registry_is_down(tmp_path):
    rep = reuse.check(make(tmp_path, Net(world(down=("pypi.org",)))), ["tenacity", "no-such-pkg"], "pypi")
    assert [c.status for c in rep["candidates"]] == ["UNVERIFIED", "UNVERIFIED"]
    assert "gave no answer" in rep["candidates"][1].gates["G1"]
    assert "NOT FOUND" not in reuse.render(rep)


def test_check_never_says_dropped(tmp_path):
    def body(name):
        return {"releases": {"1.0": [{"yanked": True}]}}  # G1 passes, G4 fails: shown, not dropped

    rep = reuse.check(make(tmp_path, Net(world(registry_body=body))), ["tenacity"], "pypi")
    assert rep["candidates"][0].status == "FAIL" and not any("dropped" in n for n in rep["notes"])
    assert "dropped" not in reuse.render(rep)


def test_g1_runs_before_the_shortlist_so_a_missing_name_takes_no_slot(tmp_path):
    ghost = record("alpha", None, "alpha retry", dependent_repos_count=10**7)  # best R and most adopted, not on PyPI
    beta = record("beta", "o/beta", "beta retry", dependent_repos_count=1, dependent_packages_count=0)
    w = world(github=lambda q: [gh_repo("o/beta", "beta retry", 10)], keywords={"retry": [ghost]},
              lookups={"github.com/o/beta": [beta]}, registry=frozenset({"beta"}))  # fmt: skip
    rep = reuse.search(make(tmp_path, Net(w)), ["retry"], "pypi", n=1, encoder=encoder)
    assert [c.name for c in rep["candidates"]] == ["beta"]


def test_enrichment_is_capped_at_enrich_cap_times_n(tmp_path):
    items = [gh_repo(f"o/alpha{i}", "alpha retry", 10) for i in range(20)]  # all at R 1.0, inside the floor
    net = Net(world(github=lambda q: items, lookups={}, registry=frozenset()))
    reuse.search(make(tmp_path, net), ["retry"], "pypi", n=2, encoder=encoder)
    looked = [u for u in net.urls(reuse.ECOSYSTEMS) if "/packages/lookup" in u]
    assert len(looked) == reuse.ENRICH_CAP * 2


def test_cli_check_json_exit_codes_and_errors(fake_cli, capsys):
    assert dev(["reuse", "check", "tenacity"]) == 0
    assert "tenacity [pypi] github.com/jd/tenacity  PASS" in capsys.readouterr().out
    assert dev(["reuse", "check", "tenacity", "nope-not-a-package", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [c["status"] for c in data["candidates"]] == ["pass", "NOT FOUND"] and data["mode"] == "check"
    assert "record" not in data["candidates"][0]
    assert dev(["reuse", "check"]) == 2
    assert dev(["reuse", "check", "tenacity", "--ecosystem", "any"]) == 2
    assert dev(["reuse", "retry", "with", "backoff"]) == 2  # an unquoted need is refused, not half-searched
    with pytest.raises(ValueError, match="for check"):
        reuse.check(reuse.client(), ["x"], "any")


@pytest.mark.parametrize(
    ("eco", "body"),
    [
        ("npm", {"repository": {"url": "git+https://github.com/O/Pkg.git"}, "description": "d"}),
        ("cargo", {"crate": {"repository": "https://github.com/o/pkg", "description": "d"}}),
        ("pypi", {"info": {"home_page": "https://example.org", "summary": "d"}}),
    ],
)
def test_from_registry_reads_each_registrys_repository_field(eco, body):
    c = reuse.from_registry("pkg", eco, body)
    assert (c.name, c.ecosystem, c.description) == ("pkg", eco, "d")
    assert c.repo == ("" if eco == "pypi" else "github.com/o/pkg") and c.record is body


def test_cli_help_shows_the_check_form_and_its_options(capsys):  # #179
    with pytest.raises(SystemExit):
        dev(["reuse", "--help"])
    lines = capsys.readouterr().out.splitlines()
    check = next(line for line in lines if "reuse check NAME" in line)
    assert all(o in check for o in ("--ecosystem {pypi,npm,cargo}", "--json", "--allow-copyleft"))
    assert any('reuse "<need>"' in line and "--from" in line for line in lines)


MIXED_TIERS = {"MIT": "Silver", "Apache-2.0": "Silver", "GPL-3.0-only": "strong"}


@pytest.mark.parametrize(
    ("rec", "outcome"),
    [  # #182: a listed (undeclared) licence set mixing strong copyleft with others is flagged, never passed
        ({"licenses": None, "normalized_licenses": ["MIT", "GPL-3.0-only"]}, "flag"),
        ({"licenses": "MIT License", "normalized_licenses": ["GPL-3.0-only", "MIT"]}, "flag"),
        ({"licenses": "MIT OR GPL-3.0-only", "normalized_licenses": ["MIT", "GPL-3.0-only"]}, "pass"),  # declared
        ({"licenses": None, "normalized_licenses": ["MIT", "Apache-2.0"]}, "pass"),  # no strong copyleft
        ({"licenses": None, "normalized_licenses": ["MIT"]}, "pass"),
    ],
)  # fmt: skip
def test_g5_flags_an_undeclared_licence_list_mixing_strong_copyleft(rec, outcome):
    c = reuse.from_record({"name": "x", "ecosystem": "pypi", **rec})
    g5 = reuse.gates(c, NOW, MIXED_TIERS, False)["G5"]
    assert g5.startswith(f"{outcome} (")
    assert ("licence combination undeclared" in g5) == (outcome == "flag")


def test_undeclared_licence_survives_a_merge_and_yields_to_the_registrys_expression(tmp_path):  # #182
    into = {"github.com/o/duo": reuse.Candidate(name="o/duo", repo="github.com/o/duo", license="MIT")}
    reuse.merge(into, reuse.from_record(record("duo", "o/duo", lic=("MIT", "GPL-3.0-only"))))
    assert "licence combination undeclared" in reuse.gates(into["github.com/o/duo"], NOW, MIXED_TIERS, False)["G5"]
    w = world(named={"duo": record("duo", "o/duo", lic=("MIT", "GPL-3.0-only"))},
              registry_body=lambda name: {"info": {"name": name, "license_expression": "MIT OR GPL-3.0-only"}},
              registry=frozenset({"duo"}))  # fmt: skip
    (c,) = reuse.check(make(tmp_path, Net(w)), ["duo"], "pypi")["candidates"]
    assert c.license == "MIT OR GPL-3.0-only" and "undeclared" not in c.gates["G5"]
