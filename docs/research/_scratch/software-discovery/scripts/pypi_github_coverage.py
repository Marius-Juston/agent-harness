"""Coverage between PyPI and GitHub. Usage: python -I cov.py <simple.json>"""
import json, random, re, sys, time, urllib.request, urllib.parse, collections, subprocess
UA = {"User-Agent": "agent-harness-probe (coverage study)"}
def get(url):
    for t in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(5 * (t + 1))
        except Exception:
            time.sleep(5 * (t + 1))
    return None
def host(u):
    m = re.match(r"https?://(?:www\.)?([^/]+)", (u or "").lower())
    return m.group(1) if m else "none"
random.seed(13)
names = [p["name"] for p in json.load(open(sys.argv[1]))["projects"]]
def pypi_side(sample, label):
    c = collections.Counter()
    for n in sample:
        d = get(f"https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/{urllib.parse.quote(n)}")
        c[host(d.get("repository_url")) if d else "lookup-failed"] += 1
        time.sleep(0.25)
    tot = sum(c.values())
    print(f"{label}: n={tot}", {k: f"{v} ({100*v/tot:.0f}%)" for k, v in c.most_common(6)})
pypi_side(random.sample(names, 150), "random PyPI projects -> repo host")
top = get("https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages?sort=downloads&order=desc&per_page=150") or []
pypi_side([p["name"] for p in top], "top-150 PyPI by downloads -> repo host")
# reverse: GitHub Python repos with >=100 stars, sampled across star bands: on PyPI?
for band in ["100..300", "300..1000", "1000..5000", ">5000"]:
    r = subprocess.run(["gh", "api", "-X", "GET", "search/repositories", "-f", f"q=language:python stars:{band} pushed:>2025-10-01", "-f", "per_page=50", "-f", "sort=updated"], capture_output=True, text=True)
    items = json.loads(r.stdout)["items"]
    on = 0
    for i in items:
        d = get("https://packages.ecosyste.ms/api/v1/packages/lookup?repository_url=" + urllib.parse.quote(i["html_url"]))
        on += bool([p for p in (d or []) if p.get("ecosystem") == "pypi"])
        time.sleep(0.25)
    print(f"GitHub Python repos stars {band}, pushed in last year: {on}/{len(items)} have a PyPI package")
