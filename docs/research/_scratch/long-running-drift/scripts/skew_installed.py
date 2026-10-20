"""For each installed record of a plugin, compare the commit it was installed from with the release tag its version names.

Usage: python skew_installed.py <installed_plugins.json> <plugin-name> <git repo of the marketplace>

Prints version, install commit, the commit of tag v<version>, and the release tags that contain the install commit.
A record whose commit differs from its tag's commit is a build the version string does not identify.
"""

import json
import subprocess
import sys


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False).stdout.strip()


records_path, plugin, repo = sys.argv[1:4]
with open(records_path) as fh:
    data = json.load(fh)
for key, entries in data.get("plugins", {}).items():
    if not key.startswith(plugin + "@"):
        continue
    for e in entries:
        version, sha = e.get("version", "?"), e.get("gitCommitSha") or ""
        tag_sha = git(repo, "rev-parse", f"v{version}^{{commit}}")
        contained_in = git(repo, "tag", "--contains", sha).split() if sha else []
        same = "same" if sha and tag_sha == sha else "DIFFERENT"
        print(
            f"{key} scope={e.get('scope')} version={version} install={sha[:7]} tag=v{version}:{tag_sha[:7]} "
            f"({same}); tags containing install: {contained_in or 'none'}; updated {e.get('lastUpdated')}"
        )
