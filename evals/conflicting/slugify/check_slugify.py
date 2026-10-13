# ruff: noqa: S101
from slugify import slugify


def test_slugify_1():
    assert slugify("a b") == "a-b"


def test_slugify_2():
    assert slugify("Two  Words") == "two-words"


def test_slugify_3():
    assert slugify("Hello World Zq") == "Hello_World_Zq"
