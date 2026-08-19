"""CLI 侧的生命周期适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepflow.core.hook import Hook, HookContext

if TYPE_CHECKING:
    from deepflow.core.component import StageResult
    from deepflow.models.manifest import StepConfig


class CliRendererHook(Hook):
    """把通用生命周期事件映射到 CLI PipelineRenderer。"""

    thread_safe = True

    def __init__(self, renderer: Any) -> None:
        super().__init__()
        self._renderer = renderer

    def _call(self, method: str, **kwargs: Any) -> None:
        callback = getattr(self._renderer, method, None)
        if callback is not None:
            callback(**kwargs)

    def on_stage_start(self, ctx: HookContext, stage: str) -> None:
        self._call("on_stage_started", stage=stage)

    def on_stage_finish(self, ctx: HookContext, stage: str) -> None:
        self._call("on_stage_completed", stage=stage)

    def on_cases_ready(self, ctx: HookContext, total: int) -> None:
        self._call("on_preprocess_iterator", total=total)

    def on_step_finish(
        self, ctx: HookContext, stage: str, step: StepConfig, result: StageResult
    ) -> None:
        if stage in ("preprocess", "postprocess"):
            self._call("on_step_completed", stage=stage)

    def on_case_finish(
        self, ctx: HookContext, status: str, duration_ms: float, completed: int, total: int
    ) -> None:
        case_id = ctx.case.id if ctx.case is not None else ""
        if status == "success":
            self._call("on_case_completed", case_id=case_id)
        else:
            self._call("on_case_failed", case_id=case_id)
