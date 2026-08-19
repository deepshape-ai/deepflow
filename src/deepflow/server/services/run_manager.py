"""
运行生命周期管理。

职责：
    - 在后台线程启动 Orchestrator
    - 内存中跟踪活跃运行
    - 持久化运行状态到 JSON 文件
    - 为 WebSocket 提供事件桥接
    - 处理取消请求

线程模型：
    FastAPI 事件循环线程
        └── create_run() → executor.submit(_execute_run)
                                └── 后台线程：Orchestrator.run()
                                    ├── _emit() → loop.call_soon_threadsafe()
                                    └── ThreadPoolExecutor (casewise workers)
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepflow.engine.orchestrator import CancellationError, Orchestrator, case_ctx, run_ctx
from deepflow.models.manifest import Manifest
from deepflow.server.events import AsyncEventBridge
from deepflow.server.hooks import EventEmitterHook
from deepflow.server.models import ProgressInfo, RunResponse, RunStatus

logger = logging.getLogger(__name__)


class _RunLogFilter(logging.Filter):
    """基于 contextvars 隔离并发 run 的日志。

    每个 run 的 FileHandler 附带此 filter，仅放行当前 run_id 的日志记录。
    同时将 run_id / case_id 注入 LogRecord 供 Formatter 使用。
    """

    __slots__ = ("_run_id",)

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_ctx.get("")  # type: ignore[attr-defined]
        record.case_id = case_ctx.get("")  # type: ignore[attr-defined]
        return record.run_id == self._run_id  # type: ignore[attr-defined]


class RunState:
    """单次运行的内存状态。

    在 RunManager 内部使用，封装运行的全部可变状态。
    外部通过 to_response() 获取不可变的 API 响应视图。
    """

    def __init__(
        self,
        run_id: str,
        manifest: Manifest | None = None,
        manifest_dir: Path | None = None,
        *,
        pipeline_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.manifest = manifest
        self.manifest_dir = manifest_dir
        self.pipeline_id = pipeline_id

        self.status: RunStatus = RunStatus.PENDING
        self.created_at: datetime = datetime.now(timezone.utc)
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.error: str | None = None
        self.total_cases: int = 0

        self.cancel_event: threading.Event = threading.Event()
        self.event_bridge: AsyncEventBridge | None = None
        self.orchestrator: Orchestrator | None = None

    def to_response(self) -> RunResponse:
        """生成当前快照的 API 响应（线程安全读取 metrics）。"""
        completed = 0
        failed = 0
        if self.orchestrator:
            cases = self.orchestrator.metrics_collector.cases
            completed = len(cases)
            failed = sum(1 for c in cases.values() if c.status == "failed")

        return RunResponse(
            id=self.run_id,
            pipeline_id=self.pipeline_id,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            progress=ProgressInfo(
                total_cases=self.total_cases,
                completed_cases=completed,
                failed_cases=failed,
            ),
            error=self.error,
        )


class RunManager:
    """运行管理器 — 应用级单例，管理所有 Pipeline 运行的生命周期。"""

    def __init__(self, runs_dir: Path, *, max_workers: int = 4) -> None:
        self._runs_dir = runs_dir
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="run-mgr",
        )
        self._load_persisted_runs()

    # ── 公共接口 ──────────────────────────────────────────────────

    async def create_run(
        self,
        manifest_dict: dict[str, Any],
        *,
        manifest_dir: Path | None = None,
        pipeline_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> RunState:
        """验证 manifest、分配 run_id、隔离 workspace、提交后台执行。

        Args:
            manifest_dict: 完整 manifest 配置字典。
            manifest_dir: 组件路径解析根目录。Pipeline 运行时传入 pipeline 目录；
                          直接运行时为 None，仅支持 plugin 组件。
            pipeline_id: 关联的 pipeline ID（可选）。
            overrides: 覆盖 manifest 配置（可选）。支持根级配置及各 stage 步骤
                       的 config 覆盖。格式示例::

                           {
                               "concurrency": 4,
                               "casewise": {"0": {"ai_endpoint": "http://..."}}
                           }
        """
        # 合并 overrides
        effective = deepcopy(manifest_dict)
        if overrides:
            # 根级框架参数覆盖
            for key in ("workspace", "concurrency"):
                if key in overrides:
                    effective[key] = overrides[key]
            # vars 覆盖
            if "vars" in overrides:
                effective.setdefault("vars", {}).update(overrides["vars"])
            pipeline = effective.get("pipeline", {})
            for stage in ("preprocess", "casewise", "postprocess"):
                stage_patch = overrides.get(stage)
                if not stage_patch:
                    continue
                steps = pipeline.get(stage, [])
                for idx_str, config_patch in stage_patch.items():
                    idx = int(idx_str)
                    if 0 <= idx < len(steps):
                        steps[idx].setdefault("config", {}).update(config_patch)

        manifest = Manifest.model_validate(effective)
        run_id = uuid.uuid4().hex[:8]

        # 隔离 workspace：每次运行使用独立目录
        run_workspace = self._runs_dir / run_id / "workspace"
        manifest.workspace = run_workspace

        # manifest_dir 决定组件路径解析根目录
        # Pipeline 运行 → pipeline 目录；直接运行 → run 自身目录（仅内置组件可用）
        if manifest_dir is not None:
            source_snapshot = self._runs_dir / run_id / "source"
            shutil.copytree(manifest_dir, source_snapshot)
            effective_dir = source_snapshot
        else:
            effective_dir = self._runs_dir / run_id
        state = RunState(run_id, manifest, effective_dir, pipeline_id=pipeline_id)

        # 创建事件桥接
        loop = asyncio.get_running_loop()
        state.event_bridge = AsyncEventBridge(loop)

        with self._lock:
            self._runs[run_id] = state

        # 提交到后台线程
        self._executor.submit(self._execute_run, state)
        return state

    def get_run(self, run_id: str) -> RunState | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self, pipeline_id: str | None = None) -> list[RunState]:
        with self._lock:
            runs = list(self._runs.values())
        if pipeline_id:
            runs = [r for r in runs if r.pipeline_id == pipeline_id]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def cancel_run(self, run_id: str) -> bool:
        """请求取消运行。返回是否成功发起取消。"""
        state = self.get_run(run_id)
        if state is None or state.status != RunStatus.RUNNING:
            return False
        state.cancel_event.set()
        return True

    def delete_run(self, run_id: str) -> bool:
        """删除已完成的运行（内存 + 磁盘）。运行中的不允许删除。"""
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return False
            if state.status in (RunStatus.PENDING, RunStatus.RUNNING):
                return False
            del self._runs[run_id]

        # 清理磁盘
        run_dir = self._runs_dir / run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)
        return True

    def get_run_log_path(self, run_id: str) -> Path | None:
        """返回运行日志文件路径，不存在则返回 None。"""
        path = self._runs_dir / run_id / "run.log"
        return path if path.exists() else None

    async def shutdown(self) -> None:
        """优雅关闭：取消所有活跃运行，等待线程退出。"""
        with self._lock:
            active = [s for s in self._runs.values() if s.status == RunStatus.RUNNING]
        for s in active:
            s.cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)

    # ── 内部实现 ──────────────────────────────────────────────────

    def _load_persisted_runs(self) -> None:
        """启动时从磁盘加载已完成的运行记录，使历史运行可查询/可删除。"""
        for run_dir in self._runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            state_file = run_dir / "run.json"
            run_id = run_dir.name
            if not state_file.exists():
                # 孤儿目录（运行中被强制终止，未写 run.json）：标记为 FAILED 以便用户删除
                state = RunState(run_id)
                state.status = RunStatus.FAILED
                state.error = "运行异常终止（未正常写入状态文件）"
                self._runs[run_id] = state
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                state = RunState(data["run_id"], pipeline_id=data.get("pipeline_id"))
                state.status = RunStatus(data["status"])
                state.created_at = datetime.fromisoformat(data["created_at"])
                state.started_at = (
                    datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
                )
                state.completed_at = (
                    datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
                )
                state.error = data.get("error")
                self._runs[state.run_id] = state
            except Exception:
                logger.warning("Failed to load persisted run from %s", run_dir)

    def _execute_run(self, state: RunState) -> None:
        """在后台线程中执行 Pipeline。这是同步世界的入口。"""
        state.status = RunStatus.RUNNING
        state.started_at = datetime.now(timezone.utc)
        run_token = run_ctx.set(state.run_id)
        case_token = case_ctx.set("")
        log_handler: logging.FileHandler | None = None
        final_status = RunStatus.FAILED
        final_error: str | None = None
        assert state.event_bridge is not None
        event_hook = EventEmitterHook(
            state.event_bridge,
            run_id=state.run_id,
            pipeline_name=state.manifest.name,
        )
        try:
            run_dir = self._runs_dir / state.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            log_handler = self._attach_run_log(run_dir / "run.log", state.run_id)
            orchestrator = Orchestrator(
                manifest=state.manifest,
                manifest_dir=state.manifest_dir,
                run_id=state.run_id,
                cancel_event=state.cancel_event,
                builtin_hooks=[event_hook],
            )
            state.orchestrator = orchestrator
            orchestrator.run()
            final_status = RunStatus.COMPLETED
        except CancellationError:
            final_status = RunStatus.CANCELLED
        except Exception as e:
            final_status = RunStatus.FAILED
            final_error = str(e)
            if state.orchestrator is None:
                event_hook.emit_construction_failure()
            logger.exception("Run %s failed", state.run_id)
        finally:
            state.completed_at = datetime.now(timezone.utc)
            if log_handler is not None:
                try:
                    self._detach_run_log(log_handler)
                except Exception:
                    logger.exception("Run %s log cleanup failed", state.run_id)
            state.error = final_error
            state.status = final_status
            try:
                self._persist_run(state)
            except Exception:
                logger.exception("Run %s final state persistence failed", state.run_id)
            case_ctx.reset(case_token)
            run_ctx.reset(run_token)

    @staticmethod
    def _attach_run_log(log_path: Path, run_id: str) -> logging.FileHandler:
        """为本次运行创建独立日志文件，通过 _RunLogFilter 隔离并发 run。"""
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(run_id)s/%(case_id)s] %(name)s %(levelname)s - %(message)s",
        ))
        handler.addFilter(_RunLogFilter(run_id))
        logging.getLogger().addHandler(handler)
        return handler

    @staticmethod
    def _detach_run_log(handler: logging.FileHandler) -> None:
        """运行结束后移除日志 handler，关闭文件。"""
        logging.getLogger().removeHandler(handler)
        handler.close()

    def _persist_run(self, state: RunState) -> None:
        """将运行最终状态写入磁盘。"""
        run_dir = self._runs_dir / state.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": state.run_id,
            "pipeline_id": state.pipeline_id,
            "status": state.status.value,
            "created_at": state.created_at.isoformat(),
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "completed_at": state.completed_at.isoformat() if state.completed_at else None,
            "error": state.error,
        }
        state_file = run_dir / "run.json"
        temp = state_file.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp.replace(state_file)
