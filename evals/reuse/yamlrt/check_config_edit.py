# ruff: noqa: S101
from config_edit import set_key

DOC = """\
# service settings
server:
  host: "example.org"  # public name
  port: 8080

# keep last
debug: false
"""


def test_replaces_value_and_keeps_comments():
    assert set_key(DOC, "server.port", 9090) == DOC.replace("8080", "9090")


def test_adds_key_and_keeps_rest():
    out = set_key(DOC, "server.tls", True)
    assert "# service settings" in out
    assert "# public name" in out
    assert '"example.org"' in out
    assert "tls: true" in out
