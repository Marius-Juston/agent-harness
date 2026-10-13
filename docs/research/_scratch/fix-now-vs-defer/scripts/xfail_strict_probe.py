"""Does the weaken check flag a tightening edit (xfail_strict = true added to pyproject.toml)?

(#149) Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/xfail_strict_probe.py`.
"""
from devkit import weaken
diff = """diff --git a/pyproject.toml b/pyproject.toml
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,2 +1,3 @@
 [tool.pytest.ini_options]
+xfail_strict = true
 testpaths = ["tests"]
"""
print(weaken.findings(diff))
