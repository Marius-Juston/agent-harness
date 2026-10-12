"""agent-stats token accounting against a transcript fixture in Claude Code's on-disk shape.

Claude Code writes one JSONL line per content block, each repeating the message's usage. Older builds wrote the
usage seen at stream start (stop_reason null, output_tokens a handful) on every line but the last; newer builds
repeat the final usage on every line. Either way a message counts once, with its final usage.
"""

from pathlib import Path

from devkit import stats

FIXTURE = Path(__file__).parent / "fixtures/transcript_usage.jsonl"


def test_output_tokens_match_final_usage_once_per_message():
    run = stats.scan(FIXTURE)
    # msg_a final 781 (stream-start 6 ignored), msg_b only stream-start 8, msg_c final 698 logged three times
    assert run["output"] == 781 + 8 + 698
    assert run["messages"] == 3
    assert run["partial"] == 1  # msg_b never logged a final usage: its 8 is a lower bound


def test_input_tokens_counted_once_per_message():
    run = stats.scan(FIXTURE)
    assert run["fresh"] == (3 + 500) + (2 + 0) + (1 + 200)
    assert run["cached"] == 10000 + 12000 + 15000
    assert run["calls"] == 3
    assert run["result_chars"] == 12


def test_report_names_the_usage_field(monkeypatch):
    monkeypatch.setattr(stats, "collect", lambda: {"builder": [stats.scan(FIXTURE)]})
    text = stats.report()
    assert "usage.output_tokens" in text
    assert "1,487" in text
    assert "33%" in text  # share of messages with only a stream-start usage
