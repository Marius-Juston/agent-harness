"""The project's tests. This first one checks the harness is installed: replace it as real tests arrive."""

from devkit.cli import command_map


def test_the_harness_commands_are_installed():
    assert {"check", "test", "board", "pr", "integrate"} <= set(command_map())
