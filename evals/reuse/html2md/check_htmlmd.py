# ruff: noqa: S101
from htmlmd import to_markdown

PAGE = """<html><head><style>p{color:red}</style><script>alert(1)</script></head><body>
<h2>Fractions</h2><p>Read <b>carefully</b> and see <a href="https://example.org/f">the guide</a>.
<ul><li>halves<li>thirds</ul></body></html>"""


def test_structure():
    md = to_markdown(PAGE)
    assert "## Fractions" in md
    assert "**carefully**" in md
    assert "[the guide](https://example.org/f)" in md
    assert "- halves" in md
    assert "- thirds" in md


def test_drops_scripts_and_styles():
    md = to_markdown(PAGE)
    assert "alert" not in md
    assert "color" not in md
