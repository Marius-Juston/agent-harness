# ruff: noqa: S101
from wordcount import word_count


def test_word_count_1():
    assert word_count("") == 0


def test_word_count_2():
    assert word_count("one two") == 2


def test_word_count_3():
    assert word_count("don't stop-me now") == 5
