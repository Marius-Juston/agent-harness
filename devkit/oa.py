"""Open-access locators for a DOI's full text (#9): where `dev lit read` and cite-check look once a paper's record
names no readable copy. Europe PMC by DOI for a PMC copy (PubMed's open full texts, as JATS XML), Unpaywall's
locations (only with a contact email) and CORE's repository copies, asked in that order and only while the ones
before gave no full text. Pure: lit.Client fetches through its shared buckets, backoff and cache.

Rates, one machine-wide bucket each. Unpaywall's REST API (api.unpaywall.org/v2/<doi>?email=<contact>) asks for an
email on every call and is "limited to 100,000 calls per day" (unpaywall.org/api/v2, as quoted by a web search
2026-11-19; the page renders by script), so 1/s, which cannot reach that in a day. CORE's v3 API (api.core.ac.uk/v3)
documents per-window limits, as quoted by the same search: one batch or five single search requests per 10 s
(core.ac.uk/documentation/api; the page answered 403 to the fetch tool), so one request per 10 s, the strictest of
them; a free key (CORE_API_KEY) goes in an Authorization header. Europe PMC keeps lit's own `europepmc` bucket.
"""

import json
import re
import urllib.parse
from collections.abc import Callable

from devkit import config, fulltext

LOCATOR_RATES = {"unpaywall": (1.0, 1.0), "core": (0.1, 1.0)}
CONTACT_ENV = "UNPAYWALL_EMAIL"  # else [tool.devkit] contact_email; never a default, so no address ships in the repo
CORE_KEY_ENV = "CORE_API_KEY"
PMC_PAGE = re.compile(
    r"(?:europepmc\.org/(?:abstract/|article/)?PMC/|ncbi\.nlm\.nih\.gov/pmc/articles/|pmc\.ncbi\.nlm\.nih\.gov/articles/)"
    r"(PMC\d+)",
    re.I,
)

Copy = tuple[str, str, str]  # (bucket, URL, text already in hand or '' to fetch)
Locator = tuple[str, str, Callable[[str], list[Copy]]]  # (bucket, URL to ask, its answer -> copies)


def unpaywall_url(doi: str) -> str:
    return f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}"


def core_url(doi: str) -> str:
    return f"https://api.core.ac.uk/v3/search/works?q={urllib.parse.quote(f'doi:"{doi}"', safe='')}&limit=1"


def parse_unpaywall(body: str) -> list[str]:
    """An Unpaywall record's open-access URLs, best first: the best location's PDF, then its page, then every other
    location's (oa_locations), deduped."""
    data = json.loads(body)
    locs = [data.get("best_oa_location") or {}, *(data.get("oa_locations") or [])]
    urls = [u for loc in locs if isinstance(loc, dict) for k in ("url_for_pdf", "url") if (u := loc.get(k))]
    return list(dict.fromkeys(u for u in urls if isinstance(u, str)))


def parse_core(body: str) -> list[tuple[str, str]]:
    """(URL, text) of each copy CORE names: the text it extracted from the PDF it holds (`fullText`, named by the
    download URL, else the CORE page), else ('<download URL>', '') to fetch. A `fullText` under fulltext.MIN_WORDS is
    not a paper: without a key CORE answers "Not available for public API users." there (seen 2026-11-19)."""
    out = []
    for w in json.loads(body).get("results") or []:
        text, url = w.get("fullText") or "", w.get("downloadUrl") or ""
        if not (isinstance(text, str) and isinstance(url, str)):
            continue
        if len(text.split()) >= fulltext.MIN_WORDS:
            out.append((url or f"https://core.ac.uk/works/{w.get('id')}", text))
        elif url:
            out.append((url, ""))
    return out


def text_source(url: str) -> tuple[str, str]:
    """(the bucket, the URL to fetch) for an open-access link: a PMC page becomes Europe PMC's JATS XML of it; arXiv
    keeps arXiv's bucket; any other host shares the `fulltext` one."""
    if m := PMC_PAGE.search(url):
        return "europepmc", f"https://www.ebi.ac.uk/europepmc/webservices/rest/{m.group(1).upper()}/fullTextXML"
    if urllib.parse.urlsplit(url).hostname in ("arxiv.org", "export.arxiv.org"):
        return "arxiv", url
    return "fulltext", url


def locators(
    doi: str, env: dict[str, str], europepmc_url: str, europepmc_links: Callable[[str], list[str]]
) -> list[Locator]:
    """What to ask for `doi`, in order. Europe PMC's search (lit's own URL and parser, so its answer is the one
    `get` cached) names a PMC copy; Unpaywall is asked only with a contact email in `env`."""
    out: list[Locator] = [
        ("europepmc", europepmc_url, lambda b: [(*text_source(u), "") for u in europepmc_links(b)]),
        ("core", core_url(doi), lambda b: [("fulltext", u, t) for u, t in parse_core(b)]),
    ]
    if env.get(CONTACT_ENV):
        out.insert(1, ("unpaywall", unpaywall_url(doi), lambda b: [(*text_source(u), "") for u in parse_unpaywall(b)]))
    return out


def auth(source: str, url: str, env: dict[str, str]) -> tuple[str, dict[str, str]] | None:
    """The url and headers for a locator's call (None: not a locator): the contact address and the key are added
    here, after the cache key is taken, so neither is stored."""
    if source == "unpaywall" and (k := env.get(CONTACT_ENV)):
        return url + ("&" if "?" in url else "?") + "email=" + urllib.parse.quote(k), {}
    if source == "core" and (k := env.get(CORE_KEY_ENV)):
        return url, {"authorization": "Bearer " + k}
    return None


def contact_email(settings: Callable[[], config.Config] = config.get) -> str:
    """[tool.devkit] contact_email, or '' when unset or the config does not parse (lit still works without it)."""
    try:
        return settings().contact_email
    except (config.ConfigError, OSError, RuntimeError, ValueError):
        return ""
