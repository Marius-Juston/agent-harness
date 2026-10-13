"""Guards for what devkit fetches from the network (ruff S310, S314; docs/research/free-security-scanning.md), and the
one HTTP GET and one atomic file write that citecheck and lit share (#139)."""

import contextlib
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

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


def http_get(
    url: str,
    *,
    ua: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    cap: int = 2_000_000,
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[int, dict[str, str], str]:
    """(status, headers, at most `cap` bytes of body) for a GET of an http(s) `url`. A refused scheme or a network
    failure is status 0 with the reason as the body; an HTTP error is its status with an empty body. A header or URL
    urllib cannot send raises ValueError, for the caller to word (its message may quote a key)."""
    if why := bad_scheme(url):
        return 0, {}, why
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*", **(headers or {})})  # noqa: S310
    try:
        with (opener or OPENER).open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(cap).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {}, str(e)


def atomic_write(path: Path, text: str) -> None:
    """Replace `path` with `text` atomically: a parallel reader sees the old file or the new one, never half of one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def parse_xml(body: str) -> ET.Element:
    """Parse XML from the network. A document with a DTD is refused: entity expansion attacks (billion laughs,
    quadratic blowup) need one, the Atom feeds devkit reads never carry one, and older Expat builds are exposed."""
    if DTD.search(body):
        raise ET.ParseError("XML with a DTD or entity declaration refused")
    return ET.fromstring(body)  # noqa: S314 - no DTD, so no entity expansion
