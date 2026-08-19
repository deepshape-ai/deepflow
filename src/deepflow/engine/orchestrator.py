from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepflow.core.case_log import CaseLogHandler, case_ctx, run_ctx  # noqa: F401 — re-export
from deepflow.core.component import PreprocessOutput, StageStatus
from deepflow.core.context import CaseContext, PipelineContext
from deepflow.core.error_formatter import ErrorFormatter
from deepflow.core.errors import FatalError
from deepflow.core.hook import Hook, HookContext, HookDispatcher
from deepflow.core.iterator import BaseIterator
from deepflow.core.store import ContextStore
from deepflow.engine.hook_loader import HookLoader
from deepflow.engine.loader import ComponentLoader
from deepflow.metrics import MetricsCollector
from deepflow.models.dataset import DatasetItem
from deepflow.models.manifest import Manifest, StepConfig

logger = logging.getLogger(__name__)


class CancellationError(Exception):
    """Pipeline 被用户取消时抛出。"""


@dataclass(frozen=True)
class _CaseCompletion:
    hook_ctx: HookContext
    status: str
    duration_ms: float


class _FatalCase(FatalError):
    def __init__(self, error: FatalError, completion: _CaseCompletion) -> None:
        super().__init__(str(error))
        self.error = error
        self.completion = completion


class Orchestrator:
    """Pipeline 执行器，管理三阶段流水线的完整生命周期。

    生命周期扩展统一走 hook 总线(HookDispatcher):
    - 内置 hook(CLI 渲染 / server 事件)走 builtin 段,先于用户 hook
    - 用户 hook 经 manifest.hooks 声明或 add_hook() 编程注册
    CLI 与 server 通过 add_hook(..., builtin=True) 显式注册各自适配器。
    """

    def __init__(
        self,
        manifest: Manifest,
        manifest_dir: Path,
        *,
        run_id: str | None = None,
        cancel_event: threading.Event | None = None,
        builtin_hooks: Iterable[Hook] = (),
    ) -> None:
        self.manifest = manifest
        self.manifest_dir = manifest_dir
        self.workspace = manifest.workspace
        self.metrics_collector = MetricsCollector(self.workspace)

        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self._cancel = cancel_event
        self._pipeline_store = ContextStore()
        self._case_log: CaseLogHandler | None = None
        self._started = False

        # hook 总线:内置 hook 走 builtin 段(先于用户 hook)
        self._hooks = HookDispatcher()
        for hook in builtin_hooks:
            self._hooks.add(hook, builtin=True)
        for hook_cfg in manifest.hooks:
            self._hooks.add(HookLoader.load(hook_cfg.src, hook_cfg.config, manifest_dir))

        # case 总数由 casewise 阶段确定;完成排位由协调线程按 as_completed 顺序分配。
        self._total_cases = 0
        # run/stage 级挂点共享的 pipeline 级 hook 上下文(惰性建立)
        self._stage_hook_ctx: HookContext | None = None

        run_ctx.set(self.run_id)

    # ── 公共接口 ──────────────────────────────────────────────────

    def add_hook(self, hook: Hook, *, builtin: bool = False) -> Hook:
        """注册 hook。框架适配器用 builtin=True,业务 hook 使用默认用户段。"""
        if self._started:
            raise RuntimeError("运行开始后不能注册 hook")
        return self._hooks.add(hook, builtin=builtin)

    def dry_run(self) -> dict[str, Any]:
        """执行 preprocess 获取真实数据集,不触发生命周期 hook。"""
        self._begin()
        hooks = self._hooks
        self._hooks = HookDispatcher()
        try:
            return self._build_dry_run()
        finally:
            self._hooks = hooks

    def _build_dry_run(self) -> dict[str, Any]:
        """执行 preprocess 获取真实数据集，返回执行计划。"""
        logger.info("Starting dry-run: %s", self.manifest.name)

        # 实际执行 preprocess
        stage_ctx = self._pipeline_hook_ctx()
        self._hooks.dispatch("on_stage_start", ctx=stage_ctx, stage="preprocess")
        iterator = self._run_preprocess()
        self._hooks.dispatch("on_stage_finish", ctx=stage_ctx, stage="preprocess")

        # 收集 case 列表
        cases = list(iterator) if iterator else []

        # 收集 casewise step 信息
        casewise_steps = []
        for step_config in self.manifest.pipeline.casewise:
            try:
                comp_class = ComponentLoader.resolve_class(step_config.src, self.manifest_dir)
                casewise_steps.append({"src": step_config.src, "class_name": comp_class.__name__})
            except Exception:
                casewise_steps.append({"src": step_config.src, "class_name": "?"})

        # 收集 postprocess step 信息
        postprocess_steps = []
        for step_config in self.manifest.pipeline.postprocess:
            try:
                comp_class = ComponentLoader.resolve_class(step_config.src, self.manifest_dir)
                postprocess_steps.append({"src": step_config.src, "class_name": comp_class.__name__})
            except Exception:
                postprocess_steps.append({"src": step_config.src, "class_name": "?"})

        concurrency = self.manifest.concurrency
        estimated_batches = (len(cases) + concurrency - 1) // concurrency if concurrency > 0 else 0

        return {
            "pipeline_name": self.manifest.name,
            "workspace": str(self.workspace),
            "preprocess_steps": len(self.manifest.pipeline.preprocess),
            "total_cases": len(cases),
            "concurrency": concurrency,
            "casewise_steps": casewise_steps,
            "estimated_batches": estimated_batches,
            "postprocess_steps": postprocess_steps,
        }

    def run(self) -> None:
        """执行完整 pipeline"""
        self._begin()
        logger.info("Starting pipeline: %s", self.manifest.name)
        stage_ctx = self._pipeline_hook_ctx()

        try:
            self._hooks.dispatch("on_run_start", ctx=stage_ctx)
            self._write_run_state("running")
            self._check_cancelled()

            logger.info("=== Preprocess ===")
            self._hooks.dispatch("on_stage_start", ctx=stage_ctx, stage="preprocess")
            iterator = self._run_preprocess()
            self._hooks.dispatch("on_stage_finish", ctx=stage_ctx, stage="preprocess")

            self._check_cancelled()

            logger.info("=== Casewise ===")
            self._hooks.dispatch("on_stage_start", ctx=stage_ctx, stage="casewise")
            self._run_casewise(iterator)
            self._hooks.dispatch("on_stage_finish", ctx=stage_ctx, stage="casewise")

            self._check_cancelled()

            logger.info("=== Postprocess ===")
            self._hooks.dispatch("on_stage_start", ctx=stage_ctx, stage="postprocess")
            self._run_postprocess()
            self._hooks.dispatch("on_stage_finish", ctx=stage_ctx, stage="postprocess")

            self.metrics_collector.save_summary()
            self._write_run_state("completed")
            self._hooks.dispatch("on_run_finish", ctx=stage_ctx, status="completed", error=None)
            logger.info("Pipeline completed")

        except CancellationError as e:
            self._hooks.dispatch("on_run_finish", ctx=stage_ctx, status="cancelled", error=e)
            self._best_effort_write_run_state("cancelled")
            raise
        except Exception as e:
            self._hooks.dispatch("on_run_finish", ctx=stage_ctx, status="failed", error=e)
            self._best_effort_write_run_state("failed")
            raise

    def cancel(self) -> None:
        """请求取消运行（线程安全）。"""
        if self._cancel is not None:
            self._cancel.set()

    # ── hook 上下文与进度 ─────────────────────────────────────────

    def _begin(self) -> None:
        """Orchestrator 是一次性执行对象,禁止 dry-run/run 或 run/run 复用。"""
        if self._started:
            raise RuntimeError("Orchestrator 只能执行一次,请为新运行创建新实例")
        self._started = True

    def _hook_ctx(self, exec_ctx: PipelineContext | CaseContext) -> HookContext:
        """把当前阶段的执行 context 包进 hook 信封,附 run_id / metrics_collector。"""
        return HookContext(run_id=self.run_id, ctx=exec_ctx, metrics_collector=self.metrics_collector)

    def _pipeline_hook_ctx(self) -> HookContext:
        """run/stage 级挂点共享的 pipeline 级 hook 上下文(惰性建立,共享 store)。"""
        if self._stage_hook_ctx is None:
            self._stage_hook_ctx = self._hook_ctx(self._create_pipeline_context())
        return self._stage_hook_ctx

    def _dispatch_case_finish(self, completion: _CaseCompletion, completed: int) -> None:
        """由协调线程按完成顺序分发,保证 completed 对观察端严格单调。"""
        self._hooks.dispatch(
            "on_case_finish",
            ctx=completion.hook_ctx,
            status=completion.status,
            duration_ms=completion.duration_ms,
            completed=completed,
            total=self._total_cases,
        )

    # ── 取消 ─────────────────────────────────────────────────────

    def _check_cancelled(self) -> None:
        """检查取消标志，已设置则抛出 CancellationError。"""
        if self._cancel is not None and self._cancel.is_set():
            raise CancellationError("Pipeline cancelled by user")

    # ── 内部实现 ─────────────────────────────────────────────────

    def _write_run_state(self, status: str) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        state = {
            "run_id": self.run_id,
            "status": status,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed": self.metrics_collector.completed_count,
        }
        state_file = self.workspace / "run_state.json"
        with state_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def _best_effort_write_run_state(self, status: str) -> None:
        """失败收尾不能用二次落盘错误遮蔽原始异常。"""
        try:
            self._write_run_state(status)
        except Exception:
            logger.exception("无法写入 run_state.json 的终态 %s", status)

    def _create_pipeline_context(self) -> PipelineContext:
        return PipelineContext(
            workspace=self.workspace,
            metrics_collector=self.metrics_collector,
            vars=self.manifest.vars,
            store=self._pipeline_store,
        )

    def _create_case_context(self, case_item: DatasetItem) -> CaseContext:
        casespace = self.workspace / "cases" / case_item.id
        casespace.mkdir(parents=True, exist_ok=True)
        return CaseContext(case=case_item, casespace=casespace, vars=self.manifest.vars)

    def _load_step(self, step_config: StepConfig):
        return ComponentLoader.load(
            src=step_config.src,
            config=step_config.config,
            retry=step_config.retry,
            manifest_dir=self.manifest_dir,
        )

    def _run_preprocess(self) -> BaseIterator:
        ctx = self._create_pipeline_context()
        hook_ctx = self._hook_ctx(ctx)
        found_iterator: BaseIterator | None = None
        iterator_source: str | None = None

        for step_config in self.manifest.pipeline.preprocess:
            self._check_cancelled()
            logger.info("  Step: %s", step_config.src)
            self._hooks.dispatch("on_step_start", ctx=hook_ctx, stage="preprocess", step=step_config)
            component = self._load_step(step_config)
            result = component.run(ctx)
            self._hooks.dispatch("on_step_finish", ctx=hook_ctx, stage="preprocess",
                                 step=step_config, result=result)
            logger.info("  Status: %s", result.status.value)

            if result.status == StageStatus.FAILED:
                error_msg = ErrorFormatter.format_step_failure(
                    step_name=step_config.src,
                    reason=result.output.message,
                )
                raise RuntimeError(error_msg)

            if isinstance(result.output, PreprocessOutput) and result.output.iterator is not None:
                if found_iterator is not None:
                    raise RuntimeError(
                        f"多个 preprocess 组件提交了 iterator: {iterator_source} 和 {step_config.src}，"
                        f"只允许一个组件提交 iterator"
                    )
                found_iterator = result.output.iterator
                iterator_source = step_config.src

        if found_iterator is None:
            raise RuntimeError("preprocess 阶段必须恰好有一个组件提交 iterator，但没有组件提交")

        return found_iterator

    def _run_casewise(self, iterator: BaseIterator) -> None:
        cases = list(iter(iterator))
        total = len(cases)
        self._total_cases = total
        concurrency = self.manifest.concurrency
        logger.info("  Cases: %d, Concurrency: %d", total, concurrency)
        self._hooks.dispatch("on_cases_ready", ctx=self._pipeline_hook_ctx(), total=total)

        # per-case 日志路由：casewise 阶段挂载，阶段结束（含异常退出）后卸载
        case_log = CaseLogHandler(self.workspace / "cases")
        root_logger = logging.getLogger()
        root_logger.addHandler(case_log)
        self._case_log = case_log

        try:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="casewise") as executor:
                futures = {
                    executor.submit(self._run_single_case, case, index): case
                    for index, case in enumerate(cases)
                }

                completed = 0
                terminal_error: BaseException | None = None
                for future in as_completed(futures):
                    case_item = futures[future]

                    if (
                        terminal_error is None
                        and self._cancel is not None
                        and self._cancel.is_set()
                    ):
                        terminal_error = CancellationError("Pipeline cancelled by user")
                        for pending in futures:
                            pending.cancel()

                    try:
                        completion = future.result()
                        completed += 1
                        self._dispatch_case_finish(completion, completed)
                        logger.info("  [%d/%d] Case %s completed", completed, total, case_item.id)
                    except (CancellationError, FutureCancelledError) as error:
                        if terminal_error is None and isinstance(error, CancellationError):
                            terminal_error = error
                    except _FatalCase as fatal:
                        completed += 1
                        self._dispatch_case_finish(fatal.completion, completed)
                        if terminal_error is None:
                            terminal_error = fatal.error
                            for pending in futures:
                                pending.cancel()
                    except Exception as e:
                        error_msg = ErrorFormatter.format_case_error(
                            case_id=case_item.id,
                            error=e,
                            completed=completed,
                            total=total,
                        )
                        logger.error(error_msg)

                if terminal_error is not None:
                    raise terminal_error
        finally:
            self._case_log = None
            root_logger.removeHandler(case_log)
            case_log.close()

    def _run_single_case(self, case_item: DatasetItem, index: int) -> _CaseCompletion:
        run_ctx.set(self.run_id)
        case_ctx.set(case_item.id)

        start_time = time.time()
        ctx = self._create_case_context(case_item)
        hook_ctx = self._hook_ctx(ctx)
        self._hooks.dispatch("on_case_start", ctx=hook_ctx, index=index,
                             total=self._total_cases, concurrent=True)

        case_metrics: dict[str, Any] = {}
        case_status = "success"
        error_type = ""
        error_message = ""
        failed_step = ""

        try:
            for step_config in self.manifest.pipeline.casewise:
                failed_step = step_config.src
                self._check_cancelled()
                self._hooks.dispatch("on_step_start", ctx=hook_ctx, stage="casewise",
                                     step=step_config, concurrent=True)
                component = self._load_step(step_config)
                result = component.run(ctx)
                self._hooks.dispatch("on_step_finish", ctx=hook_ctx, stage="casewise",
                                     step=step_config, result=result, concurrent=True)

                if hasattr(result.output, "metrics"):
                    case_metrics.update(result.output.metrics)

                if result.status not in (StageStatus.SUCCESS, StageStatus.SKIPPED):
                    case_status = "failed"
                    error = result.error
                    if error is not None:
                        error_type = type(error).__name__
                        error_message = str(error) or error_type
                        # 异常携带的 partial metrics 保留进 case 记录
                        partial = getattr(error, "metrics", None)
                        if isinstance(partial, dict):
                            case_metrics.update(partial)
                    else:
                        # 防御路径：FAILED 结果未携带异常（不应发生）
                        error_type = "StepFailed"
                        error_message = result.output.message
                    logger.warning("  Case %s failed at %s", case_item.id, step_config.src)
                    break
        except CancellationError:
            raise
        except FatalError as e:
            # FatalError 终止整条 pipeline；先落盘该 case 便于事后诊断
            duration = (time.time() - start_time) * 1000
            self.metrics_collector.record(
                case_id=case_item.id,
                metrics=case_metrics,
                status="failed",
                duration_ms=duration,
                error_type=type(e).__name__,
                error_message=str(e) or type(e).__name__,
                failed_step=failed_step,
            )
            raise _FatalCase(e, _CaseCompletion(hook_ctx, "failed", duration)) from e
        except Exception as e:
            case_status = "failed"
            error_type = type(e).__name__
            error_message = str(e) or error_type
            partial = getattr(e, "metrics", None)
            if isinstance(partial, dict):
                case_metrics.update(partial)
            error_msg = ErrorFormatter.format_case_error(
                case_id=case_item.id,
                error=e,
            )
            logger.error(error_msg)
        finally:
            case_ctx.set("")
            # 关闭该 case 的日志句柄（需在重置 case_ctx 之后，保证不再有新记录路由进来）
            if self._case_log is not None:
                with suppress(Exception):
                    self._case_log.close_case(case_item.id)

        duration_ms = (time.time() - start_time) * 1000
        self.metrics_collector.record(
            case_id=case_item.id,
            metrics=case_metrics,
            status=case_status,
            duration_ms=duration_ms,
            error_type=error_type,
            error_message=error_message,
            failed_step=failed_step,
        )

        return _CaseCompletion(hook_ctx, case_status, duration_ms)

    def _run_postprocess(self) -> None:
        ctx = self._create_pipeline_context()
        hook_ctx = self._hook_ctx(ctx)

        for step_config in self.manifest.pipeline.postprocess:
            self._check_cancelled()
            logger.info("  Step: %s", step_config.src)
            self._hooks.dispatch("on_step_start", ctx=hook_ctx, stage="postprocess", step=step_config)
            component = self._load_step(step_config)
            result = component.run(ctx)
            self._hooks.dispatch("on_step_finish", ctx=hook_ctx, stage="postprocess",
                                 step=step_config, result=result)
            logger.info("  Status: %s", result.status.value)
