# ruff: noqa: S101
from chunk import chunk


def test_chunk_1():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_chunk_2():
    assert chunk([], 3) == []


def test_chunk_3():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4]]
