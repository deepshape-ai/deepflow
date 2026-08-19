"""内置 hook:把生命周期挂点桥接到 EventEmitter,供 server WebSocket 广播。

放在 server 侧,保证 CLI 路径不加载本模块(对齐原 Orchestrator._emit 的惰性 import 约束)。
事件 data 字段与改动前的 _emit 调用点逐一对应,保持 server 可观测行为不变。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from deepflow.core.hook import Hook, HookContext
from deepflow.server.events import Event, EventType

if TYPE_CHECKING:
    from deepflow.core.component import StageResult
    from deepflow.models.manifest import StepConfig
    from deepflow.server.events import EventEmitter

logger = logging.getLogger(__name__)


class EventEmitterHook(Hook):
    """把挂点映射为 EventEmitter 事件,保持 server 可观测行为不变。

    thread_safe=True:AsyncEventBridge.emit 经 loop.call_soon_threadsafe 已线程安全。
    不实现 on_cases_ready——原本无对应事件。
    """

    thread_safe = True

    def __init__(self, emitter: EventEmitter, run_id: str, pipeline_name: str = "") -> None:
        super().__init__()
        self._emitter = emitter
        self._run_id = run_id
        self._pipeline_name = pipeline_name

    def _emit(self, event_type: EventType, **data: Any) -> None:
        try:
            self._emitter.emit(Event(type=event_type, run_id=self._run_id, data=data))
        except Exception:
            logger.debug("事件发射失败: %s", event_type.value, exc_info=True)

    def on_run_start(self, ctx: HookContext) -> None:
        self._emit(EventType.RUN_STARTED, name=self._pipeline_name)

    def on_run_finish(self, ctx: HookContext, status: str, error: Exception | None) -> None:
        event_type = {
            "completed": EventType.RUN_COMPLETED,
            "cancelled": EventType.RUN_CANCELLED,
        }.get(status, EventType.RUN_FAILED)
        self._emit(event_type)

    def emit_construction_failure(self) -> None:
        """Report a run that failed before an Orchestrator context existed."""
        self._emit(EventType.RUN_FAILED)

    def on_stage_start(self, ctx: HookContext, stage: str) -> None:
        self._emit(EventType.STAGE_STARTED, stage=stage)

    def on_stage_finish(self, ctx: HookContext, stage: str) -> None:
        self._emit(EventType.STAGE_COMPLETED, stage=stage)

    def on_step_start(self, ctx: HookContext, stage: str, step: StepConfig) -> None:
        data: dict[str, Any] = {"stage": stage, "step": step.src}
        if ctx.case is not None:
            data["case_id"] = ctx.case.id
        self._emit(EventType.STEP_STARTED, **data)

    def on_step_finish(self, ctx: HookContext, stage: str, step: StepConfig, result: StageResult) -> None:
        data: dict[str, Any] = {"stage": stage, "step": step.src, "status": result.status.value}
        if ctx.case is not None:
            data["case_id"] = ctx.case.id
        self._emit(EventType.STEP_COMPLETED, **data)

    def on_case_start(self, ctx: HookContext, index: int, total: int) -> None:
        self._emit(EventType.CASE_STARTED, case_id=ctx.case.id if ctx.case is not None else "")

    def on_case_finish(
        self, ctx: HookContext, status: str, duration_ms: float, completed: int, total: int
    ) -> None:
        event_type = EventType.CASE_COMPLETED if status == "success" else EventType.CASE_FAILED
        self._emit(
            event_type,
            case_id=ctx.case.id if ctx.case is not None else "",
            status=status,
            duration_ms=duration_ms,
        )
