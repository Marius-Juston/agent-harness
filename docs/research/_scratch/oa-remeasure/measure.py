"""#622: how many SOURCED paper claims in docs/research reach a full text through `dev lit`'s full-text path, by
publisher, with and without the open-access locator keys (UNPAYWALL_EMAIL, CORE_API_KEY).

Run from the repository root, at background priority (every request goes through dev lit's shared, machine-wide
rate buckets and backoff, so it stays within each source's limit):

    nice -n 19 uv run python docs/research/_scratch/oa-remeasure/measure.py [--cache DIR] [--out FILE]

Two arms over one fresh cache directory (default .scratch/oa-remeasure, never committed), so nothing a run before
this one cached (a 'none' found without a key) decides the answer:
- `keyless`: the client's environment without UNPAYWALL_EMAIL and CORE_API_KEY (#623's measurement setting);
- `keyed`: the environment as `dev lit` sees it (keys from the environment or the secrets file, which only
  devkit reads; this script never prints or stores a key).
Publisher: Crossref's `publisher` for the DOI; an arXiv id is "arXiv". Prints a markdown table and writes the per
paper results (ident, publisher, claims, abstract-only claims, each arm's text URL or why none) as JSON.
"""

import argparse
import collections
import json
import re
import sys
import urllib.parse
from pathlib import Path

from devkit import citecheck, lit, oa, paths

REPO = Path(__file__).resolve().parents[4]
LOCATOR_KEYS = (oa.CONTACT_ENV, oa.CORE_KEY_ENV)


def note_claims() -> list[citecheck.Claim]:
    out = []
    for p in sorted((REPO / "docs" / "research").glob("*.md")):
        if p.name != "INDEX.md":
            out += citecheck.claims(p, p.read_text())
    return out


def publisher(c: lit.Client, ident: str) -> str:
    if lit.ARXIV_ID.match(ident) or ident.lower().startswith("10.48550/arxiv."):
        return "arXiv"
    try:
        body = c.fetch("crossref", f"https://api.crossref.org/works/{urllib.parse.quote(ident, safe='/')}")
        return json.loads(body)["message"].get("publisher") or "unknown"
    except (lit.SourceError, ValueError, KeyError):
        return "unknown"


def via(url: str) -> str:
    """Where a text came from, by its URL's host (the locator that named it is not recorded by dev lit)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    for name, hosts in (("arXiv", ("arxiv.org",)), ("Europe PMC", ("ebi.ac.uk", "europepmc.org")),
                        ("CORE", ("core.ac.uk",))):  # fmt: skip
        if any(host == h or host.endswith("." + h) for h in hosts):
            return name
    return host or "?"


def read(c: lit.Client, ident: str) -> dict:
    for _ in range(2):  # a transient answer is retried once; still transient is recorded as such
        try:
            ft, why = c.full_text(ident)
        except lit.SourceError as exc:
            why, ft = f"transient: {exc}", None
            continue
        return {"url": ft.url, "via": via(ft.url), "words": ft.words()} if ft else {"none": why}
    return {"none": why}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(REPO / ".scratch" / "oa-remeasure"))
    ap.add_argument("--out", default=str(REPO / ".scratch" / "oa-remeasure.json"))
    ap.add_argument("--table", help="only print the table of an earlier run's --out file")
    a = ap.parse_args()
    if a.table:
        rows = json.loads(Path(a.table).read_text())
        print(table(rows, sum(r["claims"] for r in rows)))
        return 0
    base = lit.client()  # the environment dev lit sees: keys from the env or the secrets file, the contact email
    missing = [k for k in LOCATOR_KEYS if not base.env.get(k)]
    if missing:
        print(f"warning: {', '.join(missing)} not set: the keyed arm is not keyed", file=sys.stderr)
    rate_dir = paths.user_state_dir("lit")
    keyed = lit.Client(Path(a.cache), env=base.env, rate_dir=rate_dir)
    keyless = lit.Client(
        Path(a.cache), env={k: v for k, v in base.env.items() if k not in LOCATOR_KEYS}, rate_dir=rate_dir
    )

    claims = note_claims()
    by_ident: dict[str, list[citecheck.Claim]] = collections.defaultdict(list)
    for cl in claims:
        by_ident[cl.ident].append(cl)
    rows = []
    for i, (ident, cls) in enumerate(sorted(by_ident.items()), 1):
        row = {"ident": ident, "publisher": publisher(keyed, ident), "claims": len(cls),
               "abstract_only": sum(cl.abstract() for cl in cls),
               "keyless": read(keyless, ident), "keyed": read(keyed, ident)}  # fmt: skip
        rows.append(row)
        status = "/".join("text" if "url" in row[arm] else "none" for arm in ("keyless", "keyed"))
        print(f"[{i}/{len(by_ident)}] {ident} {row['publisher']}: {status}", file=sys.stderr, flush=True)
    Path(a.out).write_text(json.dumps(rows, indent=1))
    print(table(rows, len(claims)))
    return 0


def cell(t: collections.Counter, arm: str) -> str:
    return f"{t[arm + '_none']} ({t[arm + '_none_papers']}) [{t[arm + '_none_abstract_only']}]"


def table(rows: list[dict], n_claims: int) -> str:
    agg: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        # Crossref spells one publisher two ways: "Association for Computing Machinery (ACM)" and "ACM"
        t = agg[m.group(1) if (m := re.search(r"\(([^()]+)\)$", r["publisher"])) else r["publisher"]]
        t["claims"] += r["claims"]
        t["abstract_only"] += r["abstract_only"]
        for arm in ("keyless", "keyed"):
            if "url" not in r[arm]:
                t[f"{arm}_none"] += r["claims"]
                t[f"{arm}_none_papers"] += 1
                t[f"{arm}_none_abstract_only"] += r["abstract_only"]
                t[f"{arm}_transient"] += r[arm]["none"].startswith("transient")
        if "url" in r["keyed"] and "url" not in r["keyless"]:
            t["reached"] += r["claims"]
    lines = [
        f"{n_claims} claims on {len(rows)} papers. Cells: claims (papers) without a full text [abstract-only claims]",
        "",
        "| publisher (Crossref) | claims [abstract-only] | without, locator keys unset | without, keys set "
        "| claims reached by the keys |",
        "|---|---|---|---|---|",
    ]
    order = sorted(agg, key=lambda p: (-agg[p]["keyless_none"], -agg[p]["claims"], p))
    total: collections.Counter = collections.Counter()
    for p in order:
        t = agg[p]
        total.update(t)
        lines.append(
            f"| {p} | {t['claims']} [{t['abstract_only']}] | {cell(t, 'keyless')} | {cell(t, 'keyed')} "
            f"| {t['reached']} |"
        )
    lines.append(
        f"| **total** | {total['claims']} [{total['abstract_only']}] | {cell(total, 'keyless')} "
        f"| {cell(total, 'keyed')} | {total['reached']} |"
    )
    transient = total["keyless_transient"] + total["keyed_transient"]
    lines.append(f"\ntransient (a locator or host did not answer twice): {transient} paper-arms")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
