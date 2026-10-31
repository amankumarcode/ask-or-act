import pytest

from askoract.registry import Registry


def test_register_and_get():
    reg: Registry[type] = Registry("widget")

    @reg.register("a")
    class A: ...

    assert reg.get("a") is A
    assert "a" in reg
    assert reg.names() == ["a"]


def test_duplicate_name_rejected():
    reg: Registry[int] = Registry("number")
    reg.add("one", 1)
    with pytest.raises(ValueError, match="already registered"):
        reg.add("one", 2)


def test_unknown_name_lists_available():
    reg: Registry[int] = Registry("number")
    reg.add("one", 1)
    with pytest.raises(KeyError, match="unknown number 'two'; available: one"):
        reg.get("two")
