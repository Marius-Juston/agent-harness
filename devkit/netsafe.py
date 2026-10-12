"""Guards for what devkit fetches from the network (ruff S310, S314; docs/research/free-security-scanning.md)."""

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SCHEMES = {"http", "https"}
DTD = re.compile(r"<!(DOCTYPE|ENTITY)", re.I)


def bad_scheme(url: str) -> str | None:
    """Why `url` must not be opened (urllib also opens file: and data: URLs), or None when it is http(s)."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    return None if scheme in SCHEMES else f"refused URL scheme {scheme or '(none)'!r}: only http and https are fetched"


class HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects to http(s) only (urllib's default also follows ftp:)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if why := bad_scheme(newurl):
            raise urllib.error.URLError(f"redirect {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(HttpOnlyRedirects)


def parse_xml(body: str) -> ET.Element:
    """Parse XML from the network. A document with a DTD is refused: entity expansion attacks (billion laughs,
    quadratic blowup) need one, the Atom feeds devkit reads never carry one, and older Expat builds are exposed."""
    if DTD.search(body):
        raise ET.ParseError("XML with a DTD or entity declaration refused")
    return ET.fromstring(body)  # noqa: S314 - no DTD, so no entity expansion
