from __future__ import annotations

import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepflow.core.case_log import CaseLogHandler, case_ctx, run_ctx  # noqa: F401 — re-export
from deepflow.core.component import PreprocessOutput, StageStatus
from deepflow.core.context import CaseContext, PipelineContext
from deepflow.core.error_formatter import ErrorFormatter
from deepflow.core.errors import FatalError
from deepflow.core.iterator import BaseIterator
from deepflow.core.store import ContextStore
from deepflow.engine.loader import ComponentLoader
from deepflow.metrics import MetricsCollector
from deepflow.models.dataset import DatasetItem
from deepflow.models.manifest import Manifest, StepConfig

if TYPE_CHECKING:
    from deepflow.server.events import EventEmitter

logger = logging.getLogger(__name__)


class CancellationError(Exception):
    """Pipeline 被用户取消时抛出。"""


class Orchestrator:
    """Pipeline 执行器，管理三阶段流水线的完整生命周期。

    可选的 event_emitter 和 cancel_event 参数使 Orchestrator 可被 API 层驱动，
    同时保持 CLI 路径的零开销（两者默认 None，所有新逻辑被跳过）。
    """

    def __init__(
        self,
        manifest: Manifest,
        manifest_dir: Path,
        *,
        run_id: str | None = None,
        event_emitter: EventEmitter | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.manifest = manifest
        self.manifest_dir = manifest_dir
        self.workspace = manifest.workspace
        self.metrics_collector = MetricsCollector(self.workspace)

        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self._emitter = event_emitter
        self._cancel = cancel_event
        self._pipeline_store = ContextStore()
        self._renderer: Any = None
        self._case_log: CaseLogHandler | None = None

        run_ctx.set(self.run_id)

    # ── 公共接口 ──────────────────────────────────────────────────

    def set_renderer(self, renderer: Any) -> None:
        """设置 CLI 渲染器。"""
        self._renderer = renderer

    def _notify_renderer(self, method: str, **kwargs: Any) -> None:
        """安全调用 renderer 回调。"""
        if self._renderer and hasattr(self._renderer, method):
            try:
                getattr(self._renderer, method)(**kwargs)
            except Exception:
                pass  # renderer 错误不应影响 pipeline 执行

    def dry_run(self) -> dict[str, Any]:
        """执行 preprocess 获取真实数据集，返回执行计划。"""
        self._setup_import_path()
        logger.info("Starting dry-run: %s", self.manifest.name)

        # 实际执行 preprocess
        self._notify_renderer("on_stage_started", stage="preprocess")
        iterator = self._run_preprocess()
        self._notify_renderer("on_stage_completed", stage="preprocess")

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
        self._setup_import_path()
        logger.info("Starting pipeline: %s", self.manifest.name)
        self._emit("run.started", name=self.manifest.name)
        self._write_run_state("running")

        try:
            self._check_cancelled()

            logger.info("=== Preprocess ===")
            self._emit("stage.started", stage="preprocess")
            self._notify_renderer("on_stage_started", stage="preprocess")
            iterator = self._run_preprocess()
            self._emit("stage.completed", stage="preprocess")
            self._notify_renderer("on_stage_completed", stage="preprocess")

            self._check_cancelled()

            logger.info("=== Casewise ===")
            self._emit("stage.started", stage="casewise")
            self._notify_renderer("on_stage_started", stage="casewise")
            self._run_casewise(iterator)
            self._emit("stage.completed", stage="casewise")
            self._notify_renderer("on_stage_completed", stage="casewise")

            self._check_cancelled()

            logger.info("=== Postprocess ===")
            self._emit("stage.started", stage="postprocess")
            self._notify_renderer("on_stage_started", stage="postprocess")
            self._run_postprocess()
            self._emit("stage.completed", stage="postprocess")
            self._notify_renderer("on_stage_completed", stage="postprocess")

            self.metrics_collector.save_summary()
            self._write_run_state("completed")
            self._emit("run.completed")
            logger.info("Pipeline completed")

        except CancellationError:
            self._write_run_state("cancelled")
            self._emit("run.cancelled")
            raise
        except Exception:
            self._write_run_state("failed")
            self._emit("run.failed")
            raise

    def cancel(self) -> None:
        """请求取消运行（线程安全）。"""
        if self._cancel is not None:
            self._cancel.set()

    def _setup_import_path(self) -> None:
        """将 manifest 所在目录加入 sys.path，使本地组件可以互相 import。"""
        manifest_str = str(self.manifest_dir)
        if manifest_str not in sys.path:
            sys.path.insert(0, manifest_str)
            logger.debug("Added manifest dir to sys.path: %s", manifest_str)

    # ── 事件与取消 ────────────────────────────────────────────────

    def _emit(self, event_type_value: str, **data: Any) -> None:
        """有 emitter 才发射事件，CLI 路径为 no-op。

        惰性导入 server.events，确保 CLI 运行时不加载 server 包。
        """
        if self._emitter is None:
            return
        from deepflow.server.events import Event, EventType

        try:
            self._emitter.emit(Event(
                type=EventType(event_type_value),
                run_id=self.run_id,
                data=data,
            ))
        except Exception:
            logger.debug("Event emission failed for %s", event_type_value, exc_info=True)

    def _check_cancelled(self) -> None:
        """检查取消标志，已设置则抛出 CancellationError。"""
        if self._cancel is not None and self._cancel.is_set():
            raise CancellationError("Pipeline cancelled by user")

    # ── 内部实现（与原有逻辑一致，仅在关键点插入 _emit / _check_cancelled）──

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
        found_iterator: BaseIterator | None = None
        iterator_source: str | None = None

        for step_config in self.manifest.pipeline.preprocess:
            self._check_cancelled()
            logger.info("  Step: %s", step_config.src)
            self._emit("step.started", stage="preprocess", step=step_config.src)
            component = self._load_step(step_config)
            result = component.run(ctx)
            self._emit("step.completed", stage="preprocess", step=step_config.src,
                        status=result.status.value)
            self._notify_renderer("on_step_completed", stage="preprocess")
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
        concurrency = self.manifest.concurrency
        logger.info("  Cases: %d, Concurrency: %d", total, concurrency)
        self._notify_renderer("on_preprocess_iterator", total=total)

        # per-case 日志路由：casewise 阶段挂载，阶段结束（含异常退出）后卸载
        case_log = CaseLogHandler(self.workspace / "cases")
        root_logger = logging.getLogger()
        root_logger.addHandler(case_log)
        self._case_log = case_log

        try:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="casewise") as executor:
                futures = {executor.submit(self._run_single_case, case): case for case in cases}

                for completed, future in enumerate(as_completed(futures), 1):
                    case_item = futures[future]

                    # 取消时不再等待剩余 future
                    if self._cancel is not None and self._cancel.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise CancellationError("Pipeline cancelled by user")

                    try:
                        future.result()
                        logger.info("  [%d/%d] Case %s completed", completed, total, case_item.id)
                    except CancellationError:
                        # worker 内检测到取消：交由下一轮循环顶部的取消检查统一抛出
                        continue
                    except FatalError:
                        # FatalError 终止整条 pipeline，不再等待剩余 future
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise
                    except Exception as e:
                        error_msg = ErrorFormatter.format_case_error(
                            case_id=case_item.id,
                            error=e,
                            completed=completed,
                            total=total,
                        )
                        logger.error(error_msg)
        finally:
            self._case_log = None
            root_logger.removeHandler(case_log)
            case_log.close()

    def _run_single_case(self, case_item: DatasetItem) -> None:
        run_ctx.set(self.run_id)
        case_ctx.set(case_item.id)

        start_time = time.time()
        ctx = self._create_case_context(case_item)
        self._emit("case.started", case_id=case_item.id)

        case_metrics: dict[str, Any] = {}
        case_status = "success"
        error_type = ""
        error_message = ""
        failed_step = ""

        try:
            for step_config in self.manifest.pipeline.casewise:
                failed_step = step_config.src
                self._check_cancelled()
                self._emit("step.started", stage="casewise", step=step_config.src,
                            case_id=case_item.id)
                component = self._load_step(step_config)
                result = component.run(ctx)
                self._emit("step.completed", stage="casewise", step=step_config.src,
                            case_id=case_item.id, status=result.status.value)

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
            self._emit("case.failed", case_id=case_item.id, status="failed",
                       duration_ms=duration)
            raise
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
                try:
                    self._case_log.close_case(case_item.id)
                except Exception:
                    pass  # 日志清理失败不影响执行结果

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

        event_type = "case.completed" if case_status == "success" else "case.failed"
        self._emit(event_type, case_id=case_item.id, status=case_status, duration_ms=duration_ms)
        if case_status == "success":
            self._notify_renderer("on_case_completed", case_id=case_item.id)
        else:
            self._notify_renderer("on_case_failed", case_id=case_item.id)

    def _run_postprocess(self) -> None:
        ctx = self._create_pipeline_context()

        for step_config in self.manifest.pipeline.postprocess:
            self._check_cancelled()
            logger.info("  Step: %s", step_config.src)
            self._emit("step.started", stage="postprocess", step=step_config.src)
            component = self._load_step(step_config)
            result = component.run(ctx)
            self._emit("step.completed", stage="postprocess", step=step_config.src,
                        status=result.status.value)
            self._notify_renderer("on_step_completed", stage="postprocess")
            logger.info("  Status: %s", result.status.value)
