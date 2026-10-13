"""Does raising [tests] min_tests need an owner-approved [[raise]]? (it should not: a floor raise tightens)

(#149) Run from the repository root: `uv run python docs/research/_scratch/fix-now-vs-defer/scripts/floor_raise_probe.py`.
"""
from devkit.checks import ratchet_raises
old = {"raise": [], "defaults": {}, "words": {}, "tests": {"min_tests": 759}}
up = {"raise": [], "defaults": {}, "words": {}, "tests": {"min_tests": 874}}
down = {"raise": [], "defaults": {}, "words": {}, "tests": {"min_tests": 700}}
print("raise:", ratchet_raises(up, old, [], "owner"))
print("lower:", ratchet_raises(down, old, [], "owner"))
