"""Count tracked .py files at HEAD that fail ast.parse under the running Python (#111 audit). Usage: python parsecount.py <repo>"""

import ast
import subprocess
import sys

repo = sys.argv[1]
files = subprocess.run(["git", "-C", repo, "ls-files", "*.py"], capture_output=True, text=True).stdout.split()
bad = []
for f in files:
    src = subprocess.run(["git", "-C", repo, "show", f"HEAD:{f}"], capture_output=True).stdout.decode(
        "utf-8", "replace"
    )
    try:
        ast.parse(src)
    except SyntaxError:
        bad.append(f)
print(sys.version.split()[0], len(files), "py files;", len(bad), "fail to parse")
