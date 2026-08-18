"""ContextStore 单元测试。"""
import pytest
from deepflow.core.store import ContextStore


def test_set_and_get():
    store = ContextStore()
    store.set("key", 42)
    assert store.get("key") == 42


def test_get_default():
    store = ContextStore()
    assert store.get("missing") is None
    assert store.get("missing", "fallback") == "fallback"


def test_has():
    store = ContextStore()
    assert not store.has("key")
    store.set("key", "value")
    assert store.has("key")


def test_overwrite():
    store = ContextStore()
    store.set("key", 1)
    store.set("key", 2)
    assert store.get("key") == 2
