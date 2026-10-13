# Shared header of every fit-check prompt (#196, fixed before any arm run)
The text between the two `---` lines starts every prompt, with `<PACKAGE>` and `<NEED>` filled in.

---
A developer has chosen the Python package `<PACKAGE>` (from PyPI) for this need:

<NEED>

Environment: the newest release of `<PACKAGE>`, installed with `uv add <PACKAGE>` (no extras; its declared dependencies come with it, nothing else is added), on CPython 3.13, Linux x86_64, with no other Python packages and no extra system libraries.
---
