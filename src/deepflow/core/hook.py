"""统一的生命周期 Hook 总线。

deepflow 的三阶段流水线(preprocess → casewise 并发 → postprocess)在各层级暴露
只读观察挂点,让使用者在不修改组件代码的前提下横切插入日志、追踪、指标、通知等逻辑。

组成:
    Hook            观察 hook 基类,9 个挂点方法默认空实现,只覆盖关心的
    HookContext     传给所有挂点的"信封":run_id + 当前执行 ctx + pipeline 级句柄
    HookDispatcher  注册与分发:内置优先 + 注册 FIFO + fail-open + 并发保护

设计纪律:
    - 纯观察:hook 不修改 ctx 数据,无返回值语义;任何异常被吞掉记日志,绝不影响主流程
    - 并发:worker 线程挂点中,thread_safe=False 的 hook 由框架按 hook 粒度串行化
    - 可靠性:hook 同步执行且 fail-open;远程 IO 应自行设置超时或写入 durable outbox
    - 本模块不得 import deepflow.server(保持 CLI 路径零 server 依赖)
"""

from __future__ import annotations

import itertools
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepflow.core.context import CaseContext, PipelineContext
from deepflow.core.store import ContextStore

if TYPE_CHECKING:
    from deepflow.core.component import StageResult
    from deepflow.metrics import MetricsCollector
    from deepflow.models.dataset import DatasetItem
    from deepflow.models.manifest import StepConfig

logger = logging.getLogger(__name__)

@dataclass
class HookContext:
    """传给所有 hook 挂点的统一上下文("信封")。

    并非第三种数据 context:`ctx` 就是组件拿到的同一个 PipelineContext /
    CaseContext 对象,此处额外补 run_id 与 pipeline 级 metrics_collector,
    并提供便捷只读 property。

    pipeline 级挂点(ctx 为 PipelineContext)时 case 为 None;
    case 级挂点(ctx 为 CaseContext)时 case 非 None。
    """

    run_id: str
    ctx: PipelineContext | CaseContext
    metrics_collector: MetricsCollector

    @property
    def case(self) -> DatasetItem | None:
        """当前 case:case 级挂点非 None,pipeline 级挂点为 None。"""
        return self.ctx.case if isinstance(self.ctx, CaseContext) else None

    @property
    def vars(self) -> dict[str, Any]:
        return self.ctx.vars

    @property
    def store(self) -> ContextStore:
        return self.ctx.store

    @property
    def workspace(self) -> Path:
        if isinstance(self.ctx, PipelineContext):
            return self.ctx.workspace
        return self.metrics_collector.workspace


class Hook:
    """生命周期观察 hook 基类。

    所有挂点方法默认空实现,子类只覆盖关心的;第一参数均为 HookContext。

    类属性:
        Config:       可选的 pydantic BaseModel 内部类,声明并校验 config(对齐组件体系)
        thread_safe:  对 worker 线程挂点有意义——False(默认)时框架
                      按 hook 粒度加锁串行化;True 时 hook 自行保证线程安全,框架不加锁

    纯观察约定:不修改 ctx 数据;任何异常被框架吞掉记日志,绝不影响 pipeline。
    """

    Config: type | None = None
    thread_safe: bool = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw_config = config or {}
        self.config = self.Config.model_validate(raw_config) if self.Config else raw_config

    # ── run 级(主线程)────────────────────────────────────────────
    def on_run_start(self, ctx: HookContext) -> None:
        """pipeline 开始运行时触发。"""
        ...

    def on_run_finish(self, ctx: HookContext, status: str, error: Exception | None) -> None:
        """pipeline 结束时触发。status ∈ {"completed", "failed", "cancelled"}。"""
        ...

    # ── stage 级(主线程)──────────────────────────────────────────
    def on_stage_start(self, ctx: HookContext, stage: str) -> None:
        """阶段(preprocess/casewise/postprocess)开始时触发。"""
        ...

    def on_stage_finish(self, ctx: HookContext, stage: str) -> None:
        """阶段完成时触发。"""
        ...

    def on_cases_ready(self, ctx: HookContext, total: int) -> None:
        """preprocess 产出 case 列表、casewise 开始前触发,给出 case 总数。"""
        ...

    # ── step 级(preprocess/postprocess 于主线程;casewise 于 worker 线程)──
    def on_step_start(self, ctx: HookContext, stage: str, step: StepConfig) -> None:
        """单个组件执行前触发。"""
        ...

    def on_step_finish(self, ctx: HookContext, stage: str, step: StepConfig, result: StageResult) -> None:
        """单个组件执行后触发(含 skipped / failed)。"""
        ...

    # ── case 级(worker 线程并发)─────────────────────────────────
    def on_case_start(self, ctx: HookContext, index: int, total: int) -> None:
        """单个 case 开始处理时触发。index 为提交顺序(0-based)。"""
        ...

    def on_case_finish(
        self, ctx: HookContext, status: str, duration_ms: float, completed: int, total: int
    ) -> None:
        """单个 case 处理完毕触发。completed 为完成排位(1-based,已含本 case)。"""
        ...


class HookDispatcher:
    """hook 注册与分发中心。

    - 顺序:内置(builtin)段先于用户(user)段,段内按注册 FIFO
    - fail-open:单个 hook 抛异常只记日志,不影响其他 hook 与主流程
    - 并发:concurrent=True 时对 thread_safe=False 的 hook 按 hook 粒度加锁串行化

    dispatcher 不硬编码任何挂点名,纯靠 getattr 反射分发——新增挂点无需改这里。
    """

    def __init__(self) -> None:
        self._builtin: list[Hook] = []
        self._user: list[Hook] = []
        self._locks: dict[Hook, threading.Lock] = {}

    def add(self, hook: Hook, *, builtin: bool = False) -> Hook:
        """注册 hook 并原样返回(便于链式)。builtin=True 排在内置段(先于用户段)。"""
        (self._builtin if builtin else self._user).append(hook)
        if not hook.thread_safe:
            self._locks[hook] = threading.Lock()
        return hook

    def dispatch(self, point: str, *, concurrent: bool = False, **kwargs: Any) -> None:
        """向所有定义了挂点 point 的 hook 分发一次调用。

        concurrent=True 表示当前在 worker 线程:对 thread_safe=False 的 hook
        用其专属锁串行化。
        """
        for hook in itertools.chain(self._builtin, self._user):
            hook_method = getattr(type(hook), point, None)
            if hook_method is None or hook_method is getattr(Hook, point, None):
                continue
            method = getattr(hook, point)
            try:
                lock = self._locks.get(hook) if concurrent else None
                if lock is not None:
                    with lock:
                        method(**kwargs)
                else:
                    method(**kwargs)
            except Exception:
                logger.debug(
                    "hook %s 在挂点 %s 抛异常,已忽略",
                    type(hook).__name__, point, exc_info=True,
                )

    def __len__(self) -> int:
        return len(self._builtin) + len(self._user)
