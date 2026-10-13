"""R2b check 8b: the upstream-docs sweep, run once today: every code.claude.com/docs page cited in either repository
(with or without https://, in code comments or prose; research scratch excluded) is fetched; the page must load and
each cited #fragment must exist as an id on it. Also prints whether the cited page is cited from code (.py/.sh/.json).
usage: python3 r2_doc_anchors.py
"""

import collections
import re
import subprocess
import time
import urllib.request

import r2_common as c

PAT = re.compile(r"(?:https?://)?code\.claude\.com/docs/en/[\w/.-]*[\w/](?:#[\w-]+)?")
cites = collections.defaultdict(set)
for name, (repo, ref) in c.REPOS.items():
    out = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "grep",
            "-n",
            "-F",
            "code.claude.com/docs/en/",
            ref,
            "--",
            ".",
            ":!**/_scratch/**",
            ":!docs/generated/**",
        ],
        capture_output=True,
        text=True,
    ).stdout
    for ln in out.splitlines():
        _, f, _, text = ln.split(":", 3)
        for u in PAT.findall(text):
            u = u.removeprefix("https://").removeprefix("http://")
            cites[u].add((name, f))
pages = collections.defaultdict(set)
for u in cites:
    page, _, frag = u.partition("#")
    pages[page.rstrip("/.")].add(frag)
print(f"{len(cites)} distinct cited URLs on {len(pages)} pages")
bad = 0
for page, frags in sorted(pages.items()):
    try:
        with urllib.request.urlopen(
            urllib.request.Request("https://" + page, headers={"User-Agent": "Mozilla/5.0"}), timeout=30
        ) as r:
            html = r.read().decode("utf-8", "replace")
            st = r.status
    except urllib.error.HTTPError as e:
        html, st = "", e.code
    except Exception as e:
        html, st = "", type(e).__name__
    ids = set(re.findall(r'id="([^"]+)"', html))
    missing = sorted(f for f in frags if f and f not in ids)
    from_code = any(
        f.endswith((".py", ".sh", ".json", ".toml")) for u2, w in cites.items() if u2.startswith(page) for _, f in w
    )
    flag = "OK" if st == 200 and not missing else "FLAG"
    bad += flag == "FLAG"
    print(f"  {flag} {st} {page} frags={len([f for f in frags if f])} missing={missing} code-cited={from_code}")
    time.sleep(0.5)
print(f"pages flagged: {bad} of {len(pages)}")
