"""
事件系统：桥接同步 Orchestrator 与异步 API 消费者。

架构：
    同步工作线程 → emit() → loop.call_soon_threadsafe() → asyncio.Queue → WebSocket

设计决策：
    - EventEmitter 使用 Protocol（非 ABC），Orchestrator 零耦合
    - Event 使用 frozen dataclass，不可变，跨线程传递无需拷贝
    - AsyncEventBridge 使用 fan-out 模式，支持多个 WebSocket 订阅同一 run
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Pipeline 执行过程中的所有事件类型。"""

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    CASE_STARTED = "case.started"
    CASE_COMPLETED = "case.completed"
    CASE_FAILED = "case.failed"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"


@dataclass(frozen=True)
class Event:
    """不可变事件载体，安全跨线程传递。"""

    type: EventType
    run_id: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "data": self.data,
        }


@runtime_checkable
class EventEmitter(Protocol):
    """事件发射协议 — Orchestrator 唯一可见的契约。

    使用 Protocol 而非 ABC，Orchestrator 无需 import server 包。
    任何实现了 emit(Event) -> None 的对象都满足此协议。
    """

    def emit(self, event: Event) -> None: ...


class AsyncEventBridge:
    """线程安全桥接：同步 emit() → asyncio.Queue → 异步消费者。

    工作线程（ThreadPoolExecutor）调用 emit()，
    通过 loop.call_soon_threadsafe() 安全调度到事件循环线程，
    fan-out 分发到所有订阅者的 asyncio.Queue。
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._subscribers: list[asyncio.Queue[Event]] = []

    def emit(self, event: Event) -> None:
        """从任意线程安全调用。"""
        try:
            self._loop.call_soon_threadsafe(self._dispatch, event)
        except RuntimeError:
            # 事件循环已关闭，静默忽略
            logger.debug("Event loop closed, dropping event: %s", event.type.value)

    def _dispatch(self, event: Event) -> None:
        """在事件循环线程执行，向所有订阅者广播。"""
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full, dropping event: %s", event.type.value)

    def subscribe(self) -> asyncio.Queue[Event]:
        """创建并注册新的订阅者队列。"""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """移除订阅者队列。"""
        with contextlib.suppress(ValueError):
            self._subscribers.remove(queue)
