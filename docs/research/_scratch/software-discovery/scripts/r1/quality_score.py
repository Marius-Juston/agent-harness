"""Prototype of the r1-ranking quality score Q and gates, on live keyless APIs.

Weights = share of 115 surveyed developers rating each factor "high influence"
(Larios Vargas et al. 2020, Table 2), normalised. Normalisation per signal is the
OpenSSF criticality_score form log(1+S)/log(1+max(S,T)). Thresholds T are ASSUMED.
Usage: python3 -I quality_score.py requests httpx nose
"""
import datetime as dt
import json
import math
import sys
import urllib.parse
import urllib.request

HIGH = {"maturity": 62, "documentation": 51, "maintenance": 44, "security": 39, "popularity": 30, "community": 30}
W = {k: v / sum(HIGH.values()) for k, v in HIGH.items()}
NOW = dt.datetime(2026, 10, 13, tzinfo=dt.UTC)


def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "r1-ranking-probe"})  # noqa: S310 (https constants only)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return json.load(r)
    except Exception as e:
        return {"_error": str(e)}


def pike(s, t):
    s = max(s, 0)
    return math.log1p(s) / math.log1p(max(s, t))


def score(name):
    pypi = get(f"https://pypi.org/pypi/{name}/json")
    if "_error" in pypi:
        return {"name": name, "gate": "FAIL: not on PyPI (hallucination guard)"}
    osv = get("https://api.osv.dev/v1/query", json.dumps({"package": {"name": name, "ecosystem": "PyPI"}}).encode())
    mal = [v["id"] for v in osv.get("vulns", []) if v["id"].startswith("MAL-")]
    uploads = [f["upload_time_iso_8601"] for files in pypi["releases"].values() for f in files]
    first = min(dt.datetime.fromisoformat(u.replace("Z", "+00:00")) for u in uploads)
    last = max(dt.datetime.fromisoformat(u.replace("Z", "+00:00")) for u in uploads)
    age_days, idle_days = (NOW - first).days, (NOW - last).days
    ver = pypi["info"]["version"]
    dd = get(f"https://api.deps.dev/v3/systems/pypi/packages/{name}/versions/{urllib.parse.quote(ver, safe='')}")
    lic = dd.get("licenses", [])
    repo = next((p["projectKey"]["id"] for p in dd.get("relatedProjects", []) if p.get("relationType") == "SOURCE_REPO"), None)
    proj = get(f"https://api.deps.dev/v3/projects/{urllib.parse.quote(repo, safe='')}") if repo else {}
    sc = proj.get("scorecard", {})
    checks = {c["name"]: c["score"] for c in sc.get("checks", [])}
    dl = get(f"https://pypistats.org/api/packages/{name.lower()}/recent").get("data", {}).get("last_month", 0)
    gates = []
    if mal:
        gates.append(f"FAIL: OSV malicious {mal}")
    if age_days < 90:
        gates.append("HOLD: first release < 90 days")
    if dd.get("isDeprecated") or proj.get("_archived"):
        gates.append("FAIL: deprecated")
    f = {
        "maturity": 0.5 * pike(age_days / 30, 60) + 0.5 * pike(len(pypi["releases"]), 50),
        "documentation": 1.0 if any("doc" in k.lower() for k in (pypi["info"].get("project_urls") or {})) else 0.5 * pike(len(pypi["info"].get("description") or ""), 10000),
        "maintenance": (checks["Maintained"] / 10) if checks.get("Maintained", -1) >= 0 else max(0.0, min(1.0, (730 - idle_days) / (730 - 180))),
        "security": (sc["overallScore"] / 10) if "overallScore" in sc else 0.5,
        "popularity": 0.5 * pike(dl, 1_000_000) + 0.5 * pike(proj.get("starsCount", 0), 10_000),
        "community": pike(proj.get("forksCount", 0), 1_000),  # proxy; contributor counts need GitHub
    }
    q = sum(W[k] * f[k] for k in W)
    return {"name": name, "license": lic, "repo": repo, "age_days": age_days, "idle_days": idle_days,
            "downloads_month": dl, "gates": gates or ["PASS"], "factors": {k: round(v, 2) for k, v in f.items()}, "Q": round(q, 3)}


if __name__ == "__main__":
    print("weights", {k: round(v, 3) for k, v in W.items()})
    for n in sys.argv[1:]:
        print(json.dumps(score(n)))
