"""组件间内存数据传递。"""
from __future__ import annotations

from typing import Any


class ContextStore:
    """轻量级键值存储，同阶段组件间共享。

    同一 case 的所有 casewise step 共享同一个实例。
    同一 pipeline 的所有 preprocess/postprocess step 共享同一个实例。
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def has(self, key: str) -> bool:
        return key in self._data
