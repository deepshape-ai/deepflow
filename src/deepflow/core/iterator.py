from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TypeVar

T = TypeVar("T")


class BaseIterator(ABC):
    """迭代器基类，preprocess 组件可返回此实例供 casewise 阶段遍历"""

    @abstractmethod
    def __iter__(self) -> Iterator[T]: ...


class MemoryIterator(BaseIterator):
    """简单的列表迭代器实现

    最常用的迭代器实现，将数据集列表包装为迭代器。

    Example:
        >>> from deepflow import MemoryIterator, DatasetItem
        >>> items = [DatasetItem(id="1"), DatasetItem(id="2")]
        >>> iterator = MemoryIterator(items)
        >>> for item in iterator:
        ...     print(item.id)
    """

    def __init__(self, items: list[T]) -> None:
        self.items = items

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)
